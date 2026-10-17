"""Kiểm tra MỘT video TikTok qua tikwm.com (REST công khai, không cần cookie) — port từ CheckviewV2 `check_tiktok.py`.

Lấy: lượt xem / thích / bình luận / chia sẻ, ngày đăng (`create_time`, epoch giây — mốc tuyệt đối), quốc gia (`region`), thời lượng, tiêu đề.
Giới hạn của tikwm bản miễn phí ~1 request/giây: `_throttle()` giữ khoảng cách tối thiểu giữa hai lần gọi trong cùng tiến trình.
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from checkers.common import fail, stat
from pools import force_remote_dns

TIKWM_ENDPOINT = "https://www.tikwm.com/api/"
TIMEOUT_S = 15.0
MIN_INTERVAL_S = 1.1
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.tikwm.com",
    "Referer": "https://www.tikwm.com/",
}
_VIDEO_ID_RE = re.compile(r"/video/(\d+)")
_USER_RE = re.compile(r"tiktok\.com/@([^/?#]+)", re.I)

_lock = threading.Lock()
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def username_from_url(url: str) -> str:
    m = _USER_RE.search(url or "")
    return m.group(1) if m else ""


def parse_tikwm(payload: dict, url: str) -> dict:
    """Phản hồi tikwm `{code, msg, data}` -> kết quả chuẩn (hoặc lỗi kèm lý do của tikwm)."""
    if payload.get("code") != 0:
        return fail(f"[tikwm] {payload.get('msg') or 'tikwm error'}")
    d = payload.get("data") or {}
    if not d:
        return fail("[tikwm] empty data")
    author = d.get("author") or {}
    return stat(
        username=author.get("unique_id") or author.get("nickname") or username_from_url(url),
        views=d.get("play_count"), likes=d.get("digg_count"), comments=d.get("comment_count"), shares=d.get("share_count"),
        published_at=d.get("create_time"), duration_seconds=d.get("duration"), region=d.get("region"),
        title=str(d.get("title") or ""), cover_url=str(d.get("cover") or ""), video_id=str(d.get("id") or ""), url=url,
    )


def _check_once(url: str, proxy: str | None = None, *, client: httpx.Client | None = None) -> dict:
    """Không ném lỗi: mạng / proxy / HTTP đều thành `{"ok": False, "error": "[tikwm] ..."}`."""
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S, headers=_HEADERS, follow_redirects=True, proxy=force_remote_dns(proxy))
    try:
        _throttle()
        r = http.post(TIKWM_ENDPOINT, data={"url": url, "hd": "0"})
        r.raise_for_status()
        return parse_tikwm(r.json(), url)
    except httpx.HTTPStatusError as exc:
        return fail(f"[tikwm] HTTP {exc.response.status_code}")
    except httpx.ProxyError:
        return fail("[tikwm] Proxy error")
    except httpx.TimeoutException:
        return fail(f"[tikwm] Timeout sau {TIMEOUT_S:.0f}s")
    except httpx.RequestError as exc:
        return fail(f"[tikwm] Network: {type(exc).__name__}")
    except ValueError:
        return fail("[tikwm] Invalid JSON")
    finally:
        if own:
            http.close()


_TRANSIENT = re.compile(r"limit|too many|request/second|HTTP 5\d\d|Timeout|Network|Invalid JSON|Proxy error", re.I)


def check_video(url: str, proxy: str | None = None, *, client: httpx.Client | None = None, retries: int = 2) -> dict:
    """`_check_once` + thử lại các lỗi tạm thời của tikwm (giới hạn tốc độ, 5xx, hết thời gian, proxy): đo thật cho thấy 1–2/15 video lỗi
    chập chờn rồi lần sau đo được ngay. Lỗi thật của video (link sai, video bị xoá) không thử lại."""
    result = _check_once(url, proxy, client=client)
    for n in range(1, retries + 1):
        if result.get("ok") or not _TRANSIENT.search(str(result.get("error") or "")):
            break
        time.sleep(1.5 * n)
        result = _check_once(url, proxy, client=client)
    return result
