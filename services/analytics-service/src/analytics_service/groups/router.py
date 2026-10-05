"""Nhóm kênh (CRUD theo chủ sở hữu) + nhãn nền tảng. Dưới /v1/analytics/groups và alias legacy /v1/viewer/groups."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from manager_contracts import ApiError, ErrorCode
from manager_sdk import Principal
from pydantic import BaseModel, Field

from analytics_service.deps import User, container
from analytics_service.filters import FilterQuery
from analytics_service.groups import repository as repo
from analytics_service.readmodels import repository as readrepo
from analytics_service.readmodels.query import QuerySpec

router = APIRouter(tags=["groups"])


class NameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AddMemberBody(BaseModel):
    channel_ref: str = Field(..., min_length=1, max_length=200)
    platform_tag: str = Field("", max_length=50)
    country_override: str = Field("", max_length=50)


async def _owned(conn, group_id: str, principal: Principal) -> dict:
    """Chặn sửa nhóm của người khác. admin/dash_admin XEM được nhóm của bất kỳ ai nhưng KHÔNG sửa hộ."""
    group = await repo.get_group(conn, group_id)
    if not group:
        raise ApiError(ErrorCode.NOT_FOUND, "Không tìm thấy group", status_code=404)
    if group["account_id"] != principal.sub:
        raise ApiError(ErrorCode.SCOPE_DENIED, "Không có quyền sửa group này", status_code=403)
    return group


@router.post("/groups", summary="Tạo nhóm kênh")
async def create_group(body: NameBody, principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        group = await repo.create_group(conn, principal.sub, body.name.strip())
        await readrepo.bump_seal(conn)
    return group


@router.get("/groups", summary="Nhóm của chính mình, hoặc của account chỉ định nếu admin/dash_admin")
async def list_groups(owner_account_id: str = Query("", max_length=64), principal: Principal = User,
                      c=Depends(container)) -> dict:
    target = owner_account_id if (owner_account_id and principal.is_admin) else principal.sub
    async with c.db.connection() as conn:
        return {"items": await repo.list_groups(conn, target)}


@router.patch("/groups/{group_id}", summary="Đổi tên nhóm")
async def rename_group(group_id: str, body: NameBody, principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        await _owned(conn, group_id, principal)
        await repo.rename_group(conn, group_id, body.name.strip())
        await readrepo.bump_seal(conn)
    return {"ok": True}


@router.delete("/groups/{group_id}", summary="Xoá nhóm")
async def delete_group(group_id: str, principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        await _owned(conn, group_id, principal)
        await repo.delete_group(conn, group_id)
        await readrepo.bump_seal(conn)
    return {"ok": True}


@router.post("/groups/{group_id}/members", summary="Thêm/cập nhật kênh vào nhóm")
async def add_member(group_id: str, body: AddMemberBody, principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        await _owned(conn, group_id, principal)
        member = await repo.upsert_member(conn, group_id, body.channel_ref.strip(), body.platform_tag.strip(),
                                          body.country_override.strip())
        await readrepo.bump_seal(conn)
    return member


@router.delete("/groups/{group_id}/members/{channel_ref}", summary="Gỡ kênh khỏi nhóm")
async def remove_member(group_id: str, channel_ref: str, principal: Principal = User, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        await _owned(conn, group_id, principal)
        if not await repo.remove_member(conn, group_id, channel_ref):
            raise ApiError(ErrorCode.NOT_FOUND, "Không tìm thấy kênh trong group", status_code=404)
        await readrepo.bump_seal(conn)
    return {"ok": True}


# ── Nhãn nền tảng tự do (đi qua QueryService để có filter + freshness như mọi danh mục lọc) ──

def _tags_from_dataset(ds, ctx, sel, extra) -> dict:
    return {"items": sorted({m["platform_tag"] for g in ds.groups.values() for m in g.members.values()
                             if m["platform_tag"]})}


SPEC_PLATFORM_TAGS = QuerySpec("platform-tags", _tags_from_dataset, window="none", ignore_country=True)


@router.get("/platform-tags", summary="Danh sách nhãn nền tảng đã gắn (tự do, từ nhóm kênh)")
async def platform_tags(response: Response, query: FilterQuery = Depends(), principal: Principal = User,
                        c=Depends(container)) -> dict:
    return await c.query.run(SPEC_PLATFORM_TAGS, principal, query, response)
