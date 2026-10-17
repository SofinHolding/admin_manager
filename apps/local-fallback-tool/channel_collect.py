"""
channel_collect.py — thu thập số liệu MỘT kênh theo nền tảng (đồng bộ, chạy trong luồng riêng; `collect.process_job` gọi qua `asyncio.to_thread`).

  youtube  : YouTube Data API (xoay nhiều key; hết quota => key kế) — không có key thì yt-dlp (qua proxy nếu có).
  tiktok   : liệt kê video bằng yt-dlp (`tiktok_links`, thử lại qua proxy) + số liệu từng video qua tikwm.
  facebook : chỉ link bài/reel (cookie Facebook) — trang/profile không liệt kê được.

Danh sách video (YouTube/TikTok) lưu ở DB cục bộ: LẦN ĐẦU của một kênh lấy ĐỦ danh sách; các lần sau chỉ lấy video MỚI rồi đo số liệu
trên toàn bộ danh sách đã lưu (`_sync_list`, `channel_listing.refresh`). Kênh không nằm trong DB cục bộ thì chỉ đo N video mới nhất.
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import channel_listing
import channels_db
import tiktok_links
from checkers import facebook as fb_checker
from checkers import tiktok as tt_checker
from checkers import youtube as yt_checker
from pools import Pool, load_pools

log = logging.getLogger("collect")
DEFAULT_SETTINGS = Path(channels_db.ROOT_DIR) / ".dev" / "collector-config.json"
YOUTUBE_RECENT = 30       # video mới nhất mỗi lần khi KHÔNG có kho danh sách (YouTube Data API trả thống kê theo lô 50 => rẻ)
TIKTOK_RECENT = 15        # như trên (tikwm ~1 req/s/IP)
YTDLP_RECENT = 10         # như trên, yt-dlp (từng video một, chậm, dễ bị chặn) => ít hơn
YTDLP_CHECK_MAX = 100     # yt-dlp (không API key) chỉ đo tối đa ngần này video mới nhất mỗi lần dù danh sách đã lưu dài hơn
TIKTOK_WORKERS = 12       # luồng đo song song tối đa một kênh (còn bị giới hạn bởi số proxy)
YTDLP_WORKERS = 4
WORKERS_PER_PROXY = 2     # mỗi lần gọi qua proxy mất vài giây còn tikwm chỉ giới hạn ~1 req/s/IP => 2 luồng/proxy vẫn chưa chạm giới hạn
PROXY_ATTEMPTS = 3        # số proxy khác nhau thử cho một lần gọi trước khi báo lỗi
# 403/429 từ tikwm/Facebook = IP của proxy đó bị chặn (đo thật: cùng video qua 8 proxy khác đều đo được) => đổi proxy
_PROXY_FAULT = re.compile(r"proxy|timeout|network|connect|ssl|tunnel|socks|HTTP 403|HTTP 429", re.I)


class JobAbort(Exception):
    """Job không thể hoàn tất vì lý do nghiệp vụ (không phải lỗi hạ tầng): báo `fail` kèm lý do; `retryable=False` = đừng thử lại."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmptyChannel(Exception):
    """Kênh có thật nhưng CHƯA CÓ video: không phải lỗi. `process_job` hoàn tất job với 0 mục (không đẩy gì, không báo lỗi)."""


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


# ── Danh sách video đã lưu (YouTube / TikTok) + đo song song ──────────────────────────────

def _sync_list(channel_id: str | None, fetch: Callable[[int], list[dict]], *, full_max: int, recent: int) -> list[dict]:
    """Danh sách video cần đo của một kênh (mới nhất trước).

    - `channel_id` không có / kênh không nằm trong DB cục bộ: không lưu gì, chỉ lấy `recent` video mới nhất (cách cũ).
    - Có kho: lần đầu lấy ĐỦ danh sách rồi lưu; các lần sau chỉ lấy phần MỚI (`channel_listing.refresh`) rồi gộp. Trả TOÀN BỘ kho,
      nên lần cập nhật view sau đo thẳng trên danh sách có sẵn, không phải liệt kê lại cả kênh.
    - Lấy phần mới lỗi mà kho đã có video: đo tiếp trên kho, video mới được bắt kịp ở lần sau (số liệu quan trọng hơn danh sách).
    """
    state = channels_db.video_state(channel_id or "")
    if state is None:
        return fetch(recent)[:recent]
    known = {v["video_id"] for v in state["videos"]}
    try:
        fresh, mode = channel_listing.refresh(known, state["listed"], fetch, full_max=full_max)
    except Exception as exc:  # noqa: BLE001
        if not (state["listed"] and known):
            raise
        log.warning("Không cập nhật được danh sách video (%s) — đo tiếp trên %d video đã lưu, video mới sẽ lấy ở lần sau", str(exc)[:150], len(known))
        return state["videos"]
    saved = channels_db.store_videos(channel_id or "", fresh, complete=mode == "full")
    log.info("Danh sách video: %s — %d video mới, tổng %d", "lấy đủ" if mode == "full" else "chỉ lấy phần mới", saved["added"], saved["total"])
    in_fresh = {v["video_id"] for v in fresh}
    return fresh + [v for v in state["videos"] if v["video_id"] not in in_fresh]


def _measure(creds: Credentials, videos: list[dict], check: Callable[[dict, str | None], dict], *, workers: int) -> list[dict]:
    """Đo từng video qua proxy xoay vòng. Nhiều proxy => chạy song song (tikwm giới hạn ~1 req/s MỖI IP nên mỗi luồng một proxy); không có
    proxy => tuần tự. Kết quả giữ thứ tự `videos`."""
    def one(v: dict) -> dict:
        return to_item(_with_proxy(creds, lambda p: check(v, p)), v["video_id"])

    n = max(1, min(workers, WORKERS_PER_PROXY * len(creds.proxies) or 1, len(videos)))
    if n == 1:
        items = [one(v) for v in videos]
    else:
        with ThreadPoolExecutor(max_workers=n) as pool:
            items = list(pool.map(one, videos))
    log.info("Đã đo %d/%d video (%d luồng)", sum(1 for i in items if i["check_status"] == "ok"), len(items), n)
    return items


# ── YouTube ──────────────────────────────────────────────────────────────────────────────

def collect_youtube(input_ref: str, creds: Credentials, channel_id: str | None = None) -> tuple[list[dict], dict]:
    if not creds.youtube_keys:
        return _collect_youtube_ytdlp(input_ref, creds, channel_id)
    api = yt_checker.YouTubeApi(creds.key_pool)
    try:
        try:
            channel = api.resolve_channel(input_ref)
        except ValueError as exc:
            raise JobAbort(str(exc), retryable=False) from exc
        uploads = channel["uploads"]
        videos = _sync_list(channel_id, lambda n: [channel_listing.youtube_video(i) for i in api.recent_video_ids(uploads, n)] if uploads else [],
                            full_max=channel_listing.YOUTUBE_MAX, recent=YOUTUBE_RECENT)
        ids = [v["video_id"] for v in videos]
        stats = api.video_stats(ids) if ids else {}   # theo lô 50 => 1 đơn vị quota / 50 video
    except yt_checker.NoKeyAvailable as exc:
        raise JobAbort(str(exc)) from exc
    except yt_checker.YouTubeApiError as exc:
        raise JobAbort(str(exc)) from exc
    finally:
        api.close()
    items = [to_item(stats[vid], vid) for vid in ids if vid in stats]
    channel_stats = {k: int(channel[k]) for k in ("followers", "videos_posted") if str(channel.get(k) or "").isdigit()}
    return items, channel_stats


def _collect_youtube_ytdlp(input_ref: str, creds: Credentials, channel_id: str | None) -> tuple[list[dict], dict]:
    """Không có API key: yt-dlp liệt kê + đo từng video (không có chỉ số kênh). Chậm nên chỉ đo `YTDLP_CHECK_MAX` video mới nhất."""
    try:
        channel_url = channel_listing.youtube_channel_url(input_ref)
    except ValueError as exc:
        raise JobAbort(str(exc), retryable=False) from exc

    def fetch(n: int) -> list[dict]:
        return [channel_listing.youtube_video(i) for i in _with_listing(creds, lambda p: yt_checker.list_channel_ytdlp(channel_url, n, p))]

    try:
        videos = _sync_list(channel_id, fetch, full_max=channel_listing.YOUTUBE_MAX, recent=YTDLP_RECENT)
    except Exception as exc:  # noqa: BLE001
        if "does not have a videos tab" in str(exc):
            raise EmptyChannel("Kênh YouTube chưa có video công khai (không có tab Videos)") from exc
        raise JobAbort(f"yt-dlp không liệt kê được video: {str(exc)[:200]} (thêm YouTube API key ở Cấu hình để ổn định hơn)") from exc
    items = _measure(creds, videos[:YTDLP_CHECK_MAX], lambda v, p: yt_checker.check_video_ytdlp(v["url"], p), workers=YTDLP_WORKERS)
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

def collect_tiktok(input_ref: str, creds: Credentials, channel_id: str | None = None) -> tuple[list[dict], dict]:
    try:
        videos = _sync_list(channel_id, lambda n: channel_listing.list_tiktok(input_ref, creds, n)["videos"],
                            full_max=tiktok_links.FULL_MAX_ITEMS, recent=TIKTOK_RECENT)
    except channel_listing.ListingError as exc:
        raise JobAbort(str(exc), retryable=exc.retryable) from exc
    if not videos:
        raise EmptyChannel("Kênh TikTok chưa có video")   # fetch_links chỉ trả rỗng khi TikTok xác nhận kênh chưa có video; còn lại nó ném lỗi
    return _measure(creds, videos, lambda v, p: tt_checker.check_video(v["url"], p), workers=TIKTOK_WORKERS), {}


# ── Facebook ─────────────────────────────────────────────────────────────────────────────

def collect_facebook(input_ref: str, creds: Credentials, channel_id: str | None = None) -> tuple[list[dict], dict]:
    if not fb_checker.is_content_url(input_ref):
        raise JobAbort("Facebook: chỉ kiểm được link MỘT bài/reel/video (trang hoặc profile không liệt kê được)", retryable=False)
    if not creds.fb_cookie.strip():
        raise JobAbort("Facebook: chưa nhập cookie ở Cấu hình", retryable=False)
    result = _with_proxy(creds, lambda p: fb_checker.check_url(input_ref, creds.fb_cookie, p))
    return [to_item(result, str(result.get("url") or input_ref))], {}


COLLECTORS: dict[str, Callable[[str, Credentials, str | None], tuple[list[dict], dict]]] = {
    "youtube": collect_youtube, "tiktok": collect_tiktok, "facebook": collect_facebook,
}


def collect_channel(platform: str, input_ref: str, creds: Credentials, channel_id: str | None = None) -> tuple[list[dict], dict]:
    """`channel_id` = id kênh trong DB cục bộ (resource_id của job): có thì dùng/lưu danh sách video của kênh (YouTube/TikTok)."""
    fn = COLLECTORS.get(platform)
    if fn is None:
        raise JobAbort(f"Unsupported platform: {platform}", retryable=False)
    return fn(input_ref, creds, channel_id)
