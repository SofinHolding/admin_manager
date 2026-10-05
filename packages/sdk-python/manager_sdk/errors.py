"""Lỗi xác thực của SDK. Không gắn với kiểu response của service nào: service tự chuyển thành HTTP theo style của mình."""

from __future__ import annotations


class AuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 401, code: str = "AUTH_INVALID") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
