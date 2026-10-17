"""Danh sách video lưu theo kênh: LẦN ĐẦU lấy đủ, các lần sau chỉ lấy video mới rồi đo số liệu trên toàn bộ danh sách đã lưu."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channel_collect as cc  # noqa: E402
import channel_listing as cl  # noqa: E402
import channels_db  # noqa: E402
import collect  # noqa: E402
import tiktok_links  # noqa: E402
import video_links as vl  # noqa: E402
from checkers import tiktok as tt  # noqa: E402
from checkers import youtube as yt  # noqa: E402
from checkers.common import fail, stat  # noqa: E402

TT = "https://www.tiktok.com/@a"
KEY = "AIza" + "A" * 35


def _vid(i: int) -> dict:
    return {"video_id": str(i), "url": f"https://www.tiktok.com/@a/video/{i}", "title": "", "published_at": "", "views": None}


def _newest_first(*ids: int) -> list[dict]:
    return [_vid(i) for i in ids]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(channels_db, "DB_PATH", str(tmp_path / "channels.db"))
    conn = channels_db._get_conn()
    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "u"}], "channels": [
        {"id": "c1", "owner_account_id": "u1", "owner_username": "u", "platform": "tiktok", "input_ref": TT, "display_name": "a"}]})
    conn.close()
    return tmp_path


class Tik:
    """Giả `tiktok_links.fetch_links` + tikwm: ghi lại mỗi lần liệt kê (limit) và mỗi video được đo."""

    def __init__(self, monkeypatch, channel: list[int]):
        self.channel = channel          # id video hiện có trên kênh, mới nhất trước
        self.listed: list[int] = []     # limit của từng lần liệt kê
        self.measured: list[str] = []
        self.fail_listing = False
        monkeypatch.setattr(tiktok_links, "fetch_links", self._list)
        monkeypatch.setattr(tt, "check_video", self._check)

    def _list(self, ref, *, proxies, limit, **kw):
        self.listed.append(limit)
        if self.fail_listing:
            raise tiktok_links.TikTokError("TikTok đang chặn IP này")
        vids = _newest_first(*self.channel[:limit])
        return {"videos": vids, "kind": "ok" if vids else "empty", "via": "x", "attempts": 1}

    def _check(self, url, proxy=None):
        self.measured.append(url.rsplit("/", 1)[-1])
        return stat(views=int(url.rsplit("/", 1)[-1]), url=url)

    def run(self, creds=None) -> list[str]:
        self.measured.clear()
        items, _ = cc.collect_channel("tiktok", TT, creds or cc.Credentials(), "c1")
        return [i["external_id"] for i in items]


# ── refresh(): khi nào lấy đủ, khi nào chỉ lấy phần mới ───────────────────────────────────

def test_first_time_lists_everything():
    calls: list[int] = []
    out, mode = cl.refresh(set(), False, lambda n: calls.append(n) or _newest_first(3, 2, 1), full_max=1500)
    assert mode == "full" and calls == [1500] and [v["video_id"] for v in out] == ["3", "2", "1"]


def test_later_runs_only_ask_for_one_page_when_it_connects_to_the_saved_list():
    calls: list[int] = []
    page = _newest_first(*range(40, 10, -1))                   # 30 video, cũ nhất = 11 đã có trong kho
    out, mode = cl.refresh({"11", "10", "9"}, True, lambda n: calls.append(n) or page, full_max=1500)
    assert mode == "incremental" and calls == [cl.PAGE] and len(out) == 30


def test_a_short_page_means_the_whole_channel_was_seen():
    out, mode = cl.refresh({"1"}, True, lambda n: _newest_first(2, 1), full_max=1500)
    assert mode == "incremental"


def test_page_that_does_not_reach_a_known_video_may_have_a_gap_so_the_list_is_fetched_in_full():
    calls: list[int] = []
    big = _newest_first(*range(100, 0, -1))
    out, mode = cl.refresh({"5"}, True, lambda n: calls.append(n) or big[:n], full_max=1500)
    assert mode == "full" and calls == [cl.PAGE, 1500] and len(out) == 100


def test_pinned_old_video_at_the_top_does_not_hide_a_gap():
    # video ghim cũ (id 5, đã có) nằm đầu trang nhưng video cuối trang (mới đăng gần đây) chưa có trong kho => phải lấy đủ
    page = _newest_first(5, *range(100, 71, -1))
    _, mode = cl.refresh({"5"}, True, lambda n: page if n == cl.PAGE else _newest_first(*range(100, 0, -1)), full_max=1500)
    assert mode == "full"


# ── Thu thập TikTok có kho danh sách ─────────────────────────────────────────────────────

def test_first_run_lists_the_whole_channel_and_measures_all_of_it(db, monkeypatch):
    t = Tik(monkeypatch, list(range(100, 0, -1)))
    assert len(t.run()) == 100
    assert t.listed == [tiktok_links.FULL_MAX_ITEMS] and len(t.measured) == 100       # không còn giới hạn 15 video
    state = channels_db.video_state("c1")
    assert state["listed"] and len(state["videos"]) == 100


def test_next_run_fetches_only_new_videos_then_measures_the_saved_list(db, monkeypatch):
    t = Tik(monkeypatch, list(range(100, 0, -1)))
    t.run()
    t.channel = [103, 102, 101, *t.channel]                                             # kênh đăng thêm 3 video
    ids = t.run()
    assert t.listed == [tiktok_links.FULL_MAX_ITEMS, cl.PAGE]                           # lần 2 chỉ xin 1 trang, không liệt kê lại cả kênh
    assert len(ids) == 103 and ids[:3] == ["103", "102", "101"] and sorted(t.measured, key=int) == [str(i) for i in range(1, 104)]
    assert len(channels_db.video_state("c1")["videos"]) == 103


def test_run_without_new_videos_still_measures_every_saved_video(db, monkeypatch):
    t = Tik(monkeypatch, list(range(50, 0, -1)))
    t.run()
    assert len(t.run()) == 50 and t.listed[-1] == cl.PAGE


def test_too_many_new_videos_since_last_run_triggers_a_full_relist(db, monkeypatch):
    t = Tik(monkeypatch, list(range(40, 0, -1)))
    t.run()
    t.channel = [*range(200, 140, -1), *t.channel]                                      # 60 video mới > 1 trang
    assert len(t.run()) == 100 and t.listed[-2:] == [cl.PAGE, tiktok_links.FULL_MAX_ITEMS]


def test_failed_listing_after_the_first_run_does_not_stop_the_measurement(db, monkeypatch, caplog):
    t = Tik(monkeypatch, list(range(20, 0, -1)))
    t.run()
    t.fail_listing = True
    with caplog.at_level("WARNING", logger="collect"):
        assert len(t.run()) == 20
    assert "đo tiếp trên 20 video đã lưu" in caplog.text


def test_failed_first_listing_is_a_job_abort_and_nothing_is_marked_as_listed(db, monkeypatch):
    t = Tik(monkeypatch, [3, 2, 1])
    t.fail_listing = True
    with pytest.raises(cc.JobAbort, match="chặn IP"):
        t.run()
    assert channels_db.video_state("c1") == {"listed": False, "videos": []}


def test_empty_channel_is_marked_listed_so_next_time_only_a_page_is_asked(db, monkeypatch):
    t = Tik(monkeypatch, [])
    with pytest.raises(cc.EmptyChannel):
        t.run()
    t.channel = [2, 1]                                                                  # kênh vừa đăng video đầu tiên
    assert t.run() == ["2", "1"] and t.listed == [tiktok_links.FULL_MAX_ITEMS, cl.PAGE]


def test_channel_not_in_the_local_db_keeps_the_old_recent_only_behaviour(db, monkeypatch):
    t = Tik(monkeypatch, list(range(100, 0, -1)))
    items, _ = cc.collect_channel("tiktok", TT, cc.Credentials(), "not-pulled")
    assert len(items) == cc.TIKTOK_RECENT and t.listed == [cc.TIKTOK_RECENT]
    assert channels_db.video_state("not-pulled") is None


def test_links_saved_by_the_manual_button_count_as_a_full_list(db, monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"proxies": [], "youtube_api_keys": []}), encoding="utf-8")
    monkeypatch.setattr(vl, "EXPORT_DIR", tmp_path / "out")
    t = Tik(monkeypatch, list(range(30, 0, -1)))
    assert vl.run_fetch({"platform": "tiktok", "channel_id": "c1", "url": TT}, settings_path=cfg)["ok"]
    t.listed.clear()
    t.run()
    assert t.listed == [cl.PAGE]                                                         # không liệt kê đủ lần nữa


def test_database_from_before_the_list_feature_treats_channels_with_saved_links_as_listed(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY); INSERT INTO schema_version VALUES (3);
        CREATE TABLE fetched_users (id TEXT PRIMARY KEY, username TEXT NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE local_channels (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, owner_username TEXT NOT NULL, platform TEXT NOT NULL,
          resource_type TEXT NOT NULL DEFAULT 'channel', display_name TEXT NOT NULL DEFAULT '', input_ref TEXT NOT NULL DEFAULT '',
          canonical_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', mapping_status TEXT NOT NULL DEFAULT '',
          server_video_count INTEGER NOT NULL DEFAULT 0, last_collected_at TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1, pulled INTEGER NOT NULL DEFAULT 0, pulled_at TEXT NOT NULL DEFAULT '',
          imported_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE channel_videos (channel_id TEXT NOT NULL, video_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
          published_at TEXT NOT NULL DEFAULT '', views INTEGER, fetched_at TEXT NOT NULL, PRIMARY KEY (channel_id, video_id));
        INSERT INTO local_channels (id, owner_id, owner_username, platform, imported_at, updated_at) VALUES ('with', 'u', 'u', 'tiktok', 'x', 'x'), ('without', 'u', 'u', 'tiktok', 'x', 'x');
        INSERT INTO channel_videos VALUES ('with', '1', 'https://t/1', '', '', NULL, '2026-10-17T06:45:04+00:00');
    """)
    old.commit()
    old.close()
    monkeypatch.setattr(channels_db, "DB_PATH", str(path))
    assert channels_db.video_state("with")["listed"] is True and channels_db.video_state("without")["listed"] is False


# ── Đo song song / YouTube ───────────────────────────────────────────────────────────────

def test_measuring_with_several_proxies_runs_in_parallel_but_keeps_the_listing_order(db, monkeypatch):
    t = Tik(monkeypatch, list(range(60, 0, -1)))
    seen_proxies: set = set()
    monkeypatch.setattr(tt, "check_video", lambda url, proxy=None: seen_proxies.add(proxy) or stat(views=1, url=url))
    items, _ = cc.collect_channel("tiktok", TT, cc.Credentials(proxies=[f"http://10.0.0.{i}:80" for i in range(4)]), "c1")
    assert [i["external_id"] for i in items] == [str(i) for i in range(60, 0, -1)] and len(seen_proxies) > 1


def test_a_video_that_cannot_be_measured_is_skipped_not_fatal(db, monkeypatch):
    Tik(monkeypatch, [3, 2, 1])
    monkeypatch.setattr(tt, "check_video", lambda url, proxy=None: fail("Video không tồn tại") if url.endswith("/2") else stat(views=5, url=url))
    items, _ = cc.collect_channel("tiktok", TT, cc.Credentials(), "c1")
    assert [(i["external_id"], i["check_status"]) for i in items] == [("3", "ok"), ("2", "error"), ("1", "ok")]


class YtApi:
    pages: list[int] = []
    stats_for: list[list[str]] = []
    channel_ids = [f"v{i}" for i in range(80, 0, -1)]

    def __init__(self, pool, **kw): ...
    def close(self): ...
    def resolve_channel(self, ref):
        return {"channel_id": "UC1", "uploads": "UU1", "followers": "10", "videos_posted": "80"}

    def recent_video_ids(self, uploads, limit=30):
        YtApi.pages.append(limit)
        return self.channel_ids[:limit]

    def video_stats(self, ids):
        YtApi.stats_for.append(list(ids))
        return {i: stat(views=1, video_id=i, url=f"https://www.youtube.com/watch?v={i}") for i in ids}


def test_youtube_with_api_key_lists_once_then_updates_a_page_and_measures_all_saved_videos(db, monkeypatch):
    conn = channels_db._get_conn()
    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "u"}], "channels": [
        {"id": "y1", "owner_account_id": "u1", "owner_username": "u", "platform": "youtube", "input_ref": "@k", "display_name": "k"}]})
    conn.close()
    YtApi.pages, YtApi.stats_for = [], []
    monkeypatch.setattr(yt, "YouTubeApi", YtApi)
    creds = cc.Credentials(youtube_keys=[KEY])
    items, channel = cc.collect_channel("youtube", "@k", creds, "y1")
    assert YtApi.pages == [cl.YOUTUBE_MAX] and len(items) == 80 and channel == {"followers": 10, "videos_posted": 80}
    items, _ = cc.collect_channel("youtube", "@k", creds, "y1")
    assert YtApi.pages == [cl.YOUTUBE_MAX, cl.PAGE] and len(YtApi.stats_for[-1]) == 80 and len(items) == 80


# ── Nối vào job ─────────────────────────────────────────────────────────────────────────

def test_the_job_passes_its_resource_id_so_the_channel_list_can_be_stored(tmp_path, monkeypatch):
    seen: list = []

    def fake(platform, ref, creds, channel_id=None):
        seen.append(channel_id)
        raise cc.EmptyChannel("x")
    monkeypatch.setattr(collect.channel_collect, "collect_channel", fake)

    class Client:
        async def complete_job(self, job, result=None): ...
        async def heartbeat(self, job): ...

    job = {"job_id": "j", "platform": "tiktok", "attempt": 1, "resource": {"resource_id": "c1", "external_id": TT, "display_name": "a"}}
    asyncio.run(collect.process_job(job, Client(), cc.Credentials(), collect.StatusTracker(str(tmp_path / "s.json"))))
    assert seen == ["c1"]
