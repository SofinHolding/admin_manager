"""Endpoint quản trị `/v1/reward/admin/*` — chỉ `role == 'admin'` (verify cục bộ, không gọi admin_manager).

Cấp/thu role `discord` bằng thao tác SQL trực tiếp trên bảng `accounts` của admin_manager (chỉ ghi cột
`role`, không đi qua pydantic `^(viewer|admin)$` của `admin_api.py`) — xem plan A.4. Mọi lần đổi role
ghi `reward_role_audit` trong cùng transaction (xem `store/repositories/accounts.py`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from security import AuthContext, Security
from store.pool import Pool
from store.repositories import accounts as accounts_repo

logger = logging.getLogger("reward.admin")


class RoleChangeBody(BaseModel):
    reason: str = Field("", max_length=500)


def make_router(security: Security, pool: Pool) -> APIRouter:
    router = APIRouter(prefix="/v1/reward/admin", tags=["reward-admin"])
    Admin = Depends(security.require_admin_role)
    Active = Depends(security.require_active_account)

    @router.get("/accounts", summary="Danh sách tài khoản (đọc accounts + làm giàu reward_*)")
    async def list_accounts(
        q: str | None = None, role: str | None = None, status_: str | None = None,
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        rows = await accounts_repo.list_accounts(pool, q=q, role=role, status=status_)
        return {"accounts": rows}

    @router.post("/accounts/{account_id}/grant-discord", summary="Cấp quyền reward (role='discord')")
    async def grant_discord(
        account_id: str, body: RoleChangeBody,
        admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        if account_id == admin.account_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không thể tự đổi role của chính mình")
        try:
            result = await accounts_repo.grant_discord(
                pool, target_id=account_id, actor_id=admin.account_id, reason=body.reason)
        except accounts_repo.AccountNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài khoản") from exc
        except accounts_repo.RoleChangeConflict as exc:
            message = ("Không thể đổi role của quản trị viên" if exc.current_role == "admin"
                       else "Tài khoản không đủ điều kiện để gán quyền discord (không active hoặc role không hợp lệ)")
            raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
        logger.info("grant-discord: %s -> discord bởi %s", account_id, admin.account_id)
        return {"ok": True, "username": result["username"], "from_role": result["from_role"],
                "to_role": result["to_role"]}

    @router.post("/accounts/{account_id}/revoke-discord", summary="Thu quyền reward (role về 'viewer')")
    async def revoke_discord(
        account_id: str, body: RoleChangeBody,
        admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        if account_id == admin.account_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không thể tự đổi role của chính mình")
        try:
            result = await accounts_repo.revoke_discord(
                pool, target_id=account_id, actor_id=admin.account_id, reason=body.reason)
        except accounts_repo.AccountNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài khoản") from exc
        except accounts_repo.RoleChangeConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Tài khoản không ở role discord (hiện tại: {exc.current_role})") from exc
        logger.info("revoke-discord: %s -> viewer bởi %s", account_id, admin.account_id)
        return {"ok": True, "to_role": result["to_role"], "paused_jobs": result["paused_jobs"]}

    @router.get("/role-audit", summary="Lịch sử đổi role")
    async def role_audit(
        account_id: str | None = None, limit: int = 100,
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        entries = await accounts_repo.list_role_audit(pool, account_id=account_id, limit=limit)
        return {"entries": entries}

    return router
