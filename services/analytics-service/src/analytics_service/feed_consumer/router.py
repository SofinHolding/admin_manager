"""Vận hành consumer (chỉ admin/dash_admin, chỉ dưới /v1/analytics/admin): rebuild, reconcile, dead-letter, sync."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from manager_contracts import ApiError, ErrorCode

from analytics_service.deps import Admin, container
from analytics_service.feed_consumer.consumer import SourceError
from analytics_service.readmodels import repository as repo

router = APIRouter(tags=["analytics-admin"])


def _consumer(c):
    if c.consumer is None or c.consumer.integration is None:
        raise ApiError(ErrorCode.UNAVAILABLE, "Integration chưa được cấu hình cho analytics-service", status_code=503)
    return c.consumer


@router.post("/sync", summary="Chạy ngay một vòng đồng bộ (feed + canonical)")
async def sync_now(_=Admin, c=Depends(container)) -> dict:
    return await _consumer(c).run_once()


@router.post("/rebuild", summary="Đặt revision về 0 và dựng lại read model (nguyên tử)")
async def rebuild(_=Admin, c=Depends(container)) -> dict:
    try:
        return await _consumer(c).rebuild()
    except SourceError as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, "Không đọc được canonical export của Integration: " + str(exc)[:120],
                       status_code=503) from None


@router.post("/reconcile", summary="Đối soát read model với canonical/summary (đếm + checksum)")
async def reconcile(_=Admin, c=Depends(container)) -> dict:
    try:
        return await _consumer(c).reconcile()
    except SourceError as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, "Không đọc được canonical/summary của Integration: " + str(exc)[:120],
                       status_code=503) from None


@router.get("/dead-letters", summary="Dead-letter còn mở")
async def dead_letters(limit: int = Query(100, ge=1, le=500), _=Admin, c=Depends(container)) -> dict:
    async with c.db.connection() as conn:
        items = await repo.open_dead_letters(conn, limit)
        counts = await repo.count_dead_letters(conn)
    for item in items:
        for k in ("created_at", "last_attempt_at"):
            item[k] = item[k].isoformat()
    return {"items": items, **counts}


@router.post("/dead-letters/replay", summary="Thử lại các dead-letter còn mở")
async def replay_dead_letters(limit: int = Query(100, ge=1, le=500), _=Admin, c=Depends(container)) -> dict:
    if c.consumer is None:
        raise ApiError(ErrorCode.UNAVAILABLE, "Consumer chưa được khởi tạo", status_code=503)
    return await c.consumer.replay_dead_letters(limit)
