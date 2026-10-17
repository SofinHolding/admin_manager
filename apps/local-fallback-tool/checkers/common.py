"""Hình dạng kết quả chung của các bộ kiểm tra + tiện ích nhỏ."""
from __future__ import annotations

from typing import Any


def stat(*, username: str = "", views: Any = 0, likes: Any = 0, comments: Any = 0, shares: Any = 0,
         published_at: Any = None, duration_seconds: Any = None, region: str | None = None, title: str = "",
         cover_url: str = "", video_id: str = "", url: str = "") -> dict:
    """Kết quả THÀNH CÔNG của một link. `published_at`: epoch giây / ISO — `system_api.to_report_date` quy đổi sang ngày giờ VN.
    Số liệu âm / không phải số bị coi là 0 (nền tảng trả null cho số ẩn)."""
    out: dict = {"ok": True, "username": username, "views": to_int(views), "likes": to_int(likes),
                 "comments": to_int(comments), "shares": to_int(shares)}
    if published_at not in (None, ""):
        out["published_at"] = published_at
    if isinstance(duration_seconds, (int, float)) and not isinstance(duration_seconds, bool) and duration_seconds > 0:
        out["duration_seconds"] = int(duration_seconds)
    for key, value in (("region", region), ("title", title), ("cover_url", cover_url), ("video_id", video_id), ("url", url)):
        if value:
            out[key] = value
    return out


def fail(message: str) -> dict:
    return {"ok": False, "error": message}


def to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


class QuietYdlLogger:
    """Logger của yt-dlp không in gì: lỗi đã được bắt và phân loại ở nơi gọi. Không có nó, MỖI lượt thử bị chặn (đã dự kiến, ~75% lượt TikTok)
    vẫn in một dòng `ERROR: ...` ra stderr và làm nhật ký đầy lỗi giả."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
