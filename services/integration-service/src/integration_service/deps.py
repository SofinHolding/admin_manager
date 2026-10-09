"""Dependency FastAPI: xác thực USER (token RS256 qua JWKS), xác thực MÁY (API key), kiểm scope."""

from __future__ import annotations

import dataclasses
import re

from fastapi import Depends, Header, Request
from fastapi.security import HTTPBearer
from manager_contracts import ErrorCode
from manager_sdk import AuthError, Principal, make_dependencies

from integration_service.container import Container
from integration_service.credentials.service import MachineContext
from integration_service.errors import forbidden
from integration_service.http_utils import client_ip

_INSTALLATION_ID = re.compile(r"^ins_[0-9a-f]{32}$")


def get_container(request: Request) -> Container:
    return request.app.state.container


user_bearer = HTTPBearer(scheme_name="UserAccessToken", bearerFormat="JWT", auto_error=False,
                         description="Access token RS256 do Identity ký (mặt phẳng USER). Verify offline bằng JWKS.")
machine_bearer = HTTPBearer(scheme_name="MachineApiKey", bearerFormat="mpk_<key_id>.<secret>", auto_error=False,
                            description="API key máy do Integration cấp (mặt phẳng MÁY). Token user KHÔNG dùng được ở đây.")

_user = make_dependencies(lambda request: request.app.state.container.verifier)
require_user = _user.require_user
require_admin = _user.require_admin      # role admin hoặc dash_admin (xem toàn bộ)


async def require_super_admin(principal: Principal = Depends(require_user)) -> Principal:
    """Chỉ role `admin` (không gồm dash_admin): cấp scope hệ thống, sửa client, sửa registry."""
    if principal.role != "admin":
        raise AuthError("Chỉ quản trị viên cao nhất mới có quyền này", status_code=403, code=ErrorCode.SCOPE_DENIED.value)
    return principal


async def require_tool_integration(principal: Principal = Depends(require_user)) -> Principal:
    """Bất kỳ user nào muốn tạo/quản lý Integration Client phải có capability:tool_integration.
    role=admin luôn pass (principal.has_perm trả True cho admin mọi quyền)."""
    if not principal.has_perm("capability:tool_integration"):
        raise AuthError("Tài khoản chưa được cấp quyền sử dụng Tool tích hợp",
                        status_code=403, code=ErrorCode.SCOPE_DENIED.value)


def machine(scope: str):
    """Dependency mặt phẳng MÁY: API key + scope. Token user (JWT) ở đây => AUTH_INVALID."""

    async def dep(request: Request, authorization: str = Header("", include_in_schema=False),
                  x_contract_version: str | None = Header(None, description="Phiên bản hợp đồng, vd 2026-10-01"),
                  x_installation_id: str | None = Header(None, description="installation_id từ handshake (cho audit)"),
                  idempotency_key: str | None = Header(
                      None, alias="Idempotency-Key", description="8-128 ký tự [A-Za-z0-9_.:-]; cho mọi POST/PUT ghi"),
                  _bearer=Depends(machine_bearer)) -> MachineContext:
        request.state.plane = "machine"  # để handler lỗi validation trả RECORD_VALIDATION_FAILED
        container = get_container(request)
        ctx = await container.credentials.authenticate(authorization, client_ip(request), x_contract_version)
        if x_installation_id and _INSTALLATION_ID.match(x_installation_id):
            ctx = dataclasses.replace(ctx, installation_id=x_installation_id)
        if scope not in ctx.scopes:
            await container.audit.record(
                action="auth.scope_denied", result=ErrorCode.SCOPE_DENIED.value, request=request, status_code=403,
                detail={"required": scope, "path": request.url.path}, account_id=ctx.owner_account_id,
                client_id=ctx.client_id, key_id=ctx.key_id)
            raise forbidden(ErrorCode.SCOPE_DENIED, f"Thiếu scope {scope}", {"required": scope})
        return ctx

    return dep
