"""Alias và tên chuẩn quốc gia (admin/dash_admin). Dưới /v1/analytics/admin/* và alias legacy /v1/admin/*.

Hình dạng response giữ nguyên admin-server cũ. Mọi thay đổi ghi PostgreSQL + tăng seal cache trong CÙNG transaction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from manager_contracts import ApiError, ErrorCode
from pydantic import BaseModel, Field

from analytics_service.country_config import repository as repo
from analytics_service.deps import Admin, container
from analytics_service.readmodels import repository as readrepo

router = APIRouter(tags=["country-config"])


class CountryAliasBody(BaseModel):
    alias: str = Field(..., min_length=1, max_length=100)
    canonical: str = Field(..., min_length=1, max_length=100)


class CanonicalBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


@router.get("/country-canonicals", summary="Danh sách tên quốc gia chuẩn")
async def list_canonicals(_=Admin, c=Depends(container)) -> dict:
    async with c.db.connection() as conn:
        return {"items": await repo.list_canonicals(conn)}


@router.post("/country-canonicals", summary="Tạo tên quốc gia chuẩn mới")
async def create_canonical(body: CanonicalBody, _=Admin, c=Depends(container)) -> dict:
    name = body.name.strip()
    async with c.db.transaction() as conn:
        existing = await repo.get_canonical(conn, name)
        if existing:
            raise ApiError(ErrorCode.RESOURCE_CONFLICT, f'Tên chuẩn "{existing["name"]}" đã tồn tại', status_code=409)
        row = await repo.create_canonical(conn, name)
        await readrepo.bump_seal(conn)
    return row


@router.patch("/country-canonicals/{name}", summary="Đổi tên 1 tên quốc gia chuẩn (cascade sang alias)")
async def rename_canonical(name: str, body: CanonicalBody, _=Admin, c=Depends(container)) -> dict:
    new_name = body.name.strip()
    async with c.db.transaction() as conn:
        current = await repo.get_canonical(conn, name)
        if not current:
            raise ApiError(ErrorCode.NOT_FOUND, "Không tìm thấy tên chuẩn", status_code=404)
        if new_name.lower() != current["name"].lower():
            conflict = await repo.get_canonical(conn, new_name)
            if conflict:
                raise ApiError(ErrorCode.RESOURCE_CONFLICT, f'Tên chuẩn "{conflict["name"]}" đã tồn tại',
                               status_code=409)
        await repo.rename_canonical(conn, current["name"], new_name)
        await readrepo.bump_seal(conn)
    return {"ok": True, "name": new_name}


@router.delete("/country-canonicals/{name}", summary="Xoá 1 tên quốc gia chuẩn")
async def delete_canonical(name: str, _=Admin, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        row = await repo.get_canonical(conn, name)
        if not row:
            raise ApiError(ErrorCode.NOT_FOUND, "Không tìm thấy tên chuẩn", status_code=404)
        in_use = await repo.count_aliases_for(conn, row["name"])
        if in_use:
            raise ApiError(ErrorCode.RESOURCE_CONFLICT,
                           f"Còn {in_use} biến thể đang dùng tên chuẩn này — xoá hết biến thể trong nhóm trước khi xoá "
                           "tên chuẩn", status_code=409)
        await repo.delete_canonical(conn, row["name"])
        await readrepo.bump_seal(conn)
    return {"ok": True}


@router.get("/country-aliases", summary="Danh sách chuẩn hoá quốc gia (alias → tên chuẩn)")
async def list_aliases(_=Admin, c=Depends(container)) -> dict:
    async with c.db.connection() as conn:
        return {"items": await repo.list_aliases(conn)}


@router.post("/country-aliases", summary="Thêm/sửa 1 dòng chuẩn hoá quốc gia")
async def upsert_alias(body: CountryAliasBody, _=Admin, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        canon = await repo.get_canonical(conn, body.canonical.strip())
        if not canon:
            raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED,
                           'Tên chuẩn chưa tồn tại — tạo tên chuẩn trước rồi chọn lại', status_code=400)
        # Dùng đúng chữ hoa/thường đã lưu của tên chuẩn: loại rủi ro tạo nhóm trùng do gõ lệch case.
        row = await repo.upsert_alias(conn, body.alias, canon["name"])
        await readrepo.bump_seal(conn)
    return row


@router.delete("/country-aliases/{alias}", summary="Xoá 1 dòng chuẩn hoá quốc gia")
async def delete_alias(alias: str, _=Admin, c=Depends(container)) -> dict:
    async with c.db.transaction() as conn:
        if not await repo.delete_alias(conn, alias):
            raise ApiError(ErrorCode.NOT_FOUND, "Không tìm thấy alias", status_code=404)
        await readrepo.bump_seal(conn)
    return {"ok": True}
