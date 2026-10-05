"""GET /traffic, /top-channels, /metrics (dưới /v1/analytics và alias legacy /v1/viewer)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from manager_contracts import ApiError, ErrorCode
from manager_sdk import Principal

from analytics_service.deps import User, container
from analytics_service.filters import FilterQuery
from analytics_service.readmodels.query import QuerySpec
from analytics_service.traffic.calc import METRICS, top_channels, traffic_metric

router = APIRouter(tags=["analytics"])


def _build_traffic(ds, ctx, sel, extra) -> dict:
    body = traffic_metric(ds, sel, ctx.date_from, ctx.date_to, sel.country or ctx.country)
    metric = extra.get("metric")
    if metric:
        body["metric"] = metric
        body["series"] = [{"date": d["date"], "value": d[metric]} for d in body["days"]]
    return body


def _build_top(ds, ctx, sel, extra) -> dict:
    return top_channels(ds, sel, ctx.date_from, ctx.date_to, extra["limit"])


_CATALOG = [
    {"key": "views", "label": "Lượt xem", "ready": True, "note": ""},
    {"key": "interactions", "label": "Lượt tương tác", "ready": True,
     "note": "Từ bài đăng (reactions). Kênh chỉ có số liệu theo ngày không có chỉ số này."},
    {"key": "comments", "label": "Bình luận", "ready": True,
     "note": "Từ bài đăng (comments). Kênh chỉ có số liệu theo ngày không có chỉ số này."},
    {"key": "videos", "label": "Số video", "ready": True, "note": ""},
]

SPEC_TRAFFIC = QuerySpec("traffic", _build_traffic)
SPEC_TOP = QuerySpec("top-channels", _build_top)
SPEC_METRICS = QuerySpec("metrics", lambda ds, ctx, sel, extra: {"metrics": _CATALOG}, window="none")


@router.get("/traffic", summary="Traffic theo chỉ số (TrafficMetricSeries + filter + freshness)")
async def get_traffic(response: Response, metric: str = Query("", description="views|interactions|comments|videos"),
                      query: FilterQuery = Depends(), principal: Principal = User, c=Depends(container)) -> dict:
    if metric and metric not in METRICS:
        raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, "metric không hợp lệ", status_code=422)
    return await c.query.run(SPEC_TRAFFIC, principal, query, response, {"metric": metric})


@router.get("/top-channels", summary="Top kênh/page theo lượt xem")
async def get_top_channels(response: Response, limit: int = Query(15, ge=1, le=100), query: FilterQuery = Depends(),
                           principal: Principal = User, c=Depends(container)) -> dict:
    return await c.query.run(SPEC_TOP, principal, query, response, {"limit": limit})


@router.get("/metrics", summary="Danh sách metric khả dụng")
async def get_metrics(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                      c=Depends(container)) -> dict:
    return await c.query.run(SPEC_METRICS, principal, query, response)
