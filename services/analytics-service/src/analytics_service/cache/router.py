"""POST /cache/flush (admin): xoá mọi khoá `analytics:*` và tăng seal. Dưới /v1/analytics/admin và alias /v1/admin."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from analytics_service.deps import Admin, container
from analytics_service.readmodels import repository as repo

router = APIRouter(tags=["cache"])


@router.post("/cache/flush", summary="Xoá cache Redis — ép tổng hợp lại từ PostgreSQL")
async def flush_cache(_=Admin, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        await repo.bump_seal(conn)       # khoá cũ vô hiệu NGAY cả khi Redis không xoá được
    removed = await c.cache.flush()
    return {"ok": True, "removed": removed, "enabled": c.cache.configured}
