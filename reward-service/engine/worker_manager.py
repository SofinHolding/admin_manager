"""Worker manager — quản lý N job chạy song song, mỗi job 1 task + 1 bể token (`TokenPool`) riêng,
mỗi token một `GatewaySession` riêng — plan D.3 (phần mới, không có ở Node).

Song song tối đa `REWARD_MAX_CONCURRENT_JOBS` job, BẮT BUỘC khác `channel_id` (khoá
`ux_reward_lock_channel` → 409 nếu kẹt — kiểm tra SYNCHRONOUS trong `start()` trước khi trả response,
không đợi task nền). Token Discord CHỈ tồn tại trong closure cục bộ của task job đó — không vào biến
toàn cục, log, hay `repr`.

Mọi kiểm tra có thể thất bại (role, cấu hình, token, lệnh slash) chạy TRƯỚC khi chiếm lock và đặt job
`running` — start thất bại không để lại job kẹt ở `running` mà không có task.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import socket
import time

from crypto import TokenCrypto
from discord.gateway_session import GatewaySession
from discord.http import DiscordHttpClient
from discord.slash_commands import fetch_guild_command
from domain.defaults import DEFAULT_SUCCESS_PATTERN
from engine import confirmation as confirmation_engine
from engine import executor_slash
from engine import reconcile as reconcile_engine
from engine.job_runner import JobRunner, JobRunnerConfig, LockHeldError
from engine.token_pool import TokenPool, TokenSlot
from settings import RewardSettings
from store.pool import Pool
from store.repositories import credentials as credentials_repo
from store.repositories import events as events_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo
from store.repositories import tokens as tokens_repo

logger = logging.getLogger("reward.engine.worker_manager")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class TooManyJobsError(Exception):
    """Đã đạt `REWARD_MAX_CONCURRENT_JOBS` — router trả 429."""


class CredentialInvalidError(Exception):
    """Tài khoản không active/không đủ role, cấu hình chưa `status='valid'` hoặc không có token dùng được
    — router trả 403."""


class CommandNotFoundError(Exception):
    """Không tìm thấy slash command trên Discord lúc start job — router trả 400."""


class _RunningJob:
    __slots__ = ("task", "runner", "http_client", "tokens", "started_at")

    def __init__(self, *, task: asyncio.Task, runner: JobRunner,
                 http_client: DiscordHttpClient, tokens: TokenPool, started_at: float) -> None:
        self.task = task
        self.runner = runner
        self.http_client = http_client
        self.tokens = tokens
        self.started_at = started_at


class WorkerManager:
    """`start`/`pause`/`resume`/`stop` job nền — trạng thái sống trong tiến trình (`uvicorn --workers 1`)."""

    def __init__(self, *, pool: Pool, settings: RewardSettings, crypto: TokenCrypto) -> None:
        self._pool = pool
        self._settings = settings
        self._crypto = crypto
        self._running: dict[int, _RunningJob] = {}
        self._start_lock = asyncio.Lock()

    def is_running(self, job_id: int) -> bool:
        return job_id in self._running

    def running_count(self) -> int:
        return len(self._running)

    async def _build_slots(self, job: dict, token_rows: list[dict], http_client: DiscordHttpClient):
        """Giải mã + kiểm lệnh slash bằng TỪNG token. Token không thấy lệnh (chưa vào guild / sai quyền)
        bị đánh dấu `invalid` và loại khỏi bể. Trả `(slots, command)`."""
        slots: list[TokenSlot] = []
        command = None
        missing: list[dict] = []
        last_exc: Exception | None = None
        for row in token_rows:
            try:
                token = self._crypto.decrypt(row["token_ciphertext"])
            except ValueError as exc:
                last_exc = exc
                continue
            try:
                cmd = await fetch_guild_command(
                    http_client, api_base=self._settings.discord_api_base, token=token,
                    guild_id=job["guild_id"], command_name=job["command_name"])
            except Exception as exc:  # noqa: BLE001 — lỗi mạng: bỏ qua token này, không đánh dấu hỏng
                last_exc = exc
                continue
            if cmd is None:
                missing.append(row)
                continue
            command = command or cmd
            slots.append(TokenSlot(
                token_id=row["id"], label=row["label"] or (row["discord_username"] or f"#{row['id']}"),
                token=token,
                session=GatewaySession(gateway_url=self._settings.discord_gateway_url, token=token)))
        if missing:
            async with self._pool.acquire() as conn:
                for row in missing:
                    await tokens_repo.mark_invalid(
                        conn, token_id=row["id"],
                        last_error=f"Không tìm thấy lệnh /{job['command_name']} trong guild bằng token này")
        if not slots:
            if last_exc is not None and not missing:
                raise last_exc
            raise CommandNotFoundError(f"Không tìm thấy lệnh /{job['command_name']} trong guild")
        return slots, command

    async def start(self, job_id: int) -> None:
        """Khởi động job nền — mọi kiểm tra chặn (429/403/409/400) xảy ra ĐỒNG BỘ trước khi trả về.

        Ném `TooManyJobsError` / `CredentialInvalidError` / `LockHeldError` / `CommandNotFoundError`."""
        async with self._start_lock:
            if job_id in self._running:
                return
            if len(self._running) >= self._settings.max_concurrent_jobs:
                raise TooManyJobsError("Hệ thống đang bận — đã đạt số job chạy song song tối đa")

            async with self._pool.acquire() as conn:
                job = await jobs_repo.get_job(conn, job_id)
                if job is None:
                    raise CredentialInvalidError("Không tìm thấy job")
                account = await conn.fetchrow(
                    "SELECT role, status FROM accounts WHERE id=$1", job["account_id"])
                if (account is None or account["status"] != "active"
                        or account["role"] not in ("discord", "admin")):
                    raise CredentialInvalidError("Tài khoản không còn đủ quyền chạy job (role/status)")
                cred = await credentials_repo.get_by_account(conn, job["account_id"])
                if cred is None or cred["status"] != "valid":
                    raise CredentialInvalidError("Cấu hình Discord chưa xác thực (status != 'valid')")
                token_rows = await tokens_repo.list_usable(conn, job["account_id"])
                if not token_rows:
                    raise CredentialInvalidError(
                        "Chưa có token Discord hợp lệ nào đang bật — hãy thêm hoặc xác thực lại token")

                # Job cũ có thể giữ success_pattern rỗng (chụp lúc cấu hình chưa có pattern) → mọi item sẽ
                # `unknown`. Bổ sung từ cấu hình hiện tại (hoặc mặc định) và ghi lại vào job.
                success_src = job["success_pattern"]
                if job["confirm_mode"] == "reply" and not success_src:
                    success_src = cred["success_pattern"] or DEFAULT_SUCCESS_PATTERN
                    await jobs_repo.set_success_pattern(conn, job_id, success_src)

            http_client = DiscordHttpClient(read_timeout_s=self._settings.http_timeout_s)
            try:
                slots, command = await self._build_slots(job, token_rows, http_client)
            except Exception:
                await http_client.aclose()
                raise
            tokens = TokenPool(slots, delay_ms=job["delay_ms"], jitter_ms=job["jitter_ms"])

            try:
                async with self._pool.acquire() as conn:
                    # Chiếm lock ĐỒNG BỘ ngay trong request — JobRunner.run() re-acquire vô hại (cùng job_id).
                    got_lock = await lock_repo.acquire(
                        conn, job_id=job_id, channel_id=job["channel_id"], account_id=job["account_id"],
                        pid=os.getpid(), host=socket.gethostname())
                    if not got_lock:
                        raise LockHeldError(f"Kênh {job['channel_id']} đang có job khác chạy")
                    await jobs_repo.set_status(conn, job_id, "running", started_at=_now())
            except Exception:
                await tokens.close()
                await http_client.aclose()
                raise

            success_pattern = re.compile(success_src) if success_src else None
            failure_pattern = re.compile(job["failure_pattern"]) if job["failure_pattern"] else None

            executor_config = executor_slash.ExecutorConfig(
                api_base=self._settings.discord_api_base, guild_id=job["guild_id"],
                channel_id=job["channel_id"], account_id=job["account_id"])
            confirmation_config = confirmation_engine.ConfirmationConfig(
                api_base=self._settings.discord_api_base, channel_id=job["channel_id"],
                confirm_mode=job["confirm_mode"],
                confirm_timeout_s=self._settings.confirm_timeout_s,
                confirm_poll_interval_s=self._settings.confirm_poll_interval_s,
                success_pattern=success_pattern, failure_pattern=failure_pattern,
                leveling_bot_id=job["leveling_bot_id"])
            runner_config = JobRunnerConfig(
                channel_id=job["channel_id"], account_id=job["account_id"],
                max_item_retries=job["max_item_retries"],
                unknown_pause_threshold=job["unknown_pause_threshold"],
                confirm_mode=job["confirm_mode"], success_pattern=success_pattern,
                reconcile_grace_s=self._settings.reconcile_grace_s)
            runner = JobRunner(
                pool=self._pool, config=runner_config, http_client=http_client, tokens=tokens,
                command=command, executor_config=executor_config, confirmation_config=confirmation_config)
            # token plaintext chỉ còn sống trong các `TokenSlot` của bể (per-job) — xoá tham chiếu cục bộ ngay.
            del slots, token_rows

            running = _RunningJob(
                task=None, runner=runner, http_client=http_client, tokens=tokens,  # type: ignore[arg-type]
                started_at=time.monotonic())

            async def _run() -> None:
                try:
                    await runner.run(job_id)
                except Exception:  # noqa: BLE001 — lỗi worker không được làm chết tiến trình
                    logger.exception("[Reward] Job %s: lỗi không mong đợi trong worker", job_id)
                    async with self._pool.acquire() as conn:
                        await jobs_repo.set_status(conn, job_id, "paused")
                        await events_repo.add_event(
                            conn, job_id=job_id, type="auto_pause", actor="system",
                            payload={"reason": "WORKER_ERROR"})
                        await lock_repo.release(conn, job_id=job_id)
                finally:
                    await tokens.close()
                    await http_client.aclose()
                    self._running.pop(job_id, None)

            running.task = asyncio.create_task(_run(), name=f"reward-job-{job_id}")
            self._running[job_id] = running

    async def reconcile(self, job_id: int) -> reconcile_engine.ReconcileSummary:
        """Đối soát item `unknown` của job với lịch sử kênh (dùng token đầu tiên còn dùng được của chủ job).

        Chỉ gọi khi job KHÔNG chạy trong tiến trình này (runner đang chạy tự đối soát). Ném `CredentialInvalidError`
        nếu không có token dùng được."""
        async with self._pool.acquire() as conn:
            job = await jobs_repo.get_job(conn, job_id)
            if job is None:
                raise CredentialInvalidError("Không tìm thấy job")
            token_rows = await tokens_repo.list_usable(conn, job["account_id"])
        if not token_rows:
            raise CredentialInvalidError(
                "Chưa có token Discord hợp lệ nào đang bật — cần ít nhất một token để đọc lịch sử kênh")
        token = self._crypto.decrypt(token_rows[0]["token_ciphertext"])
        sp = re.compile(job["success_pattern"]) if job["success_pattern"] else None
        fp = re.compile(job["failure_pattern"]) if job["failure_pattern"] else None
        http_client = DiscordHttpClient(read_timeout_s=self._settings.http_timeout_s)
        try:
            return await reconcile_engine.reconcile_job(
                self._pool, http_client, job=job, authorization=token, api_base=self._settings.discord_api_base,
                success_pattern=sp, failure_pattern=fp, leveling_bot_id=job["leveling_bot_id"],
                grace_s=self._settings.reconcile_grace_s, wait_for_grace=False)
        finally:
            await http_client.aclose()

    async def request_stop(self, job_id: int) -> bool:
        """Set stop_event trên `JobRunner` đang chạy trong TIẾN TRÌNH NÀY — runner tự dừng SAU item
        hiện tại. Trả `False` nếu job không chạy ở đây (đã dừng, hoặc chạy ở tiến trình khác)."""
        running = self._running.get(job_id)
        if running is None:
            return False
        running.runner.request_stop()
        return True

    async def request_pause(self, job_id: int) -> bool:
        """Set pause trên `JobRunner` đang chạy — runner tự dừng SAU item hiện tại, job → `paused`
        (khác `request_stop` → `stopped`). Trả `False` nếu job không chạy trong tiến trình này."""
        running = self._running.get(job_id)
        if running is None:
            return False
        running.runner.request_pause()
        return True

    async def wait_stopped(self, job_id: int, timeout_s: float = 30.0) -> None:
        running = self._running.get(job_id)
        if running is None or running.task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(running.task), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("[Reward] Job %s không dừng trong %ss — huỷ task cưỡng bức", job_id, timeout_s)
            running.task.cancel()

    async def shutdown(self, timeout_s: float = 30.0) -> None:
        """Dừng toàn bộ job đang chạy trong tiến trình này — gọi trong `lifespan` lúc tắt service."""
        job_ids = list(self._running.keys())
        for job_id in job_ids:
            await self.request_stop(job_id)
        for job_id in job_ids:
            await self.wait_stopped(job_id, timeout_s=timeout_s)
