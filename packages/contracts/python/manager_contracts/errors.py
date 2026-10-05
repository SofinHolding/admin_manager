"""Mã lỗi ổn định (`error.code`). Client rẽ nhánh theo mã, KHÔNG parse `message`.

Thêm mã MỚI thì được (tương thích ngược). Đổi nghĩa hoặc xóa mã là breaking -> contract version mới.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    # Xác thực / phân quyền
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_REVOKED = "AUTH_REVOKED"
    SCOPE_DENIED = "SCOPE_DENIED"
    # Contract
    CONTRACT_VERSION_UNSUPPORTED = "CONTRACT_VERSION_UNSUPPORTED"
    # Integration
    PLATFORM_NOT_ALLOWED = "PLATFORM_NOT_ALLOWED"
    RESOURCE_VALIDATION_FAILED = "RESOURCE_VALIDATION_FAILED"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    PROJECT_VALIDATION_FAILED = "PROJECT_VALIDATION_FAILED"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"
    SNAPSHOT_ALREADY_COMMITTED = "SNAPSHOT_ALREADY_COMMITTED"
    SNAPSHOT_INCOMPLETE = "SNAPSHOT_INCOMPLETE"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_LEASE_EXPIRED = "JOB_LEASE_EXPIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    # Ingest data contract (docs/api/ingest-data-contract-v1.md)
    RECORD_VALIDATION_FAILED = "RECORD_VALIDATION_FAILED"
    RESOURCE_UNKNOWN = "RESOURCE_UNKNOWN"
    METRIC_UNKNOWN = "METRIC_UNKNOWN"
    SNAPSHOT_SUPERSEDED = "SNAPSHOT_SUPERSEDED"
    TIMEZONE_CHANGED = "TIMEZONE_CHANGED"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    # Chung
    REQUEST_VALIDATION_FAILED = "REQUEST_VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    SERVER_ERROR = "SERVER_ERROR"


class ApiError(Exception):
    """Lỗi nghiệp vụ có mã ổn định. Handler của app chuyển thành envelope `{ok:false,error:{...}}`."""

    def __init__(self, code: ErrorCode, message: str, *, status_code: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
