"""GET /v1/analytics/freshness — trạng thái đồng bộ (con trỏ, lag, dead-letter) cho dashboard/vận hành."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from manager_sdk import Principal

from analytics_service.deps import User, container
from analytics_service.freshness import freshness_block, load_sync_info
from analytics_service.readmodels import repository as repo

router = APIRouter(tags=["analytics"])


@router.get("/freshness", summary="Trạng thái đồng bộ")
async def get_freshness(principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.connection() as conn:
        info = await load_sync_info(conn)
        body: dict = {"freshness": freshness_block(info, None, c.now(), c.settings.stale_after_s)}
        if principal.is_admin:
            body["cursors"] = await repo.all_cursors(conn)
            body["dead_letters"] = await repo.count_dead_letters(conn)
            body["sources"] = info.sources
            body["reconcile"] = {k: v for k, v in (await repo.get_state(conn, "reconcile")).items()
                                 if not k.startswith("_")}
    body["cache"] = {"configured": c.cache.configured}
    return body
