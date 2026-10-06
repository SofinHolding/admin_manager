"""Endpoints group/tag kênh — viewer tự quản lý để gộp/phân loại kênh hiển thị trên dashboard.

Bảng riêng `channel_groups`/`channel_group_members` do admin-server sở hữu — KHÔNG liên quan
`repost_groups` (dữ liệu nghiệp vụ reup THẬT của Tool, ảnh hưởng `stats_core.cho_chay`). Đây thuần
là metadata tổ chức phía dashboard; `channel_ref` tham chiếu `target` (Facebook) hoặc `id` (kênh
YouTube) đã xuất hiện trong dữ liệu Tool của chính account. `platform_tag` là nhãn tự do (vd
"tiktok") để hiển thị đa nền tảng mà KHÔNG cần Tool thực sự thu thập nền tảng đó.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.groups")

_PRIVILEGED_ROLES = ("admin", "dash_admin")


class CreateGroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class RenameGroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AddMemberBody(BaseModel):
    channel_ref: str = Field(..., min_length=1, max_length=200)
    platform_tag: str = Field("", max_length=50)
    country_override: str = Field("", max_length=50)


def make_router(db: Db) -> APIRouter:
    router = APIRouter(prefix="/v1/viewer/groups", tags=["groups"])

    async def require_user(authorization: str = Header("")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        return _decode_access_token(authorization[7:])

    User = Depends(require_user)

    async def _owned_group(group_id: str, payload: dict) -> dict:
        """Tải group + chặn sửa group của người khác. admin/dash_admin XEM được group của bất kỳ ai
        (qua `owner_account_id` ở `GET`) nhưng KHÔNG được sửa hộ — group là do chính nhân sự tự quản
        lý, giữ đúng ranh giới sở hữu."""
        g = await db.get_channel_group(group_id)
        if not g:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy group")
        if g["account_id"] != payload["sub"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền sửa group này")
        return g

    @router.post("", summary="Tạo group kênh mới")
    async def create_group(body: CreateGroupBody, payload: dict = User) -> dict:
        return await db.create_channel_group(payload["sub"], body.name.strip())

    @router.get("", summary="Danh sách group — của chính mình, hoặc của account chỉ định nếu admin/dash_admin")
    async def list_groups(owner_account_id: str = Query(""), payload: dict = User) -> dict:
        role = payload.get("role", "")
        target = owner_account_id if (owner_account_id and role in _PRIVILEGED_ROLES) \
            else payload["sub"]
        groups = await db.list_channel_groups(target)
        for g in groups:
            g["members"] = await db.list_group_members(g["id"])
        return {"items": groups}

    @router.patch("/{group_id}", summary="Đổi tên group")
    async def rename_group(group_id: str, body: RenameGroupBody, payload: dict = User) -> dict:
        await _owned_group(group_id, payload)
        await db.rename_channel_group(group_id, body.name.strip())
        return {"ok": True}

    @router.delete("/{group_id}", summary="Xoá group")
    async def delete_group(group_id: str, payload: dict = User) -> dict:
        await _owned_group(group_id, payload)
        await db.delete_channel_group(group_id)
        return {"ok": True}

    @router.post("/{group_id}/members", summary="Thêm/cập nhật kênh vào group")
    async def add_member(group_id: str, body: AddMemberBody, payload: dict = User) -> dict:
        await _owned_group(group_id, payload)
        return await db.add_group_member(
            group_id, body.channel_ref.strip(), body.platform_tag.strip(),
            body.country_override.strip())

    @router.delete("/{group_id}/members/{channel_ref}", summary="Gỡ kênh khỏi group")
    async def remove_member(group_id: str, channel_ref: str, payload: dict = User) -> dict:
        await _owned_group(group_id, payload)
        ok = await db.remove_group_member(group_id, channel_ref)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy kênh trong group")
        return {"ok": True}

    return router
