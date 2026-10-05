"""Xác thực cho API legacy: JWT USER của hệ mới (RS256, verify offline qua JWKS) với thông điệp lỗi của legacy.

Legacy dùng JWT HS256 + secret chung (đã bị loại bỏ); sau Phase 1 web chỉ giữ token do Identity ký. Hành vi giữ nguyên:
thiếu Bearer => 401 "Thiếu token"; token hết hạn / hỏng => 401 với thông điệp tương ứng; sai role => 403.
"""

from __future__ import annotations

from fastapi import Header, Request
from manager_sdk import AuthError, Principal

from integration_service.legacy_acl.errors import LegacyError

OPS_ROLES = ("admin", "dash_admin")
ADMIN_ROLE = "admin"


async def _principal(request: Request, authorization: str) -> Principal:
    if not authorization.startswith("Bearer "):
        raise LegacyError(401, "Thiếu token")
    try:
        return await request.app.state.container.verifier.verify(authorization[7:])
    except AuthError as exc:
        raise LegacyError(401, exc.message) from None


async def require_user(request: Request, authorization: str = Header("")) -> Principal:
    return await _principal(request, authorization)


async def require_ops(request: Request, authorization: str = Header("")) -> Principal:
    principal = await _principal(request, authorization)
    if principal.role not in OPS_ROLES:
        raise LegacyError(403, "Chỉ admin/dash_admin được truy cập")
    return principal


async def require_admin(request: Request, authorization: str = Header("")) -> Principal:
    principal = await _principal(request, authorization)
    if principal.role != ADMIN_ROLE:
        raise LegacyError(403, "Chỉ admin được thao tác này")
    return principal
