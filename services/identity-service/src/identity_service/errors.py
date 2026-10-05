"""Lỗi domain của identity-service. Router chuyển thành HTTP; service không biết gì về HTTP framework."""

from __future__ import annotations


class DomainError(Exception):
    """Lỗi nghiệp vụ kèm mã HTTP và thông điệp hiển thị cho người dùng."""

    def __init__(self, status_code: int, detail: str, headers: dict[str, str] | None = None, *,
                 code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        self.code = code  # mã ổn định cho máy đọc (API mới); lỗi legacy không có
