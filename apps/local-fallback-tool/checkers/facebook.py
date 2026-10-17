"""Kiểm tra MỘT link bài / reel / video Facebook bằng cookie — port rút gọn từ CheckviewV2 `check_facebook.py`.

Dùng scraper nguyên văn ở `facebook_scraper/` (HTTP, curl_cffi nếu có). Thêm so với scraper: nhận biết link chia sẻ (`/share/`, `fb.watch`),
tự phân loại reel/post sau khi resolve, và chuẩn hoá kết quả. KHÔNG làm bước tra tên hiển thị bằng cookie khác (bản gốc xoay cả pool cookie
cho việc chỉ để làm đẹp thông báo — dễ khiến Facebook checkpoint cookie).

Chỉ kiểm LINK BÀI/REEL. Trang/profile Facebook không liệt kê được bài ở đây (cần trình duyệt) — `is_content_url()` để nơi gọi phân biệt.
"""
from __future__ import annotations

import asyncio
import os
import re
import threading

import httpx

from checkers.common import fail, stat
from checkers.facebook_scraper.core.exceptions.http import BadRequestException
from checkers.facebook_scraper.fb_scraper import FBScraperService
from pools import force_remote_dns

try:
    from curl_cffi.requests import AsyncSession as _CffiAsyncSession  # type: ignore
    _HAS_CURL_CFFI = True
except Exception:  # noqa: BLE001
    _HAS_CURL_CFFI = False

# Scraper đọc cookie từ biến môi trường FB_COOKIES (toàn tiến trình) — khoá để hai lần kiểm song song không đè cookie của nhau.
_ENV_LOCK = threading.Lock()
_FB_LOGIN_MARKERS = ("/login", "/checkpoint", "/recover", "two_step_verification")
_CONTENT_MARKERS = ("/reel/", "/reels/", "/posts/", "/videos/", "/share/", "/watch", "fb.watch/", "story.php", "permalink.php", "/photo")
_PFBID_RE = re.compile(r"^pfbid[A-Za-z0-9]{10,}$", re.I)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


def validate_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return "URL trống"
    if url.count("://") > 1:
        return f"URL hỏng (nhiều scheme): {url[:80]}"
    if not url.lower().startswith(("http://", "https://")):
        return "URL không bắt đầu bằng http(s)://"
    return None


def is_content_url(url: str) -> bool:
    """True nếu link trỏ vào MỘT bài/reel/video (kiểm được); False với trang/profile."""
    low = (url or "").lower()
    return any(m in low for m in _CONTENT_MARKERS)


def detect_subtype(url: str) -> str:
    low = url.lower()
    if "/reel/" in low or "watch/?v=" in low or "/share/v/" in low or "/share/r/" in low:
        return "reel"
    if re.search(r"/videos/(\d+)", low) or re.search(r"/videos/[^/]+/(\d+)", low):
        return "reel"
    return "post"


def is_share_link(url: str) -> bool:
    low = url.lower()
    return "/share/" in low or "fb.watch/" in low


def _cookie_dict(cookie: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in cookie.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


async def resolve_share_url(url: str, cookie: str, proxy: str | None = None) -> str:
    """Resolve `/share/` / `fb.watch/` -> URL reel/video thật. Không resolve được (chết / login-wall / checkpoint) => trả URL gốc
    để phía sau báo "không khả dụng", KHÔNG bịa id."""
    if not is_share_link(url):
        return url
    cookies, headers = _cookie_dict(cookie), {"user-agent": _UA, "accept-language": "vi,en-US;q=0.9,en;q=0.8",
                                              "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    p = force_remote_dns(proxy)
    try:
        if _HAS_CURL_CFFI:
            async with _CffiAsyncSession(impersonate="chrome146") as session:
                response = await session.get(url, headers=headers, cookies=cookies,
                                             proxies={"http": p, "https": p} if p else None, allow_redirects=True, timeout=15)
        else:
            async with httpx.AsyncClient(http2=True, follow_redirects=True, max_redirects=20, timeout=15, cookies=cookies, proxy=p) as client:
                response = await client.get(url, headers=headers)
    except Exception:  # noqa: BLE001
        return url
    resolved = str(response.url)
    low = resolved.lower()
    if any(m in low for m in _FB_LOGIN_MARKERS) or low.rstrip("/") in ("https://www.facebook.com", "http://www.facebook.com"):
        return url
    if not is_share_link(low):
        return resolved
    for pattern in (r'"video_id":"(\d+)"', r'"videoID":"(\d+)"', r'"reel_id":"(\d+)"', r"/reel/(\d+)", r'"story_fbid":"(\d+)"'):
        m = re.search(pattern, response.text)
        if m:
            return f"https://www.facebook.com/reel/{m.group(1)}"
    return url


def _owner(result: dict) -> str:
    user_id = str(result.get("user_id") or "")
    if user_id and user_id != "0" and not _PFBID_RE.match(user_id):
        return user_id                       # id số của chủ bài: ổn định kể cả khi đổi tên vanity
    return str(result.get("username") or "")


async def _run(url: str, cookie: str, proxy: str | None, sub_type: str | None) -> dict:
    invalid = validate_url(url)
    if invalid:
        return fail(f"[fb] {invalid}")
    if not cookie.strip():
        return fail("[fb] cookie rỗng — nhập cookie Facebook ở Cài đặt")
    resolved = await resolve_share_url(url, cookie, proxy)
    kind = sub_type or detect_subtype(resolved)
    try:
        with _ENV_LOCK:
            os.environ["FB_COOKIES"] = cookie
            if kind == "reel":
                result = await FBScraperService.get_reel_by_url(resolved, proxy=proxy)
            else:
                result = await FBScraperService.get_post_by_url(resolved, proxy=proxy)
    except BadRequestException as exc:
        return fail(f"[fb] {exc.detail}")
    except Exception as exc:  # noqa: BLE001
        return fail(f"[fb] {type(exc).__name__}: {exc}")
    if kind == "reel":
        views, likes = result.get("plays"), result.get("likes")
    else:                                    # bài viết không có lượt xem: likes = tổng reactions
        reactions = result.get("reactions") or {}
        views, likes = 0, (sum(reactions.values()) if reactions else result.get("likes"))
    return stat(username=_owner(result), views=views, likes=likes, comments=result.get("comments"), shares=result.get("shares"),
                published_at=result.get("timestamp"), title=str(result.get("username") or ""), url=resolved)


def check_url(url: str, cookie: str, proxy: str | None = None, sub_type: str | None = None) -> dict:
    """Kiểm một link bài/reel (đồng bộ — gọi trong luồng riêng). Không ném lỗi."""
    return asyncio.run(_run(url, cookie, proxy, sub_type))
