"""Nghiệp vụ Integration Client.

Hai nguồn tạo DUY NHẤT (luật 0 của thiết kế tool-key):
  * client PERSONAL (`ownership=personal`, `client_kind=primary_tool`): CHỈ tự động qua
    `provisioning/handlers.py` khi account có `capability:tool_integration` — không ai tạo tay.
  * client SHARED (`ownership=shared`, `client_kind in (fallback_tool, service)`): CHỈ role `admin`, qua wizard
    bắt buộc server giữ bước: `draft -> configured -> active` (`create_wizard` -> `configure` -> `activate`).
"""

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
from integration_service.errors import ApiProblem, Extra, conflict, forbidden, invalid, not_found
from integration_service.feed import events
from integration_service.installations import repository as installations
from integration_service.platforms import repository as registry
from integration_service.security import new_id


def public_client(row: dict) -> dict:
    """Hình dạng công khai của client: KHÔNG có secret/hash."""
    out = {"client_id": row["id"], "owner_account_id": row["owner_account_id"], "tool_name": row["tool_name"],
           "client_kind": row["client_kind"], "status": row["status"], "ownership": row["ownership"],
           "purpose": row["purpose"], "contract_version": row["contract_version"],
           "allowed_platforms": list(row["allowed_platforms"]), "allowed_scopes": list(row["allowed_scopes"]),
           "created_from": row["created_from"], "configured_at": row.get("configured_at"),
           "activated_at": row.get("activated_at"), "activated_by": row.get("activated_by"),
           "created_at": row["created_at"], "updated_at": row["updated_at"]}
    # owner_username / activated_by_username có trong row khi list_for JOIN account_projection
    if row.get("owner_username"):
        out["owner_username"] = row["owner_username"]
    if row.get("activated_by_username"):
        out["activated_by_username"] = row["activated_by_username"]
    if "credentials" in row:   # list_for: tóm tắt credential chưa revoked (xem ClientService.list)
        out["credentials"] = row["credentials"]
    return out


def public_credential(row: dict) -> dict:
    return {"key_id": row["key_id"], "scopes": list(row["scopes"]), "status": row["status"], "label": row["label"],
            "expires_at": row["expires_at"], "last_used_at": row["last_used_at"], "created_at": row["created_at"],
            "revoked_at": row.get("revoked_at"), "reveal_state": row.get("reveal_state"),
            "reveal_expires_at": row.get("reveal_expires_at"), "revealed_at": row.get("revealed_at"),
            "bound_device_id": row.get("bound_device_id"), "bound_installation_id": row.get("bound_installation_id"),
            "activated_at": row.get("activated_at")}


def credential_summary(row: dict) -> dict:
    """Tóm tắt credential cho `GET /clients` (list) — đủ để admin thấy khoá của user nào mà không gọi detail."""
    return {"key_id": row["key_id"], "status": row["status"], "reveal_state": row.get("reveal_state"),
            "bound_device_id": row.get("bound_device_id"), "activated_at": row.get("activated_at"),
            "last_used_at": row["last_used_at"], "expires_at": row["expires_at"]}


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

    async def load_owned_personal(self, conn: asyncpg.Connection, principal: Principal, *, lock: bool = False) -> dict:
        """Client personal của CHÍNH principal (dùng cho `/me/*`) — 404 nếu chưa có (chưa được cấp capability)."""
        row = await repository.get_personal(conn, principal.sub, lock=lock)
        if row is None:
            raise not_found(Extra.CLIENT_NOT_FOUND, "Chưa có Tool cá nhân — cần được cấp quyền tool_integration")
        return row

    # ── Wizard client dùng chung (CHỈ admin) ─────────────────────────────────────
    async def create_wizard(self, principal: Principal, *, tool_name: str, client_kind: str, purpose: str,
                            platforms: list[str]) -> dict:
        if client_kind == "primary_tool":
            raise invalid("client_kind=primary_tool chỉ được tạo tự động từ capability:tool_integration",
                          {"client_kind": client_kind})
        if client_kind not in policy.CLIENT_KINDS:
            raise invalid("client_kind không hợp lệ", {"client_kind": client_kind})
        now = self._now()
        async with self._db.transaction() as conn:
            registered = await registry.enabled_platforms(conn)
            allowed = sorted(set(platforms) & registered) if platforms else []
            row = await repository.insert(
                conn, id=new_id("cli"), owner_account_id=principal.sub, tool_name=tool_name,
                client_kind=client_kind, status="draft", ownership="shared", purpose=purpose,
                allowed_platforms=allowed, allowed_scopes=[], created_from="api",
                contract_version=CONTRACT_VERSION, now=now)
        return row

    async def configure(self, client_id: str, *, allowed_platforms: list[str], allowed_scopes: list[str]) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            row = await repository.get(conn, client_id, lock=True)
            if row is None:
                raise not_found(Extra.CLIENT_NOT_FOUND, "Không tìm thấy client")
            if row["status"] != "draft":
                raise conflict(Extra.CLIENT_NOT_READY, "Chỉ cấu hình được client đang ở trạng thái draft")
            registered = await registry.enabled_platforms(conn)
            platforms = sorted(set(allowed_platforms))
            if not platforms or not set(platforms) <= registered:
                raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform ngoài registry")
            scopes = sorted(set(allowed_scopes))
            unknown = [s for s in scopes if s not in policy.ALL_SCOPES]
            if unknown:
                raise invalid("Scope không hợp lệ", {"unknown": unknown}, ErrorCode.REQUEST_VALIDATION_FAILED)
            if policy.SYSTEM_SCOPE in scopes and row["client_kind"] != "fallback_tool":
                raise invalid(f"{policy.SYSTEM_SCOPE} chỉ cấp cho client_kind=fallback_tool", None,
                              ErrorCode.REQUEST_VALIDATION_FAILED)
            if row["client_kind"] != "fallback_tool" and any(s in scopes for s in policy.FALLBACK_SCOPES):
                raise invalid("Scope jobs:* chỉ dành cho client_kind=fallback_tool", None,
                              ErrorCode.REQUEST_VALIDATION_FAILED)
            row = await repository.configure(conn, client_id, allowed_platforms=platforms, allowed_scopes=scopes,
                                             now=now)
        return row

    async def activate(self, principal: Principal, client_id: str, *, confirm_tool_name: str) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            row = await repository.get(conn, client_id, lock=True)
            if row is None:
                raise not_found(Extra.CLIENT_NOT_FOUND, "Không tìm thấy client")
            if row["status"] != "draft" or row["configured_at"] is None:
                raise conflict(Extra.CLIENT_NOT_READY, "Client cần cấu hình (PUT .../config) trước khi kích hoạt")
            if confirm_tool_name != row["tool_name"]:
                raise invalid("confirm_tool_name không khớp tool_name của client", None,
                              ErrorCode.REQUEST_VALIDATION_FAILED)
            row = await repository.activate(conn, client_id, activated_by=principal.sub, now=now)
        return row

    async def list(self, principal: Principal, *, owner: str | None, status: str | None, limit: int,
                   offset: int) -> list[dict]:
        if not principal.is_admin:
            if owner and owner != principal.sub:
                return []
            owner = principal.sub  # người thường chỉ thấy client của mình
        async with self._db.connection() as conn:
            rows = await repository.list_for(conn, owner=owner, status=status, limit=limit, offset=offset)
            out = []
            for row in rows:
                creds = [credential_summary(c) for c in await credentials.list_for_client(conn, row["id"])
                        if c["status"] != "revoked"]
                out.append({**row, "credentials": creds})
            return out

    async def detail(self, principal: Principal, client_id: str) -> dict:
        async with self._db.connection() as conn:
            row = await self.load_visible(conn, client_id, principal)
            out = public_client(row)
            # Bổ sung owner_username và activated_by_username từ account_projection
            proj = await repository.get_projection(conn, row["owner_account_id"])
            if proj and proj.get("username"):
                out["owner_username"] = proj["username"]
            if row.get("activated_by"):
                ab_proj = await repository.get_projection(conn, row["activated_by"])
                if ab_proj and ab_proj.get("username"):
                    out["activated_by_username"] = ab_proj["username"]
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
            if fields.get("status") == "active" and row["status"] == "draft":
                raise conflict(Extra.CLIENT_NOT_READY, "Client còn draft: dùng POST .../activate sau khi cấu hình")
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
