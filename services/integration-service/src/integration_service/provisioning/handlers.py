"""Áp dụng sự kiện Identity (account.*) vào Integration: nuôi `account_projection` và client PERSONAL.

Đúng theo docs/api/internal-feed-contract-v1.md §3 và integration-api-v1.md §2:
  * account.registered                      -> CHỈ cập nhật account_projection (không còn tạo client — identity
                                               không còn gửi `provisioning`, xem §1 thiết kế tool-key 2026-10-14).
  * account.verified                        -> email_verified, client personal pending -> active.
  * account.permission_changed
      capability:tool_integration granted    -> tạo/kích hoạt lại client PERSONAL + credential reveal_state=pending
                                               nếu chưa có credential active (idempotent).
      capability:tool_integration revoked    -> client personal -> suspended(by=capability), thu hồi MỌI credential.
  * account.status_changed=suspended        -> client suspended + THU HỒI MỌI credential.
  * account.deleted                         -> client deleted + thu hồi credential.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
from manager_contracts import CONTRACT_VERSION

from integration_service.clients import policy
from integration_service.clients import repository as clients_repo
from integration_service.clients.service import ClientService
from integration_service.credentials import repository as credentials_repo
from integration_service.credentials.service import CredentialService
from integration_service.feed import events
from integration_service.platforms import repository as registry
from integration_service.security import new_id

HANDLED_TYPES = ("account.registered", "account.verified", "account.status_changed", "account.deleted",
                 "account.permission_changed")


class PermanentEventError(Exception):
    """Sự kiện sai nội dung: thử lại vô ích => dead-letter ngay (kèm mã lỗi ổn định)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _seq(event: dict) -> int:
    seq = event.get("seq")
    return seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else 0


async def apply_event(conn: asyncpg.Connection, clients: ClientService, credentials: CredentialService,
                      event: dict, now: datetime, *, replay: bool = False) -> None:
    """`replay=True`: áp lại event dead-letter (có thể CŨ hơn trạng thái hiện tại). Khi đó `account.registered` chỉ điền
    chỗ trống của projection và `account.status_changed` bị bỏ nếu `seq` <= `status_seq` đã ghi (event mới hơn đã đặt status)."""
    etype, data = event["type"], event.get("data") or {}
    account_id = data.get("account_id")
    if etype not in HANDLED_TYPES:
        return
    if not isinstance(account_id, str) or not account_id:
        raise PermanentEventError("EVENT_INVALID", "thiếu account_id")
    if etype == "account.registered":
        await _registered(conn, data, account_id, now, seq=_seq(event), replay=replay)
    elif etype == "account.verified":
        await _verified(conn, account_id, now)
    elif etype == "account.status_changed":
        await _status_changed(conn, clients, data, account_id, now, seq=_seq(event))
    elif etype == "account.deleted":
        await _deleted(conn, clients, account_id, now, seq=_seq(event))
    elif etype == "account.permission_changed":
        await _permission_changed(conn, credentials, data, account_id, now)


async def _registered(conn, data: dict, account_id: str, now: datetime, *, seq: int = 0,
                      replay: bool = False) -> None:
    """CHỈ nuôi `account_projection` — không còn tạo client (xem docstring module)."""
    await clients_repo.upsert_projection(conn, account_id, now=now, username=data.get("username"),
                                         status=data.get("status"), email_verified=None, status_seq=seq,
                                         fill_only=replay)


async def _permission_changed(conn, credentials: CredentialService, data: dict, account_id: str,
                              now: datetime) -> None:
    """`capability:tool_integration` granted/revoked -> tạo/kích hoạt lại / suspend client PERSONAL (luật 0 thiết kế
    tool-key: client cá nhân CHỈ tạo tự động từ đây, không ai tạo tay)."""
    permission = data.get("permission", "")
    granted = data.get("granted", False)
    if permission != "capability:tool_integration":
        return
    if not granted:
        client = await clients_repo.get_personal(conn, account_id, lock=True)
        if client is not None:
            for key_id in await credentials_repo.revoke_all(conn, client["id"], "capability_revoked", now):
                await events.credential_revoked(conn, client_id=client["id"], key_id=key_id)
            await clients_repo.set_status(conn, client["id"], "suspended", "capability", now)
        return

    client = await clients_repo.get_personal(conn, account_id, lock=True)
    created_or_reactivated = False
    if client is None:
        proj = await clients_repo.get_projection(conn, account_id)
        verified = bool(proj and proj["email_verified"] and proj["status"] == "active")
        status = "active" if verified else "pending_email_verification"
        suspended = await clients_repo.get_suspended_personal_by_capability(conn, account_id, lock=True)
        if suspended is not None:
            await clients_repo.set_status(conn, suspended["id"], status, None, now)
            client = await clients_repo.get(conn, suspended["id"])
        else:
            all_platforms = sorted(await registry.enabled_platforms(conn))
            if not all_platforms:
                return  # chưa có platform nào bật: chưa thể cấp phạm vi mặc định, thử lại ở event sau (idempotent)
            client = await clients_repo.insert(
                conn, id=new_id("cli"), owner_account_id=account_id, tool_name="Tool cá nhân",
                client_kind="primary_tool", status=status, ownership="personal", purpose="",
                allowed_platforms=all_platforms, allowed_scopes=policy.default_scopes("primary_tool"),
                created_from="provisioning", contract_version=CONTRACT_VERSION, now=now)
        created_or_reactivated = True
    if created_or_reactivated:
        await events.client_provisioned(conn, client_id=client["id"], account_id=account_id, status=client["status"])
    if client["status"] in ("active", "pending_email_verification") and \
            await credentials_repo.count_active(conn, client["id"], now) == 0:
        await credentials.provision_pending_reveal(conn, client)


async def _verified(conn, account_id: str, now: datetime) -> None:
    proj = await clients_repo.get_projection(conn, account_id)
    status = "active" if proj is None or proj["status"] == "pending" else proj["status"]
    proj = await clients_repo.upsert_projection(conn, account_id, now=now, status=status, email_verified=True)
    if proj["status"] != "active":
        return  # Xác thực email KHÔNG tự mở khoá tài khoản đang bị admin khoá
    for client in await clients_repo.by_owner(conn, account_id, lock=True):
        if client["status"] == "pending_email_verification":
            await clients_repo.set_status(conn, client["id"], "active", None, now)
            await events.client_provisioned(conn, client_id=client["id"], account_id=account_id, status="active")
            # Credential cấp hộ lúc còn pending_email_verification (reveal_state=pending) giờ reveal được NGAY vì
            # CredentialService.reveal() chỉ kiểm client["status"] == "active" — không cần hành động thêm ở đây.


async def _status_changed(conn, clients: ClientService, data: dict, account_id: str, now: datetime, *,
                          seq: int = 0) -> None:
    status = data.get("status")
    if status not in ("pending", "active", "suspended"):
        raise PermanentEventError("EVENT_INVALID", "status không hợp lệ")
    current = await clients_repo.get_projection(conn, account_id)
    if seq and current is not None and seq <= current.get("status_seq", 0):
        return   # event cũ hơn trạng thái đã ghi (replay dead-letter sau khi event mới hơn đã áp): không đảo ngược
    proj = await clients_repo.upsert_projection(conn, account_id, now=now, status=status, status_seq=seq)
    for client in await clients_repo.by_owner(conn, account_id, lock=True):
        if status == "suspended" and client["status"] in ("active", "pending_email_verification"):
            await clients.change_status(conn, client, "suspended", by="account")
        elif status == "active" and client["status"] == "suspended" and client["suspended_by"] == "account":
            restored = "active" if proj["email_verified"] else "pending_email_verification"
            await clients_repo.set_status(conn, client["id"], restored, None, now)


async def _deleted(conn, clients: ClientService, account_id: str, now: datetime, *, seq: int = 0) -> None:
    await clients_repo.upsert_projection(conn, account_id, now=now, status="deleted", status_seq=seq)   # xoá là trạng thái cuối
    for client in await clients_repo.by_owner(conn, account_id, lock=True):
        if client["status"] != "deleted":
            await clients.change_status(conn, client, "deleted", by="account")
