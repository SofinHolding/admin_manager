"""Admin API quản trị OIDC client: `/v1/admin/oidc/clients` (admin/dash_admin)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from identity_service.deps import get_container, require_admin

router = APIRouter(prefix="/v1/admin/oidc/clients", tags=["oidc-admin"])


@router.post("", summary="Đăng ký OIDC client (secret của confidential client chỉ hiển thị một lần)")
async def create_client(request: Request, body: dict = Body(...), admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).oidc_clients.create(admin, body)


@router.get("", summary="Danh sách OIDC client")
async def list_clients(request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).oidc_clients.list()


@router.get("/{client_id}", summary="Chi tiết OIDC client")
async def get_client(client_id: str, request: Request, _: dict = Depends(require_admin)) -> dict:
    return await get_container(request).oidc_clients.get(client_id)


@router.patch("/{client_id}", summary="Sửa OIDC client (rotate_secret=true để xoay secret)")
async def update_client(client_id: str, request: Request, body: dict = Body(...),
                        admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).oidc_clients.update(admin, client_id, body)


@router.delete("/{client_id}", summary="Xoá OIDC client và thu hồi phiên đã cấp")
async def delete_client(client_id: str, request: Request, admin: dict = Depends(require_admin)) -> dict:
    return await get_container(request).oidc_clients.delete(admin, client_id)
