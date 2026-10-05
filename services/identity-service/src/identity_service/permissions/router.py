"""Permission API: `/v1/admin/permissions`, `/v1/accounts/{id}/permissions`, `/v1/admin/accounts/{id}/permissions/{perm}`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from identity_service.deps import bearer_token, get_container, require_admin

router = APIRouter(tags=["permissions"])


async def _actor(request: Request, authorization: str = Header("")) -> dict:
    return await get_container(request).auth.verify_access_token(bearer_token(authorization))


@router.get("/v1/admin/permissions", summary="Catalog permission")
async def catalog(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).permissions.catalog()


@router.get("/v1/accounts/{account_id}/permissions", summary="Permission hiệu lực của một tài khoản")
async def list_permissions(account_id: str, request: Request, actor: dict = Depends(_actor)) -> dict:
    return await get_container(request).permissions.list_for(actor, account_id)


@router.put("/v1/admin/accounts/{account_id}/permissions/{permission}", summary="Cấp permission")
async def grant(account_id: str, permission: str, request: Request, actor: dict = Depends(require_admin)) -> dict:
    return await get_container(request).permissions.grant(actor, account_id, permission)


@router.delete("/v1/admin/accounts/{account_id}/permissions/{permission}", summary="Thu permission")
async def revoke(account_id: str, permission: str, request: Request, actor: dict = Depends(require_admin)) -> dict:
    return await get_container(request).permissions.revoke(actor, account_id, permission)
