#!/usr/bin/env python3
"""
Local SQLite database cho channel import (local-fallback-tool).

Đọc JSON command từ stdin, ghi JSON result ra stdout.
Dùng Python built-in sqlite3 — không cần cài thêm thư viện.

Usage (từ Electron main):
  echo '{"action":"init"}' | python channels_db.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

# ── Đường dẫn ─────────────────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
DB_PATH = os.path.join(ROOT_DIR, ".dev", "channels.db")

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS fetched_users (
  id          TEXT PRIMARY KEY,
  username    TEXT NOT NULL,
  fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS local_channels (
  id              TEXT PRIMARY KEY,
  owner_id        TEXT NOT NULL,
  owner_username  TEXT NOT NULL,
  platform        TEXT NOT NULL,
  resource_type   TEXT NOT NULL DEFAULT 'channel',
  display_name    TEXT NOT NULL DEFAULT '',
  input_ref       TEXT NOT NULL DEFAULT '',
  canonical_id    TEXT NOT NULL DEFAULT '',
  url             TEXT NOT NULL DEFAULT '',
  mapping_status  TEXT NOT NULL DEFAULT '',
  enabled         INTEGER NOT NULL DEFAULT 1,
  imported_at     TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lc_owner    ON local_channels (owner_id);
CREATE INDEX IF NOT EXISTS idx_lc_platform ON local_channels (platform);
"""

CURRENT_SCHEMA_VERSION = 1
KNOWN_PLATFORMS = frozenset({"youtube", "tiktok", "facebook"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_platform(raw: str) -> str:
    """Phân loại platform: youtube|tiktok|facebook → 'other' nếu không xác định."""
    p = (raw or "").strip().lower()
    return p if p in KNOWN_PLATFORMS else "other"


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    # Seed schema version nếu chưa có
    if not conn.execute("SELECT version FROM schema_version").fetchone():
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
        conn.commit()
    return conn


# ── Actions ───────────────────────────────────────────────────────────────────

def act_init(conn: sqlite3.Connection, _cmd: dict) -> dict:
    """Khởi tạo schema — idempotent."""
    version = (conn.execute("SELECT version FROM schema_version").fetchone() or [0])[0]
    user_count = conn.execute("SELECT COUNT(*) FROM fetched_users").fetchone()[0]
    channel_count = conn.execute("SELECT COUNT(*) FROM local_channels").fetchone()[0]
    return {
        "ok": True,
        "db_path": DB_PATH,
        "schema_version": version,
        "user_count": user_count,
        "channel_count": channel_count,
    }


def act_save_users(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Lưu danh sách user từ API.

    So sánh với DB hiện tại → trả về id các user mới thêm.
    """
    users: list[dict] = cmd.get("users", [])
    now = _now()

    existing_ids: set[str] = {
        r[0] for r in conn.execute("SELECT id FROM fetched_users").fetchall()
    }
    added: list[str] = []

    for u in users:
        uid = str(u.get("id") or "").strip()
        uname = str(u.get("username") or "").strip()
        if not uid:
            continue
        if uid in existing_ids:
            conn.execute(
                "UPDATE fetched_users SET username=?, fetched_at=? WHERE id=?",
                (uname, now, uid),
            )
        else:
            conn.execute(
                "INSERT INTO fetched_users (id, username, fetched_at) VALUES (?, ?, ?)",
                (uid, uname, now),
            )
            added.append(uid)

    conn.commit()
    return {"added": added, "total": len(users)}


def act_list_users(conn: sqlite3.Connection, _cmd: dict) -> dict:
    """Trả về danh sách user kèm số kênh đã nhập."""
    rows = conn.execute("""
        SELECT
          u.id,
          u.username,
          u.fetched_at,
          COUNT(c.id) AS channel_count
        FROM fetched_users u
        LEFT JOIN local_channels c ON c.owner_id = u.id
        GROUP BY u.id
        ORDER BY u.username COLLATE NOCASE
    """).fetchall()
    return {"users": [dict(r) for r in rows]}


def act_get_user_channel_ids(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Trả về tập id kênh đã lưu của một user — dùng để detect kênh mới sau pull."""
    owner_id = str(cmd.get("owner_id") or "")
    rows = conn.execute(
        "SELECT id FROM local_channels WHERE owner_id=?", (owner_id,)
    ).fetchall()
    return {"ids": [r[0] for r in rows]}


def act_save_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Upsert kênh của một user vào DB, phân loại platform tự động.

    Nhận các item từ /v1/ops/inventory (đã lọc bỏ group ở renderer).
    Trả về số kênh mới thêm và số kênh cập nhật.
    """
    owner_id = str(cmd.get("owner_id") or "")
    owner_username = str(cmd.get("owner_username") or "")
    channels: list[dict] = cmd.get("channels", [])
    now = _now()

    existing_ids: set[str] = {
        r[0] for r in conn.execute(
            "SELECT id FROM local_channels WHERE owner_id=?", (owner_id,)
        ).fetchall()
    }

    added = 0
    updated = 0

    for ch in channels:
        cid = str(ch.get("id") or "").strip()
        if not cid:
            continue
        # Safety net: group luôn bị loại — renderer đã lọc rồi, nhưng bảo vệ thêm ở đây
        if str(ch.get("resource_type") or "").lower() == "group":
            continue

        platform = _classify_platform(ch.get("platform", ""))
        resource_type = str(ch.get("resource_type") or "channel")
        display_name = str(ch.get("display_name") or "")
        input_ref = str(ch.get("input_ref") or "")
        canonical_id = str(ch.get("canonical_external_id") or "")
        url = str(ch.get("url") or "")
        mapping_status = str(ch.get("mapping_status") or "")
        enabled = 1 if ch.get("enabled", True) else 0

        if cid in existing_ids:
            conn.execute("""
                UPDATE local_channels SET
                  owner_id=?, owner_username=?,
                  platform=?, resource_type=?,
                  display_name=?, input_ref=?, canonical_id=?,
                  url=?, mapping_status=?, enabled=?,
                  updated_at=?
                WHERE id=?
            """, (
                owner_id, owner_username,
                platform, resource_type,
                display_name, input_ref, canonical_id,
                url, mapping_status, enabled,
                now,
                cid,
            ))
            updated += 1
        else:
            conn.execute("""
                INSERT INTO local_channels (
                  id, owner_id, owner_username,
                  platform, resource_type,
                  display_name, input_ref, canonical_id,
                  url, mapping_status, enabled,
                  imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid, owner_id, owner_username,
                platform, resource_type,
                display_name, input_ref, canonical_id,
                url, mapping_status, enabled,
                now, now,
            ))
            added += 1

    conn.commit()
    return {"added": added, "updated": updated, "owner_id": owner_id}


def act_list_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Trả về kênh từ DB, lọc theo platform / owner_id / mapping_status / enabled / search.
    Group luôn bị loại.
    """
    platform       = cmd.get("platform")
    owner_id       = cmd.get("owner_id")
    mapping_status = cmd.get("mapping_status")   # "mapped" | "unmapped" | falsy = all
    enabled_filter = cmd.get("enabled_filter")   # "on" | "off" | falsy = all
    search         = (cmd.get("search") or "").strip()

    where: list[str] = ["resource_type != 'group'"]
    params: list[Any] = []

    if platform and platform != "all":
        where.append("platform=?")
        params.append(platform)
    if owner_id:
        where.append("owner_id=?")
        params.append(owner_id)
    if mapping_status == "mapped":
        where.append("mapping_status='mapped'")
    elif mapping_status == "unmapped":
        where.append("(mapping_status IS NULL OR mapping_status='' OR mapping_status!='mapped')")
    if enabled_filter == "on":
        where.append("enabled=1")
    elif enabled_filter == "off":
        where.append("enabled=0")
    if search:
        where.append("(display_name LIKE ? OR input_ref LIKE ? OR canonical_id LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    sql = "SELECT * FROM local_channels WHERE " + " AND ".join(where)
    sql += " ORDER BY owner_username COLLATE NOCASE, platform, display_name COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    return {"channels": [dict(r) for r in rows]}



def act_platform_counts(conn: sqlite3.Connection, _cmd: dict) -> dict:
    """Số kênh theo platform — luôn loại group."""
    rows = conn.execute("""
        SELECT platform, COUNT(*) AS cnt
        FROM local_channels
        WHERE resource_type != 'group'
        GROUP BY platform
    """).fetchall()
    counts: dict[str, int] = {r["platform"]: r["cnt"] for r in rows}
    return {
        "total": sum(counts.values()),
        "youtube": counts.get("youtube", 0),
        "tiktok": counts.get("tiktok", 0),
        "facebook": counts.get("facebook", 0),
        "other": counts.get("other", 0),
    }


def act_delete_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Xóa các kênh khỏi DB theo id."""
    ids: list[str] = [str(i) for i in cmd.get("ids", []) if i]
    if not ids:
        return {"deleted": 0}
    conn.execute(f"DELETE FROM local_channels WHERE id IN ({','.join('?'*len(ids))})", ids)
    conn.commit()
    return {"deleted": len(ids)}


def act_set_enabled(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Bật/tắt kênh (enabled=1/0) theo danh sách id."""
    ids: list[str] = [str(i) for i in cmd.get("ids", []) if i]
    enabled = 1 if cmd.get("enabled", True) else 0
    if not ids:
        return {"updated": 0}
    now = _now()
    conn.execute(
        f"UPDATE local_channels SET enabled=?, updated_at=? WHERE id IN ({','.join('?'*len(ids))})",
        [enabled, now, *ids],
    )
    conn.commit()
    return {"updated": len(ids)}


# ── Dispatch ──────────────────────────────────────────────────────────────────

_ACTIONS: dict[str, Any] = {
    "init":                 act_init,
    "save_users":           act_save_users,
    "list_users":           act_list_users,
    "get_user_channel_ids": act_get_user_channel_ids,
    "save_channels":        act_save_channels,
    "list_channels":        act_list_channels,
    "platform_counts":      act_platform_counts,
    "delete_channels":      act_delete_channels,
    "set_enabled":          act_set_enabled,
}


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        cmd: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}, ensure_ascii=False))
        sys.exit(1)

    action = cmd.get("action", "init")
    fn = _ACTIONS.get(action)
    if fn is None:
        print(json.dumps({"error": f"Unknown action: {action!r}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        conn = _get_conn()
        result = fn(conn, cmd)
        conn.close()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
