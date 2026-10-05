"""Idempotency-Key cho mutation: cùng key + cùng body => phát lại ĐÚNG phản hồi cũ (không ghi lần 2);
cùng key + khác body => 409 IDEMPOTENCY_CONFLICT.

Giữ chỗ (`reserve`) nằm TRONG transaction nghiệp vụ: ghi dữ liệu và lưu phản hồi là một đơn vị atomic, và hai request
trùng khoá chạy đồng thời được Postgres tuần tự hoá bằng chính unique index (request sau chờ request trước).
Chỉ phản hồi THÀNH CÔNG được lưu; lỗi (rollback) không để lại dấu vết nên có thể thử lại sau khi sửa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import asyncpg
from manager_contracts import ErrorCode

from integration_service.errors import conflict
from integration_service.idempotency import repository
from integration_service.security import sha256_hex


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: dict


class IdempotencyGuard:
    def __init__(self, *, client_id: str, method: str, path: str, key: str | None, body: bytes,
                 now: datetime) -> None:
        self.client_id = client_id
        self.method = method
        self.path = path
        self.key = key
        self.request_hash = sha256_hex(body)
        self.now = now

    @property
    def enabled(self) -> bool:
        return self.key is not None

    def _replay_or_conflict(self, row: dict) -> Replay:
        if row["request_hash"] != self.request_hash:
            raise conflict(ErrorCode.IDEMPOTENCY_CONFLICT,
                           "Idempotency-Key đã dùng cho một yêu cầu có nội dung khác")
        return Replay(row["status_code"] or 200, json.loads(row["response"]))

    async def precheck(self, conn: asyncpg.Connection) -> Replay | None:
        """Chỉ đọc: đã có phản hồi cho khoá này chưa?"""
        if not self.enabled:
            return None
        row = await repository.find(conn, self.client_id, self.method, self.path, self.key)  # type: ignore[arg-type]
        return self._replay_or_conflict(row) if row and row["response"] is not None else None

    async def reserve(self, conn: asyncpg.Connection) -> Replay | None:
        """Gọi trong transaction nghiệp vụ, TRƯỚC khi ghi. Trả Replay nếu đã xử lý xong trước đó."""
        if not self.enabled:
            return None
        if await repository.reserve(conn, self.client_id, self.method, self.path, self.key,  # type: ignore[arg-type]
                                    self.request_hash, self.now):
            return None
        row = await repository.find(conn, self.client_id, self.method, self.path, self.key)  # type: ignore[arg-type]
        if row is None or row["response"] is None:  # không thể xảy ra (hàng chưa hoàn tất chỉ tồn tại trong tx chưa commit)
            raise conflict(ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency-Key đang được xử lý")
        return self._replay_or_conflict(row)

    async def complete(self, conn: asyncpg.Connection, status_code: int, response: dict) -> None:
        if self.enabled:
            await repository.complete(conn, self.client_id, self.method, self.path, self.key,  # type: ignore[arg-type]
                                      status_code, response)
