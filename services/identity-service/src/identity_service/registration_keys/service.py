"""Quản trị invite key (admin/dash_admin). Phân quyền: chỉ `admin` thật được cấp key role `admin`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from identity_service.audit import repository as audit
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.permissions import repository as perms_repo
from identity_service.registration_keys import policy as key_policy, repository as keys
from identity_service.security import iso, utcnow

logger = logging.getLogger("identity.keys")


def key_to_dict(row: dict) -> dict:
    """Hình dạng legacy: thời gian là chuỗi ISO, không có hạn thì ''."""
    out = {"key": row["key"], "role": row["role"], "label": row["label"], "max_uses": row["max_uses"],
           "used_count": row["used_count"], "created_at": iso(row["created_at"]),
           "expires_at": iso(row["expires_at"]), "status": row["status"]}
    # Trường mới CHỈ xuất hiện khi key có dùng: phản hồi của key thường giữ y hệt legacy (parity).
    policy = keys.parse_policy(row.get("integration_policy"))
    if policy is not None:
        out["integration_policy"] = policy
    if row.get("allowed_apps"):
        out["allowed_apps"] = list(row["allowed_apps"])
    grant = keys.parse_capability_grant(row.get("capability_grant"))
    if grant is not None:
        out["capability_grant"] = grant
    return out


class RegistrationKeyService:
    def __init__(self, db: Database, *, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.now = now

    async def create(self, actor: dict, *, role: str, label: str, max_uses: int, expires_days: int,
                     integration_policy: dict | None = None, allowed_apps: list[str] | None = None,
                     capability_grant: dict | None = None) -> dict:
        # Chặn dash_admin tự leo thang bằng cách tạo key cấp quyền admin.
        if role == "admin" and actor.get("role") != "admin":
            raise DomainError(403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
        policy = key_policy.normalize_policy(integration_policy)
        apps = key_policy.normalize_apps(allowed_apps)
        grant = key_policy.normalize_capability_grant(capability_grant)
        async with self.db.transaction() as conn:
            for app in apps:  # app phải có permission `<app>:access` trong catalog (D11); kiểm lúc TẠO key
                perm = await perms_repo.get(conn, f"{app}:access")
                if not perm:
                    raise DomainError(422, "allowed_apps chứa ứng dụng chưa có trong catalog permission",
                                      code="REQUEST_VALIDATION_FAILED")
                if perm["admin_only"] and actor.get("role") != "admin":
                    raise DomainError(403, "Chỉ admin mới được gán permission này vào mã mời")
            if grant:  # capability phải có trong catalog; admin_only capability chỉ admin mới gán được
                for cap in grant["capabilities"]:
                    perm = await perms_repo.get(conn, cap)
                    if not perm:
                        raise DomainError(422, "capability_grant chứa capability chưa có trong catalog permission",
                                          code="REQUEST_VALIDATION_FAILED")
                    if perm["admin_only"] and actor.get("role") != "admin":
                        raise DomainError(403, "Chỉ admin mới được gán capability này vào mã mời")
            row = await keys.create(conn, role=role, label=label, max_uses=max_uses,
                                    expires_days=expires_days, now=self.now(), integration_policy=policy,
                                    allowed_apps=apps, capability_grant=grant)
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_created",
                               now=self.now(), target_type="invite_key", target_id=row["key"],
                               payload={"role": role, "max_uses": max_uses, "apps": apps,
                                        "integration": bool(policy and policy["allow_integration_registration"]),
                                        "capability_grant": grant})
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
