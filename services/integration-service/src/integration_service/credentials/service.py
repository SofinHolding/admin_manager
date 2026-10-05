"""Cấp / xoay / thu hồi API key máy và xác thực `Authorization: Bearer mpk_...` (mặt phẳng MÁY).

Quy tắc bảo mật:
  * plaintext chỉ trả MỘT lần ở response cấp/xoay; DB chỉ có key_id + sha256(secret);
  * mọi lỗi "không tìm thấy / sai secret / sai định dạng" trả CÙNG thông điệp (không dò được key_id);
  * trạng thái thu hồi chỉ lộ SAU khi secret đã đúng (AUTH_REVOKED);
  * user token (JWT) không phải API key => AUTH_INVALID.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from manager_contracts import CONTRACT_VERSION, ErrorCode
from manager_sdk import Principal

from integration_service.clients import repository as clients_repo
from integration_service.clients.service import ClientService
from integration_service.config import Settings
from integration_service.credentials import repository
from integration_service.db import Database
from integration_service.errors import ApiProblem, Extra, conflict, forbidden, not_found, unauthorized
from integration_service.feed import events
from integration_service.ratelimit import FixedWindowLimiter
from integration_service.security import generate_api_key, parse_api_key, sha256_hex, verify_secret

_BAD_KEY = "Thiếu hoặc sai API key"


@dataclass(frozen=True)
class MachineContext:
    """Danh tính MÁY đã xác thực. Mọi quyền sở hữu lấy từ đây — không bao giờ từ payload."""
    client_id: str
    owner_account_id: str
    key_id: str
    scopes: frozenset[str]
    allowed_platforms: frozenset[str]
    client_kind: str
    tool_name: str
    installation_id: str | None = None


class CredentialService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime], clients: ClientService,
                 rate_limiter: FixedWindowLimiter, fail_limiter: FixedWindowLimiter) -> None:
        self._db = db
        self._settings = settings
        self._now = now
        self._clients = clients
        self._rate = rate_limiter
        self._fail = fail_limiter

    # ── Xác thực mặt phẳng máy ────────────────────────────────────────────────────
    async def authenticate(self, authorization: str, ip: str, contract_version: str | None) -> MachineContext:
        if self._fail.exceeded(ip):
            raise ApiProblem(ErrorCode.RATE_LIMITED, "Quá nhiều lần xác thực sai", status_code=429,
                             headers={"Retry-After": str(self._fail.retry_after(ip))})
        parsed = parse_api_key(authorization)
        if parsed is None:
            self._fail.hit(ip)
            raise unauthorized(_BAD_KEY)
        key_id, secret = parsed
        async with self._db.connection() as conn:
            row = await repository.find_for_auth(conn, key_id)
        if not verify_secret(secret, row["secret_hash"] if row else None):
            self._fail.hit(ip)
            raise unauthorized(_BAD_KEY)
        assert row is not None
        now = self._now()
        if row["status"] == "revoked":
            raise unauthorized("API key đã bị thu hồi", ErrorCode.AUTH_REVOKED)
        if row["expires_at"] is not None and row["expires_at"] <= now:
            raise unauthorized("API key đã hết hạn")
        if row["client_status"] != "active":
            raise unauthorized("Client không còn hiệu lực", ErrorCode.AUTH_REVOKED)
        if contract_version is not None and contract_version != CONTRACT_VERSION:
            raise ApiProblem(ErrorCode.CONTRACT_VERSION_UNSUPPORTED,
                             f"Chỉ hỗ trợ contract {CONTRACT_VERSION}", status_code=400)
        self._rate.check(row["client_id"], "Vượt giới hạn request của client")
        async with self._db.connection() as conn:
            await repository.touch_last_used(conn, key_id, now)
        scopes = frozenset(row["scopes"]) & frozenset(row["allowed_scopes"])  # credential ∩ client
        return MachineContext(client_id=row["client_id"], owner_account_id=row["owner_account_id"], key_id=key_id,
                              scopes=scopes, allowed_platforms=frozenset(row["allowed_platforms"]),
                              client_kind=row["client_kind"], tool_name=row["tool_name"])

    # ── Cấp / xoay / thu hồi (mặt phẳng USER) ─────────────────────────────────────
    async def issue(self, principal: Principal, client_id: str, *, scopes: list[str] | None, label: str,
                    expires_in_days: int | None) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            client = await self._clients.load_visible(conn, client_id, principal, owner_only=True, lock=True)
            await self._require_issuable(conn, client)
            granted = self._effective_scopes(client, scopes)
            if await repository.count_active(conn, client_id, now) >= self._settings.max_credentials_per_client:
                raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số credential đang hoạt động của client", status_code=409)
            days = expires_in_days if expires_in_days is not None else (self._settings.credential_default_ttl_days or None)
            expires_at = now + timedelta(days=days) if days else None
            key_id, secret, api_key = generate_api_key()
            await repository.insert(conn, key_id=key_id, client_id=client_id, secret_hash=sha256_hex(secret),
                                    scopes=granted, label=label, expires_at=expires_at, now=now)
        return self._issued(client, key_id, api_key, granted, expires_at)

    async def rotate(self, principal: Principal, client_id: str, key_id: str, *, grace_seconds: int) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            client = await self._clients.load_visible(conn, client_id, principal, lock=True)
            await self._require_issuable(conn, client)
            old = await repository.get(conn, client_id, key_id, lock=True)
            if old is None:
                raise not_found(Extra.CREDENTIAL_NOT_FOUND, "Không tìm thấy credential")
            if old["status"] != "active" or (old["expires_at"] is not None and old["expires_at"] <= now):
                raise conflict(Extra.CLIENT_NOT_ACTIVE, "Credential không còn hiệu lực để xoay")
            if await repository.count_active(conn, client_id, now) >= self._settings.max_credentials_per_client:
                raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số credential đang hoạt động của client", status_code=409)
            new_id_, secret, api_key = generate_api_key()
            granted = [s for s in old["scopes"] if s in client["allowed_scopes"]]
            await repository.insert(conn, key_id=new_id_, client_id=client_id, secret_hash=sha256_hex(secret),
                                    scopes=granted, label=old["label"], expires_at=old["expires_at"], now=now,
                                    rotated_from=key_id)
            previous_expires: datetime | None
            if grace_seconds <= 0:
                await repository.revoke(conn, client_id, key_id, "rotated", now)
                await events.credential_revoked(conn, client_id=client_id, key_id=key_id)
                previous_expires = now
            else:
                previous_expires = now + timedelta(seconds=grace_seconds)
                if old["expires_at"] is not None:
                    previous_expires = min(previous_expires, old["expires_at"])
                await repository.set_expiry(conn, key_id, previous_expires)
        out = self._issued(client, new_id_, api_key, granted, old["expires_at"])
        out["previous_key_id"] = key_id
        out["previous_key_expires_at"] = previous_expires
        return out

    async def revoke(self, principal: Principal, client_id: str, key_id: str) -> dict:
        """Thu hồi tức thì. Gọi lại trên khoá đã thu hồi là no-op (không phát lại sự kiện)."""
        now = self._now()
        async with self._db.transaction() as conn:
            await self._clients.load_visible(conn, client_id, principal, lock=True)
            if await repository.get(conn, client_id, key_id, lock=True) is None:
                raise not_found(Extra.CREDENTIAL_NOT_FOUND, "Không tìm thấy credential")
            changed = await repository.revoke(conn, client_id, key_id, "revoked_by_user", now)
            if changed:
                await events.credential_revoked(conn, client_id=client_id, key_id=key_id)
        return {"client_id": client_id, "key_id": key_id, "status": "revoked", "already_revoked": not changed}

    # ── Nội bộ ────────────────────────────────────────────────────────────────────
    async def _require_issuable(self, conn, client: dict) -> None:
        if client["status"] == "pending_email_verification":
            raise forbidden(Extra.EMAIL_NOT_VERIFIED, "Email chưa xác thực: chưa thể cấp credential")
        if client["status"] != "active":
            raise forbidden(Extra.CLIENT_NOT_ACTIVE, "Client không ở trạng thái active")
        proj = await clients_repo.get_projection(conn, client["owner_account_id"])
        if proj is not None and (proj["status"] in ("suspended", "deleted") or not proj["email_verified"]):
            raise forbidden(Extra.EMAIL_NOT_VERIFIED if not proj["email_verified"] else Extra.ACCOUNT_INACTIVE,
                            "Tài khoản chưa đủ điều kiện cấp credential")

    @staticmethod
    def _effective_scopes(client: dict, requested: list[str] | None) -> list[str]:
        allowed = list(client["allowed_scopes"])
        if requested is None:
            return allowed
        extra = sorted(set(requested) - set(allowed))
        if extra:
            raise forbidden(ErrorCode.SCOPE_DENIED, "Scope vượt quá phạm vi client được phép", {"scopes": extra})
        return [s for s in allowed if s in set(requested)]

    @staticmethod
    def _issued(client: dict, key_id: str, api_key: str, scopes: list[str], expires_at: datetime | None) -> dict:
        return {"client_id": client["id"], "key_id": key_id, "api_key": api_key, "scopes": scopes,
                "expires_at": expires_at, "contract_version": client["contract_version"],
                "allowed_platforms": list(client["allowed_platforms"])}

