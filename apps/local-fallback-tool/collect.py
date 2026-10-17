#!/usr/bin/env python3
"""
collect.py — Thu thập dữ liệu kênh cho Shared Collector (fallback_tool).

Chạy liên tục: claim collect-job từ integration-service → phân loại nền tảng
(YouTube/TikTok/Facebook) → lấy danh sách video mới nhất → check view từng video
qua script subprocess của CheckviewV2 → push snapshot.

Phương pháp:
  - YouTube : YouTube Data API v3 (yêu cầu API key trong env)
  - TikTok  : tikwm.com public REST API (không cần key)
  - Facebook: httpx scrape + cookie (yêu cầu FB_COOKIE trong env)

Contract CheckviewV2 (subprocess stdout 1 dòng JSON):
  { "status": "ok"|"error", "username": str, "views": int, "likes": int,
    "comments": int, "shares": int, "error"?: str }

Cách chạy:
  python collect.py \\
    --gateway http://192.168.1.70:8480 \\
    --api-key mpk_<key_id>.<secret> \\
    --checkview-dir D:/workspace/CheckviewV2/backend/checker \\
    --youtube-api-key AIza...                  # (hoặc env YOUTUBE_API_KEY)
    [--proxy socks5://127.0.0.1:1080]
    [--poll-interval 30]
    [--max-concurrent 3]

Tham chiếu:
  - docs/architecture/shared-collector-design-v1.md §3-4 (delegated ingestion)
  - D:/workspace/CheckviewV2/backend/checker/check_*.py
  - D:/workspace/youtube_video/app/sources/yt_common.py  (resolve channel_id)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from system_api import DEFAULT_GATEWAY, SystemApi

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("collect")

# ── Hằng số ───────────────────────────────────────────────────────────────────

PLATFORMS = ("youtube", "tiktok", "facebook")
TIKWM_API = "https://www.tikwm.com/api/"
YT_API = "https://www.googleapis.com/youtube/v3"
_UC_RE = re.compile(r"^UC[A-Za-z0-9_-]{10,}$")
_CHANNEL_PATH_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]+)")
_HANDLE_RE = re.compile(r"(?:/@|@)([A-Za-z0-9._-]+)")

# ── Kết nối / ánh xạ dữ liệu (thuần, có test: tests/test_collect_push.py) ────────────────────

CONNECTION_FORMAT = "manager-connection/1"


class JobAbort(Exception):
    """Job không thể hoàn tất vì lý do nghiệp vụ (không phải lỗi hạ tầng): báo `fail` kèm lý do; `retryable=False` = đừng thử lại."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ConnectionFileError(Exception):
    pass


def load_connection_file(path: str) -> dict:
    """Đọc file kết nối Admin cấp kèm key dùng chung: {format, gateway, api_key, ...}. Không log nội dung (có secret)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConnectionFileError(f"không thấy file kết nối: {path}") from None
    except (OSError, ValueError):
        raise ConnectionFileError(f"file kết nối không đọc được hoặc không phải JSON: {path}") from None
    if not isinstance(data, dict) or data.get("format") != CONNECTION_FORMAT:
        raise ConnectionFileError(f"file kết nối sai định dạng (cần format={CONNECTION_FORMAT})")
    gateway, api_key = data.get("gateway"), data.get("api_key")
    if not (isinstance(gateway, str) and gateway.startswith(("http://", "https://"))):
        raise ConnectionFileError("file kết nối thiếu `gateway` (http/https)")
    if not (isinstance(api_key, str) and api_key.startswith("mpk_")):
        raise ConnectionFileError("file kết nối thiếu `api_key` hợp lệ")
    return {"gateway": gateway.rstrip("/"), "api_key": api_key}


def select_platforms(allowed: list[str], narrowed: list[str] | None) -> list[str]:
    """Nền tảng collector xử lý = key được phép ∩ collector có adapter (∩ --platforms nếu có). Giữ thứ tự `PLATFORMS`."""
    return [p for p in PLATFORMS if p in allowed and (not narrowed or p in narrowed)]


# ── Status tracker ────────────────────────────────────────────────────────────

class StatusTracker:
    """Ghi trạng thái collector ra file JSON (thread-safe; asyncio single-thread)."""
    def __init__(self, status_file: str) -> None:
        import threading
        self._file = status_file
        self._lock = threading.Lock()
        self._data: dict = {
            "session_start": datetime.now(timezone.utc).isoformat(),
            "stats": {"claimed": 0, "done": 0, "failed": 0},
            "recent_jobs": [],
            "channels": {},
        }
        if status_file:
            self._flush()

    def job_started(self, job_id: str, platform: str, channel_id: str, channel_name: str, owner: str) -> None:
        if not self._file: return
        with self._lock:
            self._data["stats"]["claimed"] += 1
            self._upsert({"job_id": job_id, "platform": platform, "channel": channel_name,
                "channel_id": channel_id, "owner": owner, "status": "running",
                "at": datetime.now(timezone.utc).isoformat()})
            self._flush()

    def job_done(self, job_id: str, platform: str, channel_id: str, channel_name: str,
                  owner: str, items: int, duration_ms: int) -> None:
        if not self._file: return
        with self._lock:
            self._data["stats"]["done"] += 1
            key = f"{platform}/{channel_id}"
            ch = self._data["channels"].get(key, {"total_ok": 0, "total_fail": 0, "last_error": None, "last_error_at": None})
            ch.update({"channel": channel_name, "platform": platform, "owner": owner,
                "total_ok": ch["total_ok"] + 1, "last_ok": datetime.now(timezone.utc).isoformat()})
            self._data["channels"][key] = ch
            self._upsert({"job_id": job_id, "platform": platform, "channel": channel_name,
                "channel_id": channel_id, "owner": owner, "status": "done",
                "items": items, "duration_ms": duration_ms, "at": datetime.now(timezone.utc).isoformat()})
            self._flush()

    def job_failed(self, job_id: str, platform: str, channel_id: str, channel_name: str,
                    owner: str, error: str, duration_ms: int) -> None:
        if not self._file: return
        with self._lock:
            self._data["stats"]["failed"] += 1
            key = f"{platform}/{channel_id}"
            ch = self._data["channels"].get(key, {"total_ok": 0, "total_fail": 0, "last_ok": None})
            ch.update({"channel": channel_name, "platform": platform, "owner": owner,
                "total_fail": ch.get("total_fail", 0) + 1, "last_error": error,
                "last_error_at": datetime.now(timezone.utc).isoformat()})
            self._data["channels"][key] = ch
            self._upsert({"job_id": job_id, "platform": platform, "channel": channel_name,
                "channel_id": channel_id, "owner": owner, "status": "failed",
                "error": error, "duration_ms": duration_ms, "at": datetime.now(timezone.utc).isoformat()})
            self._flush()

    def _upsert(self, entry: dict) -> None:
        jobs = [j for j in self._data["recent_jobs"] if j.get("job_id") != entry["job_id"]]
        jobs.insert(0, entry)
        self._data["recent_jobs"] = jobs[:50]

    def _flush(self) -> None:
        try:
            Path(self._file).parent.mkdir(parents=True, exist_ok=True)
            Path(self._file).write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

# ── CheckviewV2 subprocess caller ─────────────────────────────────────────────

def check_view_subprocess(
    checker_dir: str,
    platform: str,
    url: str,
    proxy: str | None = None,
    cookie: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """
    Gọi check_{platform}.py từ CheckviewV2 qua subprocess.

    Contract đầu ra (1 dòng JSON):
      { "status": "ok"|"error", "views": int, "likes": int,
        "comments": int, "shares": int, "username": str }
    """
    script = Path(checker_dir) / f"check_{platform}.py"
    if not script.exists():
        return {"status": "error", "error": f"checker not found: {script}"}

    env = os.environ.copy()
    if cookie:
        env["COOKIE"] = cookie
    if proxy:
        env["PROXY"] = proxy

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--url", url, "--kind", platform],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
        if not result.stdout.strip():
            return {"status": "error", "error": result.stderr.strip()[:300] or "empty output"}
        return json.loads(result.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"timeout {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)[:200]}

# ── YouTube channel resolver (pattern từ youtube_video/yt_common.py) ──────────

async def resolve_youtube_channel(
    input_ref: str, youtube_api_key: str, http: httpx.AsyncClient,
) -> tuple[str, str] | None:
    """
    Resolve URL/@handle/channel-id → (channel_id, channel_title).
    Pattern từ D:/workspace/youtube_video/app/sources/yt_common.py::resolve_channel_id.
    """
    # Trường hợp 1: UC{...} trực tiếp
    s = input_ref.strip().lstrip("@")
    if _UC_RE.match(s):
        return s, ""

    # Trường hợp 2: URL chứa /channel/UC...
    m = _CHANNEL_PATH_RE.search(input_ref)
    if m:
        return m.group(1), ""

    # Trường hợp 3: @handle hoặc /@handle
    handle_m = _HANDLE_RE.search(input_ref) or (re.match(r"^@?([A-Za-z0-9._-]{3,100})$", input_ref))
    handle = handle_m.group(1) if handle_m else None

    if not handle:
        log.warning("YouTube: không parse được input_ref=%s", input_ref)
        return None

    try:
        r = await http.get(
            f"{YT_API}/channels",
            params={
                "part": "snippet",
                "forHandle": f"@{handle}" if not handle.startswith("@") else handle,
                "key": youtube_api_key,
                "maxResults": 1,
            },
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return None
        ch = items[0]
        return ch["id"], ch["snippet"]["title"]
    except Exception as exc:  # noqa: BLE001
        log.error("YouTube channel lookup failed: %s", exc)
        return None

async def fetch_youtube_recent_videos(
    channel_id: str, youtube_api_key: str, http: httpx.AsyncClient, max_items: int = 30,
) -> list[dict]:
    """
    Lấy video mới nhất của kênh qua playlistItems (playlist UU{channel_id[2:]}).
    Pattern từ youtube_video/app/sources/youtube_scan.py::list_all_videos.
    """
    playlist_id = "UU" + channel_id[2:]
    videos = []
    page_token: str | None = None

    while len(videos) < max_items:
        params: dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(50, max_items - len(videos)),
            "key": youtube_api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            r = await http.get(f"{YT_API}/playlistItems", params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("YouTube playlistItems error: %s", exc)
            break

        for item in data.get("items", []):
            snip = item.get("snippet", {})
            vid_id = snip.get("resourceId", {}).get("videoId", "")
            if vid_id:
                videos.append({
                    "video_id": vid_id,
                    "title": snip.get("title", ""),
                    "published_at": snip.get("publishedAt", ""),
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                })
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return videos

# ── TikTok channel video listing (pattern từ tikwm.com API) ──────────────────

async def fetch_tiktok_recent_videos(
    username: str, http: httpx.AsyncClient, proxy: str | None = None, max_items: int = 30,
) -> list[dict]:
    """
    Lấy video mới nhất của user TikTok qua tikwm.com.
    Không cần cookie — giới hạn ~1 req/s.
    """
    clean = username.lstrip("@")
    proxies = {"all://": proxy} if proxy else None
    try:
        # tikwm không có direct "user videos" endpoint đơn giản
        # → dùng trang feed của user qua scrape nhẹ
        client = httpx.AsyncClient(proxies=proxies, timeout=15) if proxies else http
        r = await client.post(TIKWM_API, data={"url": f"https://www.tiktok.com/@{clean}", "hd": "0"})
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0:
            log.warning("TikTok tikwm error: %s", d.get("msg"))
            return []
        # tikwm trả về thông tin user, không phải video list → trả về placeholder
        author = d.get("data", {}).get("author", {})
        return [{"username": author.get("unique_id", clean), "platform": "tiktok", "raw": d["data"]}]
    except Exception as exc:  # noqa: BLE001
        log.warning("TikTok fetch error for @%s: %s", clean, exc)
        return []

# ── Job processor ─────────────────────────────────────────────────────────────

async def process_job(
    job: dict,
    client: SystemApi,
    checker_dir: str,
    youtube_api_key: str,
    fb_cookie: str | None,
    proxy: str | None,
    http: httpx.AsyncClient,
    tracker: StatusTracker,
) -> None:
    # ClaimedJob schema: job_id, platform, payload, resource (ResourceRef)
    job_id = job.get("job_id") or job.get("id", "")
    platform = job.get("platform", "")
    resource = job.get("resource") or {}
    input_ref = (resource.get("input_ref") or resource.get("external_id") or
                 job.get("resource_input_ref") or job.get("input_ref", ""))
    resource_id = resource.get("id") or job.get("resource_id", "")
    owner = (job.get("owner_account_id") or resource.get("owner_account_id") or
             job.get("account_id") or "")
    channel_name = resource.get("display_name") or resource.get("name") or input_ref
    channel_id = resource_id or input_ref
    start_time = time.monotonic()

    log.info("Processing job=%s platform=%s input_ref=%s", job_id, platform, input_ref[:60])
    tracker.job_started(job_id, platform, channel_id, channel_name, owner)

    try:
        # ── 1. Resolve kênh → lấy danh sách video ───────────────────────────
        items: list[dict] = []
        # Chỉ số của chính kênh (followers/videos_posted/likes) — adapter nào lấy được thì điền, rỗng = không gửi.
        channel_stats: dict = {}

        if platform == "youtube":
            resolved = await resolve_youtube_channel(input_ref, youtube_api_key, http)
            if not resolved:
                raise JobAbort(f"Cannot resolve YouTube channel: {input_ref}", retryable=False)
            channel_id, channel_title = resolved
            log.info("YouTube channel_id=%s title=%s", channel_id, channel_title)

            videos = await fetch_youtube_recent_videos(channel_id, youtube_api_key, http)
            log.info("Fetched %d videos for channel %s", len(videos), channel_id)

            # ── 2. Check view cho từng video ─────────────────────────────────
            for vid in videos[:10]:  # giới hạn 10 video đầu mỗi lần
                result = check_view_subprocess(
                    checker_dir, "youtube", vid["url"], proxy=proxy,
                )
                items.append({
                    "external_id": vid["video_id"],
                    "title": vid["title"],
                    "published_at": vid["published_at"],
                    "url": vid["url"],
                    "views": result.get("views", 0),
                    "likes": result.get("likes", 0),
                    "comments": result.get("comments", 0),
                    "check_status": result.get("status", "error"),
                })
                await asyncio.sleep(0.5)  # throttle

        elif platform == "tiktok":
            vids = await fetch_tiktok_recent_videos(input_ref, http, proxy)
            for vid in vids[:10]:
                url = vid.get("url") or f"https://www.tiktok.com/@{input_ref.lstrip('@')}"
                result = check_view_subprocess(checker_dir, "tiktok", url, proxy=proxy)
                items.append({
                    "external_id": vid.get("video_id", ""),
                    "views": result.get("views", 0),
                    "likes": result.get("likes", 0),
                    "comments": result.get("comments", 0),
                    "check_status": result.get("status", "error"),
                })

        elif platform == "facebook":
            result = check_view_subprocess(
                checker_dir, "facebook", input_ref, proxy=proxy, cookie=fb_cookie,
            )
            items.append({
                "external_id": input_ref,
                "views": result.get("views", 0),
                "likes": result.get("likes", 0),
                "comments": result.get("comments", 0),
                "check_status": result.get("status", "error"),
            })

        else:
            raise JobAbort(f"Unsupported platform: {platform}", retryable=False)

        # ── 3. Push snapshot (delegated ingestion) ───────────────────────────
        # Chỉ đẩy số liệu ĐO ĐƯỢC. Kênh không có số liệu hợp lệ nào => job FAIL có lý do (không im lặng, không đẩy 0 giả).
        items = [it for it in items if it.get("check_status") == "ok" and it.get("external_id")]
        if not items and not channel_stats:
            raise JobAbort("Không lấy được số liệu hợp lệ nào cho kênh này", retryable=True)
        await client.push_snapshot(job, items, channel_stats)
        log.info("Pushed %d items for job=%s platform=%s", len(items), job_id, platform)

        await client.complete_job(job, {"items": len(items)})
        log.info("Completed job=%s", job_id)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        tracker.job_done(job_id, platform, channel_id, channel_name, owner, len(items), duration_ms)

    except Exception as exc:  # noqa: BLE001
        log.exception("Job %s failed: %s", job_id, exc)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        tracker.job_failed(job_id, platform, channel_id, channel_name, owner, str(exc)[:400], duration_ms)
        try:
            await client.fail_job(job, str(exc)[:400], retryable=getattr(exc, "retryable", True))
        except Exception:  # noqa: BLE001
            pass

# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_loop(args: argparse.Namespace) -> None:
    youtube_api_key = args.youtube_api_key or os.environ.get("YOUTUBE_API_KEY", "")
    fb_cookie = args.fb_cookie or os.environ.get("FB_COOKIE", "")
    proxy = args.proxy or os.environ.get("PROXY", "") or None

    client = SystemApi(args.gateway, args.api_key)
    http = httpx.AsyncClient(timeout=20.0)
    sem = asyncio.Semaphore(args.max_concurrent)
    tracker = StatusTracker(args.status_file or "")

    # Handshake để lấy installation_id — retry tối đa 5 lần nếu gateway chưa sẵn sàng.
    # `platforms` khai lúc handshake chỉ là gợi ý; nguồn sự thật là danh sách key được phép trên server (bước bên dưới).
    for attempt in range(5):
        try:
            await client.handshake([])   # [] = mọi nền tảng key được phép; thu hẹp --platforms áp ở select_platforms bên dưới
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Handshake attempt %d/5 failed: %s — chờ 5s", attempt + 1, exc)
            await asyncio.sleep(5)
    else:
        log.error("Handshake thất bại sau 5 lần — dừng collector")
        return

    try:
        allowed = await client.allowed_platforms()
    except Exception as exc:  # noqa: BLE001
        log.error("Không lấy được danh sách nền tảng được phép: %s — dừng collector", exc)
        return
    active_platforms = select_platforms(allowed, args.platforms)
    if not active_platforms:
        log.error("Không có nền tảng nào vừa được key cho phép (%s) vừa có adapter (%s) — dừng collector",
                  ",".join(allowed) or "-", ",".join(PLATFORMS))
        return
    log.info("Collector started. gateway=%s platforms=%s poll_interval=%ds",
             args.gateway, ",".join(active_platforms), args.poll_interval)

    async def _process(job: dict) -> None:
        async with sem:
            await process_job(job, client, args.checkview_dir, youtube_api_key, fb_cookie, proxy, http, tracker)

    try:
        while True:
            try:
                jobs = await client.claim_jobs(active_platforms, limit=args.max_concurrent * 2)
                if jobs:
                    log.info("Claimed %d jobs", len(jobs))
                    await asyncio.gather(*[_process(j) for j in jobs], return_exceptions=True)
                else:
                    log.debug("No jobs available, sleeping %ds", args.poll_interval)
                    await asyncio.sleep(args.poll_interval)
            except httpx.HTTPStatusError as exc:
                log.error("HTTP error claiming jobs: %s %s", exc.response.status_code, exc.response.text[:200])
                await asyncio.sleep(args.poll_interval)
            except Exception as exc:  # noqa: BLE001
                log.exception("Unexpected error in main loop: %s", exc)
                await asyncio.sleep(args.poll_interval)
    finally:
        await client.aclose()
        await http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Channel data collector (fallback_tool)")
    ap.add_argument("--connection-file", default=os.environ.get("COLLECTOR_CONNECTION_FILE", ""),
                    help="File kết nối do Admin cấp cùng key dùng chung (JSON: gateway + api_key + nền tảng). "
                         "Có file này thì không cần --gateway/--api-key")
    ap.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", ""))
    ap.add_argument("--api-key", default=os.environ.get("MACHINE_API_KEY", ""),
                    help="Machine API key (mpk_<key_id>.<secret>)")
    ap.add_argument("--checkview-dir",
                    default=os.environ.get("CHECKVIEW_DIR", r"D:\workspace\CheckviewV2\backend\checker"),
                    help="Thư mục chứa check_*.py của CheckviewV2")
    ap.add_argument("--youtube-api-key", default=os.environ.get("YOUTUBE_API_KEY", ""))
    ap.add_argument("--fb-cookie", default=os.environ.get("FB_COOKIE", ""),
                    help="Facebook cookie (c_user=...; xs=...)")
    ap.add_argument("--proxy", default=os.environ.get("PROXY", ""),
                    help="Proxy URL: socks5://host:port hoặc http://host:port")
    ap.add_argument("--poll-interval", type=int, default=30, help="Giây chờ khi không có job")
    ap.add_argument("--max-concurrent", type=int, default=3, help="Số job chạy song song")
    ap.add_argument("--platforms", type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
                    default=None, help="Thu hẹp nền tảng xử lý, cách nhau bằng dấu phẩy "
                                       "(mặc định: mọi nền tảng key được phép VÀ collector có adapter)")
    ap.add_argument("--status-file", default=os.environ.get("COLLECTOR_STATUS_FILE", ""),
                    help="Đường dẫn file JSON ghi trạng thái collector (owner, channel, errors)")
    args = ap.parse_args()

    if args.connection_file:
        try:
            conn = load_connection_file(args.connection_file)
        except ConnectionFileError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        args.gateway, args.api_key = conn["gateway"], conn["api_key"]
    args.gateway = args.gateway or DEFAULT_GATEWAY

    if not args.api_key:
        print("ERROR: cần --connection-file hoặc --api-key/MACHINE_API_KEY", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_loop(args))


if __name__ == "__main__":
    main()
