"""Endpoints dữ liệu biểu đồ cho viewer — chỉ ĐỌC, yêu cầu JWT hợp lệ.

Trả về đúng shape mà frontend (Recharts) cần — giống hệt AnalyticsView bên Electron:
- /summary: KPI strip (AnalyticsSummary)
- /traffic: Traffic theo chỉ số (TrafficMetricSeries — 4 metric, scope, country)
- /top-channels: Top kênh/page (có platform, country)
- /views-by-country: Grouped bar theo ngày (CountryViewsSeries)
- /country-tags: Danh sách quốc gia từ dữ liệu (thay cho tag manager)

Viewer (role bất kỳ — cả admin lẫn viewer) đều xem được.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.viewer")


def make_router(db: Db, decode_token) -> APIRouter:
    """Nhận `db` và hàm giải JWT từ `auth.py`.

    Tách `decode_token` ra thay vì import trực tiếp — tránh circular import và dễ mock trong test.
    """
    router = APIRouter(prefix="/v1/viewer", tags=["viewer"])

    async def require_user(authorization: str = Header("")) -> dict:
        """Mọi user đã đăng nhập đều xem được — không phân biệt viewer/admin."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        payload = _decode_access_token(authorization[7:])
        return payload

    User = Depends(require_user)

    @router.get("/summary", summary="KPI tổng hợp — shape AnalyticsSummary")
    async def summary(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        country: str = Query("", description="Lọc theo quốc gia (trống = tất cả)"),
        _: dict = User,
    ) -> dict:
        return await db.summary(date_from, date_to, country)

    @router.get("/traffic", summary="Traffic theo chỉ số — shape TrafficMetricSeries")
    async def traffic(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        scope: str = Query("", description="'' | 'youtube' | 'facebook'"),
        country: str = Query("", description="Lọc theo quốc gia"),
        _: dict = User,
    ) -> dict:
        return await db.traffic_metric(date_from, date_to, scope, country)

    @router.get("/top-channels", summary="Top kênh/page theo views")
    async def top_channels(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        country: str = Query("", description="Lọc theo quốc gia"),
        limit: int = Query(15, ge=1, le=100),
        _: dict = User,
    ) -> dict:
        rows = await db.top_channels(date_from, date_to, country, limit)
        return {"items": rows}

    @router.get("/views-by-country", summary="Views theo quốc gia/ngày — shape CountryViewsSeries")
    async def views_by_country(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        _: dict = User,
    ) -> dict:
        return await db.views_by_country(date_from, date_to)

    @router.get("/country-tags", summary="Danh sách quốc gia từ dữ liệu")
    async def country_tags(_: dict = User) -> dict:
        tags = await db.country_tags()
        return {"items": [{"id": t, "name": t} for t in tags]}

    @router.get("/metrics", summary="Danh sách metric khả dụng")
    async def metrics(_: dict = User) -> dict:
        """Trả danh sách metric cố định — khớp Tool: interactions/comments cào từ Business Suite."""
        return {"metrics": [
            {"key": "views", "label": "Lượt xem", "ready": True, "note": ""},
            {"key": "interactions", "label": "Lượt tương tác", "ready": True,
             "note": "Cào từ cột Reactions của Business Suite (0 nếu cột chưa có trên trang). YouTube không có dữ liệu theo ngày."},
            {"key": "comments", "label": "Bình luận", "ready": True,
             "note": "Cào từ cột Comments của Business Suite (0 nếu cột chưa có trên trang). YouTube không có dữ liệu theo ngày."},
            {"key": "videos", "label": "Số video", "ready": True, "note": ""},
        ]}

    return router
