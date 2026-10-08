"""Endpoint `/v1/admin/accounts/{id}/capabilities` — quản lý capability (docs/architecture/capability-model-v1.md §7).

Capability chỉ là permission namespace `capability:*`: tái dùng `PermissionService` (catalog + grant/revoke +
audit + event `account.permission_changed`) của module `permissions`, KHÔNG có nghiệp vụ/bảng riêng. Chỉ khác
ở hai điểm: (1) chỉ `admin` đúng nghĩa được gọi (không gồm `dash_admin`), (2) tên permission phải có prefix
`capability:`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from identity_service.deps import get_container, require_full_admin
from identity_service.errors import DomainError
from identity_service.permissions import repository as perms_repo
from identity_service.permissions.capabilities import CAPABILITY_PREFIX

router = APIRouter(prefix="/v1/admin/accounts", tags=["capabilities"])


class GrantCapabilitiesBody(BaseModel):
    capabilities: list[str] = Field(..., min_length=1, max_length=32)


def _check_capability_name(name: str) -> None:
    if not name.startswith(CAPABILITY_PREFIX):
        raise DomainError(422, "Chỉ quản lý được permission capability:*", code="REQUEST_VALIDATION_FAILED")


@router.get("/{account_id}/capabilities", summary="Danh sách capability hiện tại của tài khoản")
async def list_capabilities(account_id: str, request: Request,
                            _: dict = Depends(require_full_admin)) -> dict:
    container = get_container(request)
    async with container.db.connection() as conn:
        granted = await perms_repo.granted(conn, account_id)
    return {"account_id": account_id,
            "capabilities": [p for p in granted if p.startswith(CAPABILITY_PREFIX)]}


@router.post("/{account_id}/capabilities", summary="Cấp capability cho tài khoản")
async def grant_capabilities(account_id: str, body: GrantCapabilitiesBody, request: Request,
                             admin: dict = Depends(require_full_admin)) -> dict:
    container = get_container(request)
    granted: list[str] = []
    for cap in body.capabilities:
        _check_capability_name(cap)
        result = await container.permissions.grant(admin, account_id, cap)
        if result["changed"]:
            granted.append(cap)
    return {"ok": True, "account_id": account_id, "granted": granted}


@router.delete("/{account_id}/capabilities/{cap_name}", summary="Thu hồi capability của tài khoản")
async def revoke_capability(account_id: str, cap_name: str, request: Request,
                            admin: dict = Depends(require_full_admin)) -> dict:
    _check_capability_name(cap_name)
    container = get_container(request)
    result = await container.permissions.revoke(admin, account_id, cap_name)
    return {"ok": True, "account_id": account_id, "revoked": cap_name, "changed": result["changed"]}
