"""Mã lỗi và ngoại lệ API của integration-service.

`manager_contracts.ErrorCode` là bộ mã DÙNG CHUNG. Mã riêng của Integration chưa có trong package dùng chung được
khai ở `Extra` (cùng dạng str-Enum nên chassis chuyển thành envelope y hệt). Khi điều phối viên thêm chúng vào
`ErrorCode`, chỉ cần đổi nguồn import — giá trị chuỗi không đổi.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from manager_contracts import ApiError, ErrorCode


class Extra(str, Enum):
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"                  # 413 body > INTEGRATION_MAX_BODY_BYTES
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"                # 403 email chủ client chưa xác thực
    CLIENT_NOT_ACTIVE = "CLIENT_NOT_ACTIVE"                  # 403 client pending/suspended/deleted
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"                    # 403 account bị khoá/xoá theo projection
    CLIENT_NOT_FOUND = "CLIENT_NOT_FOUND"                    # 404
    CREDENTIAL_NOT_FOUND = "CREDENTIAL_NOT_FOUND"            # 404
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"                        # 409 vượt hạn mức client/credential
    SNAPSHOT_NOT_WRITABLE = "SNAPSHOT_NOT_WRITABLE"          # 409 snapshot không còn ở trạng thái building
    SNAPSHOT_COMMIT_IN_PROGRESS = "SNAPSHOT_COMMIT_IN_PROGRESS"  # 409 đang commit
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"      # 409 yêu cầu trùng khoá đang xử lý
    UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"            # 415
    JOB_INVALID_STATE = "JOB_INVALID_STATE"                  # 409 chuyển trạng thái job không hợp lệ (vd complete job đã failed)
    JOB_NOT_ELIGIBLE = "JOB_NOT_ELIGIBLE"                     # 409 resource không ở execution_mode=fallback_tool / đã gỡ / bị tắt
    CREDENTIAL_DEVICE_BOUND = "CREDENTIAL_DEVICE_BOUND"       # 409 credential đã bind thiết bị khác (handshake)
    CREDENTIAL_NOT_ACTIVATED = "CREDENTIAL_NOT_ACTIVATED"     # 403 credential chưa handshake/bind thiết bị
    INSTALLATION_MISMATCH = "INSTALLATION_MISMATCH"           # 403 X-Installation-Id vắng/khác installation đã bind
    SHARED_CLIENT_REQUIRES_DELEGATION = "SHARED_CLIENT_REQUIRES_DELEGATION"  # 403 client shared ghi dữ liệu thiếu X-Delegated-Job
    CLIENT_NOT_READY = "CLIENT_NOT_READY"                     # 409 client shared còn draft (chưa configure/activate)
    REVEAL_NOT_AVAILABLE = "REVEAL_NOT_AVAILABLE"             # 404 không có credential đang chờ reveal
    REVEAL_EXPIRED = "REVEAL_EXPIRED"                         # 410 reveal đã hết hạn
    PERSONAL_CLIENT_EXISTS = "PERSONAL_CLIENT_EXISTS"         # 409 owner đích đã có client personal pending/active
    PLATFORM_CODE_EXISTS = "PLATFORM_CODE_EXISTS"             # 409 mã nền tảng (chính hoặc phụ) đã được nền tảng khác/chính nó giữ


Code = ErrorCode | Extra


class ApiProblem(ApiError):
    """Lỗi nghiệp vụ có mã ổn định + (tuỳ chọn) header, ví dụ `Retry-After`."""

    def __init__(self, code: Code, message: str, *, status_code: int = 400,
                 details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(code, message, status_code=status_code, details=details)  # type: ignore[arg-type]
        self.headers = headers or {}


def not_found(code: Code, message: str) -> ApiProblem:
    return ApiProblem(code, message, status_code=404)


def forbidden(code: Code, message: str, details: dict | None = None) -> ApiProblem:
    return ApiProblem(code, message, status_code=403, details=details)


def conflict(code: Code, message: str, details: dict | None = None) -> ApiProblem:
    return ApiProblem(code, message, status_code=409, details=details)


def gone(code: Code, message: str, details: dict | None = None) -> ApiProblem:
    return ApiProblem(code, message, status_code=410, details=details)


def unauthorized(message: str = "Thiếu hoặc sai thông tin xác thực", code: Code = ErrorCode.AUTH_INVALID) -> ApiProblem:
    return ApiProblem(code, message, status_code=401)


def invalid(message: str, details: dict | None = None, code: Code = ErrorCode.RECORD_VALIDATION_FAILED,
            status_code: int = 422) -> ApiProblem:
    return ApiProblem(code, message, status_code=status_code, details=details)
