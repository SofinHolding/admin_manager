"""
channel_collect.py — thu thập số liệu MỘT kênh theo nền tảng (đồng bộ, chạy trong luồng riêng; `collect.process_job` gọi qua `asyncio.to_thread`).

  youtube  : YouTube Data API (xoay nhiều key; hết quota => key kế) — không có key thì yt-dlp (qua proxy nếu có).
  tiktok   : liệt kê video mới nhất bằng cookie phiên của Chrome/Edge cục bộ (`tiktok_links`) + số liệu từng video qua tikwm.
  facebook : chỉ link bài/reel (cookie Facebook) — trang/profile không liệt kê được.

Kết quả: `(items, channel_stats)` đúng hình dạng `SystemApi.push_snapshot` nhận. Không có số liệu hợp lệ => `JobAbort` kèm lý do thật.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import channels_db
import tiktok_links
from checkers import facebook as fb_checker
from checkers import tiktok as tt_checker
from checkers import youtube as yt_checker
from pools import Pool, load_pools

log = logging.getLogger("collect")
DEFAULT_SETTINGS = Path(channels_db.ROOT_DIR) / ".dev" / "collector-config.json"
YOUTUBE_RECENT = 30       # video mới nhất mỗi lần (YouTube Data API trả thống kê theo lô 50 => rẻ)
TIKTOK_RECENT = 15        # tikwm ~1 req/s/IP => 15 video ~ 17 giây/kênh
YTDLP_RECENT = 10         # yt-dlp lấy từng video một (chậm, dễ bị chặn) => ít hơn
PROXY_ATTEMPTS = 3        # số proxy khác nhau thử cho một lần gọi trước khi báo lỗi
# 403/429 từ tikwm/Facebook = IP của proxy đó bị chặn (đo thật: cùng video qua 8 proxy khác đều đo được) => đổi proxy
_PROXY_FAULT = re.compile(r"proxy|timeout|network|connect|ssl|tunnel|socks|HTTP 403|HTTP 429", re.I)


class JobAbort(Exception):
    """Job không thể hoàn tất vì lý do nghiệp vụ (không phải lỗi hạ tầng): báo `fail` kèm lý do; `retryable=False` = đừng thử lại."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Credentials:
    youtube_keys: list[str] = field(default_factory=list)
    proxies: list[str] = field(default_factory=list)
    fb_cookie: str = ""

    def __post_init__(self) -> None:
        self.key_pool = Pool(self.youtube_keys, cooldown_s=yt_checker.QUOTA_COOLDOWN_S)
        self.proxy_pool = Pool(self.proxies, cooldown_s=300.0)


def load_credentials(settings_path: str | Path = DEFAULT_SETTINGS) -> Credentials:
    """Khoá YouTube + proxy (nhiều mục, xem `pools.load_pools`) và cookie Facebook — từ file cấu hình của tool;
    biến môi trường `FB_COOKIE` chỉ dùng khi file không có."""
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    keys, proxies = load_pools(str(settings_path))
    return Credentials(youtube_keys=keys, proxies=proxies,
                       fb_cookie=str(data.get("fb_cookie") or os.environ.get("FB_COOKIE", "")))


def to_item(result: dict, external_id: str) -> dict:
    """Kết quả của một bộ kiểm tra -> phần tử `items` của push_snapshot."""
    item: dict[str, Any] = {"external_id": external_id, "check_status": "ok" if result.get("ok") else "error"}
    for key in ("title", "published_at", "url", "views", "likes", "comments", "shares", "duration_seconds", "cover_url", "region"):
        if result.get(key) not in (None, ""):
            item[key] = result[key]
    return item


def _with_proxy(creds: Credentials, call: Callable[[str | None], dict]) -> dict:
    """Gọi `call(proxy)` với proxy khoẻ kế tiếp; lỗi do proxy/mạng thì đánh dấu proxy nghỉ rồi thử proxy khác. Không có proxy => đi thẳng."""
    if not creds.proxies:
        return call(None)
    result: dict = {"ok": False, "error": "Mọi proxy đều đang nghỉ (vừa lỗi) — thử lại sau vài phút hoặc kiểm tra lại proxy ở Cấu hình"}
    for _ in range(min(PROXY_ATTEMPTS, len(creds.proxies))):
        proxy = creds.proxy_pool.pick()
        if proxy is None:
            break
        result = call(proxy)
        if result.get("ok") or not _PROXY_FAULT.search(str(result.get("error") or "")):
            creds.proxy_pool.ok(proxy)
            return result
        creds.proxy_pool.bad(proxy)
    return result


# ── YouTube ──────────────────────────────────────────────────────────────────────────────

def collect_youtube(input_ref: str, creds: Credentials) -> tuple[list[dict], dict]:
    if not creds.youtube_keys:
        return _collect_youtube_ytdlp(input_ref, creds)
    api = yt_checker.YouTubeApi(creds.key_pool)
    try:
        try:
            channel = api.resolve_channel(input_ref)
        except ValueError as exc:
            raise JobAbort(str(exc), retryable=False) from exc
        ids = api.recent_video_ids(channel["uploads"], YOUTUBE_RECENT) if channel["uploads"] else []
        stats = api.video_stats(ids) if ids else {}
    except yt_checker.NoKeyAvailable as exc:
        raise JobAbort(str(exc)) from exc
    except yt_checker.YouTubeApiError as exc:
        raise JobAbort(str(exc)) from exc
    finally:
        api.close()
    items = [to_item(stats[vid], vid) for vid in ids if vid in stats]
    channel_stats = {k: int(channel[k]) for k in ("followers", "videos_posted") if str(channel.get(k) or "").isdigit()}
    return items, channel_stats


def _collect_youtube_ytdlp(input_ref: str, creds: Credentials) -> tuple[list[dict], dict]:
    """Không có API key: yt-dlp liệt kê + lấy từng video (không có chỉ số kênh)."""
    try:
        query = yt_checker.channel_query(input_ref)
    except ValueError as exc:
        raise JobAbort(str(exc), retryable=False) from exc
    channel_url = f"https://www.youtube.com/channel/{query['id']}" if "id" in query else \
        f"https://www.youtube.com/@{query.get('forHandle', '').lstrip('@') or query.get('forUsername', '')}"
    try:
        ids = list(_with_listing(creds, lambda p: yt_checker.list_channel_ytdlp(channel_url, YTDLP_RECENT, p)))
    except Exception as exc:  # noqa: BLE001
        if "does not have a videos tab" in str(exc):
            raise JobAbort("Kênh YouTube chưa có video công khai (không có tab Videos)", retryable=False) from exc
        raise JobAbort(f"yt-dlp không liệt kê được video: {str(exc)[:200]} (thêm YouTube API key ở Cấu hình để ổn định hơn)") from exc
    items = []
    for vid in ids:
        url = f"https://www.youtube.com/watch?v={vid}"
        items.append(to_item(_with_proxy(creds, lambda p, u=url: yt_checker.check_video_ytdlp(u, p)), vid))
    return items, {}


def _with_listing(creds: Credentials, call: Callable[[str | None], list[str]]) -> list[str]:
    proxy = creds.proxy_pool.pick() if creds.proxies else None
    try:
        return call(proxy)
    except Exception:
        if proxy:
            creds.proxy_pool.bad(proxy)
        raise


# ── TikTok ───────────────────────────────────────────────────────────────────────────────

def collect_tiktok(input_ref: str, creds: Credentials) -> tuple[list[dict], dict]:
    try:
        listing = tiktok_links.fetch_links(input_ref, proxies=creds.proxies, limit=TIKTOK_RECENT)
    except tiktok_links.TikTokError as exc:
        raise JobAbort(str(exc)) from exc
    videos = listing["videos"][:TIKTOK_RECENT]
    if not videos:
        if listing["kind"] == "empty":
            return [], {}                       # kênh chưa có video: không phải lỗi, nhưng cũng không có gì để đẩy
        raise JobAbort("TikTok không trả video nào cho kênh này")
    items = []
    for v in videos:
        result = _with_proxy(creds, lambda p, u=v["url"]: tt_checker.check_video(u, p))
        items.append(to_item(result, v["video_id"]))
    return items, {}


# ── Facebook ─────────────────────────────────────────────────────────────────────────────

def collect_facebook(input_ref: str, creds: Credentials) -> tuple[list[dict], dict]:
    if not fb_checker.is_content_url(input_ref):
        raise JobAbort("Facebook: chỉ kiểm được link MỘT bài/reel/video (trang hoặc profile không liệt kê được)", retryable=False)
    if not creds.fb_cookie.strip():
        raise JobAbort("Facebook: chưa nhập cookie ở Cấu hình", retryable=False)
    result = _with_proxy(creds, lambda p: fb_checker.check_url(input_ref, creds.fb_cookie, p))
    return [to_item(result, str(result.get("url") or input_ref))], {}


COLLECTORS: dict[str, Callable[[str, Credentials], tuple[list[dict], dict]]] = {
    "youtube": collect_youtube, "tiktok": collect_tiktok, "facebook": collect_facebook,
}


def collect_channel(platform: str, input_ref: str, creds: Credentials) -> tuple[list[dict], dict]:
    fn = COLLECTORS.get(platform)
    if fn is None:
        raise JobAbort(f"Unsupported platform: {platform}", retryable=False)
    return fn(input_ref, creds)
