"""Ghi audit trên kết nối RIÊNG (best-effort): audit lỗi không được làm hỏng nghiệp vụ, và audit của request thất bại
vẫn được lưu dù transaction nghiệp vụ đã rollback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Request
from manager_contracts import ErrorCode
from manager_observability import current_request_id

from manager_sdk import AuthError

from integration_service.audit import repository
from integration_service.db import Database
from integration_service.errors import ApiProblem
from integration_service.http_utils import client_ip

logger = logging.getLogger("integration.audit")


class AuditService:
    def __init__(self, db: Database, now: Callable[[], datetime]) -> None:
        self._db = db
        self._now = now

    async def record(self, *, action: str, result: str, request: Request | None = None, **fields) -> None:
        try:
            async with self._db.connection() as conn:
                await repository.insert(
                    conn, at=self._now(), action=action, result=result,
                    request_id=current_request_id(), ip=client_ip(request) if request else "", **fields)
        except Exception as exc:  # noqa: BLE001 — audit lỗi không được làm hỏng request
            logger.warning("Không ghi được audit (%s): %s", action, type(exc).__name__)

    @asynccontextmanager
    async def scope(self, request: Request, action: str, **fields):
        """Ghi một dòng audit khi khối kết thúc: result = `ok` hoặc mã lỗi ổn định."""
        extra: dict = {}
        try:
            yield extra
        except (ApiProblem, AuthError) as exc:
            code = getattr(exc.code, "value", exc.code)
            await self.record(action=action, result=code, request=request, status_code=exc.status_code,
                              **{**fields, **extra})
            raise
        except Exception:
            await self.record(action=action, result=ErrorCode.SERVER_ERROR.value, request=request, status_code=500,
                              **{**fields, **extra})
            raise
        else:
            await self.record(action=action, result="ok", request=request, **{**fields, **extra})

    async def list(self, **filters) -> list[dict]:
        async with self._db.connection() as conn:
            return await repository.query(conn, **filters)
