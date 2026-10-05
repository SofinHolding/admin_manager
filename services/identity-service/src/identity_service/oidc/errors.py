"""Lỗi OAuth2/OIDC. Khác `DomainError` (legacy `{"detail"}`): OAuth2 dùng `{"error", "error_description"}` (RFC 6749 §5.2)."""

from __future__ import annotations


class OAuthError(Exception):
    """Lỗi ở endpoint kiểu máy-với-máy (token/introspect/revoke/userinfo)."""

    def __init__(self, error: str, description: str = "", status: int = 400,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.description = description
        self.status = status
        self.headers = headers or {}


class AuthzPageError(Exception):
    """Lỗi ở /authorize khi `client_id`/`redirect_uri` KHÔNG đáng tin: hiển thị trang lỗi, TUYỆT ĐỐI không redirect."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class AuthzRedirectError(Exception):
    """Lỗi ở /authorize khi redirect_uri đã được xác minh: trả về cho client qua redirect (RFC 6749 §4.1.2.1)."""

    def __init__(self, error: str, description: str = "") -> None:
        super().__init__(error)
        self.error = error
        self.description = description
