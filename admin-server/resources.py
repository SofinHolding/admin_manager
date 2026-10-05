"""Endpoints `/v1/resources/*` — user (viewer/admin/dash_admin) tự khai báo Source + Resource khi
không có Tool A, theo đặc tả `tasks/backlog/local-operations-hub-v1.md`.

Phạm vi LUÔN là chính account đang đăng nhập (`payload["sub"]`) — xem toàn hệ thống là việc của
`ops.py` (route `/v1/ops/*`, chỉ admin/dash_admin). Route này KHÔNG cho sửa `execution_mode`/
`mapping_status`/`verification_status`/`enabled` — đó là trạng thái vận hành do Ops/Tool B điều
khiển (mục 6 "Quy tắc Execution Mode" trong đặc tả), user chỉ sửa được thông tin hiển thị.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.resources")


class CreateSourceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(..., min_length=1, max_length=50)
    project_key: str = Field("", max_length=100)
    default_country: str = Field("", max_length=100)


class UpdateSourceBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    project_key: str | None = Field(None, max_length=100)
    default_country: str | None = Field(None, max_length=100)


class CreateResourceBody(BaseModel):
    source_instance_id: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1, max_length=50)
    resource_type: str = Field("", max_length=50)
    input_ref: str = Field(..., min_length=1, max_length=500)
    username: str = Field("", max_length=200)
    url: str = Field("", max_length=500)
    display_name: str = Field("", max_length=200)
    country: str = Field("", max_length=100)


class ImportResourceBody(BaseModel):
    source_instance_id: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1, max_length=50)
    resource_type: str = Field("", max_length=50)
    # Mỗi dòng 1 URL/username/ID — trần 500 dòng/lần (xem ghi chú "Gap cần quyết định" của PM).
    lines: list[str] = Field(..., min_length=1, max_length=500)


class UpdateResourceBody(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)
    country: str | None = Field(None, max_length=100)
    resource_type: str | None = Field(None, max_length=50)


def make_router(db: Db) -> APIRouter:
    router = APIRouter(prefix="/v1/resources", tags=["resources"])

    async def require_user(authorization: str = Header("")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        return _decode_access_token(authorization[7:])

    User = Depends(require_user)

    async def _owned_source(source_id: str, payload: dict) -> dict:
        s = await db.get_resource_source(source_id)
        if not s:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy nguồn (Source)")
        if s["owner_account_id"] != payload["sub"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền thao tác nguồn này")
        return s

    async def _owned_resource(resource_id: str, payload: dict) -> dict:
        r = await db.get_managed_resource(resource_id)
        if not r:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy resource")
        if r["owner_account_id"] != payload["sub"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền thao tác resource này")
        return r

    async def _check_platform_web_entry(platform: str) -> dict:
        """Chặn tạo Source/Resource cho platform admin chưa bật `web_entry_enabled` (mục 8 đặc tả)."""
        policy = await db.get_platform_policy(platform)
        if not policy or not policy["web_entry_enabled"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Nền tảng '{platform}' chưa được phép nhập từ website")
        return policy

    @router.get("/platforms", summary="Danh sách platform user được phép nhập trên website")
    async def list_available_platforms(payload: dict = User) -> dict:
        """Khác `/v1/ops/platforms` (chỉ admin/dash_admin) — route này cho MỌI user đã đăng nhập
        gọi được, chỉ trả platform đã bật `web_entry_enabled`, dùng để dựng dropdown chọn platform
        lúc tạo Source/Resource (mục 7/8 đặc tả)."""
        policies = await db.list_platform_policies()
        return {"items": [{"platform": p["platform"]} for p in policies if p["web_entry_enabled"]]}

    # ── Source ────────────────────────────────────────────────────────────────────

    @router.get("/sources", summary="Danh sách Source của chính mình")
    async def list_sources(payload: dict = User) -> dict:
        items = await db.list_resource_sources(payload["sub"])
        return {"items": items}

    @router.post("/sources", summary="Tạo Source mới — namespace nghiệp vụ, vd 'Facebook / ITL'")
    async def create_source(body: CreateSourceBody, payload: dict = User) -> dict:
        await _check_platform_web_entry(body.platform)
        return await db.create_resource_source(
            payload["sub"], body.name.strip(), body.platform,
            body.project_key.strip(), body.default_country.strip())

    @router.patch("/sources/{source_id}", summary="Sửa tên/metadata Source")
    async def update_source(source_id: str, body: UpdateSourceBody, payload: dict = User) -> dict:
        await _owned_source(source_id, payload)
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        ok = await db.update_resource_source(source_id, updates)
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không có gì để cập nhật")
        return {"ok": True}

    # ── Resource ──────────────────────────────────────────────────────────────────

    @router.get("", summary="Danh sách resource của chính mình — filter được")
    async def list_resources(
        platform: str = Query(""), source_instance_id: str = Query(""),
        country: str = Query(""), origin: str = Query(""),
        mapping_status: str = Query(""), execution_mode: str = Query(""),
        verification_status: str = Query(""), enabled: bool | None = Query(None),
        q: str = Query(""), payload: dict = User,
    ) -> dict:
        filters = {
            "platform": platform, "source_instance_id": source_instance_id,
            "country": country, "origin": origin, "mapping_status": mapping_status,
            "execution_mode": execution_mode, "verification_status": verification_status,
            "enabled": enabled, "q": q,
        }
        items = await db.list_managed_resources(payload["sub"], filters)
        return {"items": items}

    @router.post("", summary="Nhập 1 resource (page/channel/profile) vào 1 Source")
    async def create_resource(body: CreateResourceBody, payload: dict = User) -> dict:
        await _owned_source(body.source_instance_id, payload)
        policy = await _check_platform_web_entry(body.platform)
        execution_mode = "fallback_local" if policy["fallback_enabled"] else "disabled"
        return await db.create_managed_resource(
            owner_account_id=payload["sub"], platform=body.platform,
            resource_type=body.resource_type, input_ref=body.input_ref.strip(),
            username=body.username.strip(), url=body.url.strip(),
            display_name=body.display_name.strip() or body.input_ref.strip(),
            country=body.country.strip(), origin="web_manual", execution_mode=execution_mode,
            source_instance_id=body.source_instance_id, actor_account_id=payload["sub"])

    @router.post("/import", summary="Nhập resource hàng loạt — mỗi dòng 1 URL/username/ID")
    async def import_resources(body: ImportResourceBody, payload: dict = User) -> dict:
        await _owned_source(body.source_instance_id, payload)
        policy = await _check_platform_web_entry(body.platform)
        execution_mode = "fallback_local" if policy["fallback_enabled"] else "disabled"
        created: list[dict] = []
        skipped: list[dict] = []
        seen: set[str] = set()
        for raw in body.lines:
            line = raw.strip()
            if not line:
                continue
            if line in seen:
                skipped.append({"input_ref": line, "reason": "duplicate_in_request"})
                continue
            seen.add(line)
            res = await db.create_managed_resource(
                owner_account_id=payload["sub"], platform=body.platform,
                resource_type=body.resource_type, input_ref=line, username="", url=line,
                display_name=line, country="", origin="web_manual",
                execution_mode=execution_mode, source_instance_id=body.source_instance_id,
                actor_account_id=payload["sub"])
            created.append(res)
        return {"created": created, "skipped": skipped,
                "created_count": len(created), "skipped_count": len(skipped)}

    @router.patch("/{resource_id}", summary="Sửa thông tin hiển thị resource (KHÔNG đổi execution_mode)")
    async def update_resource(resource_id: str, body: UpdateResourceBody, payload: dict = User) -> dict:
        await _owned_resource(resource_id, payload)
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        ok = await db.update_managed_resource(resource_id, updates, payload["sub"])
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không có gì để cập nhật")
        return {"ok": True}

    @router.delete("/{resource_id}", summary="Xoá resource")
    async def delete_resource(resource_id: str, payload: dict = User) -> dict:
        await _owned_resource(resource_id, payload)
        ok = await db.delete_managed_resource(resource_id, payload["sub"])
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy resource")
        return {"ok": True}

    return router
