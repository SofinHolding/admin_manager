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
        codes = await registry.code_map(conn)   # mã Tool gửi (chính/phụ) -> mã chính; lưu & kiểm quyền theo mã chính
        items = [{**it, "platform": codes.get(it["platform"], it["platform"])}
                 if isinstance(it, dict) and isinstance(it.get("platform"), str) else it for it in items]
        requested = {it["platform"] for it in items if isinstance(it, dict) and isinstance(it.get("platform"), str)}
        denied = sorted(p for p in requested if p not in ctx.allowed_platforms or p not in enabled
                        or (ctx.delegated_platform is not None and p != ctx.delegated_platform))
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
            stats = await merge.merge_resources(conn, owner=ctx.effective_owner, client_id=ctx.client_id,
                                                platform=platform, items=group, snapshot_id=None, now=now,
                                                origin="tool")
            await merge.emit_resource_changes(conn, ctx.effective_owner, platform, stats)
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

    async def list_for_owner(
        self, ctx: MachineContext, *, platform: str = "",
        resource_type: str = "", owner_account_id: str = "",
    ) -> dict:
        """Trả resource.

        fallback_tool (shared operator key):
          → thấy TẤT CẢ owner, kèm owner_username (LEFT JOIN account_projection).
          → owner_account_id param dùng để lọc thêm khi pull từng user.
        Các client_kind khác:
          → chỉ thấy resource của chính owner_account_id của key.
        """
        if platform:   # bộ lọc nhận mã bất kỳ (chính/phụ) -> mã chính, khớp cột `platform` đã chuẩn hoá lúc lưu
            async with self._db.connection() as conn:
                platform = await registry.resolve(conn, platform)
        is_shared = ctx.client_kind == "fallback_tool"
        # owner filter: shared + có param → lọc param; shared + không param → tất cả; không shared → chỉ self
        effective_owner = owner_account_id if (is_shared and owner_account_id) else (
            "" if is_shared else ctx.owner_account_id
        )

        if is_shared:
            sql = (
                "SELECT r.id, r.platform, r.resource_type, r.external_id, "
                "r.owner_account_id, "
                "COALESCE(p.username, '') AS owner_username, "
                "COALESCE(r.data->>'display_name','') AS display_name, "
                "COALESCE(r.data->>'username','') AS username, "
                "COALESCE(r.data->>'url','') AS url, "
                "r.state, "
                "COALESCE(r.data->'extensions'->'legacy'->>'mapping_status','unmapped') AS mapping_status, "
                "r.execution_mode, (r.state = 'active') AS enabled "
                "FROM resources r "
                "LEFT JOIN account_projection p ON p.account_id = r.owner_account_id "
                "WHERE NOT r.removed "
                "AND ($1 = '' OR r.owner_account_id = $1) "
                "AND ($2 = '' OR r.platform = $2) "
                "AND ($3 = '' OR r.resource_type = $3) "
                "ORDER BY COALESCE(p.username,''), r.platform, COALESCE(r.data->>'display_name','')"
            )
            params = (effective_owner, platform, resource_type)
        else:
            sql = (
                "SELECT id, platform, resource_type, external_id, "
                "owner_account_id, '' AS owner_username, "
                "COALESCE(data->>'display_name','') AS display_name, "
                "COALESCE(data->>'username','') AS username, "
                "COALESCE(data->>'url','') AS url, "
                "state, "
                "COALESCE(data->'extensions'->'legacy'->>'mapping_status','unmapped') AS mapping_status, "
                "execution_mode, (state = 'active') AS enabled "
                "FROM resources "
                "WHERE owner_account_id = $1 AND NOT removed "
                "AND ($2 = '' OR platform = $2) "
                "AND ($3 = '' OR resource_type = $3) "
                "ORDER BY platform, COALESCE(data->>'display_name','')"
            )
            params = (ctx.owner_account_id, platform, resource_type)

        async with self._db.connection() as conn:
            rows = await conn.fetch(sql, *params)
        return {
            "items": [dict(r) for r in rows],
            "is_shared": is_shared,
            "owner_account_id": ctx.owner_account_id,
        }

    async def list_resource_owners(self, ctx: MachineContext) -> dict:
        """Danh sách owner có resource.

        fallback_tool: tất cả owner trong hệ thống.
        Khác: chỉ trả chính owner của key (một user duy nhất).
        """
        if ctx.client_kind != "fallback_tool":
            return {"users": [{"id": ctx.owner_account_id, "username": "", "resource_count": 0}]}
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                "SELECT r.owner_account_id AS id, COALESCE(p.username, '') AS username, "
                "COUNT(r.id) AS resource_count "
                "FROM resources r "
                "LEFT JOIN account_projection p ON p.account_id = r.owner_account_id "
                "WHERE NOT r.removed "
                "GROUP BY r.owner_account_id, p.username "
                "ORDER BY COALESCE(p.username,''), r.owner_account_id",
            )
        return {"users": [dict(r) for r in rows]}

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
