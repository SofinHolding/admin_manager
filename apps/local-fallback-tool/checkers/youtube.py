"""YouTube: liệt kê video của kênh + lấy số liệu từng video.

Đường chính là YouTube Data API v3 với NHIỀU API key xoay vòng (key hết quota / không hợp lệ được cho nghỉ rồi chuyển key kế):
- `channels.list` (1 đơn vị): id kênh, danh sách "uploads", số người đăng ký, số video;
- `playlistItems.list` (1 đơn vị/trang 50): id các video mới nhất;
- `videos.list` (1 đơn vị/50 video): view / like / comment / ngày đăng / thời lượng — rẻ hơn nhiều so với đo từng video.
Không có key nào dùng được thì rơi về yt-dlp (không cần key, chậm hơn, mỗi video một lượt) — port từ CheckviewV2 `check_youtube.py`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx

from checkers.common import QuietYdlLogger, fail, stat
from health import classify_youtube
from pools import Pool, force_remote_dns

YT_API = "https://www.googleapis.com/youtube/v3"
TIMEOUT_S = 20.0
QUOTA_COOLDOWN_S = 6 * 3600.0       # quota YouTube reset theo ngày (giờ Thái Bình Dương); thử lại sau vài giờ
DEAD_KEY_COOLDOWN_S = 24 * 3600.0

_UC_RE = re.compile(r"^UC[A-Za-z0-9_-]{10,}$")
_CHANNEL_PATH_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]+)")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


class NoKeyAvailable(Exception):
    """Không còn API key nào dùng được (chưa thêm, hết quota hôm nay hoặc đều không hợp lệ)."""


class YouTubeApiError(Exception):
    """Lỗi YouTube không xoay key được (mạng, 404, tham số)."""


def video_id_of(url: str) -> str:
    """Id video từ `watch?v=`, `youtu.be/`, `shorts/`, `live/`, `embed/`; rỗng nếu không nhận ra."""
    text = (url or "").strip()
    if _VIDEO_ID_RE.match(text):
        return text
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else ""


def parse_duration(iso: str | None) -> int | None:
    """`PT1H2M10S` -> 3730 giây; None nếu rỗng/livestream (`P0D`)."""
    m = _DURATION_RE.match(iso or "")
    if not m:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total or None


def channel_query(ref: str) -> dict[str, str]:
    """URL kênh / `@handle` / `UC…` thuần -> tham số cho `channels.list` (id | forHandle | forUsername)."""
    raw = (ref or "").strip().rstrip("/")
    if not raw:
        raise ValueError("Thiếu URL/ID kênh YouTube")
    m = _CHANNEL_PATH_RE.search(raw)
    if m:
        return {"id": m.group(1)}
    if _UC_RE.match(raw):
        return {"id": raw}
    if "/@" in raw:
        handle = re.sub(r"/(videos|shorts|live|playlists|streams)$", "", raw.split("/@", 1)[1]).strip()
        return {"forHandle": handle}
    if raw.startswith("@"):
        return {"forHandle": raw[1:]}
    if "/c/" in raw or "/user/" in raw:
        return {"forUsername": re.split(r"/c/|/user/", raw, maxsplit=1)[-1].split("/")[0]}
    return {"forHandle": raw.split("/")[-1].lstrip("@")}


class YouTubeApi:
    def __init__(self, keys: list[str] | Pool, *, proxy: str | None = None, client: httpx.Client | None = None) -> None:
        self.pool = keys if isinstance(keys, Pool) else Pool(keys, cooldown_s=QUOTA_COOLDOWN_S)
        self._own = client is None
        self._http = client or httpx.Client(timeout=TIMEOUT_S, proxy=force_remote_dns(proxy))

    def close(self) -> None:
        if self._own:
            self._http.close()

    def get(self, path: str, params: dict[str, Any]) -> dict:
        """GET một endpoint Data API. Key hết quota / không hợp lệ => nghỉ rồi thử key kế; hết key => `NoKeyAvailable`."""
        tried = 0
        while tried < max(1, len(self.pool)):
            key = self.pool.pick()
            if key is None:
                break
            tried += 1
            try:
                r = self._http.get(f"{YT_API}/{path}", params={**params, "key": key})
            except httpx.HTTPError as exc:   # không đưa URL (có key) vào thông báo
                raise YouTubeApiError(f"Không kết nối được YouTube Data API: {type(exc).__name__}") from exc
            try:
                body = r.json()
            except ValueError:
                body = None
            if r.status_code == 200 and isinstance(body, dict):
                self.pool.ok(key)
                return body
            state, detail = classify_youtube(r.status_code, body)
            if state == "quota":
                self.pool.bad(key, QUOTA_COOLDOWN_S)
                continue
            if state == "dead":
                self.pool.bad(key, DEAD_KEY_COOLDOWN_S)
                continue
            raise YouTubeApiError(f"YouTube Data API: {detail}")
        raise NoKeyAvailable("Không còn YouTube API key nào dùng được (chưa thêm, hết quota hôm nay hoặc không hợp lệ)")

    # ── Kênh ─────────────────────────────────────────────────────────────────────────

    def resolve_channel(self, ref: str) -> dict:
        """`{channel_id, title, uploads, followers, videos_posted}`; không thấy kênh => ValueError."""
        query = channel_query(ref)
        items = self.get("channels", {**query, "part": "snippet,contentDetails,statistics"}).get("items") or []
        if not items and "forUsername" in query:   # username kiểu cũ: thử lại như handle
            items = self.get("channels", {"forHandle": query["forUsername"], "part": "snippet,contentDetails,statistics"}).get("items") or []
        if not items:
            raise ValueError(f"Không tìm thấy kênh YouTube: {ref}")
        ch = items[0]
        stats = ch.get("statistics") or {}
        hidden = bool(stats.get("hiddenSubscriberCount"))
        return {
            "channel_id": ch["id"], "title": (ch.get("snippet") or {}).get("title") or "",
            "uploads": ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or "",
            "followers": None if hidden else stats.get("subscriberCount"), "videos_posted": stats.get("videoCount"),
        }

    def recent_video_ids(self, uploads_playlist: str, limit: int = 30) -> list[str]:
        ids: list[str] = []
        token = ""
        while len(ids) < limit:
            params: dict[str, Any] = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": min(50, limit - len(ids))}
            if token:
                params["pageToken"] = token
            body = self.get("playlistItems", params)
            ids += [i["contentDetails"]["videoId"] for i in body.get("items") or [] if i.get("contentDetails", {}).get("videoId")]
            token = body.get("nextPageToken") or ""
            if not token:
                break
        return list(dict.fromkeys(ids))[:limit]

    def video_stats(self, ids: list[str]) -> dict[str, dict]:
        """`{video_id: stat(...)}` cho các video còn tồn tại (video riêng tư/đã xoá không có trong kết quả)."""
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            body = self.get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])})
            for v in body.get("items") or []:
                sn, st = v.get("snippet") or {}, v.get("statistics") or {}
                out[v["id"]] = stat(
                    username=sn.get("channelTitle") or "", views=st.get("viewCount"), likes=st.get("likeCount"),
                    comments=st.get("commentCount"), published_at=sn.get("publishedAt"),
                    duration_seconds=parse_duration((v.get("contentDetails") or {}).get("duration")),
                    title=str(sn.get("title") or ""), video_id=v["id"], url=f"https://www.youtube.com/watch?v={v['id']}")
        return out


def _iso_date(upload_date: Any) -> str | None:
    """yt-dlp `upload_date` `YYYYMMDD` -> `YYYY-MM-DD` (KHÔNG để nguyên: một chuỗi 8 chữ số bị hiểu nhầm là epoch)."""
    s = str(upload_date or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else None



# ── yt-dlp: không cần key ────────────────────────────────────────────────────────────────

Extract = Callable[[str, dict], dict | None]


def _extract_info(url: str, opts: dict) -> dict | None:
    import yt_dlp   # nạp muộn: chỉ cần khi thật sự rơi về yt-dlp

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _ydl_opts(proxy: str | None, **extra: Any) -> dict:
    return {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 30, "logger": QuietYdlLogger(),
            "proxy": force_remote_dns(proxy) or None, "extractor_args": {"youtube": {"skip": ["authcheck"]}}, **extra}


def check_video_ytdlp(url: str, proxy: str | None = None, *, extract: Extract = _extract_info) -> dict:
    """Một video qua yt-dlp (`view_count`, `like_count`, `comment_count`, `duration`, `timestamp`)."""
    try:
        info = extract(url, _ydl_opts(proxy)) or {}
    except Exception as exc:  # noqa: BLE001 — yt-dlp ném nhiều loại lỗi; rút gọn dòng ERROR cuối
        lines = [ln for ln in str(exc).splitlines() if "ERROR" in ln]
        return fail(f"[yt] {(lines[-1] if lines else str(exc))[:300]}")
    if not info:
        return fail("[yt] yt-dlp không trả dữ liệu")
    uploader = info.get("uploader_id") or info.get("uploader") or info.get("channel") or ""
    return stat(
        username=uploader.lstrip("@") if isinstance(uploader, str) else "", views=info.get("view_count"),
        likes=info.get("like_count"), comments=info.get("comment_count"),
        published_at=info.get("timestamp") or _iso_date(info.get("upload_date")), duration_seconds=info.get("duration"),
        title=str(info.get("title") or ""), video_id=str(info.get("id") or ""), url=url)


def list_channel_ytdlp(channel_url: str, limit: int = 30, proxy: str | None = None, *, extract: Extract = _extract_info) -> list[str]:
    """Id các video mới nhất của kênh bằng yt-dlp (`extract_flat`), dùng khi không có API key."""
    url = channel_url.rstrip("/")
    if not re.search(r"/(videos|shorts|streams)$", url):
        url += "/videos"
    info = extract(url, _ydl_opts(proxy, extract_flat="in_playlist", playlistend=limit, noplaylist=False)) or {}
    return list(dict.fromkeys(str(e["id"]) for e in info.get("entries") or [] if e and e.get("id")))[:limit]
