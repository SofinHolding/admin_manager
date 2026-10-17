#!/usr/bin/env python3
"""
Local SQLite database cho kênh của tool dùng chung (local-fallback-tool).

Đọc JSON command từ stdin, ghi JSON result ra stdout.
Dùng Python built-in sqlite3 — không cần cài thêm thư viện.

Hai khái niệm tách biệt trên mỗi kênh:
  - HIỆN CÓ trên website (kênh dùng tool dùng chung) — `sync_shared` làm tươi danh sách này, không đụng trạng thái kéo.
  - ĐÃ KÉO (`pulled=1`) — kênh người vận hành chọn kéo về tool. Kênh đã kéo không kéo lại được (`pull_channels` bỏ qua);
    tool giữ kết nối với kênh đó để đẩy dữ liệu lên.

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
  pulled          INTEGER NOT NULL DEFAULT 0,
  pulled_at       TEXT NOT NULL DEFAULT '',
  imported_at     TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lc_owner    ON local_channels (owner_id);
CREATE INDEX IF NOT EXISTS idx_lc_platform ON local_channels (platform);

-- Link video của từng kênh đã lấy về máy (vd toàn bộ video của kênh TikTok). Xoá cùng kênh khi kênh rời danh sách.
CREATE TABLE IF NOT EXISTS channel_videos (
  channel_id    TEXT NOT NULL,
  video_id      TEXT NOT NULL,
  url           TEXT NOT NULL,
  title         TEXT NOT NULL DEFAULT '',
  published_at  TEXT NOT NULL DEFAULT '',
  views         INTEGER,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (channel_id, video_id)
);
"""

CURRENT_SCHEMA_VERSION = 2
KNOWN_PLATFORMS = frozenset({"youtube", "tiktok", "facebook"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_platform(raw: str) -> str:
    """Phân loại platform: youtube|tiktok|facebook → 'other' nếu không xác định."""
    p = (raw or "").strip().lower()
    return p if p in KNOWN_PLATFORMS else "other"


def _ids(cmd: dict) -> list[str]:
    return list(dict.fromkeys(str(i) for i in cmd.get("ids", []) if i))


def _marks(items: list) -> str:
    return ",".join("?" * len(items))


def _migrate(conn: sqlite3.Connection) -> None:
    """v1 → v2: thêm `pulled`/`pulled_at`. v1 chưa có khái niệm "đã kéo" (mọi kênh của user được nạp cùng lúc) nên kênh cũ vào v2 ở trạng
    thái CHƯA KÉO — người vận hành tự chọn kênh nào kéo."""
    version = (conn.execute("SELECT version FROM schema_version").fetchone() or [None])[0]
    if version is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
    elif version < 2:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(local_channels)")}
        if "pulled" not in cols:
            conn.execute("ALTER TABLE local_channels ADD COLUMN pulled INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE local_channels ADD COLUMN pulled_at TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE schema_version SET version=?", (CURRENT_SCHEMA_VERSION,))
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)   # DB v1 đã có bảng: CREATE IF NOT EXISTS không thêm cột mới — _migrate lo
    _migrate(conn)
    return conn


# ── Actions ───────────────────────────────────────────────────────────────────

def act_init(conn: sqlite3.Connection, _cmd: dict) -> dict:
    """Khởi tạo schema — idempotent."""
    version = (conn.execute("SELECT version FROM schema_version").fetchone() or [0])[0]
    return {
        "ok": True,
        "db_path": DB_PATH,
        "schema_version": version,
        "user_count": conn.execute("SELECT COUNT(*) FROM fetched_users").fetchone()[0],
        "channel_count": conn.execute("SELECT COUNT(*) FROM local_channels").fetchone()[0],
    }


def act_sync_shared(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Làm tươi danh sách user + kênh HIỆN CÓ của tool dùng chung theo website, trong một giao dịch.

    - Kênh mới vào ở trạng thái CHƯA KÉO; kênh đã có giữ nguyên `pulled`/`pulled_at` và cờ bật/tắt cục bộ (chỉ cập nhật thông tin hiển thị).
    - User/kênh không còn trong danh sách (đã xoá trên web, hoặc chuyển sang Tool riêng) bị dọn khỏi DB, kể cả kênh đã kéo.
    """
    users: list[dict] = cmd.get("users", [])
    channels: list[dict] = cmd.get("channels", [])
    now = _now()

    existing_users = {r[0] for r in conn.execute("SELECT id FROM fetched_users")}
    existing_channels = {r[0] for r in conn.execute("SELECT id FROM local_channels")}

    added_users: list[str] = []
    keep_users: set[str] = set()
    for u in users:
        uid = str(u.get("id") or "").strip()
        if not uid:
            continue
        keep_users.add(uid)
        uname = str(u.get("username") or "").strip()
        if uid in existing_users:
            conn.execute("UPDATE fetched_users SET username=?, fetched_at=? WHERE id=?", (uname, now, uid))
        else:
            conn.execute("INSERT INTO fetched_users (id, username, fetched_at) VALUES (?, ?, ?)", (uid, uname, now))
            added_users.append(uid)

    added = updated = 0
    keep_channels: set[str] = set()
    for ch in channels:
        cid = str(ch.get("id") or "").strip()
        # Safety net: group không phải kênh
        if not cid or str(ch.get("resource_type") or "").lower() == "group":
            continue
        keep_channels.add(cid)
        row = (
            str(ch.get("owner_account_id") or ""), str(ch.get("owner_username") or ""),
            _classify_platform(ch.get("platform", "")), str(ch.get("resource_type") or "channel"),
            str(ch.get("display_name") or ""), str(ch.get("input_ref") or ""),
            str(ch.get("canonical_external_id") or ""), str(ch.get("url") or ""),
            str(ch.get("mapping_status") or ""),
        )
        if cid in existing_channels:
            conn.execute("""
                UPDATE local_channels SET owner_id=?, owner_username=?, platform=?, resource_type=?,
                  display_name=?, input_ref=?, canonical_id=?, url=?, mapping_status=?, updated_at=?
                WHERE id=?
            """, (*row, now, cid))
            updated += 1
        else:
            conn.execute("""
                INSERT INTO local_channels (
                  id, owner_id, owner_username, platform, resource_type, display_name, input_ref, canonical_id,
                  url, mapping_status, enabled, pulled, pulled_at, imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?)
            """, (cid, *row, 1 if ch.get("enabled", True) else 0, now, now))
            added += 1

    keep_c, keep_u = sorted(keep_channels), sorted(keep_users)
    removed_channels = conn.execute(f"DELETE FROM local_channels WHERE id NOT IN ({_marks(keep_c)})", keep_c).rowcount
    conn.execute(f"DELETE FROM local_channels WHERE owner_id NOT IN ({_marks(keep_u)})", keep_u)
    conn.execute(f"DELETE FROM fetched_users WHERE id NOT IN ({_marks(keep_u)})", keep_u)
    conn.execute("DELETE FROM channel_videos WHERE channel_id NOT IN (SELECT id FROM local_channels)")
    conn.commit()
    return {"added_users": added_users, "removed_users": len(existing_users - keep_users),
            "added_channels": added, "updated_channels": updated, "removed_channels": removed_channels}


def act_pull_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Kéo các kênh đã chọn về tool (đánh dấu ĐÃ KÉO). Kênh đã kéo trước đó bị bỏ qua — không kéo lại, trạng thái giữ nguyên."""
    ids = _ids(cmd)
    if not ids:
        return {"pulled": 0, "already": 0, "unknown": 0}
    known = {r[0]: r[1] for r in conn.execute(f"SELECT id, pulled FROM local_channels WHERE id IN ({_marks(ids)})", ids)}
    todo = [i for i in ids if known.get(i) == 0]
    now = _now()
    if todo:
        conn.execute(f"UPDATE local_channels SET pulled=1, pulled_at=?, updated_at=? WHERE id IN ({_marks(todo)})", [now, now, *todo])
    conn.commit()
    return {"pulled": len(todo), "already": sum(1 for i in ids if known.get(i) == 1), "unknown": sum(1 for i in ids if i not in known)}


def act_unpull_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Gỡ kết nối: đưa kênh đã kéo về CHƯA KÉO (kênh vẫn nằm trong danh sách hiện có, có thể kéo lại)."""
    ids = _ids(cmd)
    if not ids:
        return {"unpulled": 0}
    n = conn.execute(f"UPDATE local_channels SET pulled=0, pulled_at='', updated_at=? WHERE pulled=1 AND id IN ({_marks(ids)})",
                     [_now(), *ids]).rowcount
    conn.commit()
    return {"unpulled": n}


def act_list_users(conn: sqlite3.Connection, _cmd: dict) -> dict:
    """Trả về danh sách user kèm số kênh hiện có và số kênh đã kéo."""
    rows = conn.execute("""
        SELECT u.id, u.username, u.fetched_at,
               COUNT(c.id) AS channel_count,
               COALESCE(SUM(c.pulled), 0) AS pulled_count
        FROM fetched_users u
        LEFT JOIN local_channels c ON c.owner_id = u.id
        GROUP BY u.id
        ORDER BY u.username COLLATE NOCASE
    """).fetchall()
    return {"users": [dict(r) for r in rows]}


def act_list_channels(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Trả về kênh từ DB, lọc theo platform / owner_id / mapping_status / enabled / pulled / search. Group luôn bị loại."""
    platform       = cmd.get("platform")
    owner_id       = cmd.get("owner_id")
    mapping_status = cmd.get("mapping_status")   # "mapped" | "unmapped" | falsy = all
    enabled_filter = cmd.get("enabled_filter")   # "on" | "off" | falsy = all
    pulled_filter  = cmd.get("pulled_filter")    # "pulled" | "unpulled" | falsy = all
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
    if pulled_filter == "pulled":
        where.append("pulled=1")
    elif pulled_filter == "unpulled":
        where.append("pulled=0")
    if search:
        where.append("(display_name LIKE ? OR input_ref LIKE ? OR canonical_id LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    sql = ("SELECT c.*, (SELECT COUNT(*) FROM channel_videos v WHERE v.channel_id = c.id) AS video_count "
           "FROM local_channels c WHERE ") + " AND ".join(where)
    sql += " ORDER BY owner_username COLLATE NOCASE, platform, display_name COLLATE NOCASE"
    return {"channels": [dict(r) for r in conn.execute(sql, params).fetchall()]}


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


def act_set_enabled(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Bật/tắt kênh (enabled=1/0) theo danh sách id."""
    ids = _ids(cmd)
    if not ids:
        return {"updated": 0}
    n = conn.execute(f"UPDATE local_channels SET enabled=?, updated_at=? WHERE id IN ({_marks(ids)})",
                     [1 if cmd.get("enabled", True) else 0, _now(), *ids]).rowcount
    conn.commit()
    return {"updated": n}


def act_save_channel_videos(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Lưu link video của MỘT kênh (upsert theo `(channel_id, video_id)`): `videos=[{video_id, url, title?, published_at?, views?}]`.
    Thông tin đã có không bị xoá bằng giá trị rỗng của lần lấy sau."""
    channel_id = str(cmd.get("channel_id") or "")
    if not conn.execute("SELECT 1 FROM local_channels WHERE id=?", (channel_id,)).fetchone():
        raise ValueError("Không tìm thấy kênh trong DB cục bộ")
    now = _now()
    existing = {r[0] for r in conn.execute("SELECT video_id FROM channel_videos WHERE channel_id=?", (channel_id,))}
    added = updated = 0
    for v in cmd.get("videos", []):
        vid, url = str(v.get("video_id") or "").strip(), str(v.get("url") or "").strip()
        if not vid or not url:
            continue
        views = v.get("views") if isinstance(v.get("views"), int) and not isinstance(v.get("views"), bool) else None
        conn.execute("""
            INSERT INTO channel_videos (channel_id, video_id, url, title, published_at, views, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, video_id) DO UPDATE SET
              url=excluded.url,
              title=CASE WHEN excluded.title != '' THEN excluded.title ELSE channel_videos.title END,
              published_at=CASE WHEN excluded.published_at != '' THEN excluded.published_at ELSE channel_videos.published_at END,
              views=COALESCE(excluded.views, channel_videos.views),
              fetched_at=excluded.fetched_at
        """, (channel_id, vid, url, str(v.get("title") or ""), str(v.get("published_at") or ""), views, now))
        if vid in existing:
            updated += 1
        else:
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM channel_videos WHERE channel_id=?", (channel_id,)).fetchone()[0]
    return {"added": added, "updated": updated, "total": total}


def act_list_channel_videos(conn: sqlite3.Connection, cmd: dict) -> dict:
    """Link video đã lưu của một kênh, mới đăng trước."""
    rows = conn.execute("""
        SELECT video_id, url, title, published_at, views, fetched_at FROM channel_videos WHERE channel_id=?
        ORDER BY published_at DESC, fetched_at DESC, video_id
    """, (str(cmd.get("channel_id") or ""),)).fetchall()
    return {"videos": [dict(r) for r in rows]}


# ── Dùng bởi collect.py (không phải action của Electron) ──────────────────────────────────────────────────────

def pulled_targets(platforms: list[str]) -> list[dict]:
    """Kênh ĐÃ KÉO và đang BẬT của các nền tảng cho trước: `[{id, platform}]`. Collector chỉ xin job / nhận job của các kênh này."""
    if not platforms or not os.path.exists(DB_PATH):
        return []
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, platform FROM local_channels WHERE pulled=1 AND enabled=1 AND resource_type != 'group' "
            f"AND platform IN ({_marks(platforms)}) ORDER BY owner_username COLLATE NOCASE, display_name COLLATE NOCASE",
            platforms).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Dispatch ──────────────────────────────────────────────────────────────────

_ACTIONS: dict[str, Any] = {
    "init":            act_init,
    "sync_shared":     act_sync_shared,
    "pull_channels":   act_pull_channels,
    "unpull_channels": act_unpull_channels,
    "list_users":      act_list_users,
    "list_channels":   act_list_channels,
    "platform_counts": act_platform_counts,
    "set_enabled":     act_set_enabled,
    "save_channel_videos": act_save_channel_videos,
    "list_channel_videos": act_list_channel_videos,
}


def main() -> None:
    # stdout/stderr của tiến trình con trên Windows mặc định là cp1252 — tiếng Việt làm UnicodeEncodeError. Luôn UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
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
