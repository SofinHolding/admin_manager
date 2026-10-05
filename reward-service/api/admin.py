"""Endpoint quản trị `/v1/reward/admin/*` — cần `reward:admin` (hoặc role admin), verify cục bộ từ claim `perms`.

Quyền reward do IDENTITY quản lý. `grant-discord` / `revoke-discord` / `accounts` GIỮ đường dẫn và hình dạng response
cũ nhưng được cài bằng cách gọi API CÔNG KHAI của Identity với Bearer token CỦA CHÍNH người gọi:
    grant/revoke → PUT|DELETE /v1/admin/accounts/{id}/permissions/reward:access
    danh sách    → GET /v1/admin/users (lọc + làm giàu bằng bảng reward_* của Reward)
Identity chết ⇒ 503 `UNAVAILABLE` (không treo). `reward_role_audit` là audit RIÊNG của Reward, ghi cả kết quả gọi Identity.
Hiệu lực cục bộ (denylist, tạm dừng job) áp NGAY sau khi Identity xác nhận; event feed đến sau là no-op idempotent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from engine.worker_manager import WorkerManager
from identity_link.access import apply_reward_access
from identity_link.client import REWARD_ACCESS, IdentityClient, IdentityUnavailable
from security import AuthContext, Security
from store.pool import Pool
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo
from store.repositories import projection as proj
from store.repositories import role_audit as audit_repo

logger = logging.getLogger("reward.admin")


class RoleChangeBody(BaseModel):
    reason: str = Field("", max_length=500)


def _detail(resp) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("message") or "Identity từ chối yêu cầu")
    except ValueError:
        pass
    return "Identity từ chối yêu cầu"


def make_router(security: Security, pool: Pool, identity: IdentityClient, worker_manager: WorkerManager) -> APIRouter:
    router = APIRouter(prefix="/v1/reward/admin", tags=["reward-admin"])
    Admin = Depends(security.require_admin_role)
    Active = Depends(security.require_active_account)

    async def _audit(account_id: str, actor: AuthContext, from_role: str, to_role: str, reason: str,
                     outcome: str, identity_status: int | None) -> None:
        async with pool.acquire() as conn:
            await audit_repo.record(
                conn, account_id=account_id, actor_id=actor.account_id, from_role=from_role, to_role=to_role,
                reason=reason, outcome=outcome, identity_status=identity_status)

    async def _find_user(authorization: str, account_id: str) -> dict[str, Any]:
        """Tra tài khoản đích qua API công khai của Identity (404 nếu không có)."""
        resp = await identity.list_users(authorization)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, _detail(resp))
        for user in resp.json().get("users", []):
            if user.get("id") == account_id:
                return user
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài khoản")

    async def _change(account_id: str, body: RoleChangeBody, admin: AuthContext, authorization: str, *, grant: bool):
        if account_id == admin.account_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không thể tự đổi role của chính mình")
        before, after = ("none", REWARD_ACCESS) if grant else (REWARD_ACCESS, "none")
        try:
            user = await _find_user(authorization, account_id)
        except IdentityUnavailable:
            await _audit(account_id, admin, before, after, body.reason, "unavailable", None)
            raise
        role = str(user.get("role") or "")
        if grant:
            if role == "admin":
                raise HTTPException(status.HTTP_409_CONFLICT, "Không thể đổi role của quản trị viên")
            if user.get("status") != "active":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Tài khoản không đủ điều kiện để gán quyền discord (không active hoặc role không hợp lệ)")
        try:
            resp = await identity.set_permission(authorization, account_id, REWARD_ACCESS, grant=grant)
        except IdentityUnavailable:
            await _audit(account_id, admin, before, after, body.reason, "unavailable", None)
            raise
        if not 200 <= resp.status_code < 300:
            await _audit(account_id, admin, before, after, body.reason, "rejected", resp.status_code)
            raise HTTPException(resp.status_code, _detail(resp))
        result = resp.json()
        changed = bool(result.get("changed", True))
        if not grant and not changed:
            await _audit(account_id, admin, before, after, body.reason, "noop", resp.status_code)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Tài khoản không có quyền reward được cấp riêng để thu hồi (role hiện tại: {role})")
        await _audit(account_id, admin, before, after, body.reason, "applied", resp.status_code)
        return user, changed

    @router.get("/accounts", summary="Danh sách tài khoản (qua API Identity, làm giàu bằng dữ liệu reward_*)")
    async def list_accounts(
        q: str | None = None, role: str | None = None, status_: str | None = Query(None, alias="status"),
        authorization: str = Header(""), _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        resp = await identity.list_users(authorization)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, _detail(resp))
        users = resp.json().get("users", [])
        needle = (q or "").lower()
        users = [
            u for u in users
            if (not needle or needle in str(u.get("username", "")).lower() or needle in str(u.get("email", "")).lower())
            and (not role or u.get("role") == role) and (not status_ or u.get("status") == status_)
        ]
        ids = [u["id"] for u in users]
        async with pool.acquire() as conn:
            projections = await proj.projections_by_ids(conn, ids)
            creds = {r["account_id"]: r["status"] for r in await conn.fetch(
                "SELECT account_id, status FROM reward_discord_credentials WHERE account_id = ANY($1::text[])", ids)}
            jobs = {r["account_id"]: r for r in await conn.fetch(
                "SELECT account_id, count(*) AS jobs_total, count(*) FILTER (WHERE status = 'running') AS jobs_running "
                "FROM reward_jobs WHERE account_id = ANY($1::text[]) GROUP BY account_id", ids)}
        out = []
        for u in users:
            p = projections.get(u["id"]) or {}
            j = jobs.get(u["id"])
            out.append({
                "id": u["id"], "username": u.get("username"), "email": u.get("email"), "role": u.get("role"),
                "status": u.get("status"), "created_at": u.get("created_at"), "last_login": u.get("last_login"),
                "reward_access": bool(p.get("reward_access")) or u.get("role") in ("discord", "admin"),
                "has_credential": u["id"] in creds, "credential_status": creds.get(u["id"]),
                "jobs_total": j["jobs_total"] if j else 0, "jobs_running": j["jobs_running"] if j else 0,
            })
        return {"accounts": out}

    @router.post("/accounts/{account_id}/grant-discord", summary="Cấp quyền reward (Identity: reward:access)")
    async def grant_discord(
        account_id: str, body: RoleChangeBody, authorization: str = Header(""),
        admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        user, changed = await _change(account_id, body, admin, authorization, grant=True)
        async with pool.acquire() as conn:
            await apply_reward_access(conn, account_id=account_id, granted=True)
            await proj.upsert_projection(
                conn, account_id=account_id, username=user.get("username"), role=user.get("role"),
                status=user.get("status"))
        logger.info("grant reward:access: %s bởi %s", account_id, admin.account_id)
        return {"ok": True, "username": user.get("username"), "from_role": user.get("role"), "to_role": "discord",
                "permission": REWARD_ACCESS, "changed": changed}

    @router.post("/accounts/{account_id}/revoke-discord", summary="Thu quyền reward (Identity: reward:access)")
    async def revoke_discord(
        account_id: str, body: RoleChangeBody, authorization: str = Header(""),
        admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        user, _changed = await _change(account_id, body, admin, authorization, grant=False)
        async with pool.acquire() as conn:
            await apply_reward_access(conn, account_id=account_id, granted=False)
        paused = await worker_manager.pause_account_jobs(account_id, "ROLE_REVOKED")
        logger.info("revoke reward:access: %s bởi %s (pause %s job)", account_id, admin.account_id, len(paused))
        return {"ok": True, "to_role": "viewer", "paused_jobs": paused, "permission": REWARD_ACCESS}

    @router.get("/role-audit", summary="Lịch sử thao tác quyền (audit riêng của Reward)")
    async def role_audit(
        account_id: str | None = None, limit: int = 100,
        _admin: AuthContext = Admin, _active: AuthContext = Active,
    ) -> dict:
        async with pool.acquire() as conn:
            entries = await audit_repo.list_entries(conn, account_id=account_id, limit=limit)
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
