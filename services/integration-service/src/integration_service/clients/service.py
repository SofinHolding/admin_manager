"""Nghiệp vụ Integration Client: tạo/xem/sửa, chuyển trạng thái, đồng bộ theo account (provisioning)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg
from manager_contracts import CONTRACT_VERSION, ErrorCode
from manager_sdk import Principal

from integration_service.clients import policy, repository
from integration_service.config import Settings
from integration_service.credentials import repository as credentials
from integration_service.db import Database
from integration_service.errors import ApiProblem, Extra, forbidden, invalid, not_found
from integration_service.feed import events
from integration_service.installations import repository as installations
from integration_service.platforms import repository as registry
from integration_service.security import new_id


def public_client(row: dict) -> dict:
    """Hình dạng công khai của client: KHÔNG có secret/hash."""
    return {"client_id": row["id"], "owner_account_id": row["owner_account_id"], "tool_name": row["tool_name"],
            "client_kind": row["client_kind"], "status": row["status"], "contract_version": row["contract_version"],
            "allowed_platforms": list(row["allowed_platforms"]), "allowed_scopes": list(row["allowed_scopes"]),
            "created_from": row["created_from"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def public_credential(row: dict) -> dict:
    return {"key_id": row["key_id"], "scopes": list(row["scopes"]), "status": row["status"], "label": row["label"],
            "expires_at": row["expires_at"], "last_used_at": row["last_used_at"], "created_at": row["created_at"],
            "revoked_at": row.get("revoked_at")}


def public_installation(row: dict) -> dict:
    return {"installation_id": row["id"], "device_id": row["device_id"], "hostname": row["hostname"], "os": row["os"],
            "app_version": row["app_version"], "capabilities": list(row["capabilities"]), "status": row["status"],
            "first_seen_at": row["first_seen_at"], "last_seen_at": row["last_seen_at"]}


class ClientService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime]) -> None:
        self._db = db
        self._settings = settings
        self._now = now

    # ── Trợ giúp phân quyền ───────────────────────────────────────────────────────
    async def require_account_usable(self, conn: asyncpg.Connection, account_id: str) -> dict | None:
        proj = await repository.get_projection(conn, account_id)
        if proj and proj["status"] in ("suspended", "deleted"):
            raise forbidden(Extra.ACCOUNT_INACTIVE, "Tài khoản đang bị khoá hoặc đã xoá")
        return proj

    async def load_visible(self, conn: asyncpg.Connection, client_id: str, principal: Principal, *,
                           owner_only: bool = False, lock: bool = False) -> dict:
        """Client mà người gọi được phép thấy. Không phải chủ (và không phải admin) => 404, tránh dò id."""
        row = await repository.get(conn, client_id, lock=lock)
        if row is None or (row["owner_account_id"] != principal.sub and (owner_only or not principal.is_admin)):
            raise not_found(Extra.CLIENT_NOT_FOUND, "Không tìm thấy client")
        return row

    async def load_manageable(self, conn: asyncpg.Connection, client_id: str, principal: Principal, *,
                              lock: bool = False) -> dict:
        """Client mà người gọi được phép THAY ĐỔI (xoay/thu hồi credential): chủ client hoặc role `admin`.
        `dash_admin` chỉ được XEM (load_visible) => 403 SCOPE_DENIED (không phải 404: họ thấy client được). Người khác => 404."""
        row = await self.load_visible(conn, client_id, principal, lock=lock)
        if row["owner_account_id"] != principal.sub and principal.role != "admin":
            raise forbidden(ErrorCode.SCOPE_DENIED, "Chỉ chủ client hoặc quản trị viên cao nhất mới được thao tác credential")
        return row

    # ── Use case ──────────────────────────────────────────────────────────────────
    async def create(self, principal: Principal, *, tool_name: str, client_kind: str, platforms: list[str]) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            proj = await self.require_account_usable(conn, principal.sub)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "clients:" + principal.sub)
            if await repository.count_for_owner(conn, principal.sub) >= self._settings.max_clients_per_owner:
                raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số client tối đa cho mỗi tài khoản", status_code=409)
            registered = await registry.enabled_platforms(conn)
            allowed = sorted(set(platforms) & registered)  # cắt theo policy: chỉ platform có trong registry
            if not allowed:
                raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Không có platform hợp lệ trong yêu cầu")
            verified = bool(proj and proj["email_verified"] and proj["status"] == "active")
            row = await repository.insert(
                conn, id=new_id("cli"), owner_account_id=principal.sub, tool_name=tool_name,
                client_kind=client_kind, status="active" if verified else "pending_email_verification",
                allowed_platforms=allowed, allowed_scopes=policy.default_scopes(client_kind),
                created_from="api", contract_version=CONTRACT_VERSION, now=now)
        return row

    async def list(self, principal: Principal, *, owner: str | None, status: str | None, limit: int,
                   offset: int) -> list[dict]:
        if not principal.is_admin:
            if owner and owner != principal.sub:
                return []
            owner = principal.sub  # người thường chỉ thấy client của mình
        async with self._db.connection() as conn:
            return await repository.list_for(conn, owner=owner, status=status, limit=limit, offset=offset)

    async def detail(self, principal: Principal, client_id: str) -> dict:
        async with self._db.connection() as conn:
            row = await self.load_visible(conn, client_id, principal)
            out = public_client(row)
            out["installations"] = [public_installation(i) for i in await installations.list_for_client(conn, client_id)]
            out["credentials"] = [public_credential(c) for c in await credentials.list_for_client(conn, client_id)]
        return out

    async def admin_patch(self, client_id: str, *, fields: dict) -> dict:
        """PATCH chỉ dành cho role `admin`: status, allowed_scopes, allowed_platforms, tool_name."""
        now = self._now()
        async with self._db.transaction() as conn:
            row = await repository.get(conn, client_id, lock=True)
            if row is None:
                raise not_found(Extra.CLIENT_NOT_FOUND, "Không tìm thấy client")
            updates: dict = {}
            if "allowed_scopes" in fields:
                scopes = sorted(set(fields["allowed_scopes"]))
                unknown = [s for s in scopes if s not in policy.ALL_SCOPES]
                if unknown:
                    raise invalid("Scope không hợp lệ", {"unknown": unknown}, ErrorCode.REQUEST_VALIDATION_FAILED)
                if policy.SYSTEM_SCOPE in scopes and row["client_kind"] != "fallback_tool":
                    raise invalid(f"{policy.SYSTEM_SCOPE} chỉ cấp cho client_kind=fallback_tool", None,
                                  ErrorCode.REQUEST_VALIDATION_FAILED)
                if row["client_kind"] != "fallback_tool" and any(s in scopes for s in policy.FALLBACK_SCOPES):
                    raise invalid("Scope jobs:* chỉ dành cho client_kind=fallback_tool", None,
                                  ErrorCode.REQUEST_VALIDATION_FAILED)
                updates["allowed_scopes"] = scopes
            if "allowed_platforms" in fields:
                registered = await registry.enabled_platforms(conn)
                platforms = sorted(set(fields["allowed_platforms"]))
                if not platforms or not set(platforms) <= registered:
                    raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform ngoài registry")
                updates["allowed_platforms"] = platforms
            if "tool_name" in fields:
                updates["tool_name"] = fields["tool_name"]
            if updates:
                row = await repository.update_fields(conn, client_id, updates, now)
            if "status" in fields and fields["status"] != row["status"]:
                row = await self.change_status(conn, row, fields["status"], by="admin")
        return row

    async def change_status(self, conn: asyncpg.Connection, row: dict, status: str, *, by: str) -> dict:
        """Chuyển trạng thái client. suspended/deleted => THU HỒI MỌI credential (phát credential.revoked)."""
        now = self._now()
        if row["status"] == "deleted":
            raise ApiProblem(Extra.CLIENT_NOT_ACTIVE, "Client đã xoá", status_code=409)
        if status == "active":
            proj = await repository.get_projection(conn, row["owner_account_id"])
            if not (proj and proj["email_verified"] and proj["status"] == "active"):
                raise forbidden(Extra.EMAIL_NOT_VERIFIED,
                                "Không thể kích hoạt: email chưa xác thực hoặc tài khoản không active")
        if status in ("suspended", "deleted"):
            for key_id in await credentials.revoke_all(conn, row["id"], f"client_{status}", now):
                await events.credential_revoked(conn, client_id=row["id"], key_id=key_id)
        await repository.set_status(conn, row["id"], status, by if status == "suspended" else None, now)
        return await repository.get(conn, row["id"])  # type: ignore[return-value]
