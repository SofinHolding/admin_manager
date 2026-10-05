"""Quản trị tài khoản (admin/dash_admin): liệt kê, khoá/mở khoá, sửa thông tin/role, xoá."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import asyncpg

from identity_service.accounts import repository as accounts
from identity_service.audit import repository as audit
from identity_service.auth import refresh_repository as refresh_tokens, session_repository as sessions
from identity_service.outbox import events
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.security import iso, utcnow

logger = logging.getLogger("identity.accounts")


def account_to_dict(row: dict) -> dict:
    return {"id": row["id"], "username": row["username"], "email": row["email"], "role": row["role"],
            "invite_key": row["invite_key"], "email_verified": row["email_verified"],
            "created_at": iso(row["created_at"]), "last_login": iso(row["last_login"]),
            "status": row["status"]}


class AccountAdminService:
    def __init__(self, db: Database, *, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.now = now

    async def list(self) -> dict:
        async with self.db.connection() as conn:
            return {"users": [account_to_dict(r) for r in await accounts.list_all(conn)]}

    async def set_status(self, actor: dict, user_id: str, new_status: str) -> dict:
        if user_id == actor.get("sub"):
            raise DomainError(400, "Không thể khoá chính mình")
        async with self.db.transaction() as conn:
            before = await accounts.get_by_id(conn, user_id)
            if not before or not await accounts.set_status(conn, user_id, new_status):
                raise DomainError(404, "Tài khoản không tồn tại")
            if before["status"] != new_status:
                await events.status_changed(conn, user_id, new_status, before["status"])
            if new_status == "suspended":  # khoá tài khoản: thu hồi mọi phiên NGAY, không chờ token hết hạn
                await refresh_tokens.revoke_all(conn, user_id)
                await sessions.revoke_all(conn, user_id, self.now())
                await events.session_revoked(conn, user_id)
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="account_status_changed",
                               now=self.now(), target_type="account", target_id=user_id,
                               payload={"status": new_status})
        logger.info("Đổi status user %s -> %s", user_id, new_status)
        return {"ok": True, "status": new_status}

    async def update(self, actor: dict, user_id: str, *, username: str | None, email: str | None,
                     role: str | None) -> dict:
        updates: dict[str, str] = {}
        async with self.db.transaction() as conn:
            if username is not None:
                existing = await accounts.get_by_username(conn, username)
                if existing and existing["id"] != user_id:
                    raise DomainError(409, "Tên đăng nhập đã được sử dụng")
                updates["username"] = username
            if email is not None:
                existing = await accounts.get_by_email(conn, email)
                if existing and existing["id"] != user_id:
                    raise DomainError(409, "Email đã được đăng ký")
                updates["email"] = email.lower()
            if role is not None:
                # Chỉ `admin` thật được thăng quyền lên admin (chặn dash_admin tự leo thang qua PATCH role).
                if role == "admin" and actor.get("role") != "admin":
                    raise DomainError(403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
                if user_id == actor.get("sub") and role != "admin":
                    raise DomainError(400, "Không thể hạ role chính mình")
                updates["role"] = role
            if not updates:
                return {"ok": True, "message": "Không có thay đổi."}
            before = await accounts.get_by_id(conn, user_id)
            try:
                ok = await accounts.update_by_admin(conn, user_id, updates)
                if ok and before:
                    if "role" in updates and updates["role"] != before["role"]:
                        await events.role_changed(conn, user_id, updates["role"], before["role"])
                        await sessions.revoke_all(conn, user_id, self.now())  # đổi role: bắt cấp token mới
                        await events.session_revoked(conn, user_id)
                    if "username" in updates:
                        await events.profile_updated(conn, user_id, updates["username"])
            except asyncpg.UniqueViolationError as exc:
                if "email" in (exc.constraint_name or ""):
                    raise DomainError(409, "Email đã được đăng ký") from None
                raise DomainError(409, "Tên đăng nhập đã được sử dụng") from None
            if not ok:
                raise DomainError(404, "Tài khoản không tồn tại")
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="account_updated",
                               now=self.now(), target_type="account", target_id=user_id,
                               payload={"fields": sorted(updates)})
        logger.info("Admin cập nhật user %s: %s", user_id, sorted(updates))
        return {"ok": True, "updates": updates}

    async def delete(self, actor: dict, user_id: str) -> dict:
        if user_id == actor.get("sub"):
            raise DomainError(400, "Không thể xoá chính mình")
        async with self.db.transaction() as conn:
            if not await accounts.delete(conn, user_id):
                raise DomainError(404, "Tài khoản không tồn tại")
            await events.session_revoked(conn, user_id)
            await events.deleted(conn, user_id)
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="account_deleted",
                               now=self.now(), target_type="account", target_id=user_id)
        logger.info("Xoá user %s", user_id)
        return {"ok": True}
