"""Worker manager — quản lý N job chạy song song, mỗi job 1 task + 1 `GatewaySession` riêng
(token riêng) — plan D.3 (phần mới, không có ở Node).

Song song tối đa `REWARD_MAX_CONCURRENT_JOBS` job, BẮT BUỘC khác `channel_id` (khoá
`ux_reward_lock_channel` → 409 nếu kẹt — kiểm tra SYNCHRONOUS trong `start()` trước khi trả response,
không đợi task nền). Token Discord CHỈ tồn tại trong closure cục bộ của task job đó — không vào biến
toàn cục, log, hay `repr`.
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
from engine import confirmation as confirmation_engine
from engine import executor_slash
from engine.job_runner import JobRunner, JobRunnerConfig, LockHeldError
from settings import RewardSettings
from store.pool import Pool
from store.repositories import credentials as credentials_repo
from store.repositories import events as events_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo

logger = logging.getLogger("reward.engine.worker_manager")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class TooManyJobsError(Exception):
    """Đã đạt `REWARD_MAX_CONCURRENT_JOBS` — router trả 429."""


class CredentialInvalidError(Exception):
    """Tài khoản không active/không đủ role, hoặc credential chưa `status='valid'` — router trả 403."""


class CommandNotFoundError(Exception):
    """Không tìm thấy slash command trên Discord lúc start job — router trả 400."""


class _RunningJob:
    __slots__ = ("task", "runner", "http_client", "session", "started_at")

    def __init__(self, *, task: asyncio.Task, runner: JobRunner,
                 http_client: DiscordHttpClient, session: GatewaySession, started_at: float) -> None:
        self.task = task
        self.runner = runner
        self.http_client = http_client
        self.session = session
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
                cred = await credentials_repo.get_raw(conn, job["account_id"])
                if cred is None or cred["status"] != "valid" or not cred["token_ciphertext"]:
                    raise CredentialInvalidError("Cấu hình Discord chưa xác thực (status != 'valid')")

                # Chiếm lock ĐỒNG BỘ ngay trong request — JobRunner.run() re-acquire vô hại (cùng job_id).
                got_lock = await lock_repo.acquire(
                    conn, job_id=job_id, channel_id=job["channel_id"], account_id=job["account_id"],
                    pid=os.getpid(), host=socket.gethostname())
                if not got_lock:
                    raise LockHeldError(f"Kênh {job['channel_id']} đang có job khác chạy")
                await jobs_repo.set_status(conn, job_id, "running", started_at=_now())

                token = self._crypto.decrypt(cred["token_ciphertext"])

            http_client = DiscordHttpClient(read_timeout_s=self._settings.http_timeout_s)
            try:
                command = await fetch_guild_command(
                    http_client, api_base=self._settings.discord_api_base, token=token,
                    guild_id=job["guild_id"], command_name=job["command_name"])
            except Exception:
                await http_client.aclose()
                async with self._pool.acquire() as conn:
                    await lock_repo.release(conn, job_id=job_id)
                raise
            if command is None:
                await http_client.aclose()
                async with self._pool.acquire() as conn:
                    await lock_repo.release(conn, job_id=job_id)
                raise CommandNotFoundError(f"Không tìm thấy lệnh /{job['command_name']} trong guild")

            session = GatewaySession(gateway_url=self._settings.discord_gateway_url, token=token)
            success_pattern = re.compile(job["success_pattern"]) if job["success_pattern"] else None
            failure_pattern = re.compile(job["failure_pattern"]) if job["failure_pattern"] else None

            executor_config = executor_slash.ExecutorConfig(
                api_base=self._settings.discord_api_base, guild_id=job["guild_id"],
                channel_id=job["channel_id"], user_token=token, read_authorization=token,
                account_id=job["account_id"])
            confirmation_config = confirmation_engine.ConfirmationConfig(
                api_base=self._settings.discord_api_base, channel_id=job["channel_id"],
                read_authorization=token, confirm_mode=job["confirm_mode"],
                confirm_timeout_s=self._settings.confirm_timeout_s,
                confirm_poll_interval_s=self._settings.confirm_poll_interval_s,
                success_pattern=success_pattern, failure_pattern=failure_pattern,
                leveling_bot_id=job["leveling_bot_id"])
            runner_config = JobRunnerConfig(
                channel_id=job["channel_id"], account_id=job["account_id"],
                delay_ms=job["delay_ms"], jitter_ms=job["jitter_ms"],
                max_item_retries=job["max_item_retries"],
                unknown_pause_threshold=job["unknown_pause_threshold"],
                confirm_mode=job["confirm_mode"], success_pattern=success_pattern)
            runner = JobRunner(
                pool=self._pool, config=runner_config, http_client=http_client, session=session,
                command=command, executor_config=executor_config, confirmation_config=confirmation_config)
            # `token` chỉ còn sống trong các dataclass closure trên (per-job) — xoá biến cục bộ ngay.
            del token

            running = _RunningJob(
                task=None, runner=runner, http_client=http_client, session=session,  # type: ignore[arg-type]
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
                    await session.close()
                    await http_client.aclose()
                    self._running.pop(job_id, None)

            running.task = asyncio.create_task(_run(), name=f"reward-job-{job_id}")
            self._running[job_id] = running

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
