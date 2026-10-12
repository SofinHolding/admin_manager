"""Verify access token RS256 OFFLINE bằng JWKS của Identity (docs/api/service-auth-contract-v1.md §2-3).

Hành vi bắt buộc:
  * chỉ RS256 + `kid`; từ chối `none`, HS*, kid lạ; verify `iss` chính xác, `aud`, `exp`, `nbf`, `iat`, `sub` (leeway 30s);
  * cache JWKS trong bộ nhớ (làm mới ≤ refresh_interval, và khi gặp kid lạ nhưng không quá 1 lần/min_refetch_interval);
  * Identity chết: vẫn verify bằng khoá đã cache tới `stale_max`; kid lạ khi không lấy được JWKS → AuthError (không crash).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

from manager_sdk.errors import AuthError
from manager_sdk.principal import Principal

logger = logging.getLogger("manager_sdk.verifier")


def _rsa_public_key(jwk: dict):
    def num(value: str) -> int:
        return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")

    return RSAPublicNumbers(num(jwk["e"]), num(jwk["n"])).public_key()


class TokenVerifier:
    def __init__(self, *, issuer: str, audience: str, jwks_url: str, http_client: httpx.AsyncClient | None = None,
                 refresh_interval: float = 300.0, min_refetch_interval: float = 30.0, stale_max: float = 86400.0,
                 leeway: int = 30, timeout: float = 3.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url
        self._http = http_client
        self._owns_http = http_client is None
        self._refresh_interval = refresh_interval
        self._min_refetch = min_refetch_interval
        self._stale_max = stale_max
        self._leeway = leeway
        self._timeout = timeout
        self._clock = clock
        self._keys: dict[str, object] = {}
        self._fetched_at: float | None = None      # lần lấy JWKS THÀNH CÔNG gần nhất
        self._last_attempt: float | None = None    # lần thử lấy gần nhất (kể cả thất bại)
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    async def prefetch(self, *, retries: int = 3, backoff: float = 1.0) -> None:
        """Warm-up JWKS lúc khởi động service.

        Gọi trong lifespan startup để cache khoá ngay từ đầu — sau khi prefetch thành công,
        token đầu tiên verify offline không cần fetch thêm dù Identity tạm chết.
        Không crash nếu Identity chưa sẵn sàng: chỉ log warning, trả về bình thường;
        service vẫn khởi động được và sẽ tự tải lại khi gặp token đầu tiên.
        Idempotent: gọi lại an toàn.
        """
        for attempt in range(retries):
            async with self._lock:
                await self._fetch()
            if self._keys:
                logger.debug("JWKS prefetch thành công (%d key(s))", len(self._keys))
                return
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
        logger.warning(
            "Prefetch JWKS thất bại sau %d lần thử — sẽ tự tải lại khi nhận token đầu tiên", retries
        )

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def _fetch(self) -> None:
        self._last_attempt = self._clock()
        self.fetch_count += 1
        try:
            response = await self.http.get(self.jwks_url, timeout=self._timeout)
            response.raise_for_status()
            keys = {}
            for jwk in response.json().get("keys", []):
                if jwk.get("kty") == "RSA" and jwk.get("kid"):
                    keys[jwk["kid"]] = _rsa_public_key(jwk)
        except Exception as exc:  # noqa: BLE001 — Identity chết/JWKS hỏng: giữ khoá đã cache, không crash
            logger.warning("Không lấy được JWKS (%s): %s", self.jwks_url, type(exc).__name__)
            return
        self._keys = keys
        self._fetched_at = self._clock()

    async def _key_for(self, kid: str):
        now = self._clock()
        fresh = self._fetched_at is not None and now - self._fetched_at < self._refresh_interval
        if kid in self._keys and fresh:
            return self._keys[kid]
        async with self._lock:
            now = self._clock()
            fresh = self._fetched_at is not None and now - self._fetched_at < self._refresh_interval
            may_try = self._last_attempt is None or now - self._last_attempt >= self._min_refetch
            if (kid not in self._keys or not fresh) and may_try:
                await self._fetch()
            now = self._clock()
            usable = self._fetched_at is not None and now - self._fetched_at < self._stale_max
            if kid in self._keys and usable:
                return self._keys[kid]
        raise AuthError("Token không hợp lệ")

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise AuthError("Token không hợp lệ") from None
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not kid:
            raise AuthError("Token không hợp lệ")
        key = await self._key_for(kid)
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                                leeway=self._leeway,
                                options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud"]})
        except jwt.ExpiredSignatureError:
            raise AuthError("Token hết hạn") from None
        except jwt.InvalidTokenError:
            raise AuthError("Token không hợp lệ") from None
        return Principal.from_claims(claims)
