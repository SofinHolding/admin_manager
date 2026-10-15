"""Endpoint `/v1/admin/keys` — tương thích contract `legacy`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from identity_service.deps import get_container, require_admin

router = APIRouter(prefix="/v1/admin", tags=["admin-keys"])


class CreateKeyBody(BaseModel):
    """`extra="forbid"`: field cũ `capability_grant`/`integration_policy` không còn được chấp nhận trực tiếp,
    dùng `preset` thay thế (docs/architecture/capability-model-v1.md, mục preset)."""

    model_config = ConfigDict(extra="forbid")
    # `discord` giữ cho reward-service (đến Phase 1.5/D7 mới chuyển sang permission); `dash_admin` = ADD.
    role: str | None = Field(None, pattern=r"^(viewer|admin|discord|dash_admin)$")
    preset: str | None = Field(None, max_length=32)
    label: str = Field("", max_length=200)
    max_uses: int = Field(1, ge=1, le=1000)
    expires_days: int = Field(7, ge=1, le=365)
    allowed_apps: list[str] = Field(default_factory=list, max_length=32)   # tự cấp permission `<app>:access`


class PatchKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str | None = Field(None, max_length=32)
    label: str | None = Field(None, max_length=200)
    max_uses: int | None = Field(None, ge=1, le=1000)
    expires_days: int | None = Field(None, ge=1, le=365)


@router.post("/keys", summary="Tạo invite key mới")
async def create_key(body: CreateKeyBody, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.create(admin, role=body.role, preset=body.preset, label=body.label,
                                                    max_uses=body.max_uses, expires_days=body.expires_days,
                                                    allowed_apps=body.allowed_apps)


@router.get("/keys", summary="Danh sách invite keys")
async def list_keys(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.list()


@router.get("/keys/presets", summary="Danh sách gói quyền (preset) để UI hiển thị")
async def list_presets(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.presets()


@router.patch("/keys/{key}", summary="Sửa invite key (preset/label/max_uses/expires_days)")
async def patch_key(key: str, body: PatchKeyBody, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.patch(admin, key, preset=body.preset, label=body.label,
                                                    max_uses=body.max_uses, expires_days=body.expires_days)


@router.delete("/keys/{key}", summary="Thu hồi invite key")
async def revoke_key(key: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.revoke(admin, key)


@router.patch("/keys/{key}/suspend", summary="Tạm dừng invite key")
async def suspend_key(key: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    """Tạm dừng key đang active: không dùng đăng ký được nhưng có thể khôi phục (unsuspend)."""
    return await get_container(request).keys.suspend(admin, key)


@router.patch("/keys/{key}/unsuspend", summary="Khôi phục invite key đã tạm dừng")
async def unsuspend_key(key: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).keys.unsuspend(admin, key)
