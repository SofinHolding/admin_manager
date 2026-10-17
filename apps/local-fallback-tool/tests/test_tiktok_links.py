"""Lấy TẤT CẢ link video của một kênh TikTok: phân loại lỗi, xoay proxy, tái dùng phiên cookie, lưu DB cục bộ + xuất .txt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_cookie  # noqa: E402
import channels_db  # noqa: E402
import tiktok_links as tl  # noqa: E402

CHANNEL = "https://www.tiktok.com/@sk1kr"
SESSION = {"cookies": [{"name": "ttwid", "value": "1", "domain": ".tiktok.com", "path": "/"}], "user_agent": "UA", "proxy": ""}


def _entries(n: int) -> list[dict]:
    return [{"id": str(9000 - i), "url": f"https://www.tiktok.com/@sk1kr/video/{9000 - i}", "title": f"v{i}", "timestamp": 1792215000 + i,
             "view_count": 100 + i} for i in range(n)]


class Session:
    """`get_session` giả: đếm số lần mở trình duyệt."""

    def __init__(self) -> None:
        self.opened: list[str | None] = []

    def __call__(self, chrome_path: str, proxy: str | None) -> dict:
        self.opened.append(proxy)
        return {**SESSION, "proxy": proxy or ""}


# ── nhận dạng kênh ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", ["https://www.tiktok.com/@sk1kr", "https://www.tiktok.com/@sk1kr/video/123?lang=en", "@sk1kr", "sk1kr"])
def test_channel_url_from_common_inputs(ref):
    assert tl.channel_url(ref) == CHANNEL


def test_channel_url_rejects_garbage_with_a_friendly_error():
    with pytest.raises(tl.TikTokError):
        tl.channel_url("https://example.com/x")


# ── phân loại lỗi yt-dlp ─────────────────────────────────────────────────────────────────

def test_error_classification_matches_real_ytdlp_messages():
    assert tl.classify_error(Exception("This user does not have any videos posted")) == "empty"
    assert tl.classify_error(Exception("This account is private")) == "private"
    assert tl.classify_error(Exception("Unable to extract secondary user ID")) == "refreshable"


def test_list_videos_dedupes_keeps_order_and_converts_timestamp_to_iso():
    entries = _entries(3) + [_entries(1)[0]]
    videos = tl.list_videos(CHANNEL, SESSION, extract=lambda *a: entries)
    assert [v["video_id"] for v in videos] == ["9000", "8999", "8998"]
    assert videos[0]["published_at"].startswith("2026-10-")  and videos[0]["views"] == 100


def test_cookie_file_is_removed_after_listing_even_on_error(tmp_path):
    paths: list[str] = []

    def extract(url, jar, ua, proxy, limit):
        paths.append(jar)
        assert "ttwid" in Path(jar).read_text(encoding="utf-8")
        raise RuntimeError("Unable to extract secondary user ID")
    with pytest.raises(tl.RefreshableError):
        tl.list_videos(CHANNEL, SESSION, extract=extract)
    assert not Path(paths[0]).exists()


# ── điều phối ────────────────────────────────────────────────────────────────────────────

def test_fetch_links_opens_the_browser_once_and_reuses_the_saved_session(tmp_path):
    sess, store = Session(), tmp_path / "s.json"
    kw = dict(chrome_path="", proxies=["http://1.1.1.1:80"], session_path=store, get_session=sess, extract=lambda *a: _entries(5))
    first = tl.fetch_links(CHANNEL, **kw)
    second = tl.fetch_links(CHANNEL, **kw)
    assert len(first["videos"]) == 5 and first["browser_opens"] == 1 and second["browser_opens"] == 0
    assert sess.opened == ["http://1.1.1.1:80"]


def test_fresh_session_that_still_fails_means_proxy_problem_so_next_proxy_is_used_without_reopening_the_browser(tmp_path):
    sess, calls = Session(), []

    def extract(url, jar, ua, proxy, limit):
        calls.append(proxy)
        if proxy == "http://bad:1":
            raise RuntimeError("Unable to extract secondary user ID")
        return _entries(2)
    out = tl.fetch_links(CHANNEL, chrome_path="", proxies=["http://bad:1", "http://good:2"], session_path=tmp_path / "s.json",
                         get_session=sess, extract=extract)
    assert calls == ["http://bad:1", "http://good:2"]                 # mỗi proxy hỏng chỉ thử MỘT lần khi phiên còn mới
    assert sess.opened == ["http://bad:1", "http://good:2"] and "good" in out["via"]


def test_stale_session_is_refreshed_exactly_once_then_retried(tmp_path):
    store, sess, state = tmp_path / "s.json", Session(), {"calls": 0}
    tl.save_session(None, SESSION, store, now=lambda: 0.0)               # phiên cũ (saved_at=0)

    def extract(url, jar, ua, proxy, limit):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("expired")
        return _entries(1)
    out = tl.fetch_links(CHANNEL, chrome_path="", proxies=[], session_path=store, get_session=sess, extract=extract)
    assert state["calls"] == 2 and out["browser_opens"] == 1 and len(out["videos"]) == 1


def test_sessions_are_stored_per_proxy(tmp_path):
    store = tmp_path / "s.json"
    tl.save_session("http://a:1", {**SESSION, "user_agent": "A"}, store)
    tl.save_session("http://b:1", {**SESSION, "user_agent": "B"}, store)
    assert tl.load_session("http://a:1", store)[0]["user_agent"] == "A" and tl.load_session("http://c:1", store) == (None, False)


def test_empty_channel_is_a_result_not_an_error(tmp_path):
    def extract(*a):
        raise RuntimeError("This user does not have any videos posted")
    out = tl.fetch_links(CHANNEL, chrome_path="", proxies=[], session_path=tmp_path / "s.json", get_session=Session(), extract=extract)
    assert out["kind"] == "empty" and out["videos"] == []


def test_private_channel_stops_immediately_without_trying_other_proxies(tmp_path):
    calls = []

    def extract(url, jar, ua, proxy, limit):
        calls.append(proxy)
        raise RuntimeError("This account is private")
    with pytest.raises(tl.TikTokError, match="riêng tư"):
        tl.fetch_links(CHANNEL, chrome_path="", proxies=["http://a:1", "http://b:1"], session_path=tmp_path / "s.json", get_session=Session(), extract=extract)
    assert len(calls) == 1


def test_when_everything_fails_the_error_explains_what_to_do(tmp_path):
    def get_session(path, proxy):
        raise browser_cookie.BrowserError("Không mở được trình duyệt")
    with pytest.raises(tl.TikTokError) as exc:
        tl.fetch_links(CHANNEL, chrome_path="", proxies=[], session_path=tmp_path / "s.json", get_session=get_session)
    assert "proxy" in str(exc.value) and "Không mở được trình duyệt" in str(exc.value)


def test_proxy_password_is_never_in_the_error(tmp_path):
    def extract(*a):
        raise RuntimeError("x")
    with pytest.raises(tl.TikTokError) as exc:
        tl.fetch_links(CHANNEL, chrome_path="", proxies=["socks5://u:TOPSECRET@9.9.9.9:1080"], session_path=tmp_path / "s.json",
                       get_session=Session(), extract=extract)
    assert "TOPSECRET" not in str(exc.value)


# ── Lưu DB cục bộ + xuất .txt ────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "channels.db"
    monkeypatch.setattr(channels_db, "DB_PATH", str(path))
    conn = channels_db._get_conn()
    channels_db.act_init(conn, {})
    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": [
        {"id": "c1", "owner_account_id": "u1", "owner_username": "hieu", "platform": "tiktok", "resource_type": "channel",
         "canonical_external_id": "sk1kr", "input_ref": CHANNEL, "display_name": "sk1kr", "url": CHANNEL, "mapping_status": "mapped",
         "execution_mode": "shared", "enabled": True}]})
    conn.close()
    return path


def test_run_fetch_saves_links_exports_txt_and_is_idempotent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "EXPORT_DIR", tmp_path / "out")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"proxies": []}), encoding="utf-8")
    kw = dict(settings_path=cfg, session_path=tmp_path / "s.json", get_session=Session(), extract=lambda *a: _entries(4))
    r1 = tl.run_fetch({"channel_id": "c1", "url": CHANNEL}, **kw)
    r2 = tl.run_fetch({"channel_id": "c1", "url": CHANNEL}, **kw)
    assert (r1["found"], r1["added"], r1["total"]) == (4, 4, 4) and (r2["added"], r2["total"]) == (0, 4)
    lines = Path(r1["export"]).read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("/video/8997") and len(lines) == 4       # mới đăng nhất (timestamp lớn nhất) ở đầu
    conn = channels_db._get_conn()
    try:
        ch = channels_db.act_list_channels(conn, {})["channels"][0]
    finally:
        conn.close()
    assert ch["video_count"] == 4


def test_run_fetch_returns_error_for_unknown_channel_instead_of_raising(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "EXPORT_DIR", tmp_path / "out")
    r = tl.run_fetch({"channel_id": "nope", "url": CHANNEL}, settings_path=tmp_path / "none.json", session_path=tmp_path / "s.json",
                     get_session=Session(), extract=lambda *a: _entries(1))
    assert "error" in r


def test_later_fetch_does_not_wipe_known_title_with_blank(db):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "Tiêu đề", "views": 10}]})
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "", "views": None}]})
        row = channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"][0]
    finally:
        conn.close()
    assert row["title"] == "Tiêu đề" and row["views"] == 10


def test_videos_disappear_when_their_channel_leaves_the_shared_list(db):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u"}]})
        channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": []})
        assert channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"] == []
    finally:
        conn.close()
