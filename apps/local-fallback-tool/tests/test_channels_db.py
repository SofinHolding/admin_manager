"""DB cục bộ: kênh HIỆN CÓ trên website vs kênh ĐÃ KÉO (tool giữ kết nối). Kênh đã kéo không kéo lại được và trạng thái phải bền
qua mỗi lần Fetch, qua việc đổi tab / khởi động lại (nằm trong DB, không nằm trong state của giao diện)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels_db  # noqa: E402


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(channels_db, "DB_PATH", str(tmp_path / "c.db"))
    conn = channels_db._get_conn()

    def run(action: str, **cmd):
        return getattr(channels_db, f"act_{action}")(conn, cmd)
    return run


def _ch(cid: str, owner: str = "u1", **over) -> dict:
    return {"id": cid, "owner_account_id": owner, "owner_username": f"user_{owner}", "platform": "youtube",
            "display_name": cid, "input_ref": cid, **over}


def _users(*owners: str) -> list[dict]:
    return [{"id": o, "username": f"user_{o}"} for o in owners]


def _by_id(run, **filters) -> dict:
    return {c["id"]: c for c in run("list_channels", **filters)["channels"]}


def test_new_channels_are_unpulled_and_one_channel_can_be_pulled_alone(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    out = run("sync_shared", users=_users("u1"), channels=[_ch("c1"), _ch("c2"), _ch("c3")])
    assert out["added_channels"] == 3 and all(c["pulled"] == 0 for c in _by_id(run).values())

    assert run("pull_channels", ids=["c2"]) == {"pulled": 1, "already": 0, "unknown": 0}      # chọn ĐÚNG 1 kênh là kéo được
    assert {i: c["pulled"] for i, c in _by_id(run).items()} == {"c1": 0, "c2": 1, "c3": 0}
    assert _by_id(run)["c2"]["pulled_at"] != ""
    assert run("list_users")["users"][0]["pulled_count"] == 1 and run("list_users")["users"][0]["channel_count"] == 3


def test_an_already_pulled_channel_cannot_be_pulled_again(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    run("sync_shared", users=_users("u1"), channels=[_ch("c1"), _ch("c2")])
    run("pull_channels", ids=["c1"])
    first_pulled_at = _by_id(run)["c1"]["pulled_at"]

    assert run("pull_channels", ids=["c1", "c2", "nope"]) == {"pulled": 1, "already": 1, "unknown": 1}
    assert _by_id(run)["c1"]["pulled_at"] == first_pulled_at                                  # kéo lại không làm mới ngày kéo


def test_fetch_keeps_pulled_state_and_local_enabled_flag_but_refreshes_display_data(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    run("sync_shared", users=_users("u1"), channels=[_ch("c1"), _ch("c2")])
    run("pull_channels", ids=["c1"])
    run("set_enabled", ids=["c1"], enabled=False)

    out = run("sync_shared", users=_users("u1"), channels=[_ch("c1", display_name="Tên mới", enabled=True), _ch("c2"), _ch("c3")])
    assert (out["added_channels"], out["updated_channels"], out["removed_channels"]) == (1, 2, 0)
    c1 = _by_id(run)["c1"]
    assert c1["pulled"] == 1 and c1["enabled"] == 0 and c1["display_name"] == "Tên mới"       # trạng thái kéo + bật/tắt cục bộ không bị đè
    assert _by_id(run)["c3"]["pulled"] == 0


def test_fetch_drops_channels_and_users_that_left_the_shared_tool_even_if_pulled(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    run("sync_shared", users=_users("u1", "u2"), channels=[_ch("c1"), _ch("c2", "u2"), _ch("c3", "u2")])
    run("pull_channels", ids=["c2"])

    out = run("sync_shared", users=_users("u1"), channels=[_ch("c1")])                          # u2 chuyển sang Tool riêng
    assert out["removed_users"] == 1 and out["removed_channels"] == 2
    assert [u["id"] for u in run("list_users")["users"]] == ["u1"] and list(_by_id(run)) == ["c1"]


def test_unpull_returns_a_channel_to_unpulled_and_it_can_be_pulled_again(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    run("sync_shared", users=_users("u1"), channels=[_ch("c1"), _ch("c2")])
    run("pull_channels", ids=["c1", "c2"])
    assert run("unpull_channels", ids=["c1", "c-unknown"]) == {"unpulled": 1}
    assert {i: c["pulled"] for i, c in _by_id(run).items()} == {"c1": 0, "c2": 1}
    assert run("pull_channels", ids=["c1"])["pulled"] == 1


def test_pulled_filter(tmp_path, monkeypatch):
    run = _db(tmp_path, monkeypatch)
    run("sync_shared", users=_users("u1"), channels=[_ch("c1"), _ch("c2")])
    run("pull_channels", ids=["c1"])
    assert list(_by_id(run, pulled_filter="pulled")) == ["c1"]
    assert list(_by_id(run, pulled_filter="unpulled")) == ["c2"]
    assert len(_by_id(run)) == 2


def test_v1_database_is_migrated_with_existing_channels_left_unpulled(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version VALUES (1);
        CREATE TABLE fetched_users (id TEXT PRIMARY KEY, username TEXT NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE local_channels (
          id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, owner_username TEXT NOT NULL, platform TEXT NOT NULL,
          resource_type TEXT NOT NULL DEFAULT 'channel', display_name TEXT NOT NULL DEFAULT '', input_ref TEXT NOT NULL DEFAULT '',
          canonical_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', mapping_status TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1, imported_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO local_channels (id, owner_id, owner_username, platform, imported_at, updated_at)
          VALUES ('c1', 'u1', 'a', 'youtube', 't', 't');
    """)
    old.commit()
    old.close()

    monkeypatch.setattr(channels_db, "DB_PATH", str(path))
    conn = channels_db._get_conn()
    row = channels_db.act_list_channels(conn, {})["channels"][0]
    assert row["pulled"] == 0 and row["pulled_at"] == ""
    assert channels_db.act_init(conn, {})["schema_version"] == 2
    channels_db._get_conn()                                                                      # chạy lại không lỗi (idempotent)
