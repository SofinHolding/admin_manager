"""Cấp / xoay / thu hồi API key máy và xác thực `Authorization: Bearer mpk_...` (mặt phẳng MÁY).

Quy tắc bảo mật:
  * plaintext chỉ trả MỘT lần: (a) ở response cấp/xoay khi CHỦ client personal tự thao tác, hoặc
    (b) ở `POST /me/credential/reveal` khi credential được cấp HỘ (provisioning / admin trên personal / transfer)
    — ciphertext Fernet xoá ngay sau khi trả, KHÔNG BAO GIỜ log/`repr`;
  * admin KHÔNG BAO GIỜ thấy plaintext của credential CÁ NHÂN (luật 0 thiết kế tool-key);
  * mọi lỗi "không tìm thấy / sai secret / sai định dạng" trả CÙNG thông điệp (không dò được key_id);
  * trạng thái thu hồi chỉ lộ SAU khi secret đã đúng (AUTH_REVOKED);
  * user token (JWT) không phải API key => AUTH_INVALID;
  * 1 credential chỉ bind 1 thiết bị (device binding ở handshake, xem installations/service.py).
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
from integration_service.crypto import RevealCrypto
from integration_service.db import Database
from integration_service.errors import ApiProblem, Extra, conflict, forbidden, gone, invalid, not_found, unauthorized
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
    ownership: str = "personal"
    installation_id: str | None = None      # mặt phẳng máy KHÁC handshake: = bound_installation_id (xem deps.machine)
    bound_device_id: str | None = None       # None = credential chưa handshake lần nào (CREDENTIAL_NOT_ACTIVATED)
    bound_installation_id: str | None = None
    delegated_owner_id: str | None = None   # PHASE 4: set khi có header X-Delegated-Job hợp lệ (chỉ fallback_tool)

    @property
    def effective_owner(self) -> str:
        """Owner dùng cho QUYỀN SỞ HỮU DỮ LIỆU (snapshot/resource): `delegated_owner_id` nếu đang ingest hộ, ngược lại
        tài khoản của chính credential. KHÔNG dùng cho audit/job ownership/rate-limit — những chỗ đó vẫn phải dùng
        `owner_account_id` thật (ai thực sự gọi API)."""
        return self.delegated_owner_id or self.owner_account_id


class CredentialService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime], clients: ClientService,
                 rate_limiter: FixedWindowLimiter, fail_limiter: FixedWindowLimiter,
                 reveal_crypto: RevealCrypto | None = None) -> None:
        self._db = db
        self._settings = settings
        self._now = now
        self._clients = clients
        self._rate = rate_limiter
        self._fail = fail_limiter
        self._reveal = reveal_crypto or RevealCrypto(settings.reveal_key) if settings.reveal_key else None

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
                              client_kind=row["client_kind"], tool_name=row["tool_name"],
                              ownership=row["ownership"], bound_device_id=row["bound_device_id"],
                              bound_installation_id=row["bound_installation_id"])

    # ── Cấp / xoay / thu hồi (mặt phẳng USER) ─────────────────────────────────────
    async def issue(self, principal: Principal, client_id: str, *, scopes: list[str] | None, label: str,
                    expires_in_days: int | None) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            client = await self._clients.load_manageable(conn, client_id, principal, lock=True)
            self._require_issuable(client)
            await self._require_account_ok(conn, client)
            granted = self._effective_scopes(client, scopes)
            if await repository.count_active(conn, client_id, now) >= self._settings.max_credentials_per_client:
                raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số credential đang hoạt động của client", status_code=409)
            is_owner = client["owner_account_id"] == principal.sub
            days = expires_in_days if expires_in_days is not None else (self._settings.credential_default_ttl_days or None)
            expires_at = now + timedelta(days=days) if days else None
            if client["ownership"] == "personal" and not is_owner:
                # Admin cấp HỘ trên client personal: KHÔNG BAO GIỜ thấy plaintext — chủ phải tự reveal.
                row = await self._insert_pending_reveal(conn, client, scopes=granted, label=label,
                                                        expires_at=expires_at, now=now)
                return self._issued_pending(client, row)
            key_id, secret, api_key = generate_api_key()
            await repository.insert(conn, key_id=key_id, client_id=client_id, secret_hash=sha256_hex(secret),
                                    scopes=granted, label=label, expires_at=expires_at, now=now)
        return self._issued(client, key_id, api_key, granted, expires_at)

    async def rotate(self, principal: Principal, client_id: str, key_id: str, *, grace_seconds: int) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            client = await self._clients.load_manageable(conn, client_id, principal, lock=True)
            self._require_issuable(client)
            await self._require_account_ok(conn, client)
            old = await repository.get(conn, client_id, key_id, lock=True)
            if old is None:
                raise not_found(Extra.CREDENTIAL_NOT_FOUND, "Không tìm thấy credential")
            if old["status"] != "active" or (old["expires_at"] is not None and old["expires_at"] <= now):
                raise conflict(Extra.CLIENT_NOT_ACTIVE, "Credential không còn hiệu lực để xoay")
            if await repository.count_active(conn, client_id, now) >= self._settings.max_credentials_per_client:
                raise ApiProblem(Extra.LIMIT_EXCEEDED, "Vượt số credential đang hoạt động của client", status_code=409)
            granted = [s for s in old["scopes"] if s in client["allowed_scopes"]]
            is_owner = client["owner_account_id"] == principal.sub
            previous_expires: datetime | None
            if client["ownership"] == "personal" and not is_owner:
                new_row = await self._insert_pending_reveal(conn, client, scopes=granted, label=old["label"],
                                                             expires_at=old["expires_at"], now=now,
                                                             rotated_from=key_id)
                if grace_seconds <= 0:
                    await repository.revoke(conn, client_id, key_id, "rotated", now)
                    await events.credential_revoked(conn, client_id=client_id, key_id=key_id)
                    previous_expires = now
                else:
                    previous_expires = now + timedelta(seconds=grace_seconds)
                    if old["expires_at"] is not None:
                        previous_expires = min(previous_expires, old["expires_at"])
                    await repository.set_expiry(conn, key_id, previous_expires)
                out = self._issued_pending(client, new_row)
                out["previous_key_id"] = key_id
                out["previous_key_expires_at"] = previous_expires
                return out
            new_id_, secret, api_key = generate_api_key()
            await repository.insert(conn, key_id=new_id_, client_id=client_id, secret_hash=sha256_hex(secret),
                                    scopes=granted, label=old["label"], expires_at=old["expires_at"], now=now,
                                    rotated_from=key_id)
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
            await self._clients.load_manageable(conn, client_id, principal, lock=True)
            if await repository.get(conn, client_id, key_id, lock=True) is None:
                raise not_found(Extra.CREDENTIAL_NOT_FOUND, "Không tìm thấy credential")
            changed = await repository.revoke(conn, client_id, key_id, "revoked_by_user", now)
            if changed:
                await events.credential_revoked(conn, client_id=client_id, key_id=key_id)
        return {"client_id": client_id, "key_id": key_id, "status": "revoked", "already_revoked": not changed}

    # ── Reveal (chủ nhận plaintext của credential cấp HỘ) ────────────────────────
    async def reveal(self, principal: Principal) -> dict:
        now = self._now()
        expired = False
        async with self._db.transaction() as conn:
            client = await self._clients.load_owned_personal(conn, principal, lock=True)
            if client["status"] == "pending_email_verification":
                raise forbidden(Extra.EMAIL_NOT_VERIFIED, "Email chưa xác thực: chưa thể nhận khoá")
            if client["status"] != "active":
                raise forbidden(Extra.CLIENT_NOT_ACTIVE, "Client không ở trạng thái active")
            cred = await repository.find_pending_reveal(conn, client["id"], lock=True)
            if cred is None:
                raise not_found(Extra.REVEAL_NOT_AVAILABLE, "Không có credential đang chờ nhận")
            if cred["reveal_expires_at"] is not None and cred["reveal_expires_at"] <= now:
                # Ghi `reveal_state=expired` PHẢI commit dù sau đó báo lỗi cho người gọi — raise NGOÀI transaction
                # này (raise bên trong sẽ bị rollback, mark_reveal_expired coi như chưa từng chạy).
                await repository.mark_reveal_expired(conn, cred["key_id"])
                expired = True
            else:
                if self._reveal is None:
                    raise ApiProblem(ErrorCode.UNAVAILABLE, "Reveal chưa được cấu hình (thiếu INTEGRATION_REVEAL_KEY)",
                                     status_code=503)
                api_key = self._reveal.decrypt(cred["reveal_ciphertext"])
                await repository.mark_revealed(conn, cred["key_id"], now)
        if expired:
            raise gone(Extra.REVEAL_EXPIRED, "Reveal đã hết hạn — admin hoặc chủ phải cấp lại credential")
        return self._issued(client, cred["key_id"], api_key, list(cred["scopes"]), cred["expires_at"])

    # ── Chuyển chủ client personal (admin) ────────────────────────────────────────
    async def transfer(self, client_id: str, *, new_owner_account_id: str) -> dict:
        """Chỉ client personal. Dữ liệu đã ingest trước đó GIỮ owner cũ — chỉ đổi client + cấp credential mới
        (reveal_state=pending) cho chủ mới. Thu hồi mọi credential cũ."""
        now = self._now()
        async with self._db.transaction() as conn:
            client = await clients_repo.get(conn, client_id, lock=True)
            if client is None:
                raise not_found(Extra.CLIENT_NOT_FOUND, "Không tìm thấy client")
            if client["ownership"] != "personal":
                raise invalid("Chỉ chuyển chủ được client personal", {"ownership": client["ownership"]})
            target = await clients_repo.get_projection(conn, new_owner_account_id)
            if target is None or target["status"] != "active":
                raise invalid("Tài khoản đích không tồn tại hoặc không active", {"new_owner_account_id": "invalid"})
            if await clients_repo.get_personal(conn, new_owner_account_id, lock=True) is not None:
                raise conflict(Extra.PERSONAL_CLIENT_EXISTS, "Tài khoản đích đã có client personal pending/active")
            for key_id in await repository.revoke_all(conn, client_id, "transfer", now):
                await events.credential_revoked(conn, client_id=client_id, key_id=key_id)
            client = await clients_repo.transfer_owner(conn, client_id, new_owner_account_id, now)
            await self._insert_pending_reveal(conn, client, scopes=client["allowed_scopes"], label="", expires_at=None,
                                              now=now)
        return client

    # ── Provisioning (dùng bởi provisioning/handlers.py) ──────────────────────────
    async def provision_pending_reveal(self, conn, client: dict) -> dict:
        """Tạo credential `reveal_state=pending` cho client personal mới provision/kích hoạt lại — KHÔNG trả plaintext
        ở đây, chủ phải tự gọi `POST /v1/integrations/me/credential/reveal`. Idempotent ở tầng gọi (chỉ gọi khi
        client chưa có credential active)."""
        return await self._insert_pending_reveal(conn, client, scopes=client["allowed_scopes"], label="",
                                                 expires_at=None, now=self._now())

    async def _insert_pending_reveal(self, conn, client: dict, *, scopes: list[str], label: str,
                                     expires_at: datetime | None, now: datetime,
                                     rotated_from: str | None = None) -> dict:
        if self._reveal is None:
            raise ApiProblem(ErrorCode.UNAVAILABLE, "Reveal chưa được cấu hình (thiếu INTEGRATION_REVEAL_KEY)",
                             status_code=503)
        key_id, secret, api_key = generate_api_key()
        ciphertext = self._reveal.encrypt(api_key)
        reveal_expires_at = now + timedelta(days=self._settings.credential_reveal_ttl_days)
        return await repository.insert(conn, key_id=key_id, client_id=client["id"], secret_hash=sha256_hex(secret),
                                       scopes=scopes, label=label, expires_at=expires_at, now=now,
                                       rotated_from=rotated_from, reveal_ciphertext=ciphertext,
                                       reveal_state="pending", reveal_expires_at=reveal_expires_at)

    # ── Nội bộ ────────────────────────────────────────────────────────────────────
    def _require_issuable(self, client: dict) -> None:
        if client["status"] == "draft":
            raise conflict(Extra.CLIENT_NOT_READY, "Client dùng chung còn draft — cần configure + activate trước")
        if client["status"] == "pending_email_verification":
            raise forbidden(Extra.EMAIL_NOT_VERIFIED, "Email chưa xác thực: chưa thể cấp credential")
        if client["status"] != "active":
            raise forbidden(Extra.CLIENT_NOT_ACTIVE, "Client không ở trạng thái active")

    async def _require_account_ok(self, conn, client: dict) -> None:
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

    @staticmethod
    def _issued_pending(client: dict, cred: dict) -> dict:
        """Response cấp/xoay HỘ (admin trên personal): KHÔNG có `api_key`, có `reveal_state` để chủ biết cần vào
        `/me/credential/reveal`."""
        return {"client_id": client["id"], "key_id": cred["key_id"], "api_key": None, "scopes": list(cred["scopes"]),
                "expires_at": cred["expires_at"], "contract_version": client["contract_version"],
                "allowed_platforms": list(client["allowed_platforms"]), "reveal_state": cred["reveal_state"]}
