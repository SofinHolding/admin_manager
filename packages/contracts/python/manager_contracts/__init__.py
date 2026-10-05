"""manager_contracts — DTO/envelope/mã lỗi dùng chung (CHỈ generic, không có logic nghiệp vụ).

Package dùng chung được phép theo CLAUDE.md: không import code của bất kỳ service nào.
"""

from manager_contracts.envelope import CONTRACT_VERSION, error_envelope, ok_envelope
from manager_contracts.errors import ApiError, ErrorCode

__all__ = ["ApiError", "ErrorCode", "CONTRACT_VERSION", "ok_envelope", "error_envelope"]
