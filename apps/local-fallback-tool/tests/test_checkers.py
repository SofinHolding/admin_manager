"""Bộ kiểm tra theo nền tảng (chuyển từ CheckviewV2): TikTok/tikwm, YouTube Data API (xoay key) + yt-dlp, Facebook (nhận dạng link)."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkers import facebook as fb  # noqa: E402
from checkers import tiktok as tt  # noqa: E402
from checkers import youtube as yt  # noqa: E402
from checkers.common import stat  # noqa: E402
import system_api  # noqa: E402

KEY_A = "AIza" + "A" * 35
KEY_B = "AIza" + "B" * 35


# ── common ───────────────────────────────────────────────────────────────────────────────

def test_stat_treats_hidden_or_negative_numbers_as_zero_and_omits_empty_fields():
    s = stat(views=None, likes=-5, comments="12", shares=True, region="", title="t")
    assert (s["views"], s["likes"], s["comments"], s["shares"]) == (0, 0, 12, 0)
    assert "region" not in s and s["title"] == "t" and "published_at" not in s


# ── TikTok / tikwm ───────────────────────────────────────────────────────────────────────

TIKWM_OK = {"code": 0, "data": {"id": "7001", "play_count": 1000, "digg_count": 50, "comment_count": 7, "share_count": 3, "create_time": 1792215000,
                                "duration": 21, "region": "KR", "title": "hi", "author": {"unique_id": "sk1kr"}}}


def test_parse_tikwm_success_maps_counts_region_and_epoch_date():
    r = tt.parse_tikwm(TIKWM_OK, "https://www.tiktok.com/@sk1kr/video/7001")
    assert r["ok"] and (r["views"], r["likes"], r["comments"], r["shares"]) == (1000, 50, 7, 3)
    assert r["region"] == "KR" and r["username"] == "sk1kr" and r["duration_seconds"] == 21
    assert system_api.to_report_date(r["published_at"], r["region"]) == "2026-10-17"   # epoch -> ngày giờ Việt Nam


def test_parse_tikwm_reports_platform_message_on_failure():
    r = tt.parse_tikwm({"code": -1, "msg": "Url parsing is failed!"}, "u")
    assert r == {"ok": False, "error": "[tikwm] Url parsing is failed!"}


def test_tiktok_check_video_turns_network_problems_into_errors_not_exceptions():
    def boom(request):
        raise httpx.ConnectError("x")
    client = httpx.Client(transport=httpx.MockTransport(boom))
    assert tt.check_video("https://www.tiktok.com/@a/video/1", client=client, retries=0)["ok"] is False
    http500 = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert "HTTP 500" in tt.check_video("https://www.tiktok.com/@a/video/1", client=http500, retries=0)["error"]


def test_tikwm_transient_errors_are_retried_but_real_video_errors_are_not(monkeypatch):
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    calls: list[int] = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(200, json={"code": -1, "msg": "Free Api Limit: 1 request/second."}) if len(calls) < 3 else httpx.Response(200, json=TIKWM_OK)
    client = httpx.Client(transport=httpx.MockTransport(flaky))
    assert tt.check_video("https://www.tiktok.com/@a/video/1", client=client)["ok"] is True and len(calls) == 3

    calls.clear()
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(200, json={"code": -1, "msg": "Url parsing is failed!"})))
    assert tt.check_video("https://www.tiktok.com/@a/video/1", client=bad)["ok"] is False and len(calls) == 1


# ── YouTube: nhận dạng ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "https://youtu.be/dQw4w9WgXcQ?si=x",
                                 "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"])
def test_video_id_of_common_link_shapes(url):
    assert yt.video_id_of(url) == "dQw4w9WgXcQ"


def test_video_id_of_unknown_is_empty():
    assert yt.video_id_of("https://example.com/x") == ""


def test_parse_duration_and_livestream():
    assert yt.parse_duration("PT1H2M10S") == 3730 and yt.parse_duration("PT45S") == 45
    assert yt.parse_duration("P0D") is None and yt.parse_duration(None) is None


@pytest.mark.parametrize("ref,expected", [
    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", {"id": "UCabcdefghijklmnopqrstuv"}),
    ("UCabcdefghijklmnopqrstuv", {"id": "UCabcdefghijklmnopqrstuv"}),
    ("https://www.youtube.com/@Some.Name/videos", {"forHandle": "Some.Name"}),
    ("@handle", {"forHandle": "handle"}),
    ("https://www.youtube.com/c/OldName", {"forUsername": "OldName"}),
])
def test_channel_query(ref, expected):
    assert yt.channel_query(ref) == expected


# ── YouTube Data API: xoay key ───────────────────────────────────────────────────────────

def _api(handler) -> yt.YouTubeApi:
    return yt.YouTubeApi([KEY_A, KEY_B], client=httpx.Client(transport=httpx.MockTransport(handler)))


def _quota() -> httpx.Response:
    return httpx.Response(403, json={"error": {"code": 403, "message": "q", "errors": [{"reason": "quotaExceeded"}]}})


def test_youtube_api_moves_to_next_key_when_one_runs_out_of_quota():
    used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.params["key"]
        used.append(key)
        return _quota() if key == KEY_A else httpx.Response(200, json={"items": ["ok"]})

    api = _api(handler)
    assert api.get("videos", {"part": "id"}) == {"items": ["ok"]}
    assert used == [KEY_A, KEY_B]
    api.get("videos", {"part": "id"})
    assert used[2:] == [KEY_B]               # key A đang nghỉ => không bị gọi lại ngay


def test_youtube_api_with_every_key_exhausted_raises_clear_error():
    api = _api(lambda r: _quota())
    with pytest.raises(yt.NoKeyAvailable):
        api.get("videos", {"part": "id"})


def test_youtube_api_error_message_never_contains_the_key():
    def boom(request):
        raise httpx.ConnectError(f"fail {request.url}")
    with pytest.raises(yt.YouTubeApiError) as exc:
        _api(boom).get("videos", {"part": "id"})
    assert KEY_A not in str(exc.value)


def test_youtube_video_stats_maps_fields_and_drops_missing_videos():
    body = {"items": [{"id": "v1", "snippet": {"title": "T", "channelTitle": "C", "publishedAt": "2026-10-16T17:30:00Z"},
                       "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "2"}, "contentDetails": {"duration": "PT1M5S"}}]}
    api = _api(lambda r: httpx.Response(200, json=body))
    out = api.video_stats(["v1", "gone"])
    assert list(out) == ["v1"]
    assert (out["v1"]["views"], out["v1"]["likes"], out["v1"]["comments"], out["v1"]["duration_seconds"]) == (100, 5, 2, 65)
    assert system_api.to_report_date(out["v1"]["published_at"]) == "2026-10-17"   # 17:30Z = 00:30 giờ VN hôm sau


def test_youtube_hidden_subscriber_count_is_not_reported():
    body = {"items": [{"id": "UC1", "snippet": {"title": "t"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                       "statistics": {"subscriberCount": "0", "hiddenSubscriberCount": True, "videoCount": "9"}}]}
    ch = _api(lambda r: httpx.Response(200, json=body)).resolve_channel("@x")
    assert ch["followers"] is None and ch["videos_posted"] == "9"


# ── YouTube: yt-dlp (không key) ──────────────────────────────────────────────────────────

def test_ytdlp_video_converts_upload_date_to_a_date_not_an_epoch():
    info = {"id": "v", "view_count": 9, "like_count": 1, "comment_count": 0, "upload_date": "20261017", "uploader_id": "@chan", "title": "t", "duration": 30}
    r = yt.check_video_ytdlp("https://youtu.be/v", extract=lambda url, opts: info)
    assert r["ok"] and r["published_at"] == "2026-10-17" and r["username"] == "chan"
    assert system_api.to_report_date(r["published_at"]) == "2026-10-17"


def test_ytdlp_video_failure_keeps_last_error_line():
    def boom(url, opts):
        raise RuntimeError("noise\nERROR: [youtube] x: Video unavailable")
    assert yt.check_video_ytdlp("u", extract=boom) == {"ok": False, "error": "[yt] ERROR: [youtube] x: Video unavailable"}


def test_ytdlp_channel_listing_points_at_the_videos_tab_and_dedupes():
    seen: list[str] = []

    def extract(url, opts):
        seen.append(url)
        return {"entries": [{"id": "a"}, {"id": "a"}, None, {"id": "b"}]}
    assert yt.list_channel_ytdlp("https://www.youtube.com/@x/", 5, extract=extract) == ["a", "b"]
    assert seen == ["https://www.youtube.com/@x/videos"]


# ── Facebook ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,ok", [("https://www.facebook.com/reel/123", True), ("https://www.facebook.com/user/posts/pfbid0abc", True),
                                    ("https://fb.watch/abc/", True), ("https://www.facebook.com/somepage", False), ("", False)])
def test_facebook_is_content_url(url, ok):
    assert fb.is_content_url(url) is ok


def test_facebook_subtype_and_share_links():
    assert fb.detect_subtype("https://www.facebook.com/reel/1") == "reel"
    assert fb.detect_subtype("https://www.facebook.com/u/posts/2") != "reel"
    assert fb.is_share_link("https://www.facebook.com/share/r/abc") and not fb.is_share_link("https://www.facebook.com/reel/1")


def test_facebook_without_cookie_fails_fast_with_a_reason():
    r = fb.check_url("https://www.facebook.com/reel/1", "")
    assert r["ok"] is False and "cookie" in r["error"].lower()
