"""Resource ngoài snapshot: `POST /v1/integrations/resources/batch` (Tool) và bật/tắt bởi admin.

Cùng quy tắc hợp nhất như commit snapshot (upsert theo khoá tự nhiên, observed_at mới hơn thắng) nhưng KHÔNG suy luận
`removed`. Thay đổi ở đây phát `resource.changed` (khác với thay đổi trong snapshot, vốn được tóm tắt bởi `snapshot.committed`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg
from manager_contracts import ErrorCode

from integration_service.credentials.service import MachineContext
from integration_service.db import Database
from integration_service.errors import forbidden, not_found
from integration_service.feed import events
from integration_service.feed import repository as outbox
from integration_service.ingest import merge, schemas, validate
from integration_service.platforms import repository as registry


class ResourceService:
    def __init__(self, db: Database, now: Callable[[], datetime]) -> None:
        self._db = db
        self._now = now

    async def upsert_batch(self, conn: asyncpg.Connection, ctx: MachineContext, items: list) -> dict:
        enabled = await registry.enabled_platforms(conn)
        requested = {it["platform"] for it in items if isinstance(it, dict) and isinstance(it.get("platform"), str)}
        denied = sorted(p for p in requested if p not in ctx.allowed_platforms or p not in enabled)
        if denied:  # platform là vấn đề PHÂN QUYỀN, từ chối cả request
            raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform không nằm trong phạm vi client được phép",
                            {"platforms": denied[:10]})
        now = self._now()
        staged, rejections, dupes = validate.validate_items(
            "resources", items, default_observed_at=now, model=schemas.ResourceUpsertItem)
        await outbox.acquire_order_lock(conn)
        outcomes: dict[int, tuple[str, str]] = {}
        totals = {"created": 0, "updated": 0, "unchanged": 0, "stale_ignored": dupes}
        for platform in sorted({s.payload["platform"] for s in staged}):
            group = [s for s in staged if s.payload["platform"] == platform]
            stats = await merge.merge_resources(conn, owner=ctx.owner_account_id, client_id=ctx.client_id,
                                                platform=platform, items=group, snapshot_id=None, now=now,
                                                origin="tool")
            await merge.emit_resource_changes(conn, ctx.owner_account_id, platform, stats)
            for key in totals:
                totals[key] += getattr(stats, key)
            for index, rid, verdict in stats.outcomes:
                outcomes[index] = (rid, verdict)
        by_index = {s.index: s for s in staged}
        result_items = [{"external_id": by_index[i].payload["external_id"], "resource_id": rid, "status": verdict}
                        for i, (rid, verdict) in sorted(outcomes.items())]
        return {"received": len(items), "accepted": len(staged), "created": totals["created"],
                "updated": totals["updated"], "unchanged": totals["unchanged"],
                "stale_ignored": totals["stale_ignored"], "rejected": len(rejections),
                "rejections": [r.as_dict() for r in sorted(rejections, key=lambda r: r.index)],
                "items": result_items}

    async def admin_set_state(self, resource_id: str, state: str) -> dict:
        """Admin bật/tắt resource (đổi ngoài snapshot => resource.changed)."""
        now = self._now()
        async with self._db.transaction() as conn:
            await outbox.acquire_order_lock(conn)
            row = await conn.fetchrow("SELECT * FROM resources WHERE id = $1 FOR UPDATE", resource_id)
            if row is None:
                raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy resource")
            if row["state"] != state:
                (rev,) = await outbox.next_revisions(conn, 1)
                data = dict(row["data"])
                data["state"] = state
                row = await conn.fetchrow(
                    "UPDATE resources SET state = $2, data = $3, revision = $4, updated_at = $5 WHERE id = $1 RETURNING *",
                    resource_id, state, data, rev, now)
                await events.resource_changed(conn, resource_id=resource_id, owner_account_id=row["owner_account_id"],
                                              platform=row["platform"], external_id=row["external_id"], state=state,
                                              revision=rev)
        return {"resource_id": resource_id, "state": row["state"], "revision": row["revision"]}
