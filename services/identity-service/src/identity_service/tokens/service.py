"""Phát hành và verify access token.

RS256 (mặc định): ký bằng khoá active trong DB, header mang `kid`, claims theo docs/api/service-auth-contract-v1.md.
HS256 (chỉ khi IDENTITY_ACCESS_TOKEN_ALG=HS256): chế độ cùng tồn tại với legacy/admin-server/reward legacy, payload giữ nguyên legacy.
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

    async def issue(self, *, account: dict, perms: list[str], sid: str, client_type: str = "web",
                    audiences: list[str] | None = None, extra: dict | None = None,
                    platform_audience: bool = True) -> str:
        """`audiences`/`extra`: dùng cho access token cấp qua OIDC (thêm `aud` của client, `client_id`, `scope`).

        `platform_audience=False`: token CHỈ mang `audiences` (client bên thứ ba) — không có audience nền tảng nên
        không dùng được cho API nền tảng; `audiences` không được chứa audience nền tảng."""
        s = self.settings
        if not self.rs256:
            return legacy_jwt.create_access_token(
                s.jwt_compat_secret, account_id=account["id"], username=account["username"],
                role=account["role"], client_type=client_type, ttl_s=s.access_token_ttl_s)
        key = await self.keystore.active_key()
        now = int(time.time())
        extra_aud = [a for a in (audiences or []) if a and a != s.audience]
        if not platform_audience and not extra_aud:
            raise ValueError("token không mang audience nền tảng thì phải có audience riêng")
        aud = [s.audience, *extra_aud] if platform_audience else extra_aud
        payload = {**(extra or {}), "iss": s.issuer, "aud": aud, "sub": account["id"], "username": account["username"],
                   "role": account["role"], "perms": sorted(perms), "client_type": client_type, "sid": sid,
                   "jti": uuid.uuid4().hex, "iat": now, "nbf": now, "exp": now + s.access_token_ttl_s}
        return jwt.encode(payload, key.private_pem, algorithm="RS256",
                          headers={"kid": key.kid, "typ": "at+jwt"})

    async def sign(self, payload: dict, *, typ: str = "JWT") -> str:
        """Ký JWT RS256 bằng khoá active (id_token, logout_token). Không dùng ở chế độ HS256 tương thích."""
        if not self.rs256:
            raise RuntimeError("OIDC cần IDENTITY_ACCESS_TOKEN_ALG=RS256")
        key = await self.keystore.active_key()
        return jwt.encode(payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid, "typ": typ})

    async def verify_own(self, token: str, *, typ: str = "JWT", audience: str | list[str] | None = None,
                         verify_exp: bool = True) -> dict:
        """Verify JWT do chính Identity ký (id_token_hint...): RS256 + kid + iss khớp chính xác.

        `audience=None` không kiểm `aud` (người gọi tự kiểm). `verify_exp=False` cho `id_token_hint` đã hết hạn
        (OIDC RP-Initiated Logout cho phép)."""
        if not self.rs256:
            raise DomainError(401, "Token không hợp lệ")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None
        if header.get("alg") != "RS256" or not header.get("kid") or str(header.get("typ", "")).lower() != typ.lower():
            raise DomainError(401, "Token không hợp lệ")
        pem = await self._public_pem(header["kid"])
        if pem is None:
            raise DomainError(401, "Token không hợp lệ")
        try:
            return jwt.decode(token, pem, algorithms=["RS256"], issuer=self.settings.issuer, leeway=LEEWAY_S,
                              audience=audience, options={"require": ["iss", "sub", "aud"],
                                                          "verify_exp": verify_exp, "verify_aud": audience is not None,
                                                          "verify_iat": False})
        except jwt.ExpiredSignatureError:
            raise DomainError(401, "Token hết hạn") from None
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None

    async def _public_pem(self, kid: str) -> bytes | None:
        cached = self._pem_cache.get(kid)
        if cached and time.monotonic() - cached[1] < 60:
            return cached[0]
        pem = await self.keystore.public_pem_for(kid)
        if pem:
            self._pem_cache[kid] = (pem, time.monotonic())
        return pem

    async def verify(self, token: str, *, any_audience: bool = False) -> dict:
        """Ném DomainError(401). Chỉ chấp nhận đúng thuật toán đã cấu hình; từ chối `none`, HS* khi ở chế độ RS256.

        Mặc định đòi `aud` chứa audience nền tảng. `any_audience=True` CHỈ cho endpoint OIDC của Identity
        (userinfo/introspect/revoke) kiểm token của client bên thứ ba (aud riêng): chữ ký/iss/exp/typ vẫn kiểm đủ."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None
        alg = header.get("alg")
        if not self.rs256:
            return legacy_jwt.decode_access_token(self.settings.jwt_compat_secret, token)
        if alg != "RS256" or not header.get("kid") or str(header.get("typ", "")).lower() != "at+jwt":
            # typ=at+jwt (RFC 9068): id_token/logout_token cùng khoá ký không được dùng thay access token
            raise DomainError(401, "Token không hợp lệ")
        pem = await self._public_pem(header["kid"])
        if pem is None:
            raise DomainError(401, "Token không hợp lệ")
        try:
            return jwt.decode(token, pem, algorithms=["RS256"],
                              audience=None if any_audience else self.settings.audience,
                              issuer=self.settings.issuer, leeway=LEEWAY_S,
                              options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud"],
                                       "verify_aud": not any_audience})
        except jwt.ExpiredSignatureError:
            raise DomainError(401, "Token hết hạn") from None
        except jwt.InvalidTokenError:
            raise DomainError(401, "Token không hợp lệ") from None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
