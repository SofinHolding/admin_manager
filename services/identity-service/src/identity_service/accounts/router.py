"""Endpoint `/v1/admin/users` — tương thích contract `legacy`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from identity_service.accounts import integration_repository
from identity_service.deps import current_account, get_container, require_admin
from identity_service.errors import DomainError

router = APIRouter(prefix="/v1/admin", tags=["admin-users"])
me_router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


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


@me_router.get("/me/integration", summary="Trạng thái Integration client được tạo kèm đăng ký")
async def my_integration(request: Request, account: dict = Depends(current_account)) -> dict:
    async with get_container(request).db.connection() as conn:
        row = await integration_repository.get_for_account(conn, account["id"])
    if not row:
        raise DomainError(404, "Tài khoản không có yêu cầu integration", code="NOT_FOUND")
    out = {"client_id": row["client_id"], "status": row["status"]}
    if row["error_code"]:
        out["error_code"] = row["error_code"]
    return out
