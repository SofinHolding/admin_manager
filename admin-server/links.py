"""Endpoints liên kết tài khoản dashboard ⇄ Tool (user_id) — mã pairing dùng 1 lần.

Account đăng nhập web tạo mã (TTL 15 phút) → dán mã vào Tool (khi Tool có giao diện nhập mã — việc
đó nằm ở repo `upload-facebook`, KHÔNG thuộc phạm vi file này) → Tool gọi `/redeem` bằng chính mã đó
kèm `tool_user_id` của nó (HMAC license key, xem `upload-facebook/server/main.py::_user_id_of`) →
admin-server ghi `account_links`. KHÔNG đụng tới cơ chế session/license key hiện có của Tool.

`/redeem` KHÔNG yêu cầu Bearer JWT dashboard (Tool không giữ JWT đó) — bằng chứng uỷ quyền LÀ chính
mã: dùng 1 lần, hết hạn 15 phút, có rate-limit theo IP chống dò.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.links")


class RedeemBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)
    tool_user_id: str = Field(..., min_length=8, max_length=128)
    device_id: str = Field("", max_length=128)


def make_router(db: Db) -> APIRouter:
    router = APIRouter(prefix="/v1/viewer/links", tags=["links"])

    async def require_user(authorization: str = Header("")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        return _decode_access_token(authorization[7:])

    User = Depends(require_user)

    @router.post("/code", summary="Tạo mã liên kết Tool mới (TTL 15 phút)")
    async def create_code(payload: dict = User) -> dict:
        return await db.create_link_code(payload["sub"])

    @router.get("", summary="Danh sách Tool đã liên kết với account hiện tại")
    async def list_links(payload: dict = User) -> dict:
        return {"items": await db.list_links(payload["sub"])}

    @router.delete("/{tool_user_id}", summary="Gỡ liên kết")
    async def revoke(tool_user_id: str, payload: dict = User) -> dict:
        ok = await db.revoke_link(payload["sub"], tool_user_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy liên kết")
        return {"ok": True}

    @router.post("/redeem", summary="Tool đổi mã lấy liên kết (public — không cần JWT dashboard)")
    async def redeem(body: RedeemBody, request: Request) -> dict:
        from auth import _check_rate, _client_ip
        _check_rate(_client_ip(request), "link-redeem", 10, 300)

        result = await db.redeem_link_code(
            body.code.strip().upper(), body.tool_user_id.strip(), body.device_id.strip())
        if not result:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã không hợp lệ hoặc đã hết hạn")
        return {"ok": True}

    return router
