"""Import data local (data/*.json) vào SQLite `documents` — giả lập đồng bộ lên VPS (ít dùng).

Script đọc TẤT CẢ file JSON mà sync registry đẩy lên server, shred thành từng document rồi
INSERT/REPLACE vào bảng `documents` của shared.db — CÙNG ĐÚNG shape mà VPS nhận.

Store shapes (từ app/sync/shapes.py):
- dict_of_records: {key: record, ...} → mỗi key là 1 document
- posts_by_target: {target: [post, ...]} → mỗi bài là 1 document, khoá "<target>|<post_id>"
- list_by_id: [record, ...] → mỗi record có trường "id", khoá là record["id"]
- list_by_name: [record, ...] → khoá là record["name"].lower()
- flat_config: {key: value, ...} → mỗi cặp key/value là 1 document

Chạy:
    python import_local_data.py

Mặc định đọc từ ../data/ và ghi vào ./documents-sim.db (đặt biến DB_PATH để đổi).
"""

import json
import os
import sqlite3
import sys

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.environ.get("DB_PATH",
    os.path.join(os.path.dirname(__file__), "documents-sim.db"))

LOCAL_USER_ID = "local_user"


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
    """)
    conn.commit()


def _insert(conn, store, key, payload_str):
    conn.execute(
        "INSERT OR REPLACE INTO documents (user_id, store, key, payload, rev, deleted) "
        "VALUES (?, ?, ?, ?, 1, 0)",
        (LOCAL_USER_ID, store, key, payload_str))


def _import_dict_of_records(conn, store, file_path):
    """Shape dict_of_records: {key: record_dict, ...}"""
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0
    count = 0
    for key, record in data.items():
        if not isinstance(record, dict):
            continue
        _insert(conn, store, key, json.dumps(record, ensure_ascii=False))
        count += 1
    conn.commit()
    return count


def _import_posts_by_target(conn, store, file_path):
    """Shape posts_by_target: {target: [post, ...]}"""
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0
    count = 0
    for target, posts in data.items():
        if not isinstance(posts, list) or not posts:
            payload = json.dumps({"_target": target, "_empty": True}, ensure_ascii=False)
            _insert(conn, store, f"{target}|_empty", payload)
            count += 1
            continue
        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = post.get("post_id") or "unknown"
            record = {**post, "_target": target}
            _insert(conn, store, f"{target}|{post_id}", json.dumps(record, ensure_ascii=False))
            count += 1
    conn.commit()
    return count


def _import_list_by_id(conn, store, file_path):
    """Shape list_by_id: [record, ...] — khoá là record["id"]

    Dùng cho: repost_events, repost_groups, shorts_groups, translate_events."""
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return 0
    count = 0
    for rec in data:
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        _insert(conn, store, str(rec["id"]), json.dumps(rec, ensure_ascii=False))
        count += 1
    conn.commit()
    return count


def _import_flat_config(conn, store, file_path):
    """Shape flat_config: {key: value, ...} — mỗi cặp là 1 document."""
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0
    count = 0
    for key, value in data.items():
        payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        _insert(conn, store, key, payload)
        count += 1
    conn.commit()
    return count


def _import_nested_list(conn, store, file_path):
    """Shape nested_list: {"<container>": [record, ...]} — khoá là record["id"].

    Dùng cho: yt_channels (container "channels"). Chỉ import trường plaintext cần cho thống kê."""
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0
    count = 0
    for records in data.values():
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            _insert(conn, store, str(rec["id"]), json.dumps(rec, ensure_ascii=False))
            count += 1
    conn.commit()
    return count


# Danh sách store cần import — theo đúng registry (app/sync/registry.py)
STORES = [
    # (store_name, file_name, import_function)
    # Thống kê
    ("fb_posts",                 "fb_posts.json",                 _import_posts_by_target),
    ("fb_insights_daily",        "fb_insights_daily.json",        _import_dict_of_records),
    ("yt_insights_daily",        "yt_insights_daily.json",        _import_dict_of_records),
    ("yt_video_stats",           "yt_video_stats.json",           _import_dict_of_records),
    ("yt_channel_counts_daily",  "yt_channel_counts_daily.json",  _import_dict_of_records),
    ("yt_channels",              "yt_channels.json",              _import_nested_list),
    # Nhật ký
    ("repost_events",            "repost_events.json",            _import_list_by_id),
    ("translate_events",         "translate_events.json",         _import_list_by_id),
    # Cấu hình
    ("repost_pairings",          "repost_pairings.json",          _import_dict_of_records),
    ("repost_groups",            "repost_groups.json",            _import_list_by_id),
    ("shorts_pairings",          "shorts_pairings.json",          _import_dict_of_records),
    ("shorts_groups",            "shorts_groups.json",            _import_list_by_id),
    ("yt_countries",             "yt_countries.json",             _import_list_by_id),
    ("app_config",               "app_config.json",               _import_flat_config),
    # Metadata bổ sung — không qua sync nhưng admin cần
    ("country_tags",             "country_tags.json",             _import_flat_config),
    # Projection tên+quốc gia page cho thống kê (thay fb_pages — tên page bị seal ở production).
    ("fb_page_meta",             "fb_page_meta.json",             _import_dict_of_records),
]


def main():
    data_dir = os.path.abspath(DATA_DIR)
    db_path = os.path.abspath(DB_PATH)

    print(f"═══ Import data local vào test DB ═══")
    print(f"  Data:  {data_dir}")
    print(f"  DB:    {db_path}")
    print()

    if not os.path.isdir(data_dir):
        print(f"❌ Thư mục data không tồn tại: {data_dir}")
        sys.exit(1)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    _ensure_documents_table(conn)

    # Xoá TẤT CẢ documents cũ trước khi import lại — tránh dữ liệu ma
    conn.execute("DELETE FROM documents")
    conn.commit()

    total = 0
    for store_name, file_name, importer in STORES:
        file_path = os.path.join(data_dir, file_name)
        n = importer(conn, store_name, file_path)
        if n > 0:
            print(f"  {store_name:30s} {n:>6} bản ghi")
        total += n

    conn.close()

    print()
    print(f"✅ Tổng: {total} bản ghi đã import vào {os.path.basename(db_path)}")


if __name__ == "__main__":
    main()
