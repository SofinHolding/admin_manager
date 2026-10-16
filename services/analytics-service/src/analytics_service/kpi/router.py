"""GET /v1/analytics/summary (+ alias legacy /v1/viewer/summary)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from manager_sdk import Principal

from analytics_service.deps import User, container
from analytics_service.filters import FilterQuery
from analytics_service.kpi.calc import summary
from analytics_service.readmodels.query import QuerySpec

router = APIRouter(tags=["analytics"])


def _build(ds, ctx, sel, extra) -> dict:
    body = summary(ds, sel, ctx.date_from, ctx.date_to)
    body["linked"] = bool(ds.entities)    # false = phạm vi này chưa có kênh nào (FE hiện hướng dẫn kết nối)
    return body


SPEC = QuerySpec("summary", _build, window="prev")


@router.get("/summary", summary="KPI tổng hợp (AnalyticsSummary + filter + freshness)")
async def get_summary(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                      c=Depends(container)) -> dict:
    body = await c.query.run(SPEC, principal, query, response)
    body["last_synced_at"] = body["freshness"]["committed_at"]
    return body
