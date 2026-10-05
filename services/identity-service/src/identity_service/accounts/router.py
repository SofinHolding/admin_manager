"""Endpoint `/v1/admin/users` — tương thích contract `legacy`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from identity_service.deps import get_container, require_admin

router = APIRouter(prefix="/v1/admin", tags=["admin-users"])


class SetStatusBody(BaseModel):
    status: str = Field(..., pattern=r"^(active|suspended)$")


class UpdateUserBody(BaseModel):
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr | None = None
    role: str | None = Field(None, pattern=r"^(viewer|admin|dash_admin)$")


@router.get("/users", summary="Danh sách tài khoản")
async def list_users(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).accounts_admin.list()


@router.patch("/users/{user_id}/status", summary="Khoá / mở khoá tài khoản")
async def set_user_status(user_id: str, body: SetStatusBody, request: Request,
                          admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).accounts_admin.set_status(admin, user_id, body.status)


@router.patch("/users/{user_id}", summary="Admin chỉnh sửa thông tin user")
async def update_user(user_id: str, body: UpdateUserBody, request: Request,
                      admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).accounts_admin.update(
        admin, user_id, username=body.username, email=body.email, role=body.role)


@router.delete("/users/{user_id}", summary="Xoá tài khoản")
async def delete_user(user_id: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).accounts_admin.delete(admin, user_id)
