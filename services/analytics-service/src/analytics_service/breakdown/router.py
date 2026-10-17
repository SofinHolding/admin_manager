"""GET /channel-breakdown, /compare/{dimension}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from manager_contracts import ApiError, ErrorCode
from manager_sdk import Principal

from analytics_service.breakdown.calc import DIMENSIONS, channel_breakdown, compare
from analytics_service.deps import User, container
from analytics_service.filters import FilterQuery
from analytics_service.readmodels.query import QuerySpec

router = APIRouter(tags=["analytics"])
compare_router = APIRouter(tags=["analytics"])   # chỉ có ở đường dẫn mới (không alias legacy)


def _build_breakdown(ds, ctx, sel, extra) -> dict:
    by_ext: dict[str, list] = {}
    for e in ds.entities.values():
        by_ext.setdefault(e.ext_id, []).append(e)
    tags: dict[str, set[str]] = {}
    for g in ds.groups.values():
        for ref, m in g.members.items():
            if not m["platform_tag"]:
                continue
            owners = by_ext.get(ref)
            # ref chưa có kênh nào: giữ như cũ (đếm theo ref); ref trùng external_id của kênh owner KHÁC: không tính
            if owners is None or any(ds.group_applies(g, e.owner, e.ext_id) for e in owners):
                tags.setdefault(m["platform_tag"], set()).add(ref)
    return channel_breakdown(ds, sel, tags)


def _build_compare(ds, ctx, sel, extra) -> dict:
    body = compare(ds, sel, ctx.date_from, ctx.date_to, extra["dimension"], extra["limit"])
    if extra["dimension"] == "country":
        body["ignored_filters"] = ["country"]
    return body


SPEC_BREAKDOWN = QuerySpec("channel-breakdown", _build_breakdown, window="none")
SPEC_COMPARE = QuerySpec("compare", _build_compare)
SPEC_COMPARE_COUNTRY = QuerySpec("compare", _build_compare, ignore_country=True)


@router.get("/channel-breakdown", summary="Kênh/page theo nền tảng + quốc gia")
async def get_channel_breakdown(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                                c=Depends(container)) -> dict:
    return await c.query.run(SPEC_BREAKDOWN, principal, query, response)


@compare_router.get("/compare/{dimension}", summary="So sánh lượt xem theo chiều: platform|owner|country|channel|group")
async def get_compare(dimension: str, response: Response, limit: int = Query(50, ge=1, le=200),
                      query: FilterQuery = Depends(), principal: Principal = User, c=Depends(container)) -> dict:
    if dimension not in DIMENSIONS:
        raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, "dimension phải là một trong: " + ", ".join(DIMENSIONS),
                       status_code=422)
    spec = SPEC_COMPARE_COUNTRY if dimension == "country" else SPEC_COMPARE
    return await c.query.run(spec, principal, query, response, {"dimension": dimension, "limit": limit})
