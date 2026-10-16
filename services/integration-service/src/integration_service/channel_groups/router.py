"""Quản lý nhóm quốc gia + kênh của user (mặt phẳng USER — access token RS256 do Identity ký).

Group link với `resources` qua khớp chuỗi `resources.country = user_channel_groups.name` (không khoá ngoại,
bảng `resources` giữ nguyên không sửa — xem `services/integration-service/README.md`)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request
from manager_contracts import ErrorCode
from manager_sdk import Principal

from integration_service.api_models import Envelope
from integration_service.channel_groups import repository
from integration_service.channel_groups.schemas import ChannelGroupCreate, ChannelGroupOut, ChannelGroupUpdate
from integration_service.deps import get_container, require_user, user_bearer
from integration_service.errors import conflict, not_found
from integration_service.http_utils import error_responses, ok, ok_response

router = APIRouter(prefix="/v1/user/channel-groups", tags=["channel-groups"], dependencies=[Depends(user_bearer)])

_EXAMPLE = {"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "owner_account_id": "acc_abc123", "name": "Vietnam",
            "country_code": "VN", "sort_order": 0, "created_at": "2026-10-12T00:00:00Z"}


@router.get("", summary="Danh sách nhóm quốc gia + kênh",
            description="Chỉ thấy nhóm của chính mình (owner suy từ `sub` của access token).",
            responses={200: ok_response(Envelope[list[ChannelGroupOut]], "Danh sách nhóm", [_EXAMPLE]),
                       **error_responses(401, 422, 503)})
async def list_groups(request: Request, principal: Principal = Depends(require_user)):
    c = get_container(request)
    async with c.db.connection() as conn:
        rows = await repository.list_by_owner(conn, principal.sub)
    return ok(rows)


@router.post("", status_code=201, summary="Tạo nhóm quốc gia + kênh",
             description="Owner = `sub` của access token (không nhận từ body). Tên trùng trong phạm vi owner => 409.",
             responses={201: ok_response(Envelope[ChannelGroupOut], "Nhóm đã tạo", _EXAMPLE),
                        **error_responses(401, 409, 422, 429, 503)})
async def create_group(request: Request, body: ChannelGroupCreate, principal: Principal = Depends(require_user)):
    c = get_container(request)
    c.user_write_limiter.check(principal.sub, "Thao tác quá nhanh, thử lại sau")
    try:
        async with c.db.transaction() as conn:
            row = await repository.create(conn, principal.sub, body.name, body.country_code)
    except asyncpg.UniqueViolationError:
        raise conflict(ErrorCode.REQUEST_VALIDATION_FAILED, "Tên nhóm đã tồn tại") from None
    return ok(row, status=201)


@router.patch("/{group_id}", summary="Sửa nhóm quốc gia + kênh",
              description="Chỉ chủ nhóm mới sửa được. Không tìm thấy => 404. Đổi sang tên đã dùng => 409.",
              responses={200: ok_response(Envelope[ChannelGroupOut], "Nhóm sau khi sửa", _EXAMPLE),
                         **error_responses(401, 404, 409, 422, 429, 503)})
async def update_group(request: Request, group_id: str, body: ChannelGroupUpdate,
                       principal: Principal = Depends(require_user)):
    c = get_container(request)
    c.user_write_limiter.check(principal.sub, "Thao tác quá nhanh, thử lại sau")
    fields = body.model_dump(exclude_unset=True)
    try:
        async with c.db.transaction() as conn:
            row = await repository.update(conn, group_id, principal.sub, **fields)
    except asyncpg.UniqueViolationError:
        raise conflict(ErrorCode.REQUEST_VALIDATION_FAILED, "Tên nhóm đã tồn tại") from None
    if row is None:
        raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy nhóm")
    return ok(row)


@router.delete("/{group_id}", summary="Xoá nhóm quốc gia + kênh",
               description="Chỉ chủ nhóm mới xoá được. Không tìm thấy => 404.",
               responses={200: ok_response(Envelope[dict], "Đã xoá", {"deleted": True}),
                          **error_responses(401, 404, 422, 429, 503)})
async def delete_group(request: Request, group_id: str, principal: Principal = Depends(require_user)):
    c = get_container(request)
    c.user_write_limiter.check(principal.sub, "Thao tác quá nhanh, thử lại sau")
    async with c.db.transaction() as conn:
        deleted = await repository.delete(conn, group_id, principal.sub)
    if not deleted:
        raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy nhóm")
    return ok({"deleted": True})
