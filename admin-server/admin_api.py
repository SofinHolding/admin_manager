"""Endpoints quản trị — chỉ admin (role='admin') mới gọi được.

Quản lý invite key và tài khoản qua giao diện web thay vì chạy SQL tay.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.manage")


class CreateKeyBody(BaseModel):
    # 'discord' thêm cho module reward-service (dùng chung accounts.role, cột text tự do —
    # xem reward-service/store/migrations/001_reward_init.sql). register() dùng nguyên role
    # của key (auth.py) nên tài khoản tạo ra có role='discord' ngay, không cần bước cấp quyền
    # riêng qua reward-service nữa.
    role: str = Field("viewer", pattern=r"^(viewer|admin|discord)$")
    label: str = Field("", max_length=200)
    max_uses: int = Field(1, ge=1, le=1000)
    expires_days: int = Field(7, ge=1, le=365)


class SetStatusBody(BaseModel):
    status: str = Field(..., pattern=r"^(active|suspended)$")


class UpdateUserBody(BaseModel):
    """Admin chỉnh sửa thông tin user — tất cả field đều optional."""
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr | None = None
    role: str | None = Field(None, pattern=r"^(viewer|admin)$")


def make_router(db: Db, decode_token) -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

    async def require_admin(authorization: str = Header("")) -> dict:
        """Chỉ role='admin' mới qua — viewer gọi vào đây sẽ bị 403."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        payload = decode_token(authorization[7:])
        if payload.get("role") != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Chỉ quản trị viên mới có quyền này")
        return payload

    Admin = Depends(require_admin)

    # ── Invite keys ────────────────────────────────────────────────────────────

    @router.post("/keys", summary="Tạo invite key mới")
    async def create_key(body: CreateKeyBody, _: dict = Admin) -> dict:
        row = await db.create_invite_key(
            role=body.role,
            label=body.label,
            max_uses=body.max_uses,
            expires_days=body.expires_days,
        )
        logger.info("Tao invite key %s role=%s max=%d expires=%dd",
                     row["key"], body.role, body.max_uses, body.expires_days)
        return row

    @router.get("/keys", summary="Danh sách invite keys")
    async def list_keys(_: dict = Admin) -> dict:
        rows = await db.list_invite_keys()
        return {"keys": rows}

    @router.delete("/keys/{key}", summary="Thu hồi invite key")
    async def revoke_key(key: str, _: dict = Admin) -> dict:
        ok = await db.revoke_invite_key(key)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Key không tồn tại")
        logger.info("Thu hoi invite key %s", key)
        return {"ok": True}

    # ── Accounts ───────────────────────────────────────────────────────────────

    @router.get("/users", summary="Danh sách tài khoản")
    async def list_users(_: dict = Admin) -> dict:
        rows = await db.list_accounts()
        return {"users": rows}

    @router.patch("/users/{user_id}/status", summary="Khoá / mở khoá tài khoản")
    async def set_user_status(user_id: str, body: SetStatusBody,
                              admin: dict = Admin) -> dict:
        # Không cho admin tự khoá chính mình
        if user_id == admin.get("sub"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Không thể khoá chính mình")
        ok = await db.set_account_status(user_id, body.status)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Doi status user %s -> %s", user_id, body.status)
        return {"ok": True, "status": body.status}

    @router.patch("/users/{user_id}", summary="Admin chỉnh sửa thông tin user")
    async def update_user(user_id: str, body: UpdateUserBody, admin: dict = Admin) -> dict:
        updates: dict[str, str] = {}
        if body.username is not None:
            # Kiểm trùng username
            existing = await db.get_account_by_username(body.username)
            if existing and existing["id"] != user_id:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Tên đăng nhập đã được sử dụng")
            updates["username"] = body.username
        if body.email is not None:
            existing = await db.get_account_by_email(body.email)
            if existing and existing["id"] != user_id:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Email đã được đăng ký")
            updates["email"] = body.email.lower()
        if body.role is not None:
            # Không cho hạ role admin cuối cùng xuống viewer
            if user_id == admin.get("sub") and body.role != "admin":
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "Không thể hạ role chính mình")
            updates["role"] = body.role
        if not updates:
            return {"ok": True, "message": "Không có thay đổi."}
        ok = await db.update_account_by_admin(user_id, updates)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Admin cap nhat user %s: %s", user_id, updates)
        return {"ok": True, "updates": updates}

    @router.delete("/users/{user_id}", summary="Xoá tài khoản")
    async def delete_user(user_id: str, admin: dict = Admin) -> dict:
        if user_id == admin.get("sub"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Không thể xoá chính mình")
        ok = await db.delete_account(user_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Xoa user %s", user_id)
        return {"ok": True}

    return router
