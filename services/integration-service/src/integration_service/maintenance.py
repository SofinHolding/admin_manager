"""Bảo trì nền: đánh `abandoned` snapshot quá TTL, `failed` cho snapshot kẹt ở committing, dọn staging rác và idempotency hết hạn."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from integration_service.container import Container
from integration_service.idempotency import repository as idempotency

logger = logging.getLogger("integration.maintenance")


async def run_once(c: Container) -> dict:
    swept = await c.snapshots.sweep()
    jobs = await c.jobs.sweep()
    async with c.db.connection() as conn:
        purged = await idempotency.purge_before(conn, c.now() - timedelta(seconds=c.settings.idempotency_ttl_s))
    return {**swept, "jobs": jobs, "idempotency_purged": purged}


async def loop(c: Container) -> None:
    interval = c.settings.maintenance_interval_s
    while True:
        try:
            result = await run_once(c)
            if result["abandoned"] or result["failed"]:
                logger.info("Bảo trì snapshot: %s", {k: len(v) if isinstance(v, list) else v for k, v in result.items()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — DB chết thì thử lại lượt sau, không làm sập tiến trình
            logger.warning("Bảo trì lỗi: %s", type(exc).__name__)
        await asyncio.sleep(interval)
