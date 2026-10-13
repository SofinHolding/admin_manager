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
    --gateway http://127.0.0.1:57595 \\
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

# ── Integration-service client ────────────────────────────────────────────────

class IntegrationClient:
    """Thin HTTP client giao tiếp với integration-service qua machine credential."""

    def __init__(self, gateway: str, api_key: str, timeout: float = 20.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=gateway,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def claim_jobs(self, platforms: list[str], limit: int = 10) -> list[dict]:
        """Claim collect-jobs từ hàng đợi.

        Chỉ nhận jobs của resources execution_mode=fallback_local — đảm bảo tự nhiên:
        _rewrite() đã cancel pending-job khi mode != fallback_local, _ensure_collect_job()
        chỉ tạo job khi mode=fallback_local. Không cần filter thêm ở đây.
        """
        r = await self._http.post(
            "/v1/integrations/jobs/claim",
            json={"kinds": ["collect"], "platforms": platforms, "limit": limit},
        )
        if r.status_code == 204:
            return []
        r.raise_for_status()
        data = r.json().get("data", {})
        return data.get("jobs", []) if isinstance(data, dict) else []

    async def push_snapshot(self, job_id: str, platform: str, items: list[dict]) -> None:
        """Đẩy snapshot kèm X-Delegated-Job để gán owner từ job (delegated ingestion)."""
        idempotency_key = f"collect-{job_id}"
        r = await self._http.post(
            "/v1/integrations/snapshots",
            headers={"X-Delegated-Job": job_id, "Idempotency-Key": idempotency_key},
            json={"platform": platform},   # platform từ job, không hardcode
        )
        r.raise_for_status()
        snap = r.json()["data"]["snapshot_id"]
        BATCH = 500
        for i in range(0, max(len(items), 1), BATCH):
            batch = items[i:i + BATCH]
            if batch:
                await self._http.post(
                    f"/v1/integrations/snapshots/{snap}/items",
                    headers={"X-Delegated-Job": job_id},
                    json={"items": batch},
                )
        await self._http.post(
            f"/v1/integrations/snapshots/{snap}/commit",
            headers={"X-Delegated-Job": job_id},
        )

    async def complete_job(self, job_id: str) -> None:
        await self._http.post(f"/v1/integrations/jobs/{job_id}/complete")

    async def fail_job(self, job_id: str, reason: str) -> None:
        await self._http.post(
            f"/v1/integrations/jobs/{job_id}/fail",
            json={"reason": reason[:500]},
        )

    async def heartbeat(self, job_id: str) -> None:
        await self._http.post(f"/v1/integrations/jobs/{job_id}/heartbeat")

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
    client: IntegrationClient,
    checker_dir: str,
    youtube_api_key: str,
    fb_cookie: str | None,
    proxy: str | None,
    http: httpx.AsyncClient,
) -> None:
    job_id = job["id"]
    platform = job.get("platform", "")
    resource_id = job.get("resource_id", "")
    input_ref = job.get("resource_input_ref", "") or job.get("input_ref", "")

    log.info("Processing job=%s platform=%s input_ref=%s", job_id, platform, input_ref[:60])

    try:
        # ── 1. Resolve kênh → lấy danh sách video ───────────────────────────
        items: list[dict] = []

        if platform == "youtube":
            resolved = await resolve_youtube_channel(input_ref, youtube_api_key, http)
            if not resolved:
                await client.fail_job(job_id, f"Cannot resolve YouTube channel: {input_ref}")
                return
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
            log.warning("Unsupported platform: %s", platform)
            await client.fail_job(job_id, f"Unsupported platform: {platform}")
            return

        # ── 3. Push snapshot (delegated ingestion) ───────────────────────────
        if items:
            await client.push_snapshot(job_id, platform, items)
            log.info("Pushed %d items for job=%s platform=%s", len(items), job_id, platform)

        await client.complete_job(job_id)
        log.info("Completed job=%s", job_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("Job %s failed: %s", job_id, exc)
        try:
            await client.fail_job(job_id, str(exc)[:400])
        except Exception:  # noqa: BLE001
            pass

# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_loop(args: argparse.Namespace) -> None:
    youtube_api_key = args.youtube_api_key or os.environ.get("YOUTUBE_API_KEY", "")
    fb_cookie = args.fb_cookie or os.environ.get("FB_COOKIE", "")
    proxy = args.proxy or os.environ.get("PROXY", "") or None

    client = IntegrationClient(args.gateway, args.api_key)
    http = httpx.AsyncClient(timeout=20.0)
    sem = asyncio.Semaphore(args.max_concurrent)

    log.info("Collector started. gateway=%s poll_interval=%ds", args.gateway, args.poll_interval)

    async def _process(job: dict) -> None:
        async with sem:
            await process_job(job, client, args.checkview_dir, youtube_api_key, fb_cookie, proxy, http)

    try:
        while True:
            try:
                jobs = await client.claim_jobs(list(PLATFORMS), limit=args.max_concurrent * 2)
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
    ap.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:57595"))
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
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: --api-key hoặc MACHINE_API_KEY bắt buộc", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_loop(args))


if __name__ == "__main__":
    main()
