"""Nút "Lấy toàn bộ link video": lấy link (YouTube / TikTok) -> lưu DB cục bộ -> xuất .txt; lỗi trả về dạng `{error}` chứ không ném."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels_db  # noqa: E402
import tiktok_links as tl  # noqa: E402
import video_links as vl  # noqa: E402
from checkers import youtube as yt  # noqa: E402
from checkers.common import stat  # noqa: E402

TT = "https://www.tiktok.com/@sk1kr"
YT = "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
KEY = "AIza" + "A" * 35
BLOCKED = RuntimeError("ERROR: [tiktok:user] sk1kr: Unable to extract secondary user ID")


def _entries(n: int) -> list[dict]:
    return [{"id": str(9000 - i), "url": f"https://www.tiktok.com/@sk1kr/video/{9000 - i}", "title": f"v{i}", "timestamp": 1792215000 + i,
             "view_count": 100 + i} for i in range(n)]


def _channel(cid: str, platform: str, ref: str) -> dict:
    return {"id": cid, "owner_account_id": "u1", "owner_username": "hieu", "platform": platform, "resource_type": "channel",
            "canonical_external_id": ref, "input_ref": ref, "display_name": ref, "url": ref, "mapping_status": "mapped",
            "execution_mode": "shared", "enabled": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(channels_db, "DB_PATH", str(tmp_path / "channels.db"))
    monkeypatch.setattr(vl, "EXPORT_DIR", tmp_path / "out")
    monkeypatch.setattr(tl.time, "sleep", lambda s: None)
    conn = channels_db._get_conn()
    channels_db.act_init(conn, {})
    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": [_channel("c1", "tiktok", TT), _channel("y1", "youtube", YT)]})
    conn.close()
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"proxies": [], "youtube_api_keys": [KEY]}), encoding="utf-8")
    return cfg


# ── TikTok ───────────────────────────────────────────────────────────────────────────────

def test_tiktok_saves_links_exports_txt_and_is_idempotent(env, monkeypatch):
    monkeypatch.setattr(tl, "_extract_flat", lambda url, proxy, imp, limit: _entries(4))
    cmd = {"platform": "tiktok", "channel_id": "c1", "url": TT}
    r1, r2 = vl.run_fetch(cmd, settings_path=env), vl.run_fetch(cmd, settings_path=env)
    assert (r1["found"], r1["added"], r1["total"]) == (4, 4, 4) and (r2["added"], r2["total"]) == (0, 4)
    lines = Path(r1["export"]).read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("/video/8997") and len(lines) == 4       # mới đăng nhất (timestamp lớn nhất) ở đầu
    conn = channels_db._get_conn()
    try:
        counts = {c["id"]: c["video_count"] for c in channels_db.act_list_channels(conn, {})["channels"]}
    finally:
        conn.close()
    assert counts == {"c1": 4, "y1": 0}


def test_tiktok_blocking_is_reported_as_error_not_exception(env, monkeypatch):
    def blocked(*a):
        raise BLOCKED
    monkeypatch.setattr(tl, "_extract_flat", blocked)
    monkeypatch.setattr(tl, "MAX_ATTEMPTS", 3)
    assert "3 lượt" in vl.run_fetch({"platform": "tiktok", "channel_id": "c1", "url": TT}, settings_path=env)["error"]


def test_unknown_channel_returns_error_instead_of_raising(env, monkeypatch):
    monkeypatch.setattr(tl, "_extract_flat", lambda url, proxy, imp, limit: _entries(1))
    assert "error" in vl.run_fetch({"platform": "tiktok", "channel_id": "nope", "url": TT}, settings_path=env)


def test_unsupported_platform_is_a_clear_error(env):
    assert "chưa hỗ trợ" in vl.run_fetch({"platform": "facebook", "channel_id": "c1", "url": "x"}, settings_path=env)["error"]


# ── YouTube ──────────────────────────────────────────────────────────────────────────────

class FakeApi:
    def __init__(self, pool, **kw): ...
    def close(self): ...

    def resolve_channel(self, ref):
        return {"channel_id": "UC1", "title": "K", "uploads": "UU1", "followers": "1", "videos_posted": "3"}

    def recent_video_ids(self, uploads, limit=30):
        return ["v3", "v2", "v1"]

    def video_stats(self, ids):   # v1 riêng tư => không có thống kê nhưng link vẫn được lưu
        return {i: stat(views=10 * n, title=f"t{n}", published_at="2026-10-16T17:30:00Z", video_id=i) for n, i in enumerate(ids[:2], 1)}


def test_youtube_lists_every_video_with_title_date_and_views(env, monkeypatch):
    monkeypatch.setattr(yt, "YouTubeApi", FakeApi)
    r = vl.run_fetch({"platform": "youtube", "channel_id": "y1", "url": YT}, settings_path=env)
    assert (r["found"], r["total"], r["via"]) == (3, 3, "YouTube Data API")
    conn = channels_db._get_conn()
    try:
        rows = {v["video_id"]: v for v in channels_db.act_list_channel_videos(conn, {"channel_id": "y1"})["videos"]}
    finally:
        conn.close()
    assert rows["v3"]["title"] == "t1" and rows["v3"]["views"] == 10 and rows["v1"]["url"].endswith("v=v1") and rows["v1"]["title"] == ""


def test_youtube_api_failures_become_errors(env, monkeypatch):
    class Dead(FakeApi):
        def resolve_channel(self, ref):
            raise yt.NoKeyAvailable("Không còn YouTube API key nào dùng được")
    monkeypatch.setattr(yt, "YouTubeApi", Dead)
    assert "API key" in vl.run_fetch({"platform": "youtube", "channel_id": "y1", "url": YT}, settings_path=env)["error"]

    class Missing(FakeApi):
        def resolve_channel(self, ref):
            raise ValueError("Không tìm thấy kênh YouTube: x")
    monkeypatch.setattr(yt, "YouTubeApi", Missing)
    assert "Không tìm thấy" in vl.run_fetch({"platform": "youtube", "channel_id": "y1", "url": YT}, settings_path=env)["error"]


def test_youtube_without_keys_falls_back_to_ytdlp_ids(env, monkeypatch, tmp_path):
    cfg = tmp_path / "nokey.json"
    cfg.write_text(json.dumps({"proxies": []}), encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(yt, "list_channel_ytdlp", lambda url, limit, proxy: seen.append(url) or ["a", "b"])
    r = vl.run_fetch({"platform": "youtube", "channel_id": "y1", "url": YT}, settings_path=cfg)
    assert r["found"] == 2 and r["via"] == "yt-dlp" and seen == ["https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"]


def test_youtube_channel_without_videos_tab_is_empty_not_error(env, monkeypatch, tmp_path):
    cfg = tmp_path / "nokey.json"
    cfg.write_text("{}", encoding="utf-8")

    def no_tab(url, limit, proxy):
        raise RuntimeError("ERROR: [youtube:tab] UC1: This channel does not have a videos tab")
    monkeypatch.setattr(yt, "list_channel_ytdlp", no_tab)
    assert vl.run_fetch({"platform": "youtube", "channel_id": "y1", "url": YT}, settings_path=cfg)["kind"] == "empty"


# ── DB ───────────────────────────────────────────────────────────────────────────────────

def test_later_fetch_does_not_wipe_known_title_with_blank(env):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "Tiêu đề", "views": 10}]})
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "", "views": None}]})
        row = channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"][0]
    finally:
        conn.close()
    assert row["title"] == "Tiêu đề" and row["views"] == 10


def test_videos_disappear_when_their_channel_leaves_the_shared_list(env):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u"}]})
        channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": []})
        assert channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"] == []
    finally:
        conn.close()
