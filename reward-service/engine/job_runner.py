"""Vòng lặp job tuần tự — dịch 1-1 từ `src/reward/engine/job-runner.js`.

Luồng chốt trạng thái giữ NGUYÊN: `fail` (trước khi gửi — resolve thất bại/point sai) ⇒ `failed`;
`invoked` ⇒ `mark_sent` → `await_confirmation` → transaction chốt (`success` ⇒ ghi `distributions`
qua `insert_once`); `http_error`/`network_error` ⇒ `classify` (D.4) ⇒ `retry` nếu còn ngân sách
(`attempt_count <= max_item_retries`, item quay về `retrying`), ngược lại `failed`/`unknown`;
`pause_job=True` (401/403/404) ⇒ dừng NGAY, các item còn lại GIỮ NGUYÊN `pending`; chuỗi `unknown`
liên tiếp ≥ `unknown_pause_threshold` ⇒ auto_pause; giãn cách `delay_ms + random(0, jitter_ms)` giữa
mỗi item. `engine/callback.js` của Node đã BỎ (UI đọc thẳng DB/SSE) — không có bước report ở đây.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import random
import socket
from dataclasses import dataclass

from discord.gateway_session import GatewaySession
from discord.http import DiscordHttpClient
from discord.slash_commands import SlashCommand
from domain.error_classifier import classify
from domain.idempotency import next_nonce
from engine import confirmation as confirmation_engine
from engine import executor_slash
from store.pool import Pool
from store.repositories import attempts as attempts_repo
from store.repositories import distributions as dist_repo
from store.repositories import events as events_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo

logger = logging.getLogger("reward.engine.job_runner")

_HEARTBEAT_INTERVAL_S = 5.0


class LockHeldError(Exception):
    """Kênh Discord đang bị runner khác giữ lock (`reward_runner_lock`) — job không thể chạy."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@dataclass(frozen=True)
class JobRunnerConfig:
    channel_id: str
    account_id: str
    delay_ms: int
    jitter_ms: int
    max_item_retries: int
    unknown_pause_threshold: int
    confirm_mode: str
    success_pattern: object | None  # re.Pattern | None


class JobRunner:
    """Một instance = một lần chạy tuần tự của một `job_id` (không chia sẻ giữa các job)."""

    def __init__(
        self, *, pool: Pool, config: JobRunnerConfig,
        http_client: DiscordHttpClient, session: GatewaySession,
        command: SlashCommand, executor_config: executor_slash.ExecutorConfig,
        confirmation_config: confirmation_engine.ConfirmationConfig,
    ) -> None:
        self._pool = pool
        self._config = config
        self._http_client = http_client
        self._session = session
        self._command = command
        self._executor_config = executor_config
        self._confirmation_config = confirmation_config
        self._stop_requested = False
        self._pause_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def request_pause(self) -> None:
        """Dừng SAU item hiện tại và đặt job về `paused` (khác `request_stop` → `stopped`)."""
        self._pause_requested = True

    async def _delay(self) -> None:
        jitter = random.uniform(0, self._config.jitter_ms) if self._config.jitter_ms > 0 else 0
        await asyncio.sleep((self._config.delay_ms + jitter) / 1000)

    async def _heartbeat_loop(self, job_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                try:
                    async with self._pool.acquire() as conn:
                        await lock_repo.renew(conn, job_id=job_id)
                except Exception:  # noqa: BLE001 — renew thất bại không được làm chết vòng lặp chính
                    pass
        except asyncio.CancelledError:
            pass

    async def _check_role_and_credential(self) -> str | None:
        """C.5 quy tắc chéo #2: kiểm T2 (role/status) + `credentials.status='valid'` ở mỗi vòng lặp.

        Trả `None` nếu còn hợp lệ, ngược lại mã lý do `auto_pause` (`ROLE_REVOKED` /
        `CREDENTIAL_INVALID`)."""
        async with self._pool.acquire() as conn:
            account = await conn.fetchrow(
                "SELECT role, status FROM accounts WHERE id=$1", self._config.account_id)
            if account is None or account["status"] != "active" or account["role"] not in ("discord", "admin"):
                return "ROLE_REVOKED"
            cred = await conn.fetchrow(
                "SELECT status FROM reward_discord_credentials WHERE account_id=$1", self._config.account_id)
            if cred is None or cred["status"] != "valid":
                return "CREDENTIAL_INVALID"
        return None

    async def run(self, job_id: int) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            got = await lock_repo.acquire(
                conn, job_id=job_id, channel_id=self._config.channel_id,
                account_id=self._config.account_id, pid=os.getpid(), host=socket.gethostname())
            if not got:
                raise LockHeldError(f"Another runner holds the lock on channel {self._config.channel_id}")
            await jobs_repo.set_status(conn, job_id, "running", started_at=_now())

        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))

        if self._config.confirm_mode == "reply" and not self._config.success_pattern:
            async with self._pool.acquire() as conn:
                await events_repo.add_event(conn, job_id=job_id, type="warn_no_pattern", actor="system")
            logger.warning(
                "[Reward] confirm_mode=reply nhưng success_pattern rỗng → mọi item sẽ dừng ở \"unknown\".")

        paused = False
        consecutive_unknown = 0

        try:
            while not (self._stop_requested or self._pause_requested):
                revoke_reason = await self._check_role_and_credential()
                if revoke_reason is not None:
                    paused = True
                    async with self._pool.acquire() as conn:
                        await events_repo.add_event(
                            conn, job_id=job_id, type="auto_pause", actor="system",
                            payload={"reason": revoke_reason})
                    logger.warning("[Reward] Tạm dừng job do %s.", revoke_reason)
                    break
                async with self._pool.acquire() as conn:
                    claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
                if claim is None:
                    break
                item = claim["item"]
                attempt = claim["attempt"]

                async with self._pool.acquire() as resolve_conn:
                    obs = await executor_slash.execute(
                        item, attempt, http_client=self._http_client, session=self._session,
                        config=self._executor_config, command=self._command, pool_conn=resolve_conn)

                item_unknown = False

                if obs.outcome == "fail":
                    # Lỗi TRƯỚC khi gửi (không resolve được / point không hợp lệ) → failed, 0 request.
                    async with self._pool.acquire() as conn, conn.transaction():
                        await attempts_repo.finalize(
                            conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                            error_code=obs.failure_code, error_message=obs.failure_code)
                        await items_repo.finalize(
                            conn, item_id=item["id"], status="failed", confirmation_level="none",
                            failure_code=obs.failure_code, failure_message=obs.failure_code,
                            resolved_user_id=obs.resolved_user_id, resolve_level=obs.resolve_level)
                    consecutive_unknown = 0

                elif obs.outcome == "invoked":
                    async with self._pool.acquire() as conn:
                        await attempts_repo.mark_sent(
                            conn, attempt_id=attempt["id"], command_text=obs.command_text,
                            message_id=None, http_status=obs.http_status, posted_at=_now())

                    c = await confirmation_engine.await_confirmation(
                        self._http_client, config=self._confirmation_config, message_id=obs.anchor_message_id,
                        item=item, expected_bot_id=obs.application_id)

                    async with self._pool.acquire() as conn, conn.transaction():
                        if c.status == "success":
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="confirmed",
                                reply_message_id=c.reply_message_id, reply_author_id=c.reply_author_id,
                                reply_excerpt=c.reply_excerpt, link_mode=c.link_mode)
                            await items_repo.finalize(
                                conn, item_id=item["id"], status="success", confirmation_level=c.confirmation_level,
                                resolved_user_id=obs.resolved_user_id, resolve_level=obs.resolve_level,
                                resolved_by="system", first_sent_at=_now())
                            await dist_repo.insert_once(
                                conn, item_id=item["id"], attempt_id=attempt["id"], job_id=job_id,
                                account_id=self._config.account_id, discord_user_id=obs.resolved_user_id,
                                point=item["point"],
                                evidence={
                                    "level": c.confirmation_level, "message_id": obs.anchor_message_id,
                                    "reply_message_id": c.reply_message_id, "excerpt": c.reply_excerpt,
                                })
                        elif c.status == "failed":
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="rejected",
                                reply_message_id=c.reply_message_id, reply_author_id=c.reply_author_id,
                                reply_excerpt=c.reply_excerpt, link_mode=c.link_mode, error_code=c.failure_code)
                            await items_repo.finalize(
                                conn, item_id=item["id"], status="failed", confirmation_level=c.confirmation_level,
                                failure_code=c.failure_code, failure_message=c.failure_message,
                                resolved_user_id=obs.resolved_user_id, resolve_level=obs.resolve_level,
                                first_sent_at=_now())
                        else:
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="aborted",
                                reply_message_id=c.reply_message_id, reply_author_id=c.reply_author_id,
                                reply_excerpt=c.reply_excerpt, link_mode=c.link_mode, error_code=c.failure_code)
                            await items_repo.finalize(
                                conn, item_id=item["id"], status="unknown", confirmation_level=c.confirmation_level,
                                failure_code=c.failure_code, resolved_user_id=obs.resolved_user_id,
                                resolve_level=obs.resolve_level, first_sent_at=_now())
                    item_unknown = c.status == "unknown"
                    consecutive_unknown = consecutive_unknown + 1 if item_unknown else 0

                else:  # 'http_error' | 'network_error' → phân loại theo D.4
                    cls = classify(http_status=obs.http_status, exc=obs.error_exc)
                    budget_left = item["attempt_count"] <= self._config.max_item_retries

                    if cls.decision == "retry" and budget_left:
                        async with self._pool.acquire() as conn, conn.transaction():
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                                http_status=obs.http_status, error_code=cls.failure_code)
                            await items_repo.set_status(conn, item["id"], "retrying")
                        # consecutive_unknown giữ nguyên — retry chưa phải kết quả cuối cùng
                    else:
                        final_status = "failed" if cls.decision in ("fail", "retry") else "unknown"
                        async with self._pool.acquire() as conn, conn.transaction():
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                                http_status=obs.http_status, error_code=cls.failure_code)
                            await items_repo.finalize(
                                conn, item_id=item["id"], status=final_status, confirmation_level="none",
                                failure_code=cls.failure_code, failure_message=cls.failure_code,
                                resolved_user_id=obs.resolved_user_id, resolve_level=obs.resolve_level)
                        item_unknown = final_status == "unknown"
                        consecutive_unknown = consecutive_unknown + 1 if item_unknown else 0
                        if cls.pause_job:
                            paused = True
                            async with self._pool.acquire() as conn:
                                await events_repo.add_event(
                                    conn, job_id=job_id, type="auto_pause", actor="system",
                                    payload={"reason": cls.failure_code})
                            logger.warning(
                                "[Reward] Tạm dừng job do lỗi %s. Các item còn lại giữ pending.", cls.failure_code)
                            break

                if item_unknown and consecutive_unknown >= self._config.unknown_pause_threshold:
                    paused = True
                    async with self._pool.acquire() as conn:
                        await events_repo.add_event(
                            conn, job_id=job_id, type="auto_pause", actor="system",
                            payload={"reason": "unknown_streak", "count": consecutive_unknown})
                    logger.warning(
                        "[Reward] Tạm dừng job: %s item liên tiếp \"unknown\".", consecutive_unknown)
                    break

                if not (self._stop_requested or self._pause_requested):
                    await self._delay()

            async with self._pool.acquire() as conn:
                if paused or self._pause_requested:
                    await jobs_repo.set_status(conn, job_id, "paused")
                elif self._stop_requested:
                    await jobs_repo.set_status(conn, job_id, "stopped")
                    await events_repo.add_event(conn, job_id=job_id, type="stopped", actor="operator")
                else:
                    await jobs_repo.set_status(conn, job_id, "completed", finished_at=_now())
        finally:
            heartbeat_task.cancel()
            async with self._pool.acquire() as conn:
                await lock_repo.release(conn, job_id=job_id)

        async with self._pool.acquire() as conn:
            return await items_repo.counts(conn, job_id)
