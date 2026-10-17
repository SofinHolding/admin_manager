#!/usr/bin/env python3
"""
collect.py — Thu thập dữ liệu kênh cho Shared Collector (fallback_tool).

Chạy liên tục: claim collect-job từ integration-service → thu số liệu kênh theo nền tảng (`channel_collect.py`) → push snapshot.
Phương pháp (chi tiết ở channel_collect.py):
  - YouTube : YouTube Data API v3, xoay NHIỀU API key (không có key thì yt-dlp)
  - TikTok  : liệt kê video bằng yt-dlp (thử lại qua proxy) + số liệu từng video qua tikwm
  - Facebook: link bài/reel + cookie Facebook
  Danh sách video YouTube/TikTok lưu cục bộ: lần đầu của kênh lấy đủ, các lần sau chỉ lấy video mới rồi đo trên cả danh sách.
  Proxy: danh sách nhiều proxy, xoay vòng; proxy lỗi nghỉ ít phút rồi được thử lại.

Khoá API / proxy / cookie đọc từ file cấu hình của tool (`--settings-file`, mặc định .dev/collector-config.json), KHÔNG truyền qua dòng lệnh.

Cách chạy:
  python collect.py --connection-file connection-<client_id>.json [--settings-file .dev/collector-config.json]
                    [--poll-interval 30] [--max-concurrent 3]

Tham chiếu:
  - docs/architecture/shared-collector-design-v1.md §3-4 (delegated ingestion)
  - D:/workspace/CheckviewV2/backend/checker (nguồn tham chiếu của các bộ kiểm tra)
  - D:/workspace/youtube_video/app/sources (nguồn tham chiếu của việc lấy cookie / liệt kê video)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

import channel_collect
import channels_db
from channel_collect import Credentials, JobAbort
from system_api import DEFAULT_GATEWAY, SystemApi, report_tz, use_utf8_stdio

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

# ── Kết nối / ánh xạ dữ liệu (thuần, có test: tests/test_collect_push.py) ────────────────────

CONNECTION_FORMAT = "manager-connection/1"


class EnqueuePlan:
    """Khi nào xin server tạo job thu thập cho kênh đã kéo.

    Mỗi kênh đã kéo được xin job MỘT lần mỗi ngày (giờ Việt Nam) trong suốt thời gian collector chạy: ngay khi collector khởi động
    (mỗi mốc giờ của lịch = một đợt), khi sang ngày mới nếu collector chạy xuyên đêm, và khi người vận hành kéo thêm kênh giữa phiên.
    Server không tự tạo lại job sau khi job xong nên không có bước này thì kênh chỉ được thu đúng một lần."""

    def __init__(self) -> None:
        self._day = ""
        self._done: set[str] = set()

    def due(self, pulled_ids: list[str], today: str) -> list[str]:
        if today != self._day:
            self._day, self._done = today, set()
        return [i for i in pulled_ids if i not in self._done]

    def mark(self, ids: list[str]) -> None:
        self._done.update(ids)


def today_vn() -> str:
    return datetime.now(report_tz()).date().isoformat()


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
    """Ghi trạng thái collector ra file JSON (thread-safe; asyncio single-thread).

    `stats`/`recent_jobs` là của PHIÊN này; `channels` (lần thu gần nhất của từng kênh) được GIỮ qua các phiên — mỗi lần "Chạy ngay"
    thường chỉ chạm vài kênh, nên nếu xoá thì các kênh còn lại bị hiện "chưa thu" dù đã thu hôm trước."""
    def __init__(self, status_file: str) -> None:
        import threading
        self._file = status_file
        self._lock = threading.Lock()
        self._data: dict = {
            "session_start": datetime.now(timezone.utc).isoformat(),
            "stats": {"claimed": 0, "done": 0, "failed": 0},
            "recent_jobs": [],
            "channels": self._load_channels(status_file),
        }
        if status_file:
            self._flush()

    @staticmethod
    def _load_channels(status_file: str) -> dict:
        try:
            prior = json.loads(Path(status_file).read_text(encoding="utf-8")).get("channels") if status_file else None
        except (OSError, ValueError, AttributeError):
            return {}
        return prior if isinstance(prior, dict) else {}

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
            ch.update({"channel": channel_name, "channel_id": channel_id, "platform": platform, "owner": owner,
                "total_ok": ch.get("total_ok", 0) + 1, "last_ok": datetime.now(timezone.utc).isoformat(),
                "last_items": items, "last_error": None, "last_error_at": None})
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
            ch.update({"channel": channel_name, "channel_id": channel_id, "platform": platform, "owner": owner,
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

# ── Job processor ─────────────────────────────────────────────────────────────

async def _keep_lease(client: SystemApi, job: dict, every_s: float = 60.0) -> None:
    """Gia hạn lease của job trong lúc đang xử lý. Lần đầu của một kênh lấy cả danh sách video rồi đo hàng trăm video nên có thể lâu hơn lease mặc định (300s);
    hết lease mà job vẫn chạy thì server cho máy khác nhận trùng và lệnh đẩy dữ liệu bị từ chối. Lỗi gia hạn không làm hỏng job."""
    while True:
        await asyncio.sleep(every_s)
        try:
            await client.heartbeat(job)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gia hạn lease job=%s lỗi: %s", job.get("job_id"), exc)


async def process_job(job: dict, client: SystemApi, creds: Credentials, tracker: StatusTracker) -> None:
    # ClaimedJob schema: job_id, platform, payload, resource (ResourceRef)
    job_id = job.get("job_id") or job.get("id", "")
    platform = job.get("platform", "")
    resource = job.get("resource") or {}
    input_ref = (resource.get("input_ref") or resource.get("external_id") or
                 job.get("resource_input_ref") or job.get("input_ref", ""))
    # Job do server trả `resource.resource_id` (không phải `id`). Đây là khoá của kênh xuyên suốt: tracker, giao diện, DB cục bộ.
    resource_id = resource.get("resource_id") or resource.get("id") or job.get("resource_id", "")
    owner = (job.get("owner_account_id") or resource.get("owner_account_id") or
             job.get("account_id") or "")
    channel_name = resource.get("display_name") or resource.get("name") or input_ref
    channel_id = resource_id or input_ref
    start_time = time.monotonic()

    log.info("Processing job=%s platform=%s input_ref=%s", job_id, platform, input_ref[:60])
    tracker.job_started(job_id, platform, channel_id, channel_name, owner)
    lease = asyncio.create_task(_keep_lease(client, job))

    try:
        # items: số liệu từng video; channel_stats: chỉ số của chính kênh (followers/videos_posted/...) nếu nền tảng cho lấy.
        items, channel_stats = await asyncio.to_thread(channel_collect.collect_channel, platform, input_ref, creds, resource_id or None)

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

    except channel_collect.EmptyChannel as exc:
        # Kênh có thật nhưng chưa có video: hoàn tất job (không có gì để đẩy), KHÔNG báo lỗi và không để server thử lại liên tục.
        log.info("Job %s: %s — hoàn tất, không có dữ liệu để đẩy", job_id, exc)
        try:
            await client.complete_job(job, {"items": 0, "note": str(exc)})
        finally:
            tracker.job_done(job_id, platform, channel_id, channel_name, owner, 0, int((time.monotonic() - start_time) * 1000))
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, JobAbort):   # lỗi nghiệp vụ đã dự kiến: một dòng gọn, không traceback
            log.error("Job %s failed: %s", job_id, exc)
        else:
            log.exception("Job %s failed: %s", job_id, exc)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        tracker.job_failed(job_id, platform, channel_id, channel_name, owner, str(exc)[:400], duration_ms)
        try:
            await client.fail_job(job, str(exc)[:400], retryable=getattr(exc, "retryable", True))
        except Exception:  # noqa: BLE001
            pass
    finally:
        lease.cancel()


async def run_once(client: SystemApi, platforms: list[str], wanted: set[str] | None, limit: int,
                   process: Callable[[dict], Awaitable[None]]) -> int:
    """Chạy ngay: xin job cho kênh đã kéo + đang bật (chỉ các kênh trong `wanted` nếu có), xử lý hết rồi dừng — không chờ lịch, không chờ vòng lặp.
    Trả số job đã xử lý. Mỗi kênh bị chặn tối đa 3 lần nhận job trong một lượt (job lỗi tự thử lại sau thời gian chờ của server, không quay vòng ở đây)."""
    ids = [t["id"] for t in channels_db.pulled_targets(platforms) if wanted is None or t["id"] in wanted]
    if not ids:
        log.warning("Chạy ngay: không có kênh nào (đã kéo + đang bật%s) — dừng", " + nằm trong lựa chọn" if wanted is not None else "")
        return 0
    res = await client.enqueue_jobs(ids)
    log.info("Chạy ngay: xin job cho %d kênh (mới %d, đang có sẵn %d, bỏ qua %d)", len(ids), res["created"], res["already_active"], len(res["skipped"]))
    if res["skipped"]:
        log.warning("Kênh bị bỏ qua: %s", ", ".join(f"{s['resource_id']}={s['reason']}" for s in res["skipped"][:10]))
    handled, budget = 0, 3 * len(ids)
    while handled < budget:
        jobs = await client.claim_jobs(platforms, limit=limit, resource_ids=ids)
        if not jobs:
            break
        log.info("Claimed %d jobs", len(jobs))
        await asyncio.gather(*[process(j) for j in jobs], return_exceptions=True)
        handled += len(jobs)
    log.info("Chạy ngay xong: %d job đã xử lý", handled)
    return handled


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_loop(args: argparse.Namespace) -> None:
    creds = channel_collect.load_credentials(args.settings_file)
    client = SystemApi(args.gateway, args.api_key)
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
            await process_job(job, client, creds, tracker)

    if getattr(args, "once", False):
        wanted = set(args.resource_ids) if getattr(args, "resource_ids", None) else None
        try:
            await run_once(client, active_platforms, wanted, args.max_concurrent * 2, _process)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            hint = " — máy chủ chưa có endpoint xin job (cần deploy integration-service bản mới)" if code in (404, 405) else ""
            log.error("Chạy ngay thất bại: HTTP %s %s%s", code, exc.request.url.path, hint)
            sys.exit(1)
        finally:
            await client.aclose()
        return

    plan = EnqueuePlan()
    warned_empty = False
    try:
        while True:
            try:
                # Chỉ làm việc với kênh ĐÃ KÉO và đang bật (đọc lại mỗi vòng: kéo thêm / tắt kênh có hiệu lực ngay).
                ids = [t["id"] for t in channels_db.pulled_targets(active_platforms)]
                if not ids:
                    if not warned_empty:
                        log.warning("Chưa có kênh nào được kéo (hoặc đều đang tắt) — collector chờ. Kéo kênh ở tab Người dùng & Kênh")
                        warned_empty = True
                    await asyncio.sleep(args.poll_interval)
                    continue
                warned_empty = False
                due = plan.due(ids, today_vn())
                if due:
                    res = await client.enqueue_jobs(due)
                    plan.mark(due)
                    log.info("Đợt thu thập %s: xin job cho %d kênh (mới %d, đang có sẵn %d, bỏ qua %d)", today_vn(), len(due),
                             res["created"], res["already_active"], len(res["skipped"]))
                    if res["skipped"]:
                        log.warning("Kênh bị bỏ qua: %s", ", ".join(f"{s['resource_id']}={s['reason']}" for s in res["skipped"][:10]))
                jobs = await client.claim_jobs(active_platforms, limit=args.max_concurrent * 2, resource_ids=ids)
                if jobs:
                    log.info("Claimed %d jobs", len(jobs))
                    await asyncio.gather(*[_process(j) for j in jobs], return_exceptions=True)
                else:
                    log.debug("No jobs available, sleeping %ds", args.poll_interval)
                    await asyncio.sleep(args.poll_interval)
            except httpx.HTTPStatusError as exc:
                log.error("HTTP error: %s %s", exc.response.status_code, exc.response.text[:200])
                await asyncio.sleep(args.poll_interval)
            except Exception as exc:  # noqa: BLE001
                log.exception("Unexpected error in main loop: %s", exc)
                await asyncio.sleep(args.poll_interval)
    finally:
        await client.aclose()


def main() -> None:
    use_utf8_stdio()   # log/ERROR tiếng Việt không được làm sập tiến trình khi stdout/stderr bị chuyển hướng (Windows cp1252)
    ap = argparse.ArgumentParser(description="Channel data collector (fallback_tool)")
    ap.add_argument("--connection-file", default=os.environ.get("COLLECTOR_CONNECTION_FILE", ""),
                    help="File kết nối do Admin cấp cùng key dùng chung (JSON: gateway + api_key + nền tảng). "
                         "Có file này thì không cần --gateway/--api-key")
    ap.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", ""))
    ap.add_argument("--api-key", default=os.environ.get("MACHINE_API_KEY", ""),
                    help="Machine API key (mpk_<key_id>.<secret>)")
    ap.add_argument("--settings-file", default=os.environ.get("COLLECTOR_SETTINGS_FILE", str(channel_collect.DEFAULT_SETTINGS)),
                    help="File cấu hình của tool: youtube_api_keys[], proxies[], fb_cookie")
    ap.add_argument("--poll-interval", type=int, default=30, help="Giây chờ khi không có job")
    ap.add_argument("--max-concurrent", type=int, default=3, help="Số job chạy song song")
    ap.add_argument("--platforms", type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
                    default=None, help="Thu hẹp nền tảng xử lý, cách nhau bằng dấu phẩy "
                                       "(mặc định: mọi nền tảng key được phép VÀ collector có adapter)")
    ap.add_argument("--status-file", default=os.environ.get("COLLECTOR_STATUS_FILE", ""),
                    help="Đường dẫn file JSON ghi trạng thái collector (owner, channel, errors)")
    ap.add_argument("--once", action="store_true", help="Chạy ngay MỘT lượt (xin job + xử lý hết) rồi thoát, không chờ lịch/vòng lặp")
    ap.add_argument("--resource-ids", type=lambda s: [p.strip() for p in s.split(",") if p.strip()], default=None,
                    help="Với --once: chỉ chạy các kênh (resource_id) này, cách nhau bằng dấu phẩy")
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
