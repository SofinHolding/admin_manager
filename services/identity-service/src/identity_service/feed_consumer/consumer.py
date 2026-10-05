"""Consumer feed Integration -> cập nhật kết quả provisioning client (internal-feed-contract-v1.md §6).

Hành vi bắt buộc (mỗi mục có test):
  * Con trỏ bền `feed_cursor(source, seq)`; idempotent theo event id (`processed_events`).
  * Xử lý một event = một transaction (đánh dấu đã xử lý + cập nhật `integration_provisioning`); lỗi thì rollback cả hai.
  * Lỗi xử lý: retry backoff mũ + jitter, giới hạn số lần; quá giới hạn => `dead_letters` và TIẾP TỤC event sau.
  * Nguồn chết/lỗi/trả sai định dạng: giữ nguyên con trỏ, backoff, thử lại; KHÔNG crash, không ảnh hưởng phần còn lại.
  * Thiếu `IDENTITY_INTEGRATION_FEED_URL`/`IDENTITY_INTEGRATION_FEED_KEY` => consumer tắt (service vẫn chạy).
  * Có lệnh replay dead-letter: `python -m identity_service.feed_consumer replay`.
Không log service key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

import asyncpg
import httpx

from identity_service.config import Settings
from identity_service.db import Database
from identity_service.security import utcnow

logger = logging.getLogger("identity.feed_consumer")

SOURCE = "integration"
EVENT_TYPES = ("integration.client.provisioned", "integration.client.provision_failed")
_PAGE = 100
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class EventError(Exception):
    """Event không áp dụng được (dữ liệu sai/không khớp): retry rồi dead-letter."""


class SourceError(Exception):
    """Nguồn feed không lấy được/sai định dạng."""


async def _apply(conn: asyncpg.Connection, ev: dict, now: datetime) -> None:
    data = ev.get("data")
    if not isinstance(data, dict):
        raise EventError("data không phải object")
    client_id, account_id = data.get("client_id"), data.get("account_id")
    if not isinstance(client_id, str) or not isinstance(account_id, str):
        raise EventError("thiếu client_id/account_id")
    if ev.get("type") == "integration.client.provisioned":
        status, error_code = "provisioned", ""
    elif ev.get("type") == "integration.client.provision_failed":
        raw = data.get("error_code")
        status, error_code = "failed", (raw if isinstance(raw, str) and _ERROR_CODE.match(raw) else "PROVISION_FAILED")
    else:
        raise EventError("loại event không được hỗ trợ")
    tag = await conn.execute(
        "UPDATE integration_provisioning SET status = $3, error_code = $4, updated_at = $5 "
        "WHERE client_id = $1 AND account_id = $2", client_id, account_id, status, error_code, now)
    if not tag.endswith(" 1"):
        raise EventError("client_id không có trong yêu cầu provisioning của account này")


class IntegrationFeedConsumer:
    def __init__(self, db: Database, settings: Settings, *, now: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rand: Callable[[], float] = random.random) -> None:
        self.db = db
        self.settings = settings
        self.now = now
        self._sleep = sleep
        self._rand = rand

    @property
    def enabled(self) -> bool:
        return bool(self.settings.integration_feed_url and self.settings.integration_feed_key)

    # ── nguồn ───────────────────────────────────────────────────────────────────

    async def _fetch(self, after: int) -> dict:
        s = self.settings
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False, trust_env=False) as http:
                resp = await http.get(f"{s.integration_feed_url}/internal/v1/feed",
                                      params={"after": after, "limit": _PAGE, "types": ",".join(EVENT_TYPES)},
                                      headers={"X-Service-Key": s.integration_feed_key})
        except httpx.HTTPError as exc:
            raise SourceError(type(exc).__name__) from None
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code}")
        try:
            body = resp.json()
            events, nxt = body["events"], body["next_cursor"]
            if not isinstance(events, list) or not isinstance(nxt, int) or isinstance(nxt, bool):
                raise ValueError
            for ev in events:
                if not isinstance(ev, dict) or not isinstance(ev.get("id"), str) or not isinstance(ev.get("seq"), int):
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SourceError("phản hồi sai định dạng") from None
        return body

    # ── xử lý ───────────────────────────────────────────────────────────────────

    async def _cursor(self) -> int:
        async with self.db.connection() as conn:
            return await conn.fetchval("SELECT seq FROM feed_cursor WHERE source = $1", SOURCE) or 0

    async def _set_cursor(self, seq: int) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO feed_cursor (source, seq) VALUES ($1, $2) "
                "ON CONFLICT (source) DO UPDATE SET seq = GREATEST(feed_cursor.seq, EXCLUDED.seq)", SOURCE, seq)

    async def _process(self, ev: dict) -> str:
        """'applied' | 'duplicate'. Lỗi nghiệp vụ => EventError (transaction rollback, kể cả dấu đã xử lý)."""
        async with self.db.transaction() as conn:
            fresh = await conn.fetchval(
                "INSERT INTO processed_events (id, source) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING RETURNING 1",
                ev["id"], SOURCE)
            if not fresh:
                return "duplicate"
            await _apply(conn, ev, self.now())
        return "applied"

    def _backoff(self, attempt: int) -> float:
        s = self.settings
        return min(s.feed_backoff_cap_s, s.feed_backoff_base_s * (2 ** attempt)) * (0.5 + self._rand() / 2)

    async def _dead_letter(self, ev: dict, error: str, attempts: int) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO dead_letters (id, source, type, error, attempts, payload) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) ON CONFLICT (id) DO UPDATE SET "
                "error = EXCLUDED.error, attempts = EXCLUDED.attempts, payload = EXCLUDED.payload, replayed_at = NULL",
                ev["id"], SOURCE, str(ev.get("type", ""))[:100], error[:500], attempts,
                json.dumps(ev, ensure_ascii=False, default=str))
        logger.error("Event %s vào dead-letter sau %d lần: %s", ev["id"], attempts, error)

    async def _handle(self, ev: dict, stats: dict[str, int]) -> None:
        last = ""
        for attempt in range(self.settings.feed_max_attempts):
            try:
                stats[await self._process(ev)] += 1
                return
            except EventError as exc:
                last = str(exc)
            except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
                last = type(exc).__name__
            if attempt + 1 < self.settings.feed_max_attempts:
                await self._sleep(self._backoff(attempt))
        await self._dead_letter(ev, last, self.settings.feed_max_attempts)
        stats["dead"] += 1

    async def poll_once(self) -> dict[str, int]:
        """Kéo hết các trang hiện có. Ném SourceError nếu nguồn lỗi (con trỏ giữ nguyên phần đã xử lý)."""
        stats = {"applied": 0, "duplicate": 0, "dead": 0, "pages": 0}
        cursor = await self._cursor()
        while True:
            body = await self._fetch(cursor)
            stats["pages"] += 1
            for ev in body["events"]:
                if ev["seq"] <= cursor:
                    continue
                await self._handle(ev, stats)
                cursor = ev["seq"]
                await self._set_cursor(cursor)
            nxt = body["next_cursor"]
            if nxt > cursor:
                cursor = nxt
                await self._set_cursor(cursor)
            if not body.get("has_more") or not body["events"]:
                return stats

    async def run_forever(self) -> None:
        if not self.enabled:
            logger.info("Consumer feed Integration tắt (thiếu IDENTITY_INTEGRATION_FEED_URL/KEY)")
            return
        failures = 0
        while True:
            try:
                await self.poll_once()
                failures = 0
                delay = self.settings.feed_poll_interval_s
            except asyncio.CancelledError:
                raise
            except SourceError as exc:
                failures += 1
                delay = self._backoff(min(failures, 8))
                logger.warning("Nguồn feed Integration lỗi (%s); thử lại sau %.1fs", exc, delay)
            except Exception as exc:  # noqa: BLE001 — DB tạm chết...: không crash, thử lại
                failures += 1
                delay = self._backoff(min(failures, 8))
                logger.warning("Consumer feed lỗi vòng: %s", type(exc).__name__)
            await self._sleep(delay)

    # ── replay dead-letter ──────────────────────────────────────────────────────

    async def replay_dead_letters(self, event_id: str | None = None) -> dict[str, int]:
        async with self.db.connection() as conn:
            rows = await conn.fetch(
                "SELECT id, payload FROM dead_letters WHERE source = $1 AND replayed_at IS NULL "
                "AND ($2::text IS NULL OR id = $2) ORDER BY created_at, id", SOURCE, event_id)
        out = {"replayed": 0, "failed": 0}
        for row in rows:
            ev = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            try:
                async with self.db.transaction() as conn:
                    await conn.execute("INSERT INTO processed_events (id, source) VALUES ($1, $2) "
                                       "ON CONFLICT (id) DO NOTHING", ev["id"], SOURCE)
                    await _apply(conn, ev, self.now())
                    await conn.execute("UPDATE dead_letters SET replayed_at = $2 WHERE id = $1", ev["id"], self.now())
                out["replayed"] += 1
            except EventError as exc:
                async with self.db.connection() as conn:
                    await conn.execute("UPDATE dead_letters SET error = $2 WHERE id = $1", ev["id"], str(exc)[:500])
                out["failed"] += 1
        return out


def main(argv: list[str] | None = None) -> int:
    """`python -m identity_service.feed_consumer replay [event_id]` — chạy lại dead-letter (job riêng)."""
    import sys

    from identity_service.config import ConfigError, load_settings, require_serving_config

    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] != "replay" or len(args) > 2:
        print("Dùng: python -m identity_service.feed_consumer replay [event_id]", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
        require_serving_config(settings)
    except ConfigError as exc:
        print(f"Cấu hình sai: {exc}", file=sys.stderr)
        return 2

    async def run() -> int:
        db = Database(settings.database_url)
        try:
            result = await IntegrationFeedConsumer(db, settings).replay_dead_letters(args[1] if len(args) == 2 else None)
        finally:
            await db.close()
        print(f"Replay dead-letter: thành công={result['replayed']} thất bại={result['failed']}")
        return 0 if result["failed"] == 0 else 1

    return asyncio.run(run())
