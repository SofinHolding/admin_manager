"""Vòng lặp job tuần tự — dịch 1-1 từ `src/reward/engine/job-runner.js`, mở rộng xoay vòng nhiều token.

Luồng chốt trạng thái giữ NGUYÊN: `fail` (trước khi gửi — resolve thất bại/point sai) ⇒ `failed`;
`invoked` ⇒ `mark_sent` → `await_confirmation` → transaction chốt (`success` ⇒ ghi `distributions`
qua `insert_once`); `http_error`/`network_error` ⇒ `classify` (D.4) ⇒ `retry` nếu còn ngân sách
(`attempt_count <= max_item_retries`, item quay về `retrying`), ngược lại `failed`/`unknown`.

Xoay vòng token (`engine/token_pool.py`): mỗi item dùng 1 token chọn ngẫu nhiên, xen kẽ. Lỗi gắn với TOKEN
(401/403/404 — Discord từ chối, lệnh chưa hề chạy; Gateway không kết nối — chưa gửi gì) ⇒ vô hiệu/nghỉ token
đó và trả item về hàng đợi để token khác gửi (không tốn ngân sách retry). Job chỉ `auto_pause` khi KHÔNG còn
token dùng được hoặc chuỗi `unknown` toàn job quá dài — các item còn lại giữ `pending`.

Chống kẹt `unknown` (`engine/reconcile.py`): `unknown` ≠ chưa cộng điểm. Trước khi kết luận chuỗi `unknown` và
trước khi kết thúc/tạm dừng job, runner ĐỐI SOÁT với lịch sử kênh: reply thật tìm thấy ⇒ item thành `success`
(không gửi lại ⇒ không cộng đôi); đã quét hết kênh quá thời gian chờ mà không có reply ⇒ đánh dấu `no_reply`
để operator gửi lại bằng 1 lần bấm (`POST /jobs/{id}/retry-unknown`).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import socket
from dataclasses import dataclass

from discord.http import DiscordHttpClient
from discord.slash_commands import SlashCommand
from domain.error_classifier import classify
from domain.idempotency import next_nonce
from engine import confirmation as confirmation_engine
from engine import executor_slash
from engine import reconcile as reconcile_engine
from engine.token_pool import TokenPool, TokenSlot
from store.pool import Pool
from store.repositories import attempts as attempts_repo
from store.repositories import distributions as dist_repo
from store.repositories import events as events_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo
from store.repositories import tokens as tokens_repo

logger = logging.getLogger("reward.engine.job_runner")

_HEARTBEAT_INTERVAL_S = 5.0
_RATE_LIMIT_COOLDOWN_S = 60.0   # token bị 429 (sau khi http client đã tự retry) → nghỉ trước khi dùng lại
_SESSION_COOLDOWN_S = 20.0      # Gateway của token không kết nối được → nghỉ rồi thử lại
_MAX_SESSION_FAILURES = 3       # ... quá số lần liên tiếp này thì loại token khỏi lần chạy


class LockHeldError(Exception):
    """Kênh Discord đang bị runner khác giữ lock (`reward_runner_lock`) — job không thể chạy."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@dataclass(frozen=True)
class JobRunnerConfig:
    channel_id: str
    account_id: str
    max_item_retries: int
    unknown_pause_threshold: int
    confirm_mode: str
    success_pattern: object | None  # re.Pattern | None
    reconcile_grace_s: float = 45.0


class JobRunner:
    """Một instance = một lần chạy tuần tự của một `job_id` (không chia sẻ giữa các job)."""

    def __init__(
        self, *, pool: Pool, config: JobRunnerConfig,
        http_client: DiscordHttpClient, tokens: TokenPool,
        command: SlashCommand, executor_config: executor_slash.ExecutorConfig,
        confirmation_config: confirmation_engine.ConfirmationConfig,
    ) -> None:
        self._pool = pool
        self._config = config
        self._http_client = http_client
        self._tokens = tokens
        self._command = command
        self._executor_config = executor_config
        self._confirmation_config = confirmation_config
        self._stop_requested = False
        self._pause_requested = False
        self._unknown_ids: list[int] = []  # item `unknown` liên tiếp toàn job (mọi token)

    def request_stop(self) -> None:
        self._stop_requested = True

    def request_pause(self) -> None:
        """Dừng SAU item hiện tại và đặt job về `paused` (khác `request_stop` → `stopped`)."""
        self._pause_requested = True

    def _should_stop(self) -> bool:
        return self._stop_requested or self._pause_requested

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

    async def _auto_pause(self, job_id: int, payload: dict) -> None:
        async with self._pool.acquire() as conn:
            await events_repo.add_event(conn, job_id=job_id, type="auto_pause", actor="system", payload=payload)

    async def _disable_token(
        self, job_id: int, slot: TokenSlot, *, reason: str, detail: str, mark_invalid: bool,
    ) -> None:
        """Vô hiệu token cho phần còn lại của lần chạy (+ đánh dấu `invalid` trong DB nếu token hỏng thật)."""
        self._tokens.disable(slot, reason)
        async with self._pool.acquire() as conn:
            if mark_invalid:
                await tokens_repo.mark_invalid(conn, token_id=slot.token_id, last_error=detail)
            await events_repo.add_event(
                conn, job_id=job_id, type="token_disabled", actor="system",
                payload={"token_id": slot.token_id, "label": slot.label, "reason": reason,
                         "remaining": len(self._tokens.usable())})
        logger.warning(
            "[Reward] Job %s: vô hiệu token #%s (%s) — %s. Còn %s token.",
            job_id, slot.token_id, slot.label, reason, len(self._tokens.usable()))

    async def _sweep(self, job_id: int, *, wait: bool) -> None:
        """Đối soát item `unknown` với lịch sử kênh (xem `engine/reconcile.py`). Không bao giờ ném lỗi."""
        if self._config.confirm_mode != "reply":
            return
        slots = self._tokens.usable() or self._tokens.slots
        if not slots:
            return
        cc = self._confirmation_config
        try:
            async with self._pool.acquire() as conn:
                job = await jobs_repo.get_job(conn, job_id)
            summary = await reconcile_engine.reconcile_job(
                self._pool, self._http_client, job=job, authorization=slots[0].token, api_base=cc.api_base,
                success_pattern=cc.success_pattern, failure_pattern=cc.failure_pattern,
                leveling_bot_id=cc.leveling_bot_id, grace_s=self._config.reconcile_grace_s,
                wait_for_grace=wait, should_stop=self._should_stop)
            if summary.success or summary.failed or summary.no_reply:
                async with self._pool.acquire() as conn:
                    await events_repo.add_event(
                        conn, job_id=job_id, type="reconcile", actor="system",
                        payload={"success": summary.success, "failed": summary.failed, "no_reply": summary.no_reply,
                                 "waiting": summary.waiting, "inconclusive": summary.inconclusive})
        except Exception:  # noqa: BLE001 — đối soát lỗi không được làm hỏng job
            logger.exception("[Reward] Job %s: đối soát unknown thất bại", job_id)

    async def _still_unknown(self, ids: list[int]) -> list[int]:
        if not ids:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM reward_items WHERE id = ANY($1::bigint[]) AND status='unknown'", ids)
        alive = {r["id"] for r in rows}
        return [i for i in ids if i in alive]

    async def _resettle(self, job_id: int, slot: TokenSlot) -> None:
        """Đối soát NGAY rồi tính lại chuỗi unknown: item hoá ra đã thành công không còn bị tính là unknown."""
        await self._sweep(job_id, wait=False)
        slot.unknown_ids = await self._still_unknown(slot.unknown_ids)
        self._unknown_ids = await self._still_unknown(self._unknown_ids)

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
        threshold = self._config.unknown_pause_threshold
        global_limit = threshold * 2

        try:
            while not self._should_stop():
                slot = await self._tokens.acquire(self._should_stop)
                if slot is None:
                    if not self._should_stop():  # hết token dùng được (không phải do stop/pause)
                        paused = True
                        await self._auto_pause(job_id, {"reason": "NO_USABLE_TOKEN"})
                        logger.warning("[Reward] Tạm dừng job: không còn token Discord nào dùng được.")
                    break

                # Kiểm sau khi chờ nhịp token (có thể chờ cả giây): role bị thu trong lúc chờ phải dừng NGAY,
                # không được claim/gửi thêm item nào.
                revoke_reason = await self._check_role_and_credential()
                if revoke_reason is not None:
                    paused = True
                    await self._auto_pause(job_id, {"reason": revoke_reason})
                    logger.warning("[Reward] Tạm dừng job do %s.", revoke_reason)
                    break

                async with self._pool.acquire() as conn:
                    claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
                    if claim is not None:
                        await attempts_repo.set_token(
                            conn, attempt_id=claim["attempt"]["id"], token_id=slot.token_id)
                        await tokens_repo.touch_used(conn, slot.token_id)
                if claim is None:
                    break
                item = claim["item"]
                attempt = claim["attempt"]

                async with self._pool.acquire() as resolve_conn:
                    obs = await executor_slash.execute(
                        item, attempt, http_client=self._http_client, session=slot.session, token=slot.token,
                        config=self._executor_config, command=self._command, pool_conn=resolve_conn)

                item_unknown = False
                item_retried = False  # retry chưa phải kết quả cuối cùng → không đổi chuỗi unknown của token

                if obs.outcome == "session_error":
                    # Gateway của token không kết nối được ⇒ CHƯA gửi gì: trả item về hàng đợi (không tốn ngân
                    # sách retry, không phải `unknown`), cho token nghỉ rồi để token khác gửi.
                    slot.session_failures += 1
                    self._tokens.cool(slot, _SESSION_COOLDOWN_S)
                    async with self._pool.acquire() as conn, conn.transaction():
                        await attempts_repo.finalize(
                            conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                            error_code="SESSION_ERROR")
                        await items_repo.requeue(conn, item["id"])
                    if slot.session_failures >= _MAX_SESSION_FAILURES:
                        await self._disable_token(
                            job_id, slot, reason="SESSION_ERROR",
                            detail=f"Gateway không kết nối được {slot.session_failures} lần liên tiếp",
                            mark_invalid=False)
                        if not self._tokens.has_usable():
                            paused = True
                            await self._auto_pause(job_id, {"reason": "SESSION_ERROR"})
                            break
                    continue

                slot.session_failures = 0

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

                elif obs.outcome == "invoked":
                    async with self._pool.acquire() as conn:
                        await attempts_repo.mark_sent(
                            conn, attempt_id=attempt["id"], command_text=obs.command_text,
                            message_id=obs.anchor_message_id, http_status=obs.http_status, posted_at=_now())

                    c = await confirmation_engine.await_confirmation(
                        self._http_client, config=self._confirmation_config, authorization=slot.token,
                        message_id=obs.anchor_message_id, item=item, expected_bot_id=obs.application_id)

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
                                    "token_id": slot.token_id,
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

                else:  # 'http_error' | 'network_error' → phân loại theo D.4
                    cls = classify(http_status=obs.http_status, exc=obs.error_exc)
                    budget_left = item["attempt_count"] <= self._config.max_item_retries

                    if cls.pause_job:
                        # 401/403/404: Discord TỪ CHỐI request (lệnh chưa hề chạy) → lỗi gắn với token này.
                        # Chỉ 401 chứng minh token hỏng ⇒ mới ghi `invalid` vào DB; 403/404 có thể do kênh/quyền
                        # (sai Channel ID…) nên chỉ loại token khỏi lần chạy này.
                        await self._disable_token(
                            job_id, slot, reason=cls.failure_code,
                            detail=f"{cls.failure_code} (HTTP {obs.http_status})",
                            mark_invalid=obs.http_status == 401)
                        if self._tokens.has_usable():
                            # Còn token khác → gửi lại item bằng token khác, không tốn ngân sách retry.
                            async with self._pool.acquire() as conn, conn.transaction():
                                await attempts_repo.finalize(
                                    conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                                    http_status=obs.http_status, error_code=cls.failure_code)
                                await items_repo.requeue(conn, item["id"])
                            continue
                        async with self._pool.acquire() as conn, conn.transaction():
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                                http_status=obs.http_status, error_code=cls.failure_code)
                            await items_repo.finalize(
                                conn, item_id=item["id"], status="failed", confirmation_level="none",
                                failure_code=cls.failure_code, failure_message=cls.failure_code,
                                resolved_user_id=obs.resolved_user_id, resolve_level=obs.resolve_level)
                        paused = True
                        await self._auto_pause(job_id, {"reason": cls.failure_code})
                        logger.warning(
                            "[Reward] Tạm dừng job do lỗi %s. Các item còn lại giữ pending.", cls.failure_code)
                        break

                    if cls.decision == "retry" and budget_left:
                        if obs.http_status == 429:
                            self._tokens.cool(slot, _RATE_LIMIT_COOLDOWN_S)
                        async with self._pool.acquire() as conn, conn.transaction():
                            await attempts_repo.finalize(
                                conn, attempt_id=attempt["id"], phase="aborted", command_text=obs.command_text,
                                http_status=obs.http_status, error_code=cls.failure_code)
                            await items_repo.set_status(conn, item["id"], "retrying")
                        item_retried = True
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

                # Chuỗi unknown tính THEO TỪNG TOKEN và TOÀN JOB: success/failed/fail-trước-gửi xoá chuỗi.
                if item_unknown:
                    slot.unknown_ids.append(item["id"])
                    self._unknown_ids.append(item["id"])
                elif not item_retried:
                    slot.unknown_ids = []
                    self._unknown_ids = []

                if item_unknown and (len(slot.unknown_ids) >= threshold or len(self._unknown_ids) >= global_limit):
                    # Trước khi kết luận "token/hệ thống có vấn đề": kiểm lại lịch sử kênh — reply tới muộn
                    # sẽ biến các item này thành success và chuỗi unknown tự tan.
                    await self._resettle(job_id, slot)

                    if len(slot.unknown_ids) >= threshold:
                        streak = len(slot.unknown_ids)
                        await self._disable_token(
                            job_id, slot, reason="unknown_streak",
                            detail=f"{streak} item liên tiếp không xác nhận được", mark_invalid=False)
                        if not self._tokens.has_usable():
                            paused = True
                            await self._auto_pause(job_id, {"reason": "unknown_streak", "count": streak})
                            logger.warning("[Reward] Tạm dừng job: %s item liên tiếp \"unknown\".", streak)
                            break
                    if len(self._unknown_ids) >= global_limit:
                        paused = True
                        await self._auto_pause(
                            job_id, {"reason": "unknown_streak", "count": len(self._unknown_ids), "scope": "job"})
                        logger.warning(
                            "[Reward] Tạm dừng job: %s item unknown liên tiếp trên toàn job.", len(self._unknown_ids))
                        break

                if not slot.disabled:
                    self._tokens.release(slot)  # token chỉ được dùng lại sau delay + jitter của riêng nó

            # Trước khi đóng trạng thái: đối soát lần cuối (chờ đủ thời gian cho reply tới muộn nếu job tự kết
            # thúc/tự tạm dừng; nếu operator bấm dừng/tạm dừng thì chỉ quét nhanh, không chờ).
            await self._sweep(job_id, wait=not self._should_stop())

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
