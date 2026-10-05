"""API job: tạo/xem/huỷ (mặt phẳng USER) và claim/heartbeat/complete/fail (mặt phẳng MÁY, API key + scope)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from manager_contracts import ErrorCode
from manager_sdk import Principal

from integration_service import examples as ex
from integration_service.api_models import Envelope
from integration_service.credentials.service import MachineContext
from integration_service.deps import get_container, machine, require_user, user_bearer
from integration_service.errors import not_found
from integration_service.http_utils import error_responses, ok, ok_response
from integration_service.jobs import schemas
from integration_service.jobs.service import job_out

machine_router = APIRouter(prefix="/v1/integrations/jobs", tags=["machine"])
user_router = APIRouter(prefix="/v1/integrations/jobs", tags=["jobs"], dependencies=[Depends(user_bearer)])

_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$")


def _check_id(job_id: str) -> str:
    if not _JOB_ID.match(job_id):
        raise not_found(ErrorCode.JOB_NOT_FOUND, "Không tìm thấy job")
    return job_id


# ── Mặt phẳng MÁY ────────────────────────────────────────────────────────────────

@machine_router.post("/claim", summary="Claim job (Fallback Tool)",
                     description=("Scope `jobs:claim`. Atomic (`FOR UPDATE SKIP LOCKED`): hai worker không bao giờ nhận cùng một "
                                  "job. Chỉ nhận job của CHÍNH owner (trừ scope `jobs:claim:system` do admin cấp), đúng "
                                  "`platforms` được phép và `capabilities` mà installation đã khai ở handshake. `lease_seconds` "
                                  "≤ 900: hết hạn mà chưa complete/fail thì job quay lại hàng đợi (hết lượt thử => `dead`). "
                                  "Không idempotent theo nội dung: mỗi lần gọi có thể nhận job khác."),
                     responses={200: ok_response(Envelope[schemas.JobClaimOut], "Các job được giao (có thể rỗng)", ex.JOB_CLAIM),
                                **error_responses(400, 401, 403, 404, 422, 429, 503)})
async def claim_jobs(request: Request, body: schemas.JobClaimRequest,
                     ctx: MachineContext = Depends(machine("jobs:claim"))):
    c = get_container(request)
    async with c.audit.scope(request, "jobs.claim", **c.runner.audit_fields(ctx)) as extra:
        data = await c.jobs.claim(ctx, body)
        extra["detail"] = {"claimed": len(data["jobs"])}
    return ok(data)


@machine_router.post("/{job_id}/heartbeat", summary="Gia hạn lease",
                     description=("Scope `jobs:claim`. Chỉ client đang giữ lease. `lease_seconds` ≤ 900 tính từ lúc gọi. Lease đã "
                                  "bị thu hồi (job quay lại hàng đợi) => 409 `JOB_LEASE_EXPIRED`."),
                     responses={200: ok_response(Envelope[schemas.JobTransitionOut], "Lease mới", ex.JOB_TRANSITION),
                                **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def heartbeat(request: Request, job_id: str, body: schemas.JobHeartbeatRequest,
                    ctx: MachineContext = Depends(machine("jobs:claim"))):
    c = get_container(request)
    _check_id(job_id)

    async def work(conn, extra):
        extra["detail"] = {"job_id": job_id}
        return await c.jobs.heartbeat(conn, ctx, job_id, body)

    return await c.runner.write(request, ctx, action="jobs.heartbeat", work=work)


@machine_router.post("/{job_id}/complete", summary="Hoàn thành job",
                     description=("Scope `jobs:complete`. Idempotent: complete lại job đã `done` trả `replayed=true`. Dữ liệu canonical "
                                  "đi qua snapshot như mọi Tool; `result` chỉ là tóm tắt (≤ 64 KiB). Job đã failed/dead/cancelled => "
                                  "409 `JOB_INVALID_STATE`; lease đã mất => 409 `JOB_LEASE_EXPIRED`. Hỗ trợ `Idempotency-Key`."),
                     responses={200: ok_response(Envelope[schemas.JobTransitionOut], "Job đã done", ex.JOB_TRANSITION),
                                **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def complete(request: Request, job_id: str, body: schemas.JobCompleteRequest,
                   ctx: MachineContext = Depends(machine("jobs:complete"))):
    c = get_container(request)
    _check_id(job_id)

    async def work(conn, extra):
        extra["detail"] = {"job_id": job_id}
        return await c.jobs.complete(conn, ctx, job_id, body)

    return await c.runner.write(request, ctx, action="jobs.complete", work=work)


@machine_router.post("/{job_id}/fail", summary="Báo job thất bại",
                     description=("Scope `jobs:complete`. `retryable=true` và còn lượt thử => quay lại `pending` sau backoff mũ; hết lượt "
                                  "=> `dead`; `retryable=false` => `failed`. Idempotent với job đã failed/dead. Hỗ trợ `Idempotency-Key`."),
                     responses={200: ok_response(Envelope[schemas.JobTransitionOut], "Trạng thái sau khi fail", ex.JOB_TRANSITION),
                                **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def fail(request: Request, job_id: str, body: schemas.JobFailRequest,
               ctx: MachineContext = Depends(machine("jobs:complete"))):
    c = get_container(request)
    _check_id(job_id)

    async def work(conn, extra):
        extra["detail"] = {"job_id": job_id, "error_code": body.error_code, "retryable": body.retryable}
        return await c.jobs.fail(conn, ctx, job_id, body)

    return await c.runner.write(request, ctx, action="jobs.fail", work=work)


# ── Mặt phẳng USER ───────────────────────────────────────────────────────────────

@user_router.post("", status_code=201, summary="Tạo job cho một resource",
                  description=("Owner = chủ resource (user thường chỉ tạo cho resource của mình; admin/dash_admin cho mọi resource), "
                               "KHÔNG nhận owner từ body. Resource phải `execution_mode=fallback_tool`, đang `active`. `dedupe_key` "
                               "trùng với job đang chờ/chạy => trả job cũ (HTTP 200, `created=false`)."),
                  responses={201: ok_response(Envelope[schemas.JobCreated], "Job mới", ex.JOB),
                             **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def create_job(request: Request, body: schemas.JobCreate, principal: Principal = Depends(require_user)):
    c = get_container(request)
    async with c.audit.scope(request, "jobs.create", actor_account_id=principal.sub, resource_id=body.resource_id) as extra:
        row, created = await c.jobs.create_job(principal, body)
        extra["account_id"] = row["owner_account_id"]
        extra["detail"] = {"job_id": row["id"], "created": created}
    return ok({**job_out(row), "created": created}, status=201 if created else 200)


@user_router.get("", summary="Danh sách job",
                 description="User thường chỉ thấy job của mình; admin/dash_admin thấy tất cả, lọc `owner`.",
                 responses={200: ok_response(Envelope[schemas.JobPage], "Danh sách job", {"jobs": [ex.JOB], "next_offset": None}),
                            **error_responses(401, 403, 422, 503)})
async def list_jobs(request: Request, owner: str | None = Query(None, max_length=64),
                    status: str | None = Query(None, pattern=r"^(pending|leased|done|failed|dead|cancelled)$"),
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000),
                    principal: Principal = Depends(require_user)):
    rows = await get_container(request).jobs.list_for_user(principal, owner=owner, status=status, limit=limit,
                                                           offset=offset)
    return ok({"jobs": [job_out(r) for r in rows], "next_offset": offset + limit if len(rows) == limit else None})


@user_router.get("/{job_id}", summary="Chi tiết job",
                 description="Chủ job hoặc admin/dash_admin. Job của người khác => 404 `JOB_NOT_FOUND` (không lộ sự tồn tại).",
                 responses={200: ok_response(Envelope[schemas.JobOut], "Job", ex.JOB), **error_responses(401, 403, 404, 422, 503)})
async def get_job(request: Request, job_id: str, principal: Principal = Depends(require_user)):
    return ok(job_out(await get_container(request).jobs.get_for_user(principal, _check_id(job_id))))


@user_router.post("/{job_id}/cancel", summary="Huỷ job",
                  description="Chủ job hoặc admin. Job pending/leased => cancelled (Tool đang giữ lease sẽ nhận 409 khi complete). Idempotent.",
                  responses={200: ok_response(Envelope[schemas.JobOut], "Job đã huỷ", {**ex.JOB, "status": "cancelled"}),
                             **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def cancel_job(request: Request, job_id: str, principal: Principal = Depends(require_user)):
    c = get_container(request)
    async with c.audit.scope(request, "jobs.cancel", actor_account_id=principal.sub):
        row = await c.jobs.cancel_job(principal, _check_id(job_id))
    return ok(job_out(row))
