"""GET /views-by-country, /country-tags."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from manager_sdk import Principal

from analytics_service.country_comparison.calc import country_tags, views_by_country
from analytics_service.deps import User, container
from analytics_service.filters import FilterQuery
from analytics_service.readmodels.query import QuerySpec

router = APIRouter(tags=["analytics"])


def _build_vbc(ds, ctx, sel, extra) -> dict:
    body = views_by_country(ds, sel, ctx.date_from, ctx.date_to)
    body["ignored_filters"] = ["country"]
    return body


def _build_tags(ds, ctx, sel, extra) -> dict:
    return {"items": [{"id": t, "name": t} for t in country_tags(ds, sel)]}


SPEC_VBC = QuerySpec("views-by-country", _build_vbc, ignore_country=True)
SPEC_TAGS = QuerySpec("country-tags", _build_tags, window="none", ignore_country=True)


@router.get("/views-by-country", summary="Lượt xem theo quốc gia/ngày (biểu đồ so sánh quốc gia riêng)")
async def get_views_by_country(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                               c=Depends(container)) -> dict:
    return await c.query.run(SPEC_VBC, principal, query, response)


@router.get("/country-tags", summary="Danh sách quốc gia có trong dữ liệu")
async def get_country_tags(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                           c=Depends(container)) -> dict:
    return await c.query.run(SPEC_TAGS, principal, query, response)
