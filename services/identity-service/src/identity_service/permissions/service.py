"""Quản lý permission. Identity là nguồn sự thật; domain khác chỉ enforce từ claim `perms` trong token."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from identity_service.accounts import repository as accounts
from identity_service.audit import repository as audit
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.outbox import events
from identity_service.permissions import repository as perms_repo
from identity_service.security import utcnow

# Shim tương thích (bỏ ở Phase 6): tài khoản role legacy `discord` mặc định có reward:access.
_ROLE_IMPLIED = {"discord": ("reward:access",)}


def effective_permissions(role: str, granted: list[str]) -> list[str]:
    return sorted({*granted, *_ROLE_IMPLIED.get(role, ())})


class PermissionService:
    def __init__(self, db: Database, *, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.now = now

    async def for_account(self, conn, account: dict) -> list[str]:
        return effective_permissions(account["role"], await perms_repo.granted(conn, account["id"]))

    async def catalog(self) -> dict:
        async with self.db.connection() as conn:
            return {"permissions": [
                {"name": p["name"], "description": p["description"], "owner_service": p["owner_service"],
                 "admin_only": p["admin_only"]} for p in await perms_repo.catalog(conn)]}

    async def list_for(self, actor: dict, account_id: str) -> dict:
        if actor.get("sub") != account_id and actor.get("role") not in ("admin", "dash_admin"):
            raise DomainError(403, "Chỉ xem được quyền của chính mình")
        async with self.db.connection() as conn:
            account = await accounts.get_by_id(conn, account_id)
            if not account:
                raise DomainError(404, "Tài khoản không tồn tại")
            return {"account_id": account_id, "permissions": await self.for_account(conn, account)}

    async def _change(self, actor: dict, account_id: str, permission: str, *, grant: bool) -> dict:
        async with self.db.transaction() as conn:
            perm = await perms_repo.get(conn, permission)
            if not perm:
                raise DomainError(404, "Permission không tồn tại")
            if perm["admin_only"] and actor.get("role") != "admin":
                raise DomainError(403, "Chỉ quản trị viên (admin) mới được cấp/thu quyền này")
            account = await accounts.get_by_id(conn, account_id)
            if not account:
                raise DomainError(404, "Tài khoản không tồn tại")
            changed = (await perms_repo.grant(conn, account_id, permission, actor.get("sub", ""))) if grant \
                else (await perms_repo.revoke(conn, account_id, permission))
            if changed:
                await events.permission_changed(conn, account_id, permission, grant)
                await audit.record(conn, account_id=actor.get("sub", ""), event_type="permission_granted" if grant
                                   else "permission_revoked", now=self.now(), target_type="account",
                                   target_id=account_id, payload={"permission": permission})
            return {"ok": True, "account_id": account_id, "permission": permission, "granted": grant,
                    "changed": changed}

    async def grant(self, actor: dict, account_id: str, permission: str) -> dict:
        return await self._change(actor, account_id, permission, grant=True)

    async def revoke(self, actor: dict, account_id: str, permission: str) -> dict:
        return await self._change(actor, account_id, permission, grant=False)
