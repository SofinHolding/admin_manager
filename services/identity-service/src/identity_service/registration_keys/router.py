"""Endpoint `/v1/admin/keys` — tương thích contract `legacy`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from identity_service.deps import get_container, require_admin

router = APIRouter(prefix="/v1/admin", tags=["admin-keys"])


class CreateKeyBody(BaseModel):
    # `discord` giữ cho reward-service (đến Phase 1.5/D7 mới chuyển sang permission); `dash_admin` = ADD.
    role: str = Field("viewer", pattern=r"^(viewer|admin|discord|dash_admin)$")
    label: str = Field("", max_length=200)
    max_uses: int = Field(1, ge=1, le=1000)
    expires_days: int = Field(7, ge=1, le=365)
    integration_policy: dict | None = None      # Phase 1.7: chính sách đăng ký kèm Tool
    allowed_apps: list[str] = Field(default_factory=list, max_length=32)   # tự cấp permission `<app>:access`
    capability_grant: dict | None = None        # Phase 2: capability cấp thêm lúc đăng ký (capability:*)


@router.post("/keys", summary="Tạo invite key mới")
async def create_key(body: CreateKeyBody, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.create(admin, role=body.role, label=body.label,
                                                    max_uses=body.max_uses, expires_days=body.expires_days,
                                                    integration_policy=body.integration_policy,
                                                    allowed_apps=body.allowed_apps,
                                                    capability_grant=body.capability_grant)


@router.get("/keys", summary="Danh sách invite keys")
async def list_keys(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.list()


@router.delete("/keys/{key}", summary="Thu hồi invite key")
async def revoke_key(key: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.revoke(admin, key)
