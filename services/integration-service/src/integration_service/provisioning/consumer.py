"""Consumer feed Identity (docs/api/internal-feed-contract-v1.md §6).

  * con trỏ bền `feed_cursor('identity')`; `processed_events` => idempotent theo `id` (at-least-once an toàn);
  * mỗi event xử lý trong MỘT transaction cùng việc đánh dấu đã xử lý và dịch con trỏ;
  * lỗi tạm thời: thử lại với backoff mũ + jitter có giới hạn; quá giới hạn hoặc lỗi vĩnh viễn: `dead_letters`,
    phát `integration.client.provision_failed` (nếu là provisioning) rồi TIẾP TỤC event sau (không chặn hàng đợi);
  * Identity chết: giữ nguyên con trỏ, thử lại sau, KHÔNG crash, không ảnh hưởng phần còn lại của service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import asyncpg
from manager_sdk import FeedClient, FeedError, backoff_delays

from integration_service.clients.service import ClientService
from integration_service.config import Settings
from integration_service.db import Database
from integration_service.feed import events
from integration_service.provisioning import handlers
from integration_service.provisioning.handlers import PermanentEventError

logger = logging.getLogger("integration.provisioning")
SOURCE = "identity"


class ProvisioningConsumer:
    def __init__(self, db: Database, settings: Settings, clients: ClientService, feed: FeedClient,
                 now: Callable[[], datetime], sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._db = db
        self._settings = settings
        self._clients = clients
        self._feed = feed
        self._now = now
        self._sleep = sleep
        self.last_error: str | None = None

    # ── Vòng lặp nền ─────────────────────────────────────────────────────────────
    async def run_forever(self) -> None:
        failures = 0
        while True:
            try:
                handled, has_more = await self.poll_once()
                failures = 0
                self.last_error = None
                if has_more:
                    continue
                await self._sleep(self._settings.provisioning_poll_s)
            except asyncio.CancelledError:
                raise
            except FeedError as exc:
                failures += 1
                self.last_error = str(exc)
                logger.warning("Feed Identity không khả dụng (lần %s): %s", failures, exc)
                await self._sleep(min(60.0, self._settings.provisioning_poll_s * (2 ** min(failures, 6))))
            except Exception as exc:  # noqa: BLE001 — consumer không bao giờ làm sập tiến trình
                failures += 1
                self.last_error = type(exc).__name__
                logger.warning("Consumer provisioning lỗi (%s)", type(exc).__name__)
                await self._sleep(min(60.0, self._settings.provisioning_poll_s * (2 ** min(failures, 6))))

    # ── Một lượt kéo ────────────────────────────────────────────────────────────
    async def cursor(self) -> int:
        async with self._db.connection() as conn:
            return await conn.fetchval("SELECT COALESCE((SELECT seq FROM feed_cursor WHERE source = $1), 0)", SOURCE)

    async def poll_once(self) -> tuple[int, bool]:
        """(số event đã xử lý, còn trang tiếp). FeedError (nguồn chết) được ném ra để vòng lặp lùi nhịp."""
        after = await self.cursor()
        page = await self._feed.feed(after=after, limit=self._settings.provisioning_batch,
                                     types=list(handlers.HANDLED_TYPES))
        count = 0
        for event in page.get("events", []):
            await self.handle(event)
            count += 1
        return count, bool(page.get("has_more"))

    async def handle(self, event: dict) -> None:
        attempts = self._settings.provisioning_max_attempts
        delays = list(backoff_delays(attempts, base=0.2, cap=5.0))
        last = "unknown"
        for attempt in range(1, attempts + 1):
            try:
                async with self._db.transaction() as conn:
                    inserted = await conn.fetchval(
                        "INSERT INTO processed_events (id, type) VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING id",
                        event["id"], event["type"])
                    if inserted is not None:  # chưa xử lý bao giờ
                        await handlers.apply_event(conn, self._clients, event, self._now())
                    await self._advance(conn, event)
                return
            except PermanentEventError as exc:
                await self._dead_letter(event, exc.code, attempt)
                return
            except (asyncpg.PostgresConnectionError, OSError, TimeoutError, asyncio.TimeoutError):
                raise  # DB chết: không dead-letter oan; vòng lặp ngoài lùi nhịp và thử lại từ con trỏ cũ
            except Exception as exc:  # noqa: BLE001
                last = type(exc).__name__
                logger.warning("Xử lý event %s lỗi (lần %s/%s): %s", event.get("id"), attempt, attempts, last)
                if attempt < attempts:
                    await self._sleep(delays[attempt - 1])
        await self._dead_letter(event, f"HANDLER_ERROR:{last}", attempts)

    async def _advance(self, conn: asyncpg.Connection, event: dict) -> None:
        await conn.execute(
            "INSERT INTO feed_cursor (source, seq, updated_at) VALUES ($1, $2, $3) ON CONFLICT (source) DO UPDATE "
            "SET seq = GREATEST(feed_cursor.seq, EXCLUDED.seq), updated_at = EXCLUDED.updated_at",
            SOURCE, int(event.get("seq", 0)), self._now())

    async def _dead_letter(self, event: dict, error: str, attempts: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO dead_letters (id, type, error, attempts, payload) VALUES ($1,$2,$3,$4,$5) "
                "ON CONFLICT (id) DO UPDATE SET error = EXCLUDED.error, attempts = EXCLUDED.attempts",
                event["id"], event["type"], error, attempts, event)
            prov = (event.get("data") or {}).get("provisioning") if event["type"] == "account.registered" else None
            if isinstance(prov, dict):
                await events.client_provision_failed(
                    conn, client_id=str(prov.get("client_id", ""))[:80], account_id=str(event["data"].get("account_id", "")),
                    error_code=error.split(":")[0])
            await self._advance(conn, event)
        logger.error("Event %s vào dead-letter: %s", event.get("id"), error)

    # ── Replay dead-letter ──────────────────────────────────────────────────────
    async def replay_dead_letters(self, event_id: str | None = None) -> dict:
        """Áp lại các event trong dead_letters (sau khi đã sửa nguyên nhân). Thành công => resolved_at."""
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                "SELECT id, payload FROM dead_letters WHERE resolved_at IS NULL AND ($1::text IS NULL OR id = $1) ORDER BY created_at",
                event_id)
        replayed, failed = 0, 0
        for row in rows:
            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            try:
                async with self._db.transaction() as conn:
                    await handlers.apply_event(conn, self._clients, payload, self._now())
                    await conn.execute("INSERT INTO processed_events (id, type) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                       payload["id"], payload["type"])
                    await conn.execute("UPDATE dead_letters SET resolved_at = $2 WHERE id = $1", row["id"], self._now())
                replayed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("Replay %s vẫn lỗi: %s", row["id"], type(exc).__name__)
        return {"replayed": replayed, "failed": failed}
