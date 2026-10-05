"""Chạy một thao tác GHI của mặt phẳng máy: Idempotency-Key + transaction + audit.

Thứ tự: giữ chỗ idempotency (trong transaction) -> chạy nghiệp vụ -> lưu phản hồi (cùng transaction) -> commit.
Lỗi => rollback toàn bộ (kể cả giữ chỗ) nên Tool có thể thử lại cùng khoá sau khi sửa. Audit ghi trên kết nối riêng
nên lỗi vẫn để lại dấu vết.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

import asyncpg
from fastapi import Request

from integration_service.audit.service import AuditService
from integration_service.credentials.service import MachineContext
from integration_service.db import Database
from integration_service.http_utils import EnvelopeResponse, envelope, idempotency_key
from integration_service.idempotency.guard import IdempotencyGuard, Replay

Work = Callable[[asyncpg.Connection, dict], Awaitable[dict]]


def replay_response(replay: Replay) -> EnvelopeResponse:
    return EnvelopeResponse(status_code=replay.status_code, content=replay.body,
                            headers={"Idempotent-Replayed": "true"})


class MachineRunner:
    def __init__(self, db: Database, now: Callable[[], datetime], audit: AuditService) -> None:
        self._db = db
        self._now = now
        self._audit = audit

    @staticmethod
    def audit_fields(ctx: MachineContext) -> dict:
        return {"account_id": ctx.owner_account_id, "client_id": ctx.client_id, "key_id": ctx.key_id,
                "installation_id": ctx.installation_id}

    def guard(self, request: Request, ctx: MachineContext, raw: bytes) -> IdempotencyGuard:
        return IdempotencyGuard(client_id=ctx.client_id, method=request.method, path=request.url.path,
                                key=idempotency_key(request), body=raw, now=self._now())

    async def write(self, request: Request, ctx: MachineContext, *, action: str, work: Work,
                    status: int = 200) -> EnvelopeResponse:
        raw = await request.body()
        guard = self.guard(request, ctx, raw)
        async with self._audit.scope(request, action, **self.audit_fields(ctx)) as extra:
            async with self._db.transaction() as conn:
                replay = await guard.reserve(conn)
                if replay is not None:
                    return replay_response(replay)
                body = envelope(await work(conn, extra))
                await guard.complete(conn, status, body)
            return EnvelopeResponse(status_code=status, content=body)
