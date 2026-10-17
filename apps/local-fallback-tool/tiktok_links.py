"""
tiktok_links.py — liệt kê TẤT CẢ video của một kênh TikTok.

Cách làm (đã đo thật, số liệu ở README của tool): yt-dlp `extract_flat` KHÔNG cookie, KHÔNG trình duyệt, lặp lại
đến khi qua cổng chống bot của TikTok. Cổng này chặn NGẪU NHIÊN ~75% lượt gọi (yt-dlp báo `Unable to extract secondary user ID`) bất kể proxy, cookie
hay dấu vân tay TLS — tỉ lệ qua mỗi lượt ~25% — nhưng lượt thất bại chỉ tốn vài giây và lượt qua được cho danh sách ĐẦY ĐỦ. Vì vậy lặp tối đa
`MAX_ATTEMPTS` lượt, mỗi lượt đổi proxy kế tiếp và đổi dấu vân tay TLS (chrome-136/131/124): xác suất trượt cả 25 lượt ≈ 0,1%.
Mẫu đo (kênh 393 video, 9 proxy dân cư): 7/7 lần ra đủ 393 link, 5–60 giây; mở trình duyệt lấy cookie không làm tăng tỉ lệ qua.

Lưu DB + xuất file + CLI cho Electron nằm ở `video_links.py` (dùng chung cho YouTube và TikTok).
"""
from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone

from checkers.common import QuietYdlLogger
from pools import force_remote_dns, mask_proxy

FULL_MAX_ITEMS = 1500            # TikTok phân trang kiểu cursor: xin đủ MỘT lần, không escalate (mỗi lượt mới phải lấy lại từ trang 1)
MAX_ATTEMPTS = 25
PROBE_PROXIES = 3                # số proxy thử hỏi oEmbed khi xác nhận kênh có thật
IMPERSONATE = ("chrome-136", "chrome-131", "chrome-124")
_USER_RE = re.compile(r"tiktok\.com/@([\w.\-]+)", re.I)
_HANDLE_RE = re.compile(r"^@?([\w.\-]{2,64})$")


class TikTokError(Exception):
    """Lỗi thân thiện, hiển thị thẳng cho người dùng."""


class EmptyChannel(Exception):
    """Kênh thật sự chưa có video (không phải lỗi)."""


class PrivateChannel(Exception):
    """Kênh riêng tư / không tồn tại — thử lại không giải quyết được."""


def classify_error(exc: Exception) -> str:
    """`"empty"` | `"private"` | `"retry"` (mọi lỗi khác: bị cổng chống bot chặn, lỗi TLS/mạng/proxy — đáng thử lại với lượt kế)."""
    msg = str(exc).lower()
    if "does not have any videos posted" in msg:
        return "empty"
    if "account is private" in msg or "log into an account" in msg:
        return "private"
    return "retry"


def username_of(ref: str) -> str:
    m = _USER_RE.search(ref or "") or _HANDLE_RE.match((ref or "").strip())
    return m.group(1) if m else ""


def channel_url(ref: str) -> str:
    user = username_of(ref)
    if not user:
        raise TikTokError("Không đọc được tên kênh TikTok (cần link dạng https://www.tiktok.com/@tenkenh hoặc @tenkenh)")
    return f"https://www.tiktok.com/@{user}"


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def entry_to_video(entry: dict, username: str) -> dict | None:
    vid = str(entry.get("id") or "").strip()
    if not vid:
        return None
    views = entry.get("view_count")
    return {"video_id": vid, "url": entry.get("webpage_url") or entry.get("url") or f"https://www.tiktok.com/@{username}/video/{vid}",
            "title": str(entry.get("title") or entry.get("description") or "")[:300], "published_at": _iso(entry.get("timestamp")),
            "views": views if isinstance(views, int) and not isinstance(views, bool) else None}


# extract(url, proxy, impersonate_target, limit) -> danh sách entry của yt-dlp; ném nguyên lỗi của yt-dlp
Extract = Callable[[str, str | None, str, int], list[dict]]


def _extract_flat(url: str, proxy: str | None, impersonate: str, limit: int) -> list[dict]:
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget

    opts = {"extract_flat": True, "quiet": True, "no_warnings": True, "skip_download": True, "proxy": force_remote_dns(proxy) or None,
            "socket_timeout": 20.0, "playlistend": limit, "impersonate": ImpersonateTarget.from_str(impersonate), "logger": QuietYdlLogger()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return list((info or {}).get("entries") or [])


def _profile_confirmed(user: str, proxies: list[str | None]) -> bool:
    """Kênh CÓ tồn tại không — hỏi oEmbed công khai của TikTok (không đi qua cổng chống bot), lần lượt qua từng proxy (một proxy chập chờn
    không được kết luận thay cả hệ thống). True chỉ khi chắc chắn; mọi trường hợp khác False."""
    from curl_cffi import requests as cr

    for proxy in proxies:
        p = force_remote_dns(proxy)
        try:
            r = cr.get("https://www.tiktok.com/oembed", params={"url": f"https://www.tiktok.com/@{user}"},
                       proxies={"http": p, "https": p} if p else None, impersonate="chrome136", timeout=15)
            if r.status_code == 200 and f"@{user}".lower() in r.text.lower():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def list_videos(entries: list[dict], username: str) -> list[dict]:
    """Entry yt-dlp -> video (mới nhất trước, bỏ trùng id)."""
    out: dict[str, dict] = {}
    for e in entries:
        v = entry_to_video(e or {}, username)
        if v and v["video_id"] not in out:
            out[v["video_id"]] = v
    return list(out.values())


def fetch_links(ref: str, *, proxies: list[str], limit: int = FULL_MAX_ITEMS, extract: Extract | None = None,
                max_attempts: int | None = None, pause_s: float = 0.3, start: int | None = None,
                probe: Callable[[str, list[str | None]], bool] | None = None) -> dict:
    """Toàn bộ video của kênh: `{videos, via, attempts, kind}` (`kind`: "ok" | "empty") hoặc ném `TikTokError`.

    Lượt n dùng proxy `(start + n) % số proxy` và dấu vân tay `IMPERSONATE[n % 3]`; không có proxy thì đi bằng IP máy này. `start` ngẫu nhiên để
    các kênh/job song song không dồn vào cùng một proxy."""
    url, user = channel_url(ref), username_of(ref)
    extract = extract or _extract_flat
    max_attempts = max_attempts or MAX_ATTEMPTS
    candidates: list[str | None] = list(proxies) or [None]
    offset = random.randrange(len(candidates)) if start is None else start
    errors: list[str] = []
    for attempt in range(max_attempts):
        proxy = candidates[(offset + attempt) % len(candidates)]
        try:
            entries = extract(url, proxy, IMPERSONATE[attempt % len(IMPERSONATE)], limit)
        except Exception as exc:  # noqa: BLE001 — phân loại theo thông điệp thật của yt-dlp
            kind = classify_error(exc)
            via = mask_proxy(proxy) if proxy else "trực tiếp"
            if kind == "empty":
                return {"videos": [], "via": via, "attempts": attempt + 1, "kind": "empty"}
            if kind == "private":
                raise TikTokError("Kênh TikTok riêng tư hoặc không tồn tại — kiểm tra lại link.") from exc
            errors.append(str(exc).replace("\n", " ")[:120])
            time.sleep(pause_s)
            continue
        videos = list_videos(entries, user)
        if videos:
            return {"videos": videos, "via": mask_proxy(proxy) if proxy else "trực tiếp", "attempts": attempt + 1, "kind": "ok"}
        errors.append("TikTok trả danh sách rỗng")           # rỗng mà không có thông báo "chưa có video" = nghi bị chặn: thử lại
    probe_proxies = [candidates[(offset + i) % len(candidates)] for i in range(min(PROBE_PROXIES, len(candidates)))]
    if (probe or _profile_confirmed)(user, probe_proxies):
        # Đo thật: kênh 0 người theo dõi / chưa đăng video có trang không chứa dữ liệu kênh nên yt-dlp luôn báo "Unable to extract secondary user ID",
        # giống hệt khi bị chặn — không phân biệt được bằng yt-dlp. oEmbed xác nhận kênh có thật => nói rõ cả hai khả năng.
        raise TikTokError(f"Kênh có tồn tại nhưng không đọc được video sau {max_attempts} lượt. Thường gặp: kênh chưa đăng video nào "
                          "(0 người theo dõi) — hãy mở kênh trên TikTok kiểm tra; nếu chắc chắn có video thì TikTok đang chặn: thêm/đổi proxy "
                          "dân cư ở Cấu hình.")
    hint = ("TikTok đang chặn IP này — thêm proxy dân cư ở Cấu hình rồi thử lại" if not proxies
            else "TikTok chặn mọi lượt — thêm/đổi proxy (nên dùng proxy dân cư) ở Cấu hình rồi thử lại")
    raise TikTokError(f"Không lấy được danh sách video sau {max_attempts} lượt ({hint}). Lỗi cuối: {errors[-1] if errors else '-'}")
