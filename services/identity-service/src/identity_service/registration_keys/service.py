"""Quản trị invite key (admin/dash_admin). Phân quyền: chỉ `admin` thật được cấp/sửa key quyền admin."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from identity_service.audit import repository as audit
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.permissions import repository as perms_repo
from identity_service.registration_keys import policy as key_policy
from identity_service.registration_keys import presets as key_presets
from identity_service.registration_keys import repository as keys
from identity_service.security import iso, utcnow

logger = logging.getLogger("identity.keys")


def key_to_dict(row: dict) -> dict:
    """Hình dạng legacy: thời gian là chuỗi ISO, không có hạn thì ''. Trường mới CHỈ xuất hiện khi key có dùng."""
    out = {"key": row["key"], "role": row["role"], "label": row["label"], "max_uses": row["max_uses"],
           "used_count": row["used_count"], "created_at": iso(row["created_at"]),
           "expires_at": iso(row["expires_at"]), "status": row["status"]}
    if row.get("preset"):
        out["preset"] = row["preset"]
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

    def _resolve_preset(self, actor: dict, *, preset: str | None, role: str | None) -> tuple[str, str | None, dict | None]:
        """Trả `(role, preset lưu vào cột, capability_grant)`. Ném 422/403 nếu sai.

        Không gửi preset (client legacy chỉ gửi `role`) => cột preset NULL, không có capability_grant: key dùng default của
        role (capabilities.py) và phản hồi giữ y hệt contract legacy (không có field `preset`)."""
        if preset is not None:
            preset_def = key_presets.get(preset)
            if preset_def is None:
                raise DomainError(422, "preset không hợp lệ", code="REQUEST_VALIDATION_FAILED")
            if role is not None and role != preset_def.role:
                raise DomainError(422, "role không khớp với preset", code="REQUEST_VALIDATION_FAILED")
            resolved_role = preset_def.role
            grant: dict | None = {"capabilities": list(preset_def.capabilities), "override_defaults": True}
            preset_value = preset
        else:
            resolved_role = role or "viewer"
            grant = None
            preset_value = None
        if resolved_role == "admin" and actor.get("role") != "admin":
            raise DomainError(403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
        return resolved_role, preset_value, grant

    async def create(self, actor: dict, *, role: str | None = None, preset: str | None = None, label: str,
                     max_uses: int, expires_days: int, allowed_apps: list[str] | None = None) -> dict:
        resolved_role, preset_value, grant = self._resolve_preset(actor, preset=preset, role=role)
        apps = key_policy.normalize_apps(allowed_apps)
        async with self.db.transaction() as conn:
            for app in apps:  # app phải có permission `<app>:access` trong catalog (D11); kiểm lúc TẠO key
                perm = await perms_repo.get(conn, f"{app}:access")
                if not perm:
                    raise DomainError(422, "allowed_apps chứa ứng dụng chưa có trong catalog permission",
                                      code="REQUEST_VALIDATION_FAILED")
                if perm["admin_only"] and actor.get("role") != "admin":
                    raise DomainError(403, "Chỉ admin mới được gán permission này vào mã mời")
            row = await keys.create(conn, role=resolved_role, label=label, max_uses=max_uses,
                                    expires_days=expires_days, now=self.now(), preset=preset_value,
                                    allowed_apps=apps, capability_grant=grant)
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_created",
                               now=self.now(), target_type="invite_key", target_id=row["key"],
                               payload={"role": resolved_role, "preset": preset_value, "max_uses": max_uses,
                                        "apps": apps})
        logger.info("Tạo invite key %s role=%s preset=%s max=%d expires=%dd", row["key"], resolved_role,
                   preset_value, max_uses, expires_days)
        return key_to_dict(row)

    async def patch(self, actor: dict, key: str, *, preset: str | None = None, label: str | None = None,
                    max_uses: int | None = None, expires_days: int | None = None) -> dict:
        preset_def = None
        if preset is not None:
            preset_def = key_presets.get(preset)
            if preset_def is None:
                raise DomainError(422, "preset không hợp lệ", code="REQUEST_VALIDATION_FAILED")
        async with self.db.transaction() as conn:
            row = await keys.get(conn, key)
            if not row:
                raise DomainError(404, "Key không tồn tại")
            if row["status"] == "revoked":
                raise DomainError(409, "Key đã bị thu hồi", code="KEY_REVOKED")
            targets_admin = row["role"] == "admin" or (preset_def is not None and preset_def.role == "admin")
            if targets_admin and actor.get("role") != "admin":
                raise DomainError(403, "Chỉ quản trị viên (admin) mới được đặt/sửa key quyền admin")
            updates: dict[str, object] = {}
            if preset_def is not None:
                updates["preset"] = preset
                updates["role"] = preset_def.role
                updates["capability_grant"] = {"capabilities": list(preset_def.capabilities),
                                               "override_defaults": True}
            if label is not None:
                updates["label"] = label
            if max_uses is not None:
                if max_uses < row["used_count"]:
                    raise DomainError(422, "max_uses phải lớn hơn hoặc bằng số lượt đã dùng",
                                      code="REQUEST_VALIDATION_FAILED")
                updates["max_uses"] = max_uses
            if expires_days is not None:
                updates["expires_at"] = self.now() + timedelta(days=expires_days)
            if updates:
                row = await keys.update(conn, key, **updates)
                await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_updated",
                                   now=self.now(), target_type="invite_key", target_id=key,
                                   payload={k: v for k, v in updates.items() if k != "capability_grant"})
        logger.info("Sửa invite key %s", key)
        return key_to_dict(row)

    async def list(self) -> dict:
        async with self.db.connection() as conn:
            return {"keys": [key_to_dict(r) for r in await keys.list_all(conn)]}

    async def presets(self) -> dict:
        return {"presets": key_presets.list_presets()}

    async def revoke(self, actor: dict, key: str) -> dict:
        async with self.db.transaction() as conn:
            if not await keys.revoke(conn, key):
                raise DomainError(404, "Key không tồn tại")
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_revoked",
                               now=self.now(), target_type="invite_key", target_id=key)
        logger.info("Thu hồi invite key %s", key)
        return {"ok": True}

    async def suspend(self, actor: dict, key: str) -> dict:
        async with self.db.transaction() as conn:
            if not await keys.suspend(conn, key):
                raise DomainError(404, "Key không tồn tại hoặc không ở trạng thái active")
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_suspended",
                               now=self.now(), target_type="invite_key", target_id=key)
        logger.info("Tạm dừng invite key %s", key)
        return {"ok": True}

    async def unsuspend(self, actor: dict, key: str) -> dict:
        async with self.db.transaction() as conn:
            if not await keys.unsuspend(conn, key):
                raise DomainError(404, "Key không tồn tại hoặc không ở trạng thái suspended")
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="invite_key_unsuspended",
                               now=self.now(), target_type="invite_key", target_id=key)
        logger.info("Khôi phục invite key %s", key)
        return {"ok": True}
