"""`machine(scope)` + header tuỳ chọn `X-Delegated-Job` (PHASE 4, delegated ingestion — Shared Collector push dữ
liệu CHO owner của job đã claim thay vì owner của chính credential, xem docs/architecture/shared-collector-design-v1.md).

Chỉ `client_kind=fallback_tool` được dùng header; job phải đang `leased` bởi ĐÚNG client này và có `resource_owner_id`.

`require_for_shared=True` (các endpoint GHI dữ liệu: resources/batch, projects PUT, snapshot create/batch/commit):
client `ownership=shared` (fallback_tool hoặc service) BẮT BUỘC có `X-Delegated-Job` hợp lệ, nếu không => 403
`SHARED_CLIENT_REQUIRES_DELEGATION` (luật 0.5 của thiết kế tool-key: khóa dùng chung không được ghi dữ liệu đứng
tên admin). Vắng ở các endpoint ĐỌC (`require_for_shared=False`, mặc định) — hành vi y hệt `machine(scope)`.
"""

from __future__ import annotations

import dataclasses

from fastapi import Depends, Header, Request
from manager_contracts import ErrorCode

from integration_service.credentials.service import MachineContext
from integration_service.deps import get_container, machine
from integration_service.errors import Extra, conflict, forbidden, not_found
from integration_service.jobs import repository as jobs_repository


def delegated(scope: str, *, require_for_shared: bool = False):
    async def dep(request: Request, ctx: MachineContext = Depends(machine(scope)),
                  x_delegated_job: str | None = Header(
                      None, alias="X-Delegated-Job",
                      description="job_id đã claim (PHASE 4, chỉ fallback_tool) — gán owner dữ liệu từ job.resource_owner_id")
                  ) -> MachineContext:
        if x_delegated_job:
            if ctx.client_kind != "fallback_tool":
                raise forbidden(ErrorCode.SCOPE_DENIED, "Chỉ fallback_tool mới dùng được delegated ingestion")
            async with get_container(request).db.connection() as conn:
                job = await jobs_repository.get(conn, x_delegated_job)
            if job is None or job["claimed_by_client"] != ctx.client_id:
                raise not_found(ErrorCode.JOB_NOT_FOUND, "Không tìm thấy job")
            if job["status"] != "leased":
                raise conflict(Extra.JOB_INVALID_STATE, f"Job không ở trạng thái leased ({job['status']})")
            if job["resource_owner_id"] is None:
                raise conflict(Extra.JOB_INVALID_STATE, "Job này không hỗ trợ delegated ingestion (thiếu resource_owner_id)")
            ctx = dataclasses.replace(ctx, delegated_owner_id=job["resource_owner_id"])
        if require_for_shared and ctx.ownership == "shared" and ctx.delegated_owner_id is None:
            raise forbidden(Extra.SHARED_CLIENT_REQUIRES_DELEGATION,
                            "Client dùng chung phải ghi dữ liệu qua X-Delegated-Job (job đã claim)")
        return ctx

    return dep
