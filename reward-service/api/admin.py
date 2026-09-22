"""Endpoint quản trị `/v1/reward/admin/*` — chỉ `role == 'admin'` (verify cục bộ, không gọi admin_manager).

Cấp/thu role `discord` bằng thao tác SQL trực tiếp trên bảng `accounts` của admin_manager (chỉ ghi cột
`role`, không đi qua pydantic `^(viewer|admin)$` của `admin_api.py`) — xem plan A.4. Mọi lần đổi role
ghi `reward_role_audit` trong cùng transaction (xem `store/repositories/accounts.py`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from security import AuthContext, Security
from store.pool import Pool
from store.repositories import accounts as accounts_repo
from store.repositories import jobs as jobs_repo
from store.repositories import items as items_repo
from store.repositories import lock as lock_repo

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

    @router.get("/jobs", summary="Tất cả job (mọi tài khoản) — chỉ admin")
    async def admin_list_jobs(
        status_: str | None = Query(None, alias="status"),
        account_id: str | None = None,
        limit: int = 50, offset: int = 0,
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        async with pool.acquire() as conn:
            rows, total = await jobs_repo.list_jobs(
                conn, account_id=account_id, status=status_,
                limit=min(max(limit, 1), 200), offset=max(offset, 0))
            out = []
            for row in rows:
                counts = await items_repo.counts(conn, row["id"])
                out.append({
                    "id": row["id"], "name": row["name"], "status": row["status"],
                    "total_items": row["total_items"], "counts": counts,
                    "created_at": row["created_at"], "started_at": row["started_at"],
                    "finished_at": row["finished_at"], "owner_username": row.get("owner_username"),
                })
        return {"jobs": out, "total": total}

    @router.get("/locks", summary="Danh sách runner lock đang hoạt động — chỉ admin")
    async def admin_list_locks(
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_id, account_id, channel_id, pid, host, heartbeat_at, "
                "EXTRACT(EPOCH FROM (now() - heartbeat_at)) AS age_s "
                "FROM reward_runner_lock ORDER BY heartbeat_at DESC"
            )
        return {"locks": [dict(r) for r in rows]}

    @router.delete("/locks/{job_id}", summary="Giải phóng runner lock thủ công — chỉ admin")
    async def admin_release_lock(
        job_id: int,
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        async with pool.acquire() as conn:
            await lock_repo.release(conn, job_id=job_id)
        logger.info("admin force-release lock job_id=%s", job_id)
        return {"ok": True}

    return router
