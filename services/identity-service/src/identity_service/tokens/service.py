"""Phát hành và verify access token.

RS256 (mặc định): ký bằng khoá active trong DB, header mang `kid`, claims theo docs/api/service-auth-contract-v1.md.
HS256 (chỉ khi IDENTITY_ACCESS_TOKEN_ALG=HS256): chế độ cùng tồn tại với admin-server/reward legacy, payload giữ nguyên legacy.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import jwt

from identity_service.auth import legacy_jwt
from identity_service.config import Settings
from identity_service.errors import DomainError
from identity_service.tokens.keys import KeyStore

LEEWAY_S = 30


class TokenService:
    def __init__(self, settings: Settings, keystore: KeyStore | None) -> None:
        self.settings = settings
        self.keystore = keystore
        self._pem_cache: dict[str, tuple[bytes, float]] = {}

    @property
    def rs256(self) -> bool:
        return self.settings.access_token_alg == "RS256"

    async def issue(self, *, account: dict, perms: list[str], sid: str, client_type: str = "web") -> str:
        s = self.settings
        if not self.rs256:
            return legacy_jwt.create_access_token(
                s.jwt_compat_secret, account_id=account["id"], username=account["username"],
                role=account["role"], client_type=client_type, ttl_s=s.access_token_ttl_s)
        key = await self.keystore.active_key()
        now = int(time.time())
        payload = {"iss": s.issuer, "aud": [s.audience], "sub": account["id"], "username": account["username"],
                   "role": account["role"], "perms": sorted(perms), "client_type": client_type, "sid": sid,
                   "jti": uuid.uuid4().hex, "iat": now, "nbf": now, "exp": now + s.access_token_ttl_s}
        return jwt.encode(payload, key.private_pem, algorithm="RS256",
                          headers={"kid": key.kid, "typ": "at+jwt"})

    async def _public_pem(self, kid: str) -> bytes | None:
        cached = self._pem_cache.get(kid)
        if cached and time.monotonic() - cached[1] < 60:
            return cached[0]
        pem = await self.keystore.public_pem_for(kid)
        if pem:
            self._pem_cache[kid] = (pem, time.monotonic())
        return pem

    async def verify(self, token: str) -> dict:
        """Ném DomainError(401). Chỉ chấp nhận đúng thuật toán đã cấu hình; từ chối `none`, HS* khi ở chế độ RS256."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None
        alg = header.get("alg")
        if not self.rs256:
            return legacy_jwt.decode_access_token(self.settings.jwt_compat_secret, token)
        if alg != "RS256" or not header.get("kid"):
            raise DomainError(401, "Token không hợp lệ")
        pem = await self._public_pem(header["kid"])
        if pem is None:
            raise DomainError(401, "Token không hợp lệ")
        try:
            return jwt.decode(token, pem, algorithms=["RS256"], audience=self.settings.audience,
                              issuer=self.settings.issuer, leeway=LEEWAY_S,
                              options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud"]})
        except jwt.ExpiredSignatureError:
            raise DomainError(401, "Token hết hạn") from None
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
