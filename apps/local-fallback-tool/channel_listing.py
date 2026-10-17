"""
Danh sách video của kênh YouTube / TikTok: lấy ĐỦ một lần (lần đầu kênh được chạy), các lần sau chỉ lấy phần MỚI rồi gộp vào danh sách đã lưu.

Thuần logic + gọi nền tảng (không đụng DB) — lưu/đọc danh sách nằm ở `channels_db.video_state/store_videos`, ghép lại trong `channel_collect`.
Dùng chung cho collector (job định kỳ / "Chạy ngay") và nút "Lấy toàn bộ link video" (`video_links.py`).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import tiktok_links
from checkers import youtube as yt_checker

if TYPE_CHECKING:
    from channel_collect import Credentials

PAGE = 30                 # số video mới nhất xin ở các lần cập nhật (đủ cho cả kênh đăng nhiều video/ngày giữa hai lần chạy)
YOUTUBE_MAX = 5000        # trần số video một kênh YouTube (200 đơn vị quota)


class ListingError(Exception):
    """Lỗi thân thiện, hiển thị thẳng cho người dùng. `retryable=False`: thử lại không giải quyết được (vd link kênh sai)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def youtube_channel_url(ref: str) -> str:
    """URL kênh chuẩn cho yt-dlp từ link / `@handle` / `UC…`; `ValueError` nếu không đọc được."""
    query = yt_checker.channel_query(ref)
    if "id" in query:
        return f"https://www.youtube.com/channel/{query['id']}"
    return f"https://www.youtube.com/@{query.get('forHandle', '').lstrip('@') or query.get('forUsername', '')}"


def youtube_video(video_id: str, stat: dict | None = None) -> dict:
    stat = stat or {}
    return {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "title": str(stat.get("title") or ""),
            "published_at": str(stat.get("published_at") or ""), "views": stat.get("views")}


def list_youtube(ref: str, creds: Credentials, limit: int = YOUTUBE_MAX) -> dict:
    """`limit` video mới nhất của kênh YouTube: `{videos, via, attempts, kind}` hoặc ném `ListingError`."""
    if creds.youtube_keys:
        api = yt_checker.YouTubeApi(creds.key_pool)
        try:
            channel = api.resolve_channel(ref)
            ids = api.recent_video_ids(channel["uploads"], limit) if channel["uploads"] else []
            stats = api.video_stats(ids) if ids else {}
        except ValueError as exc:
            raise ListingError(str(exc), retryable=False) from exc
        except (yt_checker.NoKeyAvailable, yt_checker.YouTubeApiError) as exc:
            raise ListingError(str(exc)) from exc
        finally:
            api.close()
        videos = [youtube_video(i, stats.get(i)) for i in ids]
        return {"videos": videos, "via": "YouTube Data API", "attempts": 1, "kind": "ok" if videos else "empty"}
    proxy = creds.proxy_pool.pick() if creds.proxies else None
    try:
        ids = yt_checker.list_channel_ytdlp(youtube_channel_url(ref), limit, proxy)
    except ValueError as exc:
        raise ListingError(str(exc), retryable=False) from exc
    except Exception as exc:  # noqa: BLE001
        if proxy:
            creds.proxy_pool.bad(proxy)
        if "does not have a videos tab" in str(exc):
            return {"videos": [], "via": "yt-dlp", "attempts": 1, "kind": "empty"}
        raise ListingError(f"yt-dlp không liệt kê được video: {str(exc)[:200]} (thêm YouTube API key ở Cấu hình để ổn định hơn)") from exc
    videos = [youtube_video(i) for i in ids]
    return {"videos": videos, "via": "yt-dlp", "attempts": 1, "kind": "ok" if videos else "empty"}


def list_tiktok(ref: str, creds: Credentials, limit: int = tiktok_links.FULL_MAX_ITEMS) -> dict:
    try:
        return tiktok_links.fetch_links(ref, proxies=creds.proxies, limit=limit)
    except tiktok_links.TikTokError as exc:
        raise ListingError(str(exc)) from exc


def refresh(known: set[str], listed: bool, fetch: Callable[[int], list[dict]], *, full_max: int, page: int = PAGE) -> tuple[list[dict], str]:
    """Lấy danh sách video MỚI cần gộp vào kho: `(videos mới nhất trước, mode)`.

    - Chưa từng lấy đủ (`listed=False`): lấy ĐỦ (`fetch(full_max)`) — mode `"full"`.
    - Đã lấy đủ: chỉ xin `page` video mới nhất — mode `"incremental"`. Trang này nối liền với danh sách đã lưu khi video CŨ NHẤT của trang đã có trong kho
      (hoặc trang chưa đầy ⇒ đã hết kênh); nếu không, giữa hai lần chạy kênh đăng nhiều hơn một trang nên có thể sót ⇒ lấy đủ lại — mode `"full"`.
      (Video ghim của TikTok nằm đầu trang nhưng không ảnh hưởng: chỉ xét video cuối trang.)
    """
    if not listed:
        return fetch(full_max), "full"
    recent = fetch(page)
    if len(recent) < page or recent[-1]["video_id"] in known:
        return recent, "incremental"
    return fetch(full_max), "full"
