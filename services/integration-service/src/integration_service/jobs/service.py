"""Nghiệp vụ job cho Fallback Tool.

Vòng đời: `pending -> leased -> done | pending (retry) | failed (không retry) | dead (hết lượt thử)`; `cancelled` do user.
Lease có hạn: hết hạn mà chưa complete/fail thì job quay về `pending` (hoặc `dead` nếu đã dùng hết lượt thử) — worker
nền VÀ lazy ở mỗi lần claim. Lượt thử (`attempts`) được tính lúc claim.

Ownership: owner của job do SERVER gán (chủ resource); client chỉ claim/complete/fail job của chính owner mình, trừ khi có
scope `jobs:claim:system` (do admin cấp cho `fallback_tool`). Không có đường nào nhận owner từ body.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta

import asyncpg
from manager_contracts import ErrorCode
from manager_sdk import Principal

from integration_service.clients.policy import SYSTEM_SCOPE
from integration_service.config import Settings
from integration_service.credentials.service import MachineContext
from integration_service.db import Database
from integration_service.errors import ApiProblem, Extra, conflict, forbidden, invalid, not_found
from integration_service.installations import repository as installations
from integration_service.jobs import repository
from integration_service.platforms import repository as registry
from integration_service.security import new_id

FALLBACK_MODE = "fallback_tool"   # execution_mode canonical của resource chạy bằng Fallback Tool
MAX_PAYLOAD_BYTES = 16 * 1024


def _size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def job_out(row: dict) -> dict:
    return {"job_id": row["id"], "owner_account_id": row["owner_account_id"], "resource_id": row["resource_id"],
            "platform": row["platform"], "kind": row["kind"], "status": row["status"], "priority": row["priority"],
            "attempts": row["attempts"], "max_attempts": row["max_attempts"], "payload": row["payload"],
            "required_capability": row["required_capability"], "resource_owner_id": row["resource_owner_id"],
            "run_after": row["run_after"], "lease_until": row["lease_until"],
            "claimed_by_client": row["claimed_by_client"], "result": row["result"],
            "last_error_code": row["last_error_code"], "last_error_message": row["last_error_message"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"]}


def transition_out(row: dict, *, replayed: bool = False) -> dict:
    return {"job_id": row["id"], "status": row["status"], "attempts": row["attempts"],
            "max_attempts": row["max_attempts"], "run_after": row["run_after"], "lease_until": row["lease_until"],
            "completed_at": row["completed_at"], "replayed": replayed}


class JobService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime]) -> None:
        self._db = db
        self._settings = settings
        self._now = now

    # ── Mặt phẳng USER: tạo / xem / huỷ ─────────────────────────────────────────────

    async def create(self, conn: asyncpg.Connection, principal: Principal, body, *, created_by: str | None = None,
                     now: datetime | None = None, resource_id: str | None = None,
                     resource_owner_id: str | None = None) -> tuple[dict, bool]:
        """(job, created). Resource phải thuộc user (admin: bất kỳ) và ở `execution_mode=fallback_tool`. Owner = chủ resource.

        `resource_owner_id` (PHASE 4, chỉ truyền cho job `kind='collect'`): owner dữ liệu cho delegated ingestion
        (snapshots/router.py `_delegated`). Mặc định `None` — mọi job khác (và mọi job tạo trước PHASE 4) giữ
        hành vi cũ, không hỗ trợ delegated ingestion."""
        now = now or self._now()
        res = await conn.fetchrow("SELECT * FROM resources WHERE id = $1", resource_id or body.resource_id)
        if res is None or (res["owner_account_id"] != principal.sub and not principal.is_admin):
            raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy resource")   # không lộ resource của người khác
        if res["removed"] or res["state"] != "active" or res["execution_mode"] != FALLBACK_MODE:
            raise conflict(Extra.JOB_NOT_ELIGIBLE, "Resource chưa ở chế độ fallback_tool hoặc đang bị tắt/đã gỡ")
        if _size(body.payload) > MAX_PAYLOAD_BYTES:
            raise invalid("payload quá lớn", {"max_bytes": MAX_PAYLOAD_BYTES})
        owner = res["owner_account_id"]
        # Cùng owner -> tuần tự hoá để hai request đồng thời không cùng vượt hạn mức / tạo trùng dedupe_key.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "job-create:" + owner)
        if body.dedupe_key:
            existing = await repository.find_active_by_dedupe(conn, owner, body.dedupe_key)
            if existing is not None:
                return existing, False
        if await repository.count_pending(conn, owner) >= self._settings.job_max_pending_per_owner:
            raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số job đang chờ/đang chạy của owner", status_code=409)
        row = await repository.insert(
            conn, job_id=new_id("job"), owner=owner, resource_id=res["id"], platform=res["platform"], kind=body.kind,
            required_capability=body.required_capability, priority=body.priority, payload=body.payload,
            max_attempts=body.max_attempts or self._settings.job_max_attempts, dedupe_key=body.dedupe_key,
            created_by=created_by or principal.sub, now=now, resource_owner_id=resource_owner_id)
        return row, True

    async def create_job(self, principal: Principal, body) -> tuple[dict, bool]:
        async with self._db.transaction() as conn:
            return await self.create(conn, principal, body)

    @staticmethod
    def _visible_to_user(row: dict | None, principal: Principal) -> dict:
        if row is None or (row["owner_account_id"] != principal.sub and not principal.is_admin):
            raise not_found(ErrorCode.JOB_NOT_FOUND, "Không tìm thấy job")
        return row

    async def get_for_user(self, principal: Principal, job_id: str) -> dict:
        async with self._db.connection() as conn:
            return self._visible_to_user(await repository.get(conn, job_id), principal)

    async def list_for_user(self, principal: Principal, *, owner: str | None, status: str | None, limit: int,
                            offset: int) -> list[dict]:
        if not principal.is_admin:
            if owner and owner != principal.sub:
                return []
            owner = principal.sub
        async with self._db.connection() as conn:
            return await repository.list_for(conn, owner=owner, status=status, limit=limit, offset=offset)

    async def cancel(self, conn: asyncpg.Connection, principal: Principal, job_id: str, *,
                     now: datetime | None = None) -> dict:
        now = now or self._now()
        row = self._visible_to_user(await repository.get(conn, job_id, lock=True), principal)
        if row["status"] == "cancelled":
            return row   # idempotent
        if row["status"] not in ("pending", "leased"):
            raise conflict(Extra.JOB_INVALID_STATE, f"Job đã ở trạng thái cuối ({row['status']})")
        return await repository.update_state(conn, row, status="cancelled", lease_until=None, lease_token=None,
                                             completed_at=now, updated_at=now)

    async def cancel_job(self, principal: Principal, job_id: str) -> dict:
        async with self._db.transaction() as conn:
            return await self.cancel(conn, principal, job_id)

    # ── Mặt phẳng MÁY: claim / heartbeat / complete / fail ──────────────────────────

    async def claim(self, ctx: MachineContext, body) -> dict:
        now = self._now()
        if body.installation_id != ctx.installation_id:
            raise forbidden(Extra.INSTALLATION_MISMATCH,
                            "installation_id trong body phải trùng installation đã bind cho credential này")
        async with self._db.transaction() as conn:
            if not await installations.exists_for_client(conn, body.installation_id, ctx.client_id):
                raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy installation của client này")
            installation = await conn.fetchrow("SELECT capabilities FROM installations WHERE id = $1",
                                               body.installation_id)
            usable = set(ctx.allowed_platforms) & await registry.enabled_platforms(conn)
            requested = set(await registry.resolve_many(conn, list(body.platforms))) or usable
            denied = sorted(requested - usable)
            if denied:
                raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform không nằm trong phạm vi client được phép",
                                {"platforms": denied[:10]})
            # Capability hiệu lực = khai báo lúc claim ∩ khai báo lúc handshake của chính installation này.
            capabilities = sorted(set(body.capabilities) & set(installation["capabilities"]))
            await repository.reclaim_expired(conn, now)
            rows = await repository.claim(
                conn, owner=ctx.owner_account_id, system=SYSTEM_SCOPE in ctx.scopes, platforms=sorted(requested),
                capabilities=capabilities, limit=body.limit,
                lease_seconds=min(body.lease_seconds, self._settings.job_lease_max_s), client_id=ctx.client_id,
                installation_id=body.installation_id, now=now)
            summaries = await repository.resource_summaries(conn, [r["resource_id"] for r in rows if r["resource_id"]])
        jobs = []
        for r in rows:
            res = summaries.get(r["resource_id"])
            ref = None
            if res is not None:
                data = res["data"] or {}
                ref = {"resource_id": res["id"], "platform": res["platform"], "resource_type": res["resource_type"],
                       "external_id": res["external_id"], "display_name": data.get("display_name"),
                       "username": data.get("username"), "url": data.get("url")}
            jobs.append({"job_id": r["id"], "owner_account_id": r["owner_account_id"], "kind": r["kind"],
                         "platform": r["platform"], "payload": r["payload"], "attempt": r["attempts"],
                         "max_attempts": r["max_attempts"], "lease_until": r["lease_until"],
                         "lease_token": r["lease_token"], "resource": ref})
        return {"jobs": jobs, "server_time": now}

    @staticmethod
    def _visible_to_client(row: dict | None, ctx: MachineContext) -> dict:
        """Job phải nhìn thấy được với client (cùng owner hoặc scope hệ thống) — nếu không thì như không tồn tại."""
        if row is None or (row["owner_account_id"] != ctx.owner_account_id and SYSTEM_SCOPE not in ctx.scopes):
            raise not_found(ErrorCode.JOB_NOT_FOUND, "Không tìm thấy job")
        return row

    @staticmethod
    def _require_holder(row: dict, ctx: MachineContext, token: str | None) -> None:
        if row["claimed_by_client"] is None:
            raise conflict(Extra.JOB_INVALID_STATE, "Job chưa được claim")
        if row["claimed_by_client"] != ctx.client_id or (
                token is not None and row["lease_token"] is not None and row["lease_token"] != token):
            raise conflict(ErrorCode.JOB_LEASE_EXPIRED, "Job không còn thuộc lease của client này")

    async def heartbeat(self, conn: asyncpg.Connection, ctx: MachineContext, job_id: str, body) -> dict:
        now = self._now()
        row = self._visible_to_client(await repository.get(conn, job_id, lock=True), ctx)
        self._require_holder(row, ctx, body.lease_token)
        if row["status"] == "pending":
            raise conflict(ErrorCode.JOB_LEASE_EXPIRED, "Lease đã hết hạn và job đã quay lại hàng đợi")
        if row["status"] != "leased":
            raise conflict(Extra.JOB_INVALID_STATE, f"Job không ở trạng thái leased ({row['status']})")
        lease = min(body.lease_seconds, self._settings.job_lease_max_s)
        row = await repository.update_state(conn, row, lease_until=now + timedelta(seconds=lease), updated_at=now)
        return transition_out(row)

    async def complete(self, conn: asyncpg.Connection, ctx: MachineContext, job_id: str, body) -> dict:
        now = self._now()
        if _size(body.result) > self._settings.job_result_max_bytes:
            raise invalid("result quá lớn", {"max_bytes": self._settings.job_result_max_bytes})
        row = self._visible_to_client(await repository.get(conn, job_id, lock=True), ctx)
        self._require_holder(row, ctx, body.lease_token)
        if row["status"] == "done":
            return transition_out(row, replayed=True)   # idempotent: complete lần hai không đổi gì
        if row["status"] == "pending":
            raise conflict(ErrorCode.JOB_LEASE_EXPIRED, "Lease đã hết hạn và job đã quay lại hàng đợi")
        if row["status"] != "leased":
            raise conflict(Extra.JOB_INVALID_STATE, f"Không thể complete job ở trạng thái {row['status']}")
        row = await repository.update_state(conn, row, status="done", result=body.result, lease_until=None,
                                            lease_token=None, last_error_code=None, last_error_message=None,
                                            completed_at=now, updated_at=now)
        return transition_out(row)

    async def fail(self, conn: asyncpg.Connection, ctx: MachineContext, job_id: str, body) -> dict:
        now = self._now()
        row = self._visible_to_client(await repository.get(conn, job_id, lock=True), ctx)
        self._require_holder(row, ctx, body.lease_token)
        if row["status"] in ("failed", "dead"):
            return transition_out(row, replayed=True)   # idempotent
        if row["status"] == "pending":
            raise conflict(ErrorCode.JOB_LEASE_EXPIRED, "Lease đã hết hạn và job đã quay lại hàng đợi")
        if row["status"] != "leased":
            raise conflict(Extra.JOB_INVALID_STATE, f"Không thể fail job ở trạng thái {row['status']}")
        common = {"lease_until": None, "lease_token": None, "last_error_code": body.error_code,
                  "last_error_message": body.message, "updated_at": now}
        if body.retryable and row["attempts"] < row["max_attempts"]:
            delay = min(self._settings.job_backoff_max_s,
                        self._settings.job_backoff_base_s * (2 ** max(0, row["attempts"] - 1)))
            row = await repository.update_state(conn, row, status="pending",
                                                run_after=now + timedelta(seconds=delay), **common)
        else:
            status = "dead" if body.retryable else "failed"   # hết lượt thử => dead; lỗi không retry => failed
            row = await repository.update_state(conn, row, status=status, completed_at=now, **common)
        return transition_out(row)

    # ── Bảo trì ────────────────────────────────────────────────────────────────────

    async def sweep(self) -> dict:
        """Worker nền: trả job hết lease về hàng đợi (hoặc dead)."""
        async with self._db.transaction() as conn:
            rows = await repository.reclaim_expired(conn, self._now())
        return {"requeued": [r["id"] for r in rows if r["status"] == "pending"],
                "dead": [r["id"] for r in rows if r["status"] == "dead"]}
