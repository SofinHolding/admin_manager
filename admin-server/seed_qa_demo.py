"""Seed dữ liệu demo/QA đa nhân sự — cho dashboard hiển thị THỰC TẾ thay vì 1 góc nhìn duy nhất.

Vấn đề trước khi có script này: chạy dev chỉ có 1 account `admin` xem TOÀN BỘ dữ liệu Tool xuyên
suốt (LocalFileDb bỏ qua `user_ids`) — không có cách nào demo/QA tính năng vừa xây: nhiều nhân sự
mỗi người chỉ thấy Tool của mình, `dash_admin` chọn nhân sự để soi riêng, group kênh gắn nhãn đa
nền tảng. Script này:

1. Đọc dữ liệu THẬT từ `DATA_DIR` (không bịa số liệu).
2. Chia các target/channel THẬT đó ra N "nhân sự" giả lập (hash ổn định theo target — 1 target luôn
   thuộc về đúng 1 nhân sự, giống thực tế 1 page do 1 người quản lý).
3. Import vào bảng `documents` (SQLite) — cùng schema với `import_local_data.py`, nhưng gán
   `user_id` PHÂN THEO NHÂN SỰ thay vì 1 `LOCAL_USER_ID` cố định.
4. Tạo account viewer cho từng nhân sự + 1 account `dash_admin`, gắn `account_links` thẳng vào DB
   (bỏ qua luồng mã pairing HTTP — script vận hành nội bộ, không phải Tool thật gọi `/redeem`).
5. Tạo vài `channel_groups` mẫu cho mỗi nhân sự, gắn `platform_tag` đa dạng (facebook/tiktok/
   instagram) lên các kênh THẬT của họ — minh hoạ tính năng gắn nhãn nền tảng tự do.

QUAN TRỌNG: sau khi seed, phải đổi `DATABASE_URL` từ `file:///...` (LocalFileDb, bỏ qua user_ids)
sang `sqlite:///...` (SqliteDb, có lọc user_ids thật) trỏ ĐÚNG file này thì đa nhân sự mới có tác
dụng. Xem hướng dẫn in ra cuối script.

Dùng 1 lần cho môi trường dev/QA — KHÔNG chạy trên DB production (sẽ ghi đè `documents`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import bcrypt
import db as db_mod

DATA_DIR = os.environ.get(
    "DATA_DIR", "C:/Users/Admin/AppData/Roaming/fb-uploader-ui/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "admin-auth.db"))

# ── Nhân sự giả lập — tên + user_id giả (KHÔNG phải HMAC thật, chỉ đủ dài để qua validate) ────
STAFF = [
    {"username": "nv_minh", "email": "minh@demo.local", "label": "Minh — Nhóm Đông Nam Á",
     "user_id": "demo" + hashlib.sha256(b"nv_minh").hexdigest()[:28]},
    {"username": "nv_lan", "email": "lan@demo.local", "label": "Lan — Nhóm Nam Mỹ",
     "user_id": "demo" + hashlib.sha256(b"nv_lan").hexdigest()[:28]},
    {"username": "nv_tuan", "email": "tuan@demo.local", "label": "Tuấn — Nhóm Châu Phi",
     "user_id": "demo" + hashlib.sha256(b"nv_tuan").hexdigest()[:28]},
]
DASH_ADMIN = {"username": "quanly_add", "email": "add@demo.local"}
DEMO_PASSWORD = "Demo12345"

PLATFORM_TAGS = ["facebook", "tiktok", "instagram"]


def _owner_of(target: str) -> dict:
    """Hash ổn định target → 1 trong 3 nhân sự — cùng target LUÔN về cùng 1 người."""
    h = int(hashlib.sha1(target.encode("utf-8")).hexdigest(), 16)
    return STAFF[h % len(STAFF)]


def _ensure_documents_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            user_id    TEXT    NOT NULL,
            store      TEXT    NOT NULL,
            key        TEXT    NOT NULL,
            payload    TEXT,
            rev        INTEGER NOT NULL DEFAULT 1,
            clock      TEXT    NOT NULL DEFAULT '',
            deleted    INTEGER NOT NULL DEFAULT 0,
            device_id  TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, store, key)
        );
        CREATE INDEX IF NOT EXISTS documents_rev ON documents (user_id, rev);
        CREATE TABLE IF NOT EXISTS revs (user_id TEXT PRIMARY KEY, next_rev INTEGER NOT NULL);
    """)
    conn.commit()


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _insert_doc(conn, user_id, store, key, payload) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO documents (user_id, store, key, payload, rev, deleted) "
        "VALUES (?, ?, ?, ?, 1, 0)",
        (user_id, store, key, json.dumps(payload, ensure_ascii=False)))


def seed_documents(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Import dữ liệu THẬT, phân theo nhân sự. Trả `{user_id: {targets đã gán}}` để dùng tạo group."""
    conn.execute("DELETE FROM documents")

    owned_targets: dict[str, set[str]] = {s["user_id"]: set() for s in STAFF}

    # fb_page_meta.json (dict_of_records: {target: {target,name,country}}) — nguồn chính xác định
    # chủ sở hữu target, import TRƯỚC để mọi store khác tra theo cùng 1 map.
    page_meta = _read_json(os.path.join(DATA_DIR, "fb_page_meta.json"), {})
    target_owner: dict[str, str] = {}
    n_meta = 0
    for target, record in page_meta.items():
        if not isinstance(record, dict):
            continue
        owner = _owner_of(target)
        target_owner[target] = owner["user_id"]
        owned_targets[owner["user_id"]].add(target)
        _insert_doc(conn, owner["user_id"], "fb_page_meta", target, record)
        n_meta += 1

    # fb_posts.json (posts_by_target: {target: [post, ...]})
    fb_posts = _read_json(os.path.join(DATA_DIR, "fb_posts.json"), {})
    n_posts = 0
    for target, posts in fb_posts.items():
        if not isinstance(posts, list):
            continue
        uid = target_owner.get(target) or _owner_of(target)["user_id"]
        target_owner.setdefault(target, uid)
        owned_targets[uid].add(target)
        for post in posts:
            if not isinstance(post, dict):
                continue
            pid = str(post.get("post_id") or f"{target}:{n_posts}")
            _insert_doc(conn, uid, "fb_posts", f"{target}|{pid}", {**post, "target": target})
            n_posts += 1

    # repost_pairings.json (dict_of_records: {id: {target, enabled, group_id, ...}})
    pairings = _read_json(os.path.join(DATA_DIR, "repost_pairings.json"), {})
    n_pairings = 0
    for key, record in pairings.items():
        if not isinstance(record, dict) or not record.get("target"):
            continue
        target = record["target"]
        uid = target_owner.get(target) or _owner_of(target)["user_id"]
        _insert_doc(conn, uid, "repost_pairings", key, record)
        n_pairings += 1

    # repost_groups.json (list_by_id) — nhóm reup nghiệp vụ THẬT của Tool, nhỏ (vài chục) — nhân
    # bản cho MỌI nhân sự vì 1 group có thể được pairing của nhiều người tham chiếu tới.
    groups = _read_json(os.path.join(DATA_DIR, "repost_groups.json"), [])
    n_groups = 0
    if isinstance(groups, list):
        for g in groups:
            if not isinstance(g, dict) or not g.get("id"):
                continue
            for s in STAFF:
                _insert_doc(conn, s["user_id"], "repost_groups", g["id"], g)
            n_groups += 1

    # repost_events.json (list_by_id) — nhật ký đăng bài, gán theo target của event.
    events = _read_json(os.path.join(DATA_DIR, "repost_events.json"), [])
    n_events = 0
    if isinstance(events, list):
        for e in events:
            if not isinstance(e, dict) or not e.get("id") or not e.get("kind"):
                continue
            target = e.get("target") or ""
            uid = target_owner.get(target) or (_owner_of(target)["user_id"] if target else STAFF[0]["user_id"])
            _insert_doc(conn, uid, "repost_events", str(e["id"]), e)
            n_events += 1

    # yt_channels.json / yt_insights_daily.json — import nếu có (dữ liệu thật hiện đang trống ở
    # môi trường này, nhưng giữ đường import để không mất dữ liệu nếu sau này Tool có YouTube).
    yt_channels = _read_json(os.path.join(DATA_DIR, "yt_channels.json"), {})
    n_yt_chan = 0
    for ch in (yt_channels.get("channels") or []):
        if not isinstance(ch, dict) or not ch.get("id"):
            continue
        uid = _owner_of(ch["id"])["user_id"]
        owned_targets[uid].add(ch["id"])
        _insert_doc(conn, uid, "yt_channels", ch["id"], ch)
        n_yt_chan += 1

    yt_daily = _read_json(os.path.join(DATA_DIR, "yt_insights_daily.json"), {})
    n_yt_daily = 0
    for key, record in yt_daily.items():
        if not isinstance(record, dict) or not record.get("channel_id"):
            continue
        uid = _owner_of(record["channel_id"])["user_id"]
        _insert_doc(conn, uid, "yt_insights_daily", key, record)
        n_yt_daily += 1

    # revs — 1 dòng/nhân sự, doc_version() (PostgresDb) SUM lại; SqliteDb không đọc revs cho
    # doc_version nhưng bảng cần tồn tại để tool_user_id_exists các nơi khác không lệch giả định.
    for s in STAFF:
        conn.execute(
            "INSERT OR REPLACE INTO revs (user_id, next_rev) VALUES (?, ?)", (s["user_id"], 1))

    conn.commit()
    print(f"  fb_page_meta      {n_meta:>6} bản ghi (định danh chủ sở hữu target)")
    print(f"  fb_posts          {n_posts:>6} bản ghi")
    print(f"  repost_pairings   {n_pairings:>6} bản ghi")
    print(f"  repost_groups     {n_groups:>6} bản ghi (x{len(STAFF)} bản sao/nhân sự)")
    print(f"  repost_events     {n_events:>6} bản ghi")
    print(f"  yt_channels       {n_yt_chan:>6} bản ghi")
    print(f"  yt_insights_daily {n_yt_daily:>6} bản ghi")
    for s in STAFF:
        print(f"  → {s['username']:10s} sở hữu {len(owned_targets[s['user_id']]):>3} target/channel")
    return owned_targets


async def seed_accounts_and_groups(owned_targets: dict[str, set[str]]) -> None:
    d = db_mod.SqliteDb(DB_PATH)
    await d.setup()  # idempotent — không đụng account `admin` đã có sẵn

    pw_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt(rounds=10)).decode()

    # ── dash_admin ──────────────────────────────────────────────────────────────
    existing = await d.get_account_by_username(DASH_ADMIN["username"])
    if not existing:
        acc = await d.create_account(DASH_ADMIN["username"], DASH_ADMIN["email"], pw_hash,
                                     "dash_admin", "seed-qa-demo")
        await d.set_email_verified(acc["id"])
        print(f"  + Tạo account dash_admin: {DASH_ADMIN['username']} / {DEMO_PASSWORD}")
    else:
        print(f"  = Account dash_admin {DASH_ADMIN['username']} đã tồn tại, bỏ qua")

    # ── nhân sự (viewer) + link + group mẫu ───────────────────────────────────────
    for s in STAFF:
        existing = await d.get_account_by_username(s["username"])
        if existing:
            account_id = existing["id"]
            print(f"  = Account {s['username']} đã tồn tại, bỏ qua tạo mới")
        else:
            acc = await d.create_account(s["username"], s["email"], pw_hash, "viewer", "seed-qa-demo")
            await d.set_email_verified(acc["id"])
            account_id = acc["id"]
            print(f"  + Tạo account viewer: {s['username']} / {DEMO_PASSWORD} ({s['label']})")

        # Liên kết thẳng vào account_links — bỏ qua luồng mã pairing HTTP vì đây là seed nội bộ.
        targets = sorted(owned_targets.get(s["user_id"], set()))
        with d._lock:  # type: ignore[attr-defined]
            d._conn.execute(
                "INSERT INTO account_links (account_id, tool_user_id, label, status, linked_at) "
                "VALUES (?, ?, ?, 'active', ?) "
                "ON CONFLICT(account_id, tool_user_id) DO UPDATE SET status='active'",
                (account_id, s["user_id"], s["label"],
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            d._conn.commit()

        if not targets:
            continue

        # 2 group mẫu/nhân sự: 1 group "chính" (platform_tag=facebook, đúng nguồn thật) + 1 group
        # "reup đa nền tảng" gắn nhãn tự do khác (tiktok/instagram) lên CHÍNH những target đó —
        # minh hoạ tính năng gắn nhãn nền tảng tự do không cần Tool thực sự thu thập nền tảng đó.
        half = max(1, len(targets) // 2)
        group_main = await d.create_channel_group(account_id, f"{s['label']} — Kênh chính")
        for t in targets[:half]:
            await d.add_group_member(group_main["id"], t, platform_tag="facebook")

        group_alt = await d.create_channel_group(account_id, f"{s['label']} — Reup đa nền tảng")
        for i, t in enumerate(targets[half:half + 6]):
            await d.add_group_member(group_alt["id"], t, platform_tag=PLATFORM_TAGS[i % len(PLATFORM_TAGS)])

        print(f"    → group '{group_main['name']}' ({min(half, len(targets))} kênh, facebook), "
              f"'{group_alt['name']}' ({min(6, max(0, len(targets) - half))} kênh, đa nền tảng)")

    await d.close()


def main() -> None:
    print("═══ Seed dữ liệu demo/QA đa nhân sự ═══")
    print(f"  Nguồn dữ liệu thật: {DATA_DIR}")
    print(f"  DB đích (auth + documents chung 1 file): {DB_PATH}")
    print()

    if not os.path.isdir(DATA_DIR):
        print(f"❌ DATA_DIR không tồn tại: {DATA_DIR}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    _ensure_documents_table(conn)
    owned_targets = seed_documents(conn)
    conn.close()

    print()
    asyncio.run(seed_accounts_and_groups(owned_targets))

    print()
    print("✅ Xong. Tài khoản demo (mật khẩu chung):", DEMO_PASSWORD)
    print(f"   - {DASH_ADMIN['username']:10s} role=dash_admin — chọn nhân sự để soi riêng từng người")
    for s in STAFF:
        print(f"   - {s['username']:10s} role=viewer     — chỉ thấy Tool + group của chính mình")
    print()
    print("⚠️  QUAN TRỌNG: phải đổi DATABASE_URL sang SqliteDb (KHÔNG phải file:/// LocalFileDb) để")
    print("    lọc theo nhân sự thật sự có tác dụng — LocalFileDb bỏ qua user_ids theo thiết kế:")
    print(f'    DATABASE_URL="sqlite:///./{os.path.basename(DB_PATH)}"  (bỏ DATA_DIR, không cần nữa)')


if __name__ == "__main__":
    main()
