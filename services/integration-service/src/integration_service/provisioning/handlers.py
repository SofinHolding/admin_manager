"""Áp dụng sự kiện Identity (account.*) vào Integration: nuôi `account_projection` và tạo/cập nhật client.

Đúng theo docs/api/internal-feed-contract-v1.md §3 và integration-api-v1.md §2:
  * account.registered (+provisioning)  -> client `pending_email_verification` (client_id do Identity cấp, idempotent)
  * account.verified                    -> email_verified, client -> `active`
  * account.status_changed=suspended    -> client suspended + THU HỒI MỌI credential
  * account.deleted                     -> client deleted + thu hồi credential
"""

from __future__ import annotations

import re
from datetime import datetime

import asyncpg
from manager_contracts import CONTRACT_VERSION

from integration_service.clients import policy, repository
from integration_service.clients.service import ClientService
from integration_service.feed import events
from integration_service.platforms import repository as registry

HANDLED_TYPES = ("account.registered", "account.verified", "account.status_changed", "account.deleted")
_CLIENT_ID = re.compile(r"^cli_[A-Za-z0-9]{8,64}$")


class PermanentEventError(Exception):
    """Sự kiện sai nội dung: thử lại vô ích => dead-letter ngay (kèm mã lỗi ổn định)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


async def apply_event(conn: asyncpg.Connection, clients: ClientService, event: dict, now: datetime) -> None:
    etype, data = event["type"], event.get("data") or {}
    account_id = data.get("account_id")
    if etype not in HANDLED_TYPES:
        return
    if not isinstance(account_id, str) or not account_id:
        raise PermanentEventError("EVENT_INVALID", "thiếu account_id")
    if etype == "account.registered":
        await _registered(conn, clients, data, account_id, now)
    elif etype == "account.verified":
        await _verified(conn, account_id, now)
    elif etype == "account.status_changed":
        await _status_changed(conn, clients, data, account_id, now)
    elif etype == "account.deleted":
        await _deleted(conn, clients, account_id, now)


async def _registered(conn, clients: ClientService, data: dict, account_id: str, now: datetime) -> None:
    await repository.upsert_projection(conn, account_id, now=now, username=data.get("username"),
                                       status=data.get("status"), email_verified=None)
    prov = data.get("provisioning")
    if not prov:
        return
    client_id = prov.get("client_id")
    tool_name = prov.get("tool_name")
    kind = prov.get("client_kind")
    platforms = prov.get("platforms")
    valid = (isinstance(client_id, str) and _CLIENT_ID.match(client_id) and isinstance(tool_name, str)
             and 1 <= len(tool_name) <= 200 and kind in policy.CLIENT_KINDS and isinstance(platforms, list)
             and all(isinstance(p, str) for p in platforms))
    if not valid:
        raise PermanentEventError("PROVISION_INVALID", "provisioning sai định dạng")
    existing = await repository.get(conn, client_id)
    if existing is not None:
        if existing["owner_account_id"] != account_id:
            raise PermanentEventError("CLIENT_ID_CONFLICT", "client_id đã thuộc account khác")
        await events.client_provisioned(conn, client_id=client_id, account_id=account_id, status=existing["status"])
        return
    allowed = sorted(set(platforms) & await registry.enabled_platforms(conn))
    if not allowed:
        raise PermanentEventError("PLATFORM_NOT_ALLOWED", "không có platform hợp lệ")
    requested = prov.get("scopes")
    scopes = policy.clamp_scopes(requested if isinstance(requested, list) else None, kind) or policy.default_scopes(kind)
    proj = await repository.get_projection(conn, account_id)
    verified = bool(proj and proj["email_verified"] and proj["status"] == "active")
    status = "active" if verified else "pending_email_verification"
    await repository.insert(conn, id=client_id, owner_account_id=account_id, tool_name=tool_name, client_kind=kind,
                            status=status, allowed_platforms=allowed, allowed_scopes=scopes,
                            created_from="provisioning", contract_version=CONTRACT_VERSION, now=now)
    await events.client_provisioned(conn, client_id=client_id, account_id=account_id, status=status)


async def _verified(conn, account_id: str, now: datetime) -> None:
    proj = await repository.get_projection(conn, account_id)
    status = "active" if proj is None or proj["status"] == "pending" else proj["status"]
    proj = await repository.upsert_projection(conn, account_id, now=now, status=status, email_verified=True)
    if proj["status"] != "active":
        return  # Xác thực email KHÔNG tự mở khoá tài khoản đang bị admin khoá
    for client in await repository.by_owner(conn, account_id, lock=True):
        if client["status"] == "pending_email_verification":
            await repository.set_status(conn, client["id"], "active", None, now)
            await events.client_provisioned(conn, client_id=client["id"], account_id=account_id, status="active")


async def _status_changed(conn, clients: ClientService, data: dict, account_id: str, now: datetime) -> None:
    status = data.get("status")
    if status not in ("pending", "active", "suspended"):
        raise PermanentEventError("EVENT_INVALID", "status không hợp lệ")
    proj = await repository.upsert_projection(conn, account_id, now=now, status=status)
    for client in await repository.by_owner(conn, account_id, lock=True):
        if status == "suspended" and client["status"] in ("active", "pending_email_verification"):
            await clients.change_status(conn, client, "suspended", by="account")
        elif status == "active" and client["status"] == "suspended" and client["suspended_by"] == "account":
            restored = "active" if proj["email_verified"] else "pending_email_verification"
            await repository.set_status(conn, client["id"], restored, None, now)


async def _deleted(conn, clients: ClientService, account_id: str, now: datetime) -> None:
    await repository.upsert_projection(conn, account_id, now=now, status="deleted")
    for client in await repository.by_owner(conn, account_id, lock=True):
        if client["status"] != "deleted":
            await clients.change_status(conn, client, "deleted", by="account")
