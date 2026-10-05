"""Dependency FastAPI dựng từ `TokenVerifier`: `require_user`, `require_role`, `require_perm`.

Service chọn kiểu lỗi: `install_auth_error_handler(app, style="envelope"|"legacy")` chuyển AuthError thành HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from manager_sdk.errors import AuthError
from manager_sdk.principal import Principal
from manager_sdk.verifier import TokenVerifier


def make_dependencies(verifier: TokenVerifier | Callable[[Request], TokenVerifier]) -> SimpleNamespace:
    """`verifier` có thể là đối tượng hoặc hàm lấy nó từ request (vd `request.app.state.verifier`)."""

    def resolve(request: Request) -> TokenVerifier:
        return verifier(request) if callable(verifier) and not isinstance(verifier, TokenVerifier) else verifier

    async def require_user(request: Request, authorization: str = Header("")) -> Principal:
        if not authorization.startswith("Bearer "):
            raise AuthError("Thiếu token")
        return await resolve(request).verify(authorization[7:])

    def require_role(*roles: str):
        async def dep(principal: Principal = _depends(require_user)) -> Principal:
            if principal.role not in roles:
                raise AuthError("Không đủ quyền", status_code=403, code="SCOPE_DENIED")
            return principal
        return dep

    def require_perm(permission: str):
        async def dep(principal: Principal = _depends(require_user)) -> Principal:
            if not principal.has_perm(permission):
                raise AuthError("Không đủ quyền", status_code=403, code="SCOPE_DENIED")
            return principal
        return dep

    async def require_admin(principal: Principal = _depends(require_user)) -> Principal:
        if not principal.is_admin:
            raise AuthError("Chỉ quản trị viên mới có quyền này", status_code=403, code="SCOPE_DENIED")
        return principal

    return SimpleNamespace(require_user=require_user, require_role=require_role, require_perm=require_perm,
                           require_admin=require_admin)


def _depends(dep):
    from fastapi import Depends
    return Depends(dep)


def install_auth_error_handler(app: FastAPI, style: str = "envelope") -> None:
    @app.exception_handler(AuthError)
    async def _handler(request: Request, exc: AuthError) -> JSONResponse:
        if style == "legacy":
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
        from manager_contracts import error_envelope
        try:  # request-id do chassis cấp nếu có; SDK không bắt buộc phụ thuộc vào observability
            from manager_observability.request_context import current_request_id
            request_id = current_request_id()
        except ImportError:
            request_id = request.headers.get("x-request-id", "-")
        return JSONResponse(status_code=exc.status_code,
                            content=error_envelope(exc.code, exc.message, request_id=request_id))
