"""Thu thập một kênh theo nền tảng: YouTube (xoay key, rơi về yt-dlp), TikTok (liệt kê + tikwm), Facebook (chỉ link bài/reel), xoay proxy."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channel_collect as cc  # noqa: E402
import tiktok_links  # noqa: E402
from checkers import facebook as fb  # noqa: E402
from checkers import tiktok as tt  # noqa: E402
from checkers import youtube as yt  # noqa: E402
from checkers.common import fail, stat  # noqa: E402

KEY_A = "AIza" + "A" * 35


class FakeApi:
    """Thay `YouTubeApi`: không mạng."""

    def __init__(self, pool, **kw):
        self.pool = pool

    def close(self): ...

    def resolve_channel(self, ref):
        return {"channel_id": "UC1", "title": "Kênh", "uploads": "UU1", "followers": "1500", "videos_posted": "42"}

    def recent_video_ids(self, uploads, limit=30):
        return ["v1", "v2", "v3"]

    def video_stats(self, ids):   # v3 đã bị xoá/riêng tư => không có trong kết quả
        return {i: stat(views=10 * n, likes=n, comments=n, published_at="2026-10-16T17:30:00Z", title=f"t{n}", video_id=i,
                        url=f"https://www.youtube.com/watch?v={i}") for n, i in enumerate(ids[:2], 1)}


# ── YouTube ──────────────────────────────────────────────────────────────────────────────

def test_youtube_with_keys_returns_video_items_and_channel_stats(monkeypatch):
    monkeypatch.setattr(yt, "YouTubeApi", FakeApi)
    items, channel = cc.collect_channel("youtube", "@kenh", cc.Credentials(youtube_keys=[KEY_A]))
    assert [i["external_id"] for i in items] == ["v1", "v2"]
    assert items[0]["views"] == 10 and items[0]["check_status"] == "ok" and items[0]["title"] == "t1"
    assert channel == {"followers": 1500, "videos_posted": 42}


def test_youtube_hidden_subscribers_are_not_sent_as_zero(monkeypatch):
    class Hidden(FakeApi):
        def resolve_channel(self, ref):
            return {**super().resolve_channel(ref), "followers": None}
    monkeypatch.setattr(yt, "YouTubeApi", Hidden)
    _, channel = cc.collect_channel("youtube", "@kenh", cc.Credentials(youtube_keys=[KEY_A]))
    assert channel == {"videos_posted": 42}


def test_youtube_unknown_channel_aborts_without_retry(monkeypatch):
    class Missing(FakeApi):
        def resolve_channel(self, ref):
            raise ValueError("Không tìm thấy kênh YouTube: x")
    monkeypatch.setattr(yt, "YouTubeApi", Missing)
    with pytest.raises(cc.JobAbort) as exc:
        cc.collect_channel("youtube", "@x", cc.Credentials(youtube_keys=[KEY_A]))
    assert exc.value.retryable is False


def test_youtube_all_keys_exhausted_aborts_retryable_with_clear_reason(monkeypatch):
    class Exhausted(FakeApi):
        def resolve_channel(self, ref):
            raise yt.NoKeyAvailable("Không còn YouTube API key nào dùng được")
    monkeypatch.setattr(yt, "YouTubeApi", Exhausted)
    with pytest.raises(cc.JobAbort, match="API key") as exc:
        cc.collect_channel("youtube", "@x", cc.Credentials(youtube_keys=[KEY_A]))
    assert exc.value.retryable is True


def test_youtube_without_keys_falls_back_to_ytdlp_through_a_proxy(monkeypatch):
    used: list = []
    monkeypatch.setattr(yt, "list_channel_ytdlp", lambda url, limit, proxy: used.append(("list", proxy)) or ["a", "b"])
    monkeypatch.setattr(yt, "check_video_ytdlp", lambda url, proxy: used.append(("one", proxy)) or stat(views=5, video_id=url[-1], url=url))
    items, channel = cc.collect_channel("youtube", "https://www.youtube.com/@kenh", cc.Credentials(proxies=["http://1.1.1.1:80"]))
    assert [i["external_id"] for i in items] == ["a", "b"] and channel == {}
    assert {p for _, p in used} == {"http://1.1.1.1:80"}


def test_youtube_channel_without_videos_tab_is_a_clear_non_retryable_abort(monkeypatch):
    def no_tab(url, limit, proxy):
        raise RuntimeError("ERROR: [youtube:tab] UC1: This channel does not have a videos tab")
    monkeypatch.setattr(yt, "list_channel_ytdlp", no_tab)
    with pytest.raises(cc.JobAbort, match="chưa có video") as exc:
        cc.collect_channel("youtube", "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", cc.Credentials())
    assert exc.value.retryable is False


# ── TikTok ───────────────────────────────────────────────────────────────────────────────

def _listing(n: int) -> dict:
    return {"videos": [{"video_id": str(i), "url": f"https://www.tiktok.com/@a/video/{i}"} for i in range(n)], "kind": "ok", "via": "x", "browser_opens": 0}


def test_tiktok_lists_recent_videos_then_measures_each_with_tikwm(monkeypatch):
    monkeypatch.setattr(tiktok_links, "fetch_links", lambda ref, **kw: _listing(3))
    monkeypatch.setattr(tt, "check_video", lambda url, proxy=None: stat(views=7, likes=1, region="KR", published_at=1792215000, url=url))
    items, channel = cc.collect_channel("tiktok", "https://www.tiktok.com/@a", cc.Credentials())
    assert [i["external_id"] for i in items] == ["0", "1", "2"] and items[0]["region"] == "KR" and channel == {}


def test_tiktok_only_measures_the_most_recent_videos(monkeypatch):
    monkeypatch.setattr(tiktok_links, "fetch_links", lambda ref, **kw: _listing(100))
    calls: list = []
    monkeypatch.setattr(tt, "check_video", lambda url, proxy=None: calls.append(url) or stat(views=1))
    cc.collect_channel("tiktok", "@a", cc.Credentials())
    assert len(calls) == cc.TIKTOK_RECENT


def test_tiktok_listing_failure_becomes_a_job_abort_with_the_reason(monkeypatch):
    def boom(ref, **kw):
        raise tiktok_links.TikTokError("TikTok đang chặn IP này")
    monkeypatch.setattr(tiktok_links, "fetch_links", boom)
    with pytest.raises(cc.JobAbort, match="chặn IP"):
        cc.collect_channel("tiktok", "@a", cc.Credentials())


def test_tiktok_channel_without_videos_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(tiktok_links, "fetch_links", lambda ref, **kw: {"videos": [], "kind": "empty", "via": "x", "browser_opens": 0})
    assert cc.collect_channel("tiktok", "@a", cc.Credentials()) == ([], {})


# ── Facebook ─────────────────────────────────────────────────────────────────────────────

def test_facebook_page_url_is_rejected_without_retry():
    with pytest.raises(cc.JobAbort, match="bài/reel") as exc:
        cc.collect_channel("facebook", "https://www.facebook.com/somepage", cc.Credentials(fb_cookie="c_user=1"))
    assert exc.value.retryable is False


def test_facebook_needs_a_cookie_and_says_so():
    with pytest.raises(cc.JobAbort, match="cookie"):
        cc.collect_channel("facebook", "https://www.facebook.com/reel/1", cc.Credentials())


def test_facebook_reel_is_measured_and_keyed_by_resolved_url(monkeypatch):
    monkeypatch.setattr(fb, "check_url", lambda url, cookie, proxy=None, sub_type=None: stat(views=99, likes=3, url="https://www.facebook.com/reel/1"))
    creds = cc.Credentials(fb_cookie="c_user=1; xs=2")
    items, _ = cc.collect_channel("facebook", "https://www.facebook.com/share/r/abc", creds)
    assert items[0]["external_id"] == "https://www.facebook.com/reel/1" and items[0]["views"] == 99


def test_unknown_platform_is_rejected_without_retry():
    with pytest.raises(cc.JobAbort) as exc:
        cc.collect_channel("instagram", "x", cc.Credentials())
    assert exc.value.retryable is False


# ── Xoay proxy ───────────────────────────────────────────────────────────────────────────

def test_proxy_fault_rests_that_proxy_and_the_next_proxy_is_used():
    creds = cc.Credentials(proxies=["http://bad:1", "http://good:2"])
    seen: list = []

    def call(proxy):
        seen.append(proxy)
        return fail("[tikwm] Proxy error") if proxy == "http://bad:1" else stat(views=1)
    assert cc._with_proxy(creds, call)["ok"] is True
    assert seen == ["http://bad:1", "http://good:2"]
    assert creds.proxy_pool.pick() == "http://good:2"                    # bad đang nghỉ


def test_http_403_from_the_checker_means_that_proxy_is_blocked_so_another_one_is_tried():
    creds = cc.Credentials(proxies=["http://blocked:1", "http://ok:2"])
    seen: list = []

    def call(proxy):
        seen.append(proxy)
        return fail("[tikwm] HTTP 403") if proxy == "http://blocked:1" else stat(views=1)
    assert cc._with_proxy(creds, call)["ok"] is True and seen == ["http://blocked:1", "http://ok:2"]


def test_non_proxy_error_is_returned_as_is_and_proxy_stays_healthy():
    creds = cc.Credentials(proxies=["http://a:1", "http://b:2"])
    seen: list = []

    def call(proxy):
        seen.append(proxy)
        return fail("[tikwm] Url parsing is failed!")
    assert cc._with_proxy(creds, call)["error"] == "[tikwm] Url parsing is failed!"
    assert seen == ["http://a:1"]                                        # lỗi của video, không phải của proxy => không đổi proxy


def test_no_proxies_goes_direct():
    seen: list = []
    assert cc._with_proxy(cc.Credentials(), lambda p: seen.append(p) or stat(views=1))["ok"] and seen == [None]


# ── Nạp thông tin xác thực từ file cấu hình ─────────────────────────────────────────────

def test_load_credentials_reads_lists_and_cookie(tmp_path, monkeypatch):
    for name in ("YOUTUBE_API_KEY", "YOUTUBE_API_KEYS", "PROXY", "PROXIES", "FB_COOKIE"):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"youtube_api_keys": [KEY_A], "proxies": ["1.1.1.1:80"], "fb_cookie": "c_user=9"}), encoding="utf-8")
    c = cc.load_credentials(cfg)
    assert (c.youtube_keys, c.proxies, c.fb_cookie) == ([KEY_A], ["http://1.1.1.1:80"], "c_user=9")


def test_load_credentials_survives_missing_or_broken_file(tmp_path):
    assert cc.load_credentials(tmp_path / "nope.json").youtube_keys == []
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert cc.load_credentials(tmp_path / "bad.json").proxies == []
