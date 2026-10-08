"""Dependency FastAPI dùng chung cho các router."""

from __future__ import annotations

from fastapi import Header, Request

from identity_service.container import Container
from identity_service.errors import DomainError


def get_container(request: Request) -> Container:
    return request.app.state.container


def client_ip(request: Request) -> str:
    """IP gốc do gateway/nginx chuyển qua `X-Real-IP`; không dùng X-Forwarded-For vì giả mạo được nhiều tầng.

    CHỈ tin header này khi service nằm sau gateway tin cậy (không mở cổng service ra internet).
    """
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def bearer_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise DomainError(401, "Thiếu token")
    return authorization[7:]


async def current_account(request: Request, authorization: str = Header("")) -> dict:
    """Account từ access token + DB: 401 nếu không còn tồn tại, 403 nếu bị khoá."""
    container = get_container(request)
    return await container.auth.current_account(bearer_token(authorization))


async def require_admin(request: Request, authorization: str = Header("")) -> dict:
    """role admin hoặc dash_admin (kiểm theo claim trong token, như legacy). Trả về payload."""
    container = get_container(request)
    payload = await container.auth.verify_access_token(bearer_token(authorization))
    if payload.get("role") not in ("admin", "dash_admin"):
        raise DomainError(403, "Chỉ quản trị viên mới có quyền này")
    return payload


async def require_full_admin(request: Request, authorization: str = Header("")) -> dict:
    """role `admin` đúng nghĩa (KHÔNG gồm `dash_admin`) — dùng cho thao tác nhạy cảm như quản lý capability."""
    container = get_container(request)
    payload = await container.auth.verify_access_token(bearer_token(authorization))
    if payload.get("role") != "admin":
        raise DomainError(403, "Chỉ quản trị viên (admin) mới có quyền này")
    return payload
