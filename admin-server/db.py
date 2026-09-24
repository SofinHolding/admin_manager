"""Tầng CSDL cho admin web — ĐỌC bảng sync, ĐỌC/GHI bảng riêng.

Kết nối CÙNG Postgres (database ufsync) với sync server, nhưng chạy ở process riêng (port 8421).
Bảng `documents`, `user_meta` chỉ ĐỌC — sync server là người ghi duy nhất. Ba bảng mới
(`invite_keys`, `accounts`, `refresh_tokens`) do admin-server quản lý, sync server không biết tới.

Tách biệt ghi chú: nếu cần sửa DDL bảng `documents` thì sửa ở `server/db.py`, KHÔNG sửa ở đây.
File này không import gì từ `server/` — hai process không chia sẻ code runtime.

SQLite cho test: cùng lý do với `server/db.py` — toàn bộ auth flow chạy được mà không cần Postgres.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import string
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import stats_core

logger = logging.getLogger("admin.db")

# ── DDL bảng MỚI (admin-server sở hữu) ────────────────────────────────────────
#
# Payload của `documents` lưu dạng chuỗi JSON — admin-server parse bằng `json.loads` khi cần aggregate.
# Không dùng `jsonb` vì sync server cũng lưu TEXT, và admin-server chỉ ĐỌC nên không có quyền ALTER.

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS invite_keys (
    key         TEXT PRIMARY KEY,
    role        TEXT NOT NULL DEFAULT 'viewer',
    label       TEXT NOT NULL DEFAULT '',
    max_uses    INTEGER NOT NULL DEFAULT 1,
    used_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    invite_key      TEXT NOT NULL,
    email_verified  INTEGER NOT NULL DEFAULT 0,
    verify_token    TEXT NOT NULL DEFAULT '',
    verify_token_at TEXT NOT NULL DEFAULT '',
    reset_token     TEXT NOT NULL DEFAULT '',
    reset_token_at  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    last_login      TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
"""

_PG_DDL = """
CREATE TABLE IF NOT EXISTS invite_keys (
    key         text PRIMARY KEY,
    role        text NOT NULL DEFAULT 'viewer',
    label       text NOT NULL DEFAULT '',
    max_uses    integer NOT NULL DEFAULT 1,
    used_count  integer NOT NULL DEFAULT 0,
    created_at  text NOT NULL,
    expires_at  text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS accounts (
    id              text PRIMARY KEY,
    username        text NOT NULL UNIQUE,
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    role            text NOT NULL DEFAULT 'viewer',
    invite_key      text NOT NULL,
    email_verified  boolean NOT NULL DEFAULT false,
    verify_token    text NOT NULL DEFAULT '',
    verify_token_at text NOT NULL DEFAULT '',
    reset_token     text NOT NULL DEFAULT '',
    reset_token_at  text NOT NULL DEFAULT '',
    created_at      text NOT NULL,
    last_login      text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       text PRIMARY KEY,
    account_id  text NOT NULL,
    created_at  text NOT NULL,
    expires_at  text NOT NULL,
    revoked     boolean NOT NULL DEFAULT false
);
"""


def _gen_key() -> str:
    """Tạo invite key ngắn gọn dạng XXXX-XXXX — chữ in hoa + số, dễ copy-paste."""
    chars = string.ascii_uppercase + string.digits
    p1 = "".join(secrets.choice(chars) for _ in range(4))
    p2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"{p1}-{p2}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_between(a: str, b: str) -> list[str]:
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


# ── Giao diện ───────────────────────────────────────────────────────────────────

class Db:
    """Giao diện chung cho SQLite (test) và Postgres (production)."""

    async def setup(self) -> None: ...
    async def close(self) -> None: ...

    # ── Invite key ──────────────────────────────────────────────────────────────
    async def get_invite_key(self, key: str) -> dict | None: ...
    async def use_invite_key(self, key: str) -> bool: ...

    # ── Account ─────────────────────────────────────────────────────────────────
    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict: ...
    async def get_account_by_username(self, username: str) -> dict | None: ...
    async def get_account_by_email(self, email: str) -> dict | None: ...
    async def get_account_by_id(self, account_id: str) -> dict | None: ...
    async def username_exists(self, username: str) -> bool: ...
    async def email_exists(self, email: str) -> bool: ...
    async def set_email_verified(self, account_id: str) -> None: ...
    async def set_verify_token(self, account_id: str, token: str) -> None: ...
    async def get_account_by_verify_token(self, token: str) -> dict | None: ...
    async def set_reset_token(self, account_id: str, token: str) -> None: ...
    async def get_account_by_reset_token(self, token: str) -> dict | None: ...
    async def clear_reset_token(self, account_id: str) -> None: ...
    async def update_password(self, account_id: str, password_hash: str) -> None: ...
    async def update_last_login(self, account_id: str) -> None: ...

    # ── Refresh token ───────────────────────────────────────────────────────────
    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str: ...
    async def get_refresh_token(self, token: str) -> dict | None: ...
    async def revoke_refresh_token(self, token: str) -> None: ...
    async def revoke_all_refresh_tokens(self, account_id: str) -> None: ...

    # ── Admin management ─────────────────────────────────────────────────────────
    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict: ...
    async def list_invite_keys(self) -> list[dict]: ...
    async def revoke_invite_key(self, key: str) -> bool: ...
    async def list_accounts(self) -> list[dict]: ...
    async def set_account_status(self, account_id: str, new_status: str) -> bool: ...
    async def delete_account(self, account_id: str) -> bool: ...
    async def update_account_profile(self, account_id: str, updates: dict) -> None: ...
    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool: ...

    # ── Viewer data (ĐỌC XUYÊN USER từ bảng sync) ──────────────────────────────
    #
    # Admin-server đọc `documents` để dựng biểu đồ — cùng dữ liệu admin cũ ở `server/admin.py`
    # nhưng chạy ở process riêng. KHÔNG GHI vào `documents` hay `user_meta`.
    async def traffic_metric(self, date_from: str, date_to: str,
                             scope: str = "", country: str = "") -> dict: ...
    async def summary(self, date_from: str, date_to: str, country: str = "") -> dict: ...
    async def top_channels(self, date_from: str, date_to: str, country: str = "",
                           limit: int = 15) -> list[dict]: ...
    async def views_by_country(self, date_from: str, date_to: str) -> dict: ...
    async def country_tags(self) -> list[str]: ...
    async def doc_version(self) -> str: ...


# ── SQLite ──────────────────────────────────────────────────────────────────────

class SqliteDb(Db):
    """Cho test — cùng pattern với `server/db.py`."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    async def setup(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SQLITE_DDL)
            self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Invite key ──────────────────────────────────────────────────────────────

    async def get_invite_key(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM invite_keys WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    async def use_invite_key(self, key: str) -> bool:
        """Tăng `used_count` nếu còn lượt. Trả False nếu hết lượt hoặc bị thu hồi.

        Kiểm TẠI THỜI ĐIỂM GHI chứ không tách kiểm rồi ghi: hai người paste cùng key cùng lúc mà
        chỉ kiểm trước thì cả hai đều thấy "còn lượt" rồi cùng tăng — vượt trần."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE invite_keys SET used_count = used_count + 1 "
                "WHERE key = ? AND status = 'active' AND used_count < max_uses", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Account ─────────────────────────────────────────────────────────────────

    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict:
        account_id = uuid.uuid4().hex
        now = _now()
        verify_token = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO accounts "
                "(id, username, email, password_hash, role, invite_key, "
                " verify_token, verify_token_at, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (account_id, username, email.lower(), password_hash, role, invite_key,
                 verify_token, now, now))
            self._conn.commit()
        return {"id": account_id, "username": username, "email": email.lower(),
                "role": role, "verify_token": verify_token, "status": "pending"}

    async def get_account_by_username(self, username: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    async def get_account_by_email(self, email: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    async def get_account_by_id(self, account_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None

    async def username_exists(self, username: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM accounts WHERE username = ?", (username,)).fetchone()
        return row is not None

    async def email_exists(self, email: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM accounts WHERE email = ?", (email.lower(),)).fetchone()
        return row is not None

    async def set_email_verified(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET email_verified = 1, status = 'active', "
                "verify_token = '' WHERE id = ?", (account_id,))
            self._conn.commit()

    async def set_verify_token(self, account_id: str, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET verify_token = ?, verify_token_at = ? WHERE id = ?",
                (token, _now(), account_id))
            self._conn.commit()

    async def get_account_by_verify_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE verify_token = ? AND verify_token != ''",
                (token,)).fetchone()
        return dict(row) if row else None

    async def set_reset_token(self, account_id: str, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET reset_token = ?, reset_token_at = ? WHERE id = ?",
                (token, _now(), account_id))
            self._conn.commit()

    async def get_account_by_reset_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE reset_token = ? AND reset_token != ''",
                (token,)).fetchone()
        return dict(row) if row else None

    async def clear_reset_token(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET reset_token = '', reset_token_at = '' WHERE id = ?",
                (account_id,))
            self._conn.commit()

    async def update_password(self, account_id: str, password_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = ?",
                (password_hash, account_id))
            self._conn.commit()

    async def update_last_login(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET last_login = ? WHERE id = ?", (_now(), account_id))
            self._conn.commit()

    # ── Refresh token ───────────────────────────────────────────────────────────

    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str:
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=ttl_days)
        with self._lock:
            self._conn.execute(
                "INSERT INTO refresh_tokens (token, account_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token, account_id, now.isoformat(timespec="seconds"),
                 expires.isoformat(timespec="seconds")))
            self._conn.commit()
        return token

    async def get_refresh_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM refresh_tokens WHERE token = ? AND revoked = 0",
                (token,)).fetchone()
        if not row:
            return None
        r = dict(row)
        # Hết hạn thì coi như không có — caller không phải kiểm lại.
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc):
            return None
        return r

    async def revoke_refresh_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE token = ?", (token,))
            self._conn.commit()

    async def revoke_all_refresh_tokens(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE account_id = ?", (account_id,))
            self._conn.commit()

    # ── Admin management ─────────────────────────────────────────────────────────

    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict:
        """Tạo invite key mới — key tự sinh dạng XXXX-XXXX, hết hạn sau `expires_days` ngày."""
        key = _gen_key()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=expires_days)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO invite_keys (key, role, label, max_uses, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, role, label, max_uses, now.isoformat(timespec="seconds"), expires))
            self._conn.commit()
        return {"key": key, "role": role, "label": label, "max_uses": max_uses,
                "expires_at": expires, "status": "active", "used_count": 0,
                "created_at": now.isoformat(timespec="seconds")}

    async def list_invite_keys(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM invite_keys ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    async def revoke_invite_key(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE invite_keys SET status = 'revoked' WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    async def list_accounts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, username, email, role, invite_key, email_verified, "
                "created_at, last_login, status "
                "FROM accounts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    async def set_account_status(self, account_id: str, new_status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE accounts SET status = ? WHERE id = ?", (new_status, account_id))
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_account(self, account_id: str) -> bool:
        """Xoá tài khoản và revoke mọi refresh token — chỉ admin mới gọi được."""
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE account_id = ?", (account_id,))
            cur = self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def update_account_profile(self, account_id: str, updates: dict) -> None:
        """User tự cập nhật hồ sơ — chỉ cho phép username, email."""
        allowed = {"username", "email"}
        parts = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ?")
                vals.append(v)
        if not parts:
            return
        vals.append(account_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ?", tuple(vals))
            self._conn.commit()

    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool:
        """Admin chỉnh sửa user — cho phép username, email, role."""
        allowed = {"username", "email", "role"}
        parts = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ?")
                vals.append(v)
        if not parts:
            return True
        vals.append(account_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ?", tuple(vals))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Viewer data ─────────────────────────────────────────────────────────────
    #
    # Đọc từ bảng `documents` — bảng này do sync server tạo (DDL ở `server/db.py`). Admin-server
    # KHÔNG tạo bảng này; nếu bảng chưa tồn tại (test environment) thì trả rỗng thay vì nổ lỗi.

    def _safe_docs_query(self, sql: str, args: tuple) -> list[dict]:
        """Truy vấn bảng `documents` — trả [] nếu bảng chưa tồn tại (môi trường test)."""
        with self._lock:
            try:
                rows = self._conn.execute(sql, args).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    return []
                raise

    def _load_fb(self, country: str = "") -> list[dict]:
        """Tải fb_posts — lọc quốc gia nếu cần (cùng dùng cho summary/traffic/top).

        Import script lưu target gốc trong trường `_target` (để tránh trùng tên trường khác).
        Chuẩn hoá thành `target` để code sau dùng chung — giống Electron fb_store."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'fb_posts' AND deleted = 0", ())
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("_empty"):
                continue
            if country and p.get("country", "") != country:
                continue
            # Chuẩn hoá: đảm bảo mọi post đều có trường `target` dùng được
            if not p.get("target") and p.get("_target"):
                p["target"] = p["_target"]
            result.append(p)
        return result

    def _load_yt(self, country: str = "") -> list[dict]:
        """Tải yt_insights_daily — lọc quốc gia nếu cần."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'yt_insights_daily' AND deleted = 0", ())
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if country and p.get("country", "") != country:
                continue
            result.append(p)
        return result

    def _load_events(self) -> list[dict]:
        """Tải repost_events — nhật ký đăng bài (dùng đếm videos_posted trong summary).

        Electron đọc từ repost_events.json (list_by_id shape), admin đọc từ documents table.
        Chỉ lấy event có kind — bỏ record rác."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'repost_events' AND deleted = 0", ())
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("kind"):
                result.append(p)
        return result

    def _load_pairings(self) -> list[dict]:
        """Tải repost_pairings — cặp reup (dùng đếm managed/active channels trong summary).

        Electron đọc từ repost_pairings.json (dict_of_records shape)."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'repost_pairings' AND deleted = 0", ())
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("target"):
                result.append(p)
        return result

    def _load_groups(self) -> list[dict]:
        """Tải repost_groups — nhóm reup (dùng kiểm cho_chay trong summary)."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'repost_groups' AND deleted = 0", ())
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("id"):
                result.append(p)
        return result

    def _fb_page_meta(self) -> list[dict]:
        """Projection fb_page_meta (target → tên+quốc gia page) do Tool đẩy lên — thay HOÀN TOÀN
        lối đọc `fb_pages` cũ (tên page bị seal ở production). Shape dict_of_records."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'fb_page_meta' AND deleted = 0", ())
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            t = p.get("target")
            if t:
                out.append({"target": t, "name": p.get("name") or t,
                            "country": p.get("country") or ""})
        return out

    def _yt_channels_meta(self) -> list[dict]:
        """Meta kênh YouTube (id/title/country) từ store `yt_channels` — KHÔNG đọc từ
        `yt_insights_daily` (không có các trường này). Bỏ bản ghi `_meta` (không có `id`)."""
        rows = self._safe_docs_query(
            "SELECT payload FROM documents WHERE store = 'yt_channels' AND deleted = 0", ())
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row.get("payload") else {}
            cid = p.get("id")
            if cid:
                out.append({"id": cid, "title": p.get("title") or "",
                            "country": p.get("country") or ""})
        return out

    async def traffic_metric(self, date_from: str, date_to: str,
                             scope: str = "", country: str = "") -> dict:
        """Traffic theo chỉ số — nay tính qua `stats_core` (nguồn CHUNG với Tool)."""
        meta = self._fb_page_meta()
        tc = stats_core.target_country_map(meta, self._load_pairings(), self._load_groups())
        chans = self._yt_channels_meta()
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)
        return stats_core.traffic_metric(
            date_from, date_to,
            fb_posts=self._load_fb(), yt_daily=self._load_yt(),
            events=self._load_events(), scope=scope, country=country,
            yt_filter=yt_f, fb_filter=fb_f)

    async def summary(self, date_from: str, date_to: str, country: str = "") -> dict:
        """KPI tổng — nay tính qua `stats_core`. Kỳ trước: cùng độ dài, liền kề (khớp router Tool)."""
        d0 = date.fromisoformat(date_from)
        so_ngay = len(stats_core.days_between(date_from, date_to))
        prev_from = (d0 - timedelta(days=so_ngay)).isoformat()
        prev_to = (d0 - timedelta(days=1)).isoformat()

        meta = self._fb_page_meta()
        pairings = self._load_pairings()
        groups = self._load_groups()
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = self._yt_channels_meta()
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)

        events = self._load_events()
        ev_now = [e for e in events if date_from <= (e.get("date") or "") <= date_to]
        ev_prev = [e for e in events if prev_from <= (e.get("date") or "") <= prev_to]

        return stats_core.summary(
            date_from, date_to,
            fb_posts=self._load_fb(), yt_daily=self._load_yt(),
            events=ev_now, prev_events=ev_prev,
            prev_from=prev_from, prev_to=prev_to,
            pairings=pairings, groups=groups,
            yt_filter=yt_f, fb_filter=fb_f)

    async def top_channels(self, date_from: str, date_to: str, country: str = "",
                           limit: int = 15) -> list[dict]:
        """Top kênh/page theo views — nay tính qua `stats_core`. Trả list (viewer bọc {"items"})."""
        meta = self._fb_page_meta()
        tc = stats_core.target_country_map(meta, self._load_pairings(), self._load_groups())
        tn = stats_core.target_names(meta)
        chans = self._yt_channels_meta()
        result = stats_core.top_channels(
            date_from, date_to,
            fb_posts=self._load_fb(), yt_daily=self._load_yt(),
            channel_meta=chans, target_names=tn, target_country=tc,
            country=country, limit=limit)
        return result["items"]

    async def views_by_country(self, date_from: str, date_to: str) -> dict:
        """Views theo quốc gia/ngày — nay tính qua `stats_core`."""
        meta = self._fb_page_meta()
        tc = stats_core.target_country_map(meta, self._load_pairings(), self._load_groups())
        chans = self._yt_channels_meta()
        return stats_core.views_by_country(
            date_from, date_to,
            fb_posts=self._load_fb(), yt_daily=self._load_yt(),
            channel_meta=chans, target_country=tc)

    async def country_tags(self) -> list[str]:
        """Danh mục quốc gia — nay tính qua `stats_core`."""
        meta = self._fb_page_meta()
        tc = stats_core.target_country_map(meta, self._load_pairings(), self._load_groups())
        chans = self._yt_channels_meta()
        return stats_core.country_tags(channel_meta=chans, target_country=tc)

    async def doc_version(self) -> str:
        """Con dấu phiên bản kho `documents` — đổi khi có ghi mới.

        Bản SQLite dùng cho dev/test: `import_local_data.py` xoá sạch rồi nạp lại nên cả số bản
        ghi lẫn `MAX(rev)` đều đổi. Bảng chưa tồn tại → `_safe_docs_query` trả [] → mốc cố định,
        lúc đó TTL của cache là thứ duy nhất giới hạn tuổi dữ liệu."""
        rows = self._safe_docs_query(
            "SELECT COUNT(*) AS n, COALESCE(MAX(rev), 0) AS r FROM documents", ())
        if not rows:
            return "na"
        return f"{rows[0]['n']}.{rows[0]['r']}"


# ── Postgres ────────────────────────────────────────────────────────────────────

class PostgresDb(Db):
    """Cho triển khai thật — cùng CSDL ufsync với sync server."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def setup(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
        async with self._pool.acquire() as c:
            await c.execute(_PG_DDL)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Invite key ──────────────────────────────────────────────────────────────

    async def get_invite_key(self, key: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM invite_keys WHERE key = $1", key)
        return dict(row) if row else None

    async def use_invite_key(self, key: str) -> bool:
        """Xem ghi chú bản SQLite — cùng ý: kiểm và ghi CÙNG LÚC trong một câu UPDATE."""
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE invite_keys SET used_count = used_count + 1 "
                "WHERE key = $1 AND status = 'active' AND used_count < max_uses", key)
        # asyncpg trả "UPDATE 0" hoặc "UPDATE 1"
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Account ─────────────────────────────────────────────────────────────────

    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict:
        account_id = uuid.uuid4().hex
        now = _now()
        verify_token = uuid.uuid4().hex
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO accounts "
                "(id, username, email, password_hash, role, invite_key, "
                " verify_token, verify_token_at, created_at, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending')",
                account_id, username, email.lower(), password_hash, role, invite_key,
                verify_token, now, now)
        return {"id": account_id, "username": username, "email": email.lower(),
                "role": role, "verify_token": verify_token, "status": "pending"}

    async def get_account_by_username(self, username: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE username = $1", username)
        return dict(row) if row else None

    async def get_account_by_email(self, email: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE email = $1", email.lower())
        return dict(row) if row else None

    async def get_account_by_id(self, account_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        return dict(row) if row else None

    async def username_exists(self, username: str) -> bool:
        async with self._pool.acquire() as c:
            return await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM accounts WHERE username = $1)", username)

    async def email_exists(self, email: str) -> bool:
        async with self._pool.acquire() as c:
            return await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM accounts WHERE email = $1)", email.lower())

    async def set_email_verified(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET email_verified = true, status = 'active', "
                "verify_token = '' WHERE id = $1", account_id)

    async def set_verify_token(self, account_id: str, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET verify_token = $2, verify_token_at = $3 WHERE id = $1",
                account_id, token, _now())

    async def get_account_by_verify_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM accounts WHERE verify_token = $1 AND verify_token != ''", token)
        return dict(row) if row else None

    async def set_reset_token(self, account_id: str, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET reset_token = $2, reset_token_at = $3 WHERE id = $1",
                account_id, token, _now())

    async def get_account_by_reset_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM accounts WHERE reset_token = $1 AND reset_token != ''", token)
        return dict(row) if row else None

    async def clear_reset_token(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET reset_token = '', reset_token_at = '' WHERE id = $1",
                account_id)

    async def update_password(self, account_id: str, password_hash: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET password_hash = $2 WHERE id = $1",
                account_id, password_hash)

    async def update_last_login(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET last_login = $2 WHERE id = $1", account_id, _now())

    # ── Refresh token ───────────────────────────────────────────────────────────

    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str:
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=ttl_days)
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO refresh_tokens (token, account_id, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4)",
                token, account_id, now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"))
        return token

    async def get_refresh_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM refresh_tokens WHERE token = $1 AND NOT revoked", token)
        if not row:
            return None
        r = dict(row)
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc):
            return None
        return r

    async def revoke_refresh_token(self, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE token = $1", token)

    async def revoke_all_refresh_tokens(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE account_id = $1", account_id)

    # ── Admin management ─────────────────────────────────────────────────────────

    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict:
        key = _gen_key()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=expires_days)).isoformat(timespec="seconds")
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO invite_keys (key, role, label, max_uses, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                key, role, label, max_uses, now.isoformat(timespec="seconds"), expires)
        return {"key": key, "role": role, "label": label, "max_uses": max_uses,
                "expires_at": expires, "status": "active", "used_count": 0,
                "created_at": now.isoformat(timespec="seconds")}

    async def list_invite_keys(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM invite_keys ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def revoke_invite_key(self, key: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE invite_keys SET status = 'revoked' WHERE key = $1", key)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def list_accounts(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, username, email, role, invite_key, email_verified, "
                "created_at, last_login, status "
                "FROM accounts ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def set_account_status(self, account_id: str, new_status: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE accounts SET status = $2 WHERE id = $1", account_id, new_status)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def delete_account(self, account_id: str) -> bool:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE account_id = $1", account_id)
            tag = await c.execute("DELETE FROM accounts WHERE id = $1", account_id)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def update_account_profile(self, account_id: str, updates: dict) -> None:
        allowed = {"username", "email"}
        parts = []
        vals = []
        idx = 1
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ${idx}")
                vals.append(v)
                idx += 1
        if not parts:
            return
        vals.append(account_id)
        async with self._pool.acquire() as c:
            await c.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ${idx}", *vals)

    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool:
        allowed = {"username", "email", "role"}
        parts = []
        vals = []
        idx = 1
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ${idx}")
                vals.append(v)
                idx += 1
        if not parts:
            return True
        vals.append(account_id)
        async with self._pool.acquire() as c:
            tag = await c.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ${idx}", *vals)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Viewer data ─────────────────────────────────────────────────────────────

    async def _load_fb_pg(self, country: str = "") -> list[dict]:
        """Xem ghi chú `_load_fb` bản SQLite — cùng logic chuẩn hoá `_target` → `target`."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'fb_posts' AND NOT deleted")
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            if p.get("_empty"):
                continue
            if country and p.get("country", "") != country:
                continue
            if not p.get("target") and p.get("_target"):
                p["target"] = p["_target"]
            result.append(p)
        return result

    async def _load_yt_pg(self, country: str = "") -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents "
                "WHERE store = 'yt_insights_daily' AND NOT deleted")
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            if country and p.get("country", "") != country:
                continue
            result.append(p)
        return result

    async def _load_events_pg(self) -> list[dict]:
        """Xem ghi chú `_load_events` bản SQLite."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'repost_events' AND NOT deleted")
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("kind")]

    async def _load_pairings_pg(self) -> list[dict]:
        """Xem ghi chú `_load_pairings` bản SQLite."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'repost_pairings' AND NOT deleted")
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("target")]

    async def _load_groups_pg(self) -> list[dict]:
        """Xem ghi chú `_load_groups` bản SQLite."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'repost_groups' AND NOT deleted")
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("id")]

    async def _fb_page_meta_pg(self) -> list[dict]:
        """Xem `_fb_page_meta` bản SQLite — đọc projection fb_page_meta từ documents."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'fb_page_meta' AND NOT deleted")
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            t = p.get("target")
            if t:
                out.append({"target": t, "name": p.get("name") or t,
                            "country": p.get("country") or ""})
        return out

    async def _yt_channels_meta_pg(self) -> list[dict]:
        """Xem `_yt_channels_meta` bản SQLite — đọc store `yt_channels` (title/country plaintext)."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT payload FROM documents WHERE store = 'yt_channels' AND NOT deleted")
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            cid = p.get("id")
            if cid:
                out.append({"id": cid, "title": p.get("title") or "",
                            "country": p.get("country") or ""})
        return out

    async def traffic_metric(self, date_from: str, date_to: str,
                             scope: str = "", country: str = "") -> dict:
        meta = await self._fb_page_meta_pg()
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(),
                                           await self._load_groups_pg())
        chans = await self._yt_channels_meta_pg()
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)
        return stats_core.traffic_metric(
            date_from, date_to,
            fb_posts=await self._load_fb_pg(), yt_daily=await self._load_yt_pg(),
            events=await self._load_events_pg(), scope=scope, country=country,
            yt_filter=yt_f, fb_filter=fb_f)

    async def summary(self, date_from: str, date_to: str, country: str = "") -> dict:
        d0 = date.fromisoformat(date_from)
        so_ngay = len(stats_core.days_between(date_from, date_to))
        prev_from = (d0 - timedelta(days=so_ngay)).isoformat()
        prev_to = (d0 - timedelta(days=1)).isoformat()

        meta = await self._fb_page_meta_pg()
        pairings = await self._load_pairings_pg()
        groups = await self._load_groups_pg()
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = await self._yt_channels_meta_pg()
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)

        events = await self._load_events_pg()
        ev_now = [e for e in events if date_from <= (e.get("date") or "") <= date_to]
        ev_prev = [e for e in events if prev_from <= (e.get("date") or "") <= prev_to]

        return stats_core.summary(
            date_from, date_to,
            fb_posts=await self._load_fb_pg(), yt_daily=await self._load_yt_pg(),
            events=ev_now, prev_events=ev_prev,
            prev_from=prev_from, prev_to=prev_to,
            pairings=pairings, groups=groups,
            yt_filter=yt_f, fb_filter=fb_f)

    async def top_channels(self, date_from: str, date_to: str, country: str = "",
                           limit: int = 15) -> list[dict]:
        meta = await self._fb_page_meta_pg()
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(),
                                           await self._load_groups_pg())
        tn = stats_core.target_names(meta)
        chans = await self._yt_channels_meta_pg()
        result = stats_core.top_channels(
            date_from, date_to,
            fb_posts=await self._load_fb_pg(), yt_daily=await self._load_yt_pg(),
            channel_meta=chans, target_names=tn, target_country=tc,
            country=country, limit=limit)
        return result["items"]

    async def views_by_country(self, date_from: str, date_to: str) -> dict:
        meta = await self._fb_page_meta_pg()
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(),
                                           await self._load_groups_pg())
        chans = await self._yt_channels_meta_pg()
        return stats_core.views_by_country(
            date_from, date_to,
            fb_posts=await self._load_fb_pg(), yt_daily=await self._load_yt_pg(),
            channel_meta=chans, target_country=tc)

    async def country_tags(self) -> list[str]:
        meta = await self._fb_page_meta_pg()
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(),
                                           await self._load_groups_pg())
        chans = await self._yt_channels_meta_pg()
        return stats_core.country_tags(channel_meta=chans, target_country=tc)

    async def doc_version(self) -> str:
        """Con dấu phiên bản kho `documents` — đổi khi và chỉ khi sync server ghi bản ghi mới.

        `revs.next_rev` tăng đúng một lần cho MỖI lần ghi document (`server/db.py` `next_rev`),
        nên tổng của cột đó là con dấu cho toàn bộ kho. `revs` chỉ một dòng mỗi license nên quét
        hết bảng vẫn dưới 1ms — rẻ hơn nhiều so với việc phục vụ số liệu cũ.

        Bảng `revs` do sync server tạo. CSDL sạch (sync server chưa chạy lần nào) → trả mốc cố
        định thay vì nổ lỗi; khi đó TTL là thứ duy nhất giới hạn tuổi cache."""
        try:
            async with self._pool.acquire() as c:
                v = await c.fetchval("SELECT COALESCE(SUM(next_rev), 0) FROM revs")
            return str(int(v or 0))
        except Exception as e:  # noqa: BLE001 — thiếu bảng/lỗi tạm thời không được làm hỏng request
            logger.debug("doc_version that bai, dung moc co dinh: %s", e)
            return "na"


# ── LocalFileDb — đọc thẳng data/*.json, cùng nguồn với Electron ────────────────
#
# Dùng khi MODE=dev: admin-server đọc trực tiếp từ thư mục data/ của Electron, không cần bước
# import hay Postgres. Mọi thay đổi trong Electron (thêm pairing, đăng bài xong tạo event, cào
# view mới) lập tức phản ánh lên admin web khi load lại trang — cùng file, cùng logic đọc.
#
# Auth (accounts, invite_keys, refresh_tokens) vẫn dùng SQLite tách riêng (SQLITE_AUTH_PATH).
# Viewer data (fb_posts, yt_insights_daily, repost_events, repost_pairings, repost_groups, fb_pages)
# đọc thẳng từ JSON theo đúng hình dạng mà Electron ghi — không qua bảng documents.

class LocalFileDb(SqliteDb):
    """Dev mode: viewer data từ data/*.json, auth từ SQLite riêng.

    Đảm bảo 100% đồng nhất với Electron: cùng file nguồn, cùng hình dạng JSON, cùng logic tính —
    không cần bước import thủ công và không có độ trễ cập nhật."""

    def __init__(self, sqlite_auth_path: str, data_dir: str) -> None:
        super().__init__(sqlite_auth_path)
        self._data_dir = os.path.abspath(data_dir)

    # ── Đọc file JSON thô ────────────────────────────────────────────────────────

    def _read_json(self, filename: str, default=None):
        """Đọc file JSON từ data_dir — trả `default` nếu không tồn tại hoặc lỗi parse."""
        path = os.path.join(self._data_dir, filename)
        if not os.path.exists(path):
            return default if default is not None else {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default if default is not None else {}

    # ── Override _load_* để đọc từ JSON thay vì documents table ────────────────

    def _load_fb(self, country: str = "") -> list[dict]:
        """fb_posts.json — shape posts_by_target: {target: [post, ...]}

        Chuẩn hoá: gắn `target` vào mỗi bài để code phía sau dùng chung với SqliteDb._load_fb.
        Electron đọc cùng file này trong insights_store.views_by_day."""
        data = self._read_json("fb_posts.json") or {}
        result = []
        for target, posts in data.items():
            if not isinstance(posts, list):
                continue
            for post in posts:
                if not isinstance(post, dict):
                    continue
                p = {**post, "target": target}
                # country không lưu ở level bài — bỏ qua lọc nếu có yêu cầu
                if country and p.get("country", "") != country:
                    continue
                result.append(p)
        return result

    def _load_yt(self, country: str = "") -> list[dict]:
        """yt_insights_daily.json — shape dict_of_records: {"YYYY-MM-DD|channel_id": record}.

        Electron đọc cùng file này trong insights_store (yt)."""
        data = self._read_json("yt_insights_daily.json") or {}
        result = []
        for _key, record in data.items():
            if not isinstance(record, dict):
                continue
            if country and record.get("country", "") != country:
                continue
            result.append(record)
        return result

    def _load_events(self) -> list[dict]:
        """repost_events.json — shape list_by_id: [{id, kind, date, target, ...}].

        Electron đọc qua event_log.get_event_log().between(). Chỉ lấy event có `kind`."""
        data = self._read_json("repost_events.json", default=[]) or []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict) and e.get("kind")]

    def _load_pairings(self) -> list[dict]:
        """repost_pairings.json — shape dict_of_records: {id: pairing_record}.

        Electron đọc qua pairing_store.list(). Lấy các record có `target`."""
        data = self._read_json("repost_pairings.json") or {}
        if not isinstance(data, dict):
            return []
        return [v for v in data.values() if isinstance(v, dict) and v.get("target")]

    def _load_groups(self) -> list[dict]:
        """repost_groups.json — shape list_by_id: [{id, name, country, enabled, ...}].

        Electron đọc qua group_store.list_groups(). Lấy record có `id`."""
        data = self._read_json("repost_groups.json", default=[]) or []
        if not isinstance(data, list):
            return []
        return [g for g in data if isinstance(g, dict) and g.get("id")]

    def _fb_page_meta(self) -> list[dict]:
        """fb_page_meta.json (projection Tool đẩy). Nếu chưa rebuild lần nào → dựng thẳng từ
        fb_pages.json để dev luôn có tên+quốc gia (khớp production đọc bản đã sync)."""
        data = self._read_json("fb_page_meta.json") or {}
        if isinstance(data, dict) and data:
            return [v for v in data.values() if isinstance(v, dict) and v.get("target")]
        fb_pages = self._read_json("fb_pages.json") or {}
        page_countries = fb_pages.get("page_countries") or {}
        out: list[dict] = []
        for conn in fb_pages.get("connections") or []:
            if not isinstance(conn, dict):
                continue
            for pg in conn.get("pages") or []:
                if not isinstance(pg, dict):
                    continue
                pid = str(pg.get("id") or "")
                if not pid:
                    continue
                kind = "profile" if pg.get("kind") == "profile" else "page"
                out.append({"target": f"{kind}:{pid}", "name": pg.get("name") or pid,
                            "country": (page_countries.get(pid) or "").strip()})
        return out

    def _yt_channels_meta(self) -> list[dict]:
        """yt_channels.json (container `channels`) → {id,title,country}. Electron đọc list_channels()."""
        data = self._read_json("yt_channels.json") or {}
        out: list[dict] = []
        for ch in (data.get("channels") or []):
            if not isinstance(ch, dict):
                continue
            cid = ch.get("id")
            if cid:
                out.append({"id": cid, "title": ch.get("title") or "",
                            "country": ch.get("country") or ""})
        return out

    # Tên file khớp đúng các `_read_json` ở trên — đổi danh sách này khi thêm nguồn đọc mới,
    # nếu không cache sẽ không thấy file mới thay đổi.
    _VIEWER_FILES = ("fb_posts.json", "yt_insights_daily.json", "repost_events.json",
                     "repost_pairings.json", "repost_groups.json", "fb_page_meta.json",
                     "fb_pages.json", "yt_channels.json")

    async def doc_version(self) -> str:
        """Dev mode: con dấu từ (mtime, size) của các file JSON mà viewer thực sự đọc.

        Electron ghi đè file → mtime đổi → con dấu đổi → cache tự vô hiệu."""
        parts: list[str] = []
        for name in self._VIEWER_FILES:
            try:
                st = os.stat(os.path.join(self._data_dir, name))
                parts.append(f"{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append("-")
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ── Factory ─────────────────────────────────────────────────────────────────────

def make_db(url: str, data_dir: str = "") -> Db:
    """Tạo Db phù hợp từ URL.

    Scheme hỗ trợ:
    - ``sqlite:///path``  → SqliteDb (test/dev với import script)
    - ``postgresql://…``  → PostgresDb (production VPS)
    - ``file:///path``    → LocalFileDb (dev: đọc thẳng data/*.json, auth SQLite riêng)
      Kèm ``data_dir`` là thư mục data/ của Electron.
    """
    if url.startswith("file:///"):
        # Dev mode: đọc thẳng file JSON. SQLite auth ở thư mục cạnh data_dir.
        sqlite_auth = url[len("file:///"):]  # path tới file SQLite auth
        return LocalFileDb(sqlite_auth, data_dir or "../data")
    if url.startswith("sqlite:///"):
        return SqliteDb(url[len("sqlite:///"):])
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDb(url)
    raise ValueError(f"DATABASE_URL không hiểu được: {url!r}")


def _tong(m: dict[str, int | None]) -> int | None:
    """`None` nếu không có ngày nào có số — khác hẳn tổng bằng 0.

    `0` là "có dữ liệu, không ai xem"; `None` là "chưa cào ngày nào". Vẽ giống nhau lên biểu đồ là
    đọc sai số liệu."""
    co = [v for v in m.values() if v is not None]
    return sum(co) if co else None
