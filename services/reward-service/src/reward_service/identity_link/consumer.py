"""Consumer feed Identity — phản ứng CỤC BỘ với sự kiện tài khoản (internal-feed-contract-v1 §6).

Hành vi bắt buộc:
  * con trỏ bền (`feed_cursor`), idempotent theo id (`processed_events`);
  * mỗi event lỗi: retry backoff mũ + jitter có giới hạn; quá giới hạn ⇒ `dead_letters` và TIẾP TỤC event sau;
  * replay dead-letter (`replay_dead_letters`); event cũ đã bị event mới hơn cùng loại + chủ thể thay thế thì bỏ qua;
  * nguồn chết ⇒ giữ nguyên con trỏ, ghi nhận lỗi, thử lại sau; KHÔNG crash, KHÔNG ảnh hưởng request/job đang chạy.

Phản ứng nghiệp vụ (không gọi Identity trên đường request):
  * `reward:access` bị thu / tài khoản bị khoá / bị xoá / phiên bị thu hồi ⇒ ghi denylist + TẠM DỪNG ngay job đang chạy
    của tài khoản đó (thay cho transaction ghi chéo `revoke_discord` cũ); từ chối khởi chạy job mới ngay lập tức;
  * `reward:access` được cấp lại / tài khoản `active` lại ⇒ gỡ denylist tương ứng.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from manager_sdk import FeedClient, FeedError, backoff_delays
from reward_service.identity_link.access import apply_reward_access
from reward_service.store.pool import Pool
from reward_service.store.repositories import feed as feed_repo
from reward_service.store.repositories import projection as proj

logger = logging.getLogger("reward.feed")

SOURCE = "identity"
REWARD_ACCESS = "reward:access"
HANDLED_TYPES = [
    "account.registered", "account.status_changed", "account.role_changed", "account.profile_updated",
    "account.permission_changed", "account.deleted", "session.revoked",
]
_MAX_BATCHES_PER_POLL = 50
_MAX_IDLE_BACKOFF_S = 30.0

PauseCallback = Callable[[str, str], Awaitable[list[int]]]


class PermanentEventError(Exception):
    """Event hỏng/không đủ dữ liệu — thử lại vô ích, vào dead-letter ngay."""


def _parse_time(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class FeedConsumer:
    def __init__(
        self, *, pool: Pool, feed: FeedClient, on_pause: PauseCallback, batch_size: int = 100,
        max_attempts: int = 4, backoff_base: float = 0.2, poll_interval: float = 2.0, sleep=asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._feed = feed
        self._on_pause = on_pause
        self._batch = max(1, min(batch_size, 500))
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._poll_interval = poll_interval
        self._sleep = sleep
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self.processed_count = 0
        self._stop = asyncio.Event()

    # ── Vòng lặp nền ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Chạy tới khi bị huỷ. Không bao giờ ném lỗi ra ngoài (nguồn chết ⇒ chờ rồi thử lại)."""
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — consumer không được làm chết service
                logger.exception("Consumer feed gặp lỗi không mong đợi; thử lại sau")
                self.consecutive_failures += 1
            delay = self._poll_interval
            if self.consecutive_failures:
                delay = min(_MAX_IDLE_BACKOFF_S, self._poll_interval * (2 ** min(self.consecutive_failures, 5)))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    # ── Một lượt kéo ────────────────────────────────────────────────────────────

    async def poll_once(self) -> int:
        """Kéo + xử lý mọi event mới. Trả số event đã xử lý. Nguồn chết ⇒ 0 và con trỏ giữ nguyên."""
        done = 0
        for _ in range(_MAX_BATCHES_PER_POLL):
            async with self._pool.acquire() as conn:
                cursor = await feed_repo.get_cursor(conn, SOURCE)
            try:
                page = await self._feed.feed(after=cursor, limit=self._batch, types=HANDLED_TYPES)
            except FeedError as exc:
                self.last_error = str(exc)
                self.consecutive_failures += 1
                logger.warning("Nguồn feed Identity không khả dụng (%s); giữ nguyên con trỏ %s", exc, cursor)
                return done
            self.last_error = None
            self.consecutive_failures = 0
            for event in page.get("events", []):
                await self._process(event)
                done += 1
            next_cursor = int(page.get("next_cursor") or cursor)
            if next_cursor > cursor:
                async with self._pool.acquire() as conn:
                    await feed_repo.set_cursor(conn, SOURCE, next_cursor)
            if not page.get("has_more") or next_cursor <= cursor:
                break
        async with self._pool.acquire() as conn:
            await proj.prune_session_denials(conn)
        self.processed_count += done
        return done

    async def _process(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("id") or "")
        seq = event.get("seq")
        if not event_id:
            logger.error("Bỏ qua event không có id (seq=%s)", seq)
            return
        async with self._pool.acquire() as conn:
            if await feed_repo.is_processed(conn, event_id):
                return  # at-least-once: nhận lại event đã xử lý ⇒ no-op
        error = await self._apply_with_retry(event)
        if error is None:
            return
        attempts, message = error
        logger.error("Event %s (%s) vào dead-letter sau %s lần: %s", event_id, event.get("type"), attempts, message)
        async with self._pool.acquire() as conn:
            await feed_repo.add_dead_letter(conn, event=event, error=message, attempts=attempts)

    async def _apply_with_retry(self, event: dict[str, Any]) -> tuple[int, str] | None:
        delays = list(backoff_delays(self._max_attempts, base=self._backoff_base, cap=5.0))
        message = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                await self.apply_event(event)
                return None
            except PermanentEventError as exc:
                return attempt, str(exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — mọi lỗi xử lý đều đi qua retry rồi dead-letter
                message = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_attempts:
                    await self._sleep(delays[attempt - 1])
        return self._max_attempts, message

    # ── Xử lý một event (idempotent) ───────────────────────────────────────────

    async def apply_event(self, event: dict[str, Any]) -> None:
        """Áp event: thay đổi DB (upsert, idempotent) → tạm dừng job → đánh dấu đã xử lý. Lỗi ở bất kỳ bước nào
        đều an toàn để chạy lại toàn bộ."""
        event_id, type_ = str(event["id"]), str(event.get("type"))
        data = event.get("data") or {}
        if not isinstance(data, dict):
            raise PermanentEventError("data không phải object")
        pause_reason: str | None = None
        account_id = str(data.get("account_id") or "")
        when = _parse_time(event.get("time"))

        async with self._pool.acquire() as conn:
            if type_ in HANDLED_TYPES and not account_id:
                raise PermanentEventError(f"{type_} thiếu account_id")
            async with conn.transaction():
                if type_ == "account.registered":
                    await proj.upsert_projection(
                        conn, account_id=account_id, username=data.get("username"), role=data.get("role"),
                        status=data.get("status") or "active")
                elif type_ == "account.role_changed":
                    await proj.upsert_projection(conn, account_id=account_id, role=data.get("role"))
                elif type_ == "account.profile_updated":
                    await proj.upsert_projection(conn, account_id=account_id, username=data.get("username"))
                elif type_ == "account.status_changed":
                    status = str(data.get("status") or "")
                    await proj.upsert_projection(conn, account_id=account_id, status=status or None)
                    if status == "active":
                        await proj.lift_denial(conn, account_id=account_id, reason="suspended")
                    elif status:
                        await proj.add_denial(conn, account_id=account_id, reason="suspended", event_id=event_id)
                        pause_reason = "ACCOUNT_SUSPENDED"
                elif type_ == "account.permission_changed":
                    if data.get("permission") == REWARD_ACCESS:
                        granted = data.get("granted")
                        if not isinstance(granted, bool):
                            raise PermanentEventError("permission_changed thiếu granted(bool)")
                        await apply_reward_access(conn, account_id=account_id, granted=granted, event_id=event_id)
                        if not granted:
                            pause_reason = "ROLE_REVOKED"
                elif type_ == "account.deleted":
                    await proj.upsert_projection(conn, account_id=account_id, status="deleted")
                    await proj.add_denial(conn, account_id=account_id, reason="deleted", event_id=event_id)
                    pause_reason = "ACCOUNT_DELETED"
                elif type_ == "session.revoked":
                    await proj.add_denial(
                        conn, account_id=account_id, reason=proj.SESSION_REASON, sid=str(data.get("sid") or ""),
                        denied_at=when or datetime.datetime.now(datetime.timezone.utc), event_id=event_id)
                    pause_reason = "SESSION_REVOKED"

        if pause_reason:
            await self._on_pause(account_id, pause_reason)

        async with self._pool.acquire() as conn:
            await feed_repo.mark_processed(
                conn, event_id=event_id, type_=type_, subject=str(event.get("subject") or account_id),
                seq=event.get("seq"))

    # ── Replay dead-letter ──────────────────────────────────────────────────────

    async def replay_dead_letters(self) -> dict[str, int]:
        """Xử lý lại các dead-letter chưa replay (theo thứ tự seq). Trả `{replayed, skipped, failed}`."""
        result = {"replayed": 0, "skipped": 0, "failed": 0}
        async with self._pool.acquire() as conn:
            letters = await feed_repo.pending_dead_letters(conn)
        for letter in letters:
            event = letter["payload"]
            async with self._pool.acquire() as conn:
                if await feed_repo.is_processed(conn, letter["id"]):
                    await feed_repo.mark_replayed(conn, letter["id"])
                    result["skipped"] += 1
                    continue
                seq = event.get("seq")
                if seq is not None and await feed_repo.superseded(
                        conn, type_=str(event.get("type")), subject=str(event.get("subject") or ""), seq=int(seq)):
                    await feed_repo.mark_replayed(conn, letter["id"])
                    result["skipped"] += 1
                    continue
            error = await self._apply_with_retry(event)
            async with self._pool.acquire() as conn:
                if error is None:
                    await feed_repo.mark_replayed(conn, letter["id"])
                    result["replayed"] += 1
                else:
                    await feed_repo.add_dead_letter(conn, event=event, error=error[1], attempts=error[0])
                    result["failed"] += 1
        return result


if __name__ == "__main__":  # `python -m identity_link.consumer replay` — replay dead-letter bằng tay (vận hành)
    import sys

    async def _main() -> None:
        from reward_service.settings import load_settings
        settings = load_settings()
        if not settings.feed_enabled:
            raise SystemExit("Thiếu REWARD_IDENTITY_FEED_KEY — không có nguồn để replay")
        pool = Pool(settings.database_url)
        await pool.open()
        feed = FeedClient(settings.identity_feed_url, settings.identity_feed_key)

        async def _no_runtime_pause(account_id: str, reason: str) -> list[int]:
            # Tiến trình CLI không giữ WorkerManager: denylist đã đảm bảo engine tự dừng ở vòng lặp kế tiếp.
            return []

        try:
            consumer = FeedConsumer(pool=pool, feed=feed, on_pause=_no_runtime_pause)
            if len(sys.argv) > 1 and sys.argv[1] == "replay":
                print(await consumer.replay_dead_letters())
            else:
                print({"processed": await consumer.poll_once()})
        finally:
            await feed.aclose()
            await pool.close()

    asyncio.run(_main())
