"""Quản trị invite key (admin/dash_admin). Phân quyền: chỉ `admin` thật được cấp key role `admin`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from identity_service.audit import repository as audit
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.registration_keys import repository as keys
from identity_service.security import iso, utcnow

logger = logging.getLogger("identity.keys")


def key_to_dict(row: dict) -> dict:
    """Hình dạng legacy: thời gian là chuỗi ISO, không có hạn thì ''."""
    return {"key": row["key"], "role": row["role"], "label": row["label"], "max_uses": row["max_uses"],
            "used_count": row["used_count"], "created_at": iso(row["created_at"]),
            "expires_at": iso(row["expires_at"]), "status": row["status"]}


class RegistrationKeyService:
    def __init__(self, db: Database, *, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.now = now

    async def create(self, actor: dict, *, role: str, label: str, max_uses: int, expires_days: int) -> dict:
        # Chặn dash_admin tự leo thang bằng cách tạo key cấp quyền admin.
        if role == "admin" and actor.get("role") != "admin":
            raise DomainError(403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
        async with self.db.transaction() as conn:
            row = await keys.create(conn, role=role, label=label, max_uses=max_uses,
                                    expires_days=expires_days, now=self.now())
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_created",
                               now=self.now(), target_type="invite_key", target_id=row["key"],
                               payload={"role": role, "max_uses": max_uses})
        logger.info("Tạo invite key %s role=%s max=%d expires=%dd", row["key"], role, max_uses, expires_days)
        return key_to_dict(row)

    async def list(self) -> dict:
        async with self.db.connection() as conn:
            return {"keys": [key_to_dict(r) for r in await keys.list_all(conn)]}

    async def revoke(self, actor: dict, key: str) -> dict:
        async with self.db.transaction() as conn:
            if not await keys.revoke(conn, key):
                raise DomainError(404, "Key không tồn tại")
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_revoked",
                               now=self.now(), target_type="invite_key", target_id=key)
        logger.info("Thu hồi invite key %s", key)
        return {"ok": True}
