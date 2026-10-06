"""Endpoints dữ liệu biểu đồ cho viewer — chỉ ĐỌC, yêu cầu JWT hợp lệ.

Trả về đúng shape mà frontend (Recharts) cần — giống hệt AnalyticsView bên Electron:
- /summary: KPI strip (AnalyticsSummary)
- /traffic: Traffic theo chỉ số (TrafficMetricSeries — 4 metric, nền tảng, quốc gia)
- /top-channels: Top kênh/page (có platform, country)
- /views-by-country: Grouped bar theo ngày (CountryViewsSeries)
- /country-tags: Danh sách quốc gia từ dữ liệu (thay cho tag manager)

Phân quyền theo DỮ LIỆU (không chỉ theo route):
- role `admin`/`dash_admin`: mặc định xem XUYÊN USER (mọi Tool đã đồng bộ). Truyền `owner_account_id`
  để soi đúng 1 nhân sự cụ thể (đúng yêu cầu "ADD chọn 1 tài khoản thì thấy Group của người đó").
- role khác (`viewer`, `discord`, ...): CHỈ xem dữ liệu của (các) Tool đã liên kết với chính account
  đó qua `links.py` (`account_links`) — chưa liên kết Tool nào → trả rỗng + cờ `linked=false`.
- `group_id`/`platform_tag` (bất kỳ role nào): thu hẹp thêm theo group kênh hoặc nhãn nền tảng tự
  gắn (`groups.py`) — cho cả 2 cùng lúc thì GIAO (intersect) 2 tập channel_ref. `platform_tag` là
  **1 nút hợp nhất DUY NHẤT** cho toàn bộ khái niệm "nền tảng": nhận HOẶC nhãn tự do (vd "tiktok",
  suy từ `channel_group_members.platform_tag`) HOẶC 1 trong 2 giá trị dự trữ `youtube`/`facebook`
  (nền tảng GỐC, suy trực tiếp từ cấu trúc dữ liệu Tool — KHÔNG cần gắn nhãn tay). Không còn tham
  số `scope` riêng ở `/traffic` — chỉ 1 chỗ chọn nền tảng cho toàn trang, tránh 2 nút chồng chéo.
- `country_overrides`: LUÔN áp dụng (không cần bật/tắt) — viewer sửa quốc gia của 1 kênh khi map nó
  vào group (`channel_group_members.country_override`) thì mọi biểu đồ tính quốc gia đều thấy ngay,
  kể cả khi không lọc theo group/nền tảng gì — "đồng bộ" quốc gia+nền tảng cùng lúc lúc map dữ liệu.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.viewer")

_PRIVILEGED_ROLES = ("admin", "dash_admin")


def make_router(db: Db, decode_token) -> APIRouter:
    """Nhận `db` và hàm giải JWT từ `auth.py`.

    Tách `decode_token` ra thay vì import trực tiếp — tránh circular import và dễ mock trong test.
    """
    router = APIRouter(prefix="/v1/viewer", tags=["viewer"])

    async def require_user(authorization: str = Header("")) -> dict:
        """Mọi user đã đăng nhập đều gọi được — KHÔNG tra DB/suspended (T1). Phạm vi DỮ LIỆU thấy
        được thì luôn bị thu hẹp theo role + liên kết Tool ở `_resolve_scope` bên dưới."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        payload = _decode_access_token(authorization[7:])
        return payload

    User = Depends(require_user)

    _NATIVE_PLATFORMS = ("youtube", "facebook")

    async def _resolve_scope(
        payload: dict, owner_account_id: str, group_id: str, platform_tag: str,
    ) -> tuple[list[str] | None, set[str] | None, bool, dict[str, str], str, list[str] | None]:
        """`(user_ids, channel_refs, linked, country_overrides, native_scope, owner_scope)`.

        `user_ids=None` = không lọc (xuyên user, chỉ admin/dash_admin không chỉ định owner mới có).
        `linked` chỉ có ý nghĩa tham khảo cho role không đặc quyền — dùng để FE hiện banner hướng
        dẫn lấy mã liên kết khi account chưa link Tool nào. `native_scope` khác rỗng khi
        `platform_tag` là giá trị dự trữ `youtube`/`facebook` — endpoint `/traffic` dùng thêm giá
        trị này để giữ đúng ngữ nghĩa riêng-metric của `stats_core` (vd YouTube chưa có
        interactions/comments theo ngày), các endpoint khác chỉ cần `channel_refs` là đủ.
        `owner_scope` = account_id dashboard (KHÁC `user_ids` — đó là user_id Tool) — cần riêng
        cho `/channel-breakdown` để tra nhãn `platform_tag` theo đúng chủ sở hữu."""
        role = payload.get("role", "")
        if role in _PRIVILEGED_ROLES:
            if owner_account_id:
                user_ids = await db.linked_user_ids(owner_account_id)
                owner_scope: list[str] | None = [owner_account_id]
            else:
                user_ids = None
                owner_scope = None
            linked = True
        else:
            user_ids = await db.linked_user_ids(payload["sub"])
            owner_scope = [payload["sub"]]
            linked = len(user_ids) > 0

        native_scope = platform_tag if platform_tag in _NATIVE_PLATFORMS else ""
        if native_scope:
            platform_refs = await db.channel_refs_by_native_platform(native_scope, user_ids)
        elif platform_tag:
            platform_refs = await db.channel_refs_by_platform(platform_tag, owner_scope)
        else:
            platform_refs = None
        group_refs = await db.group_channel_refs(group_id) if group_id else None
        parts = [s for s in (group_refs, platform_refs) if s is not None]
        channel_refs = set.intersection(*parts) if parts else None

        country_overrides = await db.country_overrides(owner_scope)
        return user_ids, channel_refs, linked, country_overrides, native_scope, owner_scope

    @router.get("/platform-tags", summary="Danh sách nhãn nền tảng đã gắn (tự do, từ Nhóm kênh)")
    async def platform_tags(owner_account_id: str = Query(""), payload: dict = User) -> dict:
        role = payload.get("role", "")
        if role in _PRIVILEGED_ROLES:
            scope = [owner_account_id] if owner_account_id else None
        else:
            scope = [payload["sub"]]
        tags = await db.list_platform_tags(scope)
        return {"items": tags}

    @router.get("/summary", summary="KPI tổng hợp — shape AnalyticsSummary")
    async def summary(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        country: str = Query("", description="Lọc theo quốc gia (trống = tất cả)"),
        owner_account_id: str = Query("", description="admin/dash_admin: soi 1 account cụ thể"),
        group_id: str = Query("", description="Lọc thêm theo group kênh"),
        platform_tag: str = Query("", description="'youtube'|'facebook' (gốc) hoặc nhãn tự do đã gắn"),
        payload: dict = User,
    ) -> dict:
        user_ids, channel_refs, linked, country_overrides, _, _ = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        data = await db.summary(date_from, date_to, country, user_ids, channel_refs, country_overrides)
        last_synced_at = await db.last_synced_at(user_ids)
        return {**data, "linked": linked, "last_synced_at": last_synced_at}

    @router.get("/traffic", summary="Traffic theo chỉ số — shape TrafficMetricSeries")
    async def traffic(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        country: str = Query("", description="Lọc theo quốc gia"),
        owner_account_id: str = Query(""),
        group_id: str = Query(""),
        platform_tag: str = Query("", description="'youtube'|'facebook' (gốc) hoặc nhãn tự do đã gắn"),
        payload: dict = User,
    ) -> dict:
        user_ids, channel_refs, _, country_overrides, native_scope, _ = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        return await db.traffic_metric(date_from, date_to, native_scope, country, user_ids,
                                       channel_refs, country_overrides)

    @router.get("/top-channels", summary="Top kênh/page theo views")
    async def top_channels(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        country: str = Query("", description="Lọc theo quốc gia"),
        limit: int = Query(15, ge=1, le=100),
        owner_account_id: str = Query(""),
        group_id: str = Query(""),
        platform_tag: str = Query(""),
        payload: dict = User,
    ) -> dict:
        user_ids, channel_refs, _, country_overrides, _, _ = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        rows = await db.top_channels(date_from, date_to, country, limit, user_ids, channel_refs,
                                     country_overrides)
        return {"items": rows}

    @router.get("/views-by-country", summary="Views theo quốc gia/ngày — shape CountryViewsSeries")
    async def views_by_country(
        date_from: str = Query(..., description="YYYY-MM-DD"),
        date_to: str = Query(..., description="YYYY-MM-DD"),
        owner_account_id: str = Query(""),
        group_id: str = Query(""),
        platform_tag: str = Query(""),
        payload: dict = User,
    ) -> dict:
        user_ids, channel_refs, _, country_overrides, _, _ = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        return await db.views_by_country(date_from, date_to, user_ids, channel_refs, country_overrides)

    @router.get("/country-tags", summary="Danh sách quốc gia từ dữ liệu")
    async def country_tags(
        owner_account_id: str = Query(""),
        group_id: str = Query(""),
        platform_tag: str = Query(""),
        payload: dict = User,
    ) -> dict:
        user_ids, channel_refs, _, country_overrides, _, _ = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        tags = await db.country_tags(user_ids, channel_refs, country_overrides)
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

    @router.get("/channel-breakdown",
               summary="Kênh/page theo platform + chủ đề (group) + quốc gia — KHÔNG đụng stats_core.py")
    async def channel_breakdown(
        owner_account_id: str = Query(""),
        group_id: str = Query(""),
        platform_tag: str = Query(""),
        payload: dict = User,
    ) -> dict:
        """Chi tiết đứng sau ô "Kênh/Page đang bật" (mục 25.4 đặc tả
        `tasks/backlog/local-operations-hub-v1.md`) — tách theo nền tảng, có drill-down theo chủ đề
        (`repost_groups`, vd ITL/Sible) và quốc gia cho Facebook. Tính thẳng trong `db.py`, KHÔNG
        sửa `stats_core.py` (file canonical phải sync nguyên byte với repo Tool)."""
        user_ids, channel_refs, _, country_overrides, _, owner_scope = await _resolve_scope(
            payload, owner_account_id, group_id, platform_tag)
        return await db.channel_breakdown(user_ids, channel_refs, country_overrides, owner_scope)

    return router
