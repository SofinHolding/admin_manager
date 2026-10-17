"""Handshake: Tool khai báo installation/capabilities, server trả phạm vi được phép (giao của khai báo và cấp phép).

Server KHÔNG tin `tool_name` để xác định quyền sở hữu: owner/client lấy từ API key.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

import asyncpg
from manager_contracts import CONTRACT_VERSION, ErrorCode
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from integration_service.credentials import repository as credentials_repo
from integration_service.credentials.service import MachineContext
from integration_service.errors import ApiProblem, Extra, conflict, forbidden
from integration_service.ingest.schemas import Key
from integration_service.installations import repository
from integration_service.platforms import repository as registry
from integration_service.security import new_id

_CAPABILITY_BY_SCOPE = {
    "resources:write": ("resources.push",),
    "snapshots:write": ("snapshots.push",),
    "metrics:write": ("metrics.push",),
    "jobs:claim": ("jobs.pull",),
}
Short = Annotated[str, StringConstraints(max_length=200)]


class InstallationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    hostname: Short = ""
    os: Annotated[str, StringConstraints(max_length=64)] = ""
    app_version: Annotated[str, StringConstraints(max_length=64)] = ""


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: Short | None = None
    contract_version: Annotated[str, StringConstraints(max_length=32)] | None = None


class HandshakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation: InstallationInfo
    tool: ToolInfo | None = None
    platforms: list[Key] = Field(default_factory=list, max_length=20)
    capabilities: list[Annotated[str, StringConstraints(max_length=64)]] = Field(default_factory=list, max_length=50)


class InstallationService:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    async def handshake(self, conn: asyncpg.Connection, ctx: MachineContext, body: HandshakeRequest, ip: str) -> dict:
        if body.tool and body.tool.contract_version and body.tool.contract_version != CONTRACT_VERSION:
            raise ApiProblem(ErrorCode.CONTRACT_VERSION_UNSUPPORTED, f"Chỉ hỗ trợ contract {CONTRACT_VERSION}",
                             status_code=400)
        enabled = await registry.enabled_platforms(conn)
        usable = set(ctx.allowed_platforms) & enabled
        declared = await registry.resolve_many(conn, list(body.platforms))   # mã Tool khai (chính/phụ) -> mã chính
        denied = sorted(set(declared) - usable)
        if denied:
            raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform không nằm trong phạm vi client được phép",
                            {"platforms": denied[:10]})
        # Device binding (luật 0.4 thiết kế tool-key): credential đã bind thiết bị KHÁC => 409, KHÔNG tạo installation.
        cred = await conn.fetchrow("SELECT bound_device_id FROM integration_credentials WHERE key_id = $1 FOR UPDATE",
                                   ctx.key_id)
        if cred and cred["bound_device_id"] and cred["bound_device_id"] != body.installation.device_id:
            raise conflict(Extra.CREDENTIAL_DEVICE_BOUND,
                           "Credential này đã kích hoạt cho một thiết bị khác — đổi máy phải xoay khoá")
        installation, created = await repository.upsert(
            conn, new_id=new_id("ins"), client_id=ctx.client_id, device_id=body.installation.device_id,
            hostname=body.installation.hostname, os=body.installation.os, app_version=body.installation.app_version,
            capabilities=sorted(set(body.capabilities)), ip=ip, now=self._now())
        if cred and cred["bound_device_id"] is None:
            await credentials_repo.bind_device(conn, ctx.key_id, device_id=body.installation.device_id,
                                               installation_id=installation["id"], now=self._now())
        allowed_caps = {c for scope in ctx.scopes for c in _CAPABILITY_BY_SCOPE.get(scope, ())}
        accepted = sorted(set(body.capabilities) & allowed_caps)
        return {"client_id": ctx.client_id, "installation_id": installation["id"],
                "installation_created": created, "owner_account_id": ctx.owner_account_id,
                "accepted_contract_version": CONTRACT_VERSION,
                "allowed_platforms": sorted(set(declared) or usable),
                "allowed_scopes": sorted(ctx.scopes), "allowed_capabilities": sorted(allowed_caps),
                "accepted_capabilities": accepted, "server_time": self._now()}
