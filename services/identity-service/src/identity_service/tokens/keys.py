"""Khoá ký RS256: sinh, mã hoá lưu DB, xoay khoá, JWKS.

Khoá private chỉ nằm trong DB dạng mã hoá Fernet (khoá giải mã nằm ngoài DB, trong IDENTITY_KEY_ENCRYPTION_KEY).
Dump DB không đủ để ký token. Service khác chỉ thấy JWKS công khai.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_LOCK = 7_340_003  # advisory lock riêng cho việc tạo/xoay khoá
RSA_BITS = 2048


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64u(n: int) -> str:
    return _b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_pem: bytes
    public_jwk: dict


def generate_signing_key() -> SigningKey:
    private = rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)
    numbers = private.public_key().public_numbers()
    jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "n": _int_b64u(numbers.n), "e": _int_b64u(numbers.e)}
    # kid = thumbprint (RFC 7638) rút gọn: ổn định theo nội dung khoá
    canonical = json.dumps({"e": jwk["e"], "kty": "RSA", "n": jwk["n"]}, separators=(",", ":"), sort_keys=True)
    kid = _b64u(hashlib.sha256(canonical.encode()).digest())[:16]
    jwk["kid"] = kid
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return SigningKey(kid=kid, private_pem=pem, public_jwk=jwk)


class KeyStore:
    """Lấy khoá active (tự tạo lần đầu), xoay khoá, dựng JWKS. Cache ngắn trong bộ nhớ."""

    def __init__(self, db, encryption_key: str, *, grace_seconds: int = 3600, cache_ttl: float = 30.0,
                 now=lambda: datetime.now(timezone.utc)) -> None:
        self._db = db
        self._fernet = Fernet(encryption_key.encode())
        self._grace = grace_seconds
        self._ttl = cache_ttl
        self._now = now
        self._active: SigningKey | None = None
        self._loaded_at = 0.0

    def _decrypt(self, token: str) -> bytes:
        return self._fernet.decrypt(token.encode())

    async def _load_active(self, conn: asyncpg.Connection) -> SigningKey | None:
        row = await conn.fetchrow("SELECT kid, public_jwk, private_encrypted FROM signing_keys WHERE status='active'")
        if not row:
            return None
        jwk = row["public_jwk"] if isinstance(row["public_jwk"], dict) else json.loads(row["public_jwk"])
        return SigningKey(kid=row["kid"], private_pem=self._decrypt(row["private_encrypted"]), public_jwk=jwk)

    async def active_key(self) -> SigningKey:
        if self._active and time.monotonic() - self._loaded_at < self._ttl:
            return self._active
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", KEY_LOCK)
            key = await self._load_active(conn)
            if key is None:  # lần đầu: tạo khoá, an toàn khi nhiều instance khởi động cùng lúc nhờ advisory lock
                key = await self._insert(conn, generate_signing_key())
        self._active, self._loaded_at = key, time.monotonic()
        return key

    async def _insert(self, conn: asyncpg.Connection, key: SigningKey) -> SigningKey:
        await conn.execute(
            "INSERT INTO signing_keys (kid, public_jwk, private_encrypted, status) VALUES ($1, $2::jsonb, $3, 'active')",
            key.kid, json.dumps(key.public_jwk), self._fernet.encrypt(key.private_pem).decode())
        return key

    async def rotate(self) -> SigningKey:
        """Khoá mới thành active; khoá cũ thành retired nhưng vẫn publish trong JWKS thêm `grace_seconds`."""
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", KEY_LOCK)
            await conn.execute("UPDATE signing_keys SET status='retired', retired_at=$1 WHERE status='active'",
                               self._now())
            key = await self._insert(conn, generate_signing_key())
        self._active, self._loaded_at = key, time.monotonic()
        return key

    async def jwks(self) -> dict:
        await self.active_key()
        cutoff = self._now() - timedelta(seconds=self._grace)
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                "SELECT public_jwk FROM signing_keys WHERE status='active' OR retired_at > $1 "
                "ORDER BY created_at DESC", cutoff)
        return {"keys": [r["public_jwk"] if isinstance(r["public_jwk"], dict) else json.loads(r["public_jwk"])
                         for r in rows]}

    async def public_pem_for(self, kid: str) -> bytes | None:
        """Khoá công khai (PEM) của một kid còn trong JWKS — dùng để verify token do chính Identity cấp."""
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

        jwks = await self.jwks()
        for jwk in jwks["keys"]:
            if jwk["kid"] == kid:
                def num(v: str) -> int:
                    return int.from_bytes(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)), "big")
                pub = RSAPublicNumbers(num(jwk["e"]), num(jwk["n"])).public_key()
                return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        return None


def main(argv: list[str] | None = None) -> int:
    """`python -m identity_service.keys list|rotate` — quản trị khoá ký (job riêng, không qua HTTP)."""
    import asyncio
    import sys

    from identity_service.config import ConfigError, load_settings, require_serving_config
    from identity_service.db import Database

    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ("list", "rotate"):
        print("Dùng: python -m identity_service.keys list|rotate", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
        require_serving_config(settings)
    except ConfigError as exc:
        print(f"Cấu hình sai: {exc}", file=sys.stderr)
        return 2

    async def run() -> int:
        db = Database(settings.database_url)
        store = KeyStore(db, settings.key_encryption_key, grace_seconds=settings.jwks_grace_seconds)
        try:
            if args[0] == "rotate":
                key = await store.rotate()
                print(f"Đã xoay khoá. Khoá active mới: {key.kid}")
            else:
                for jwk in (await store.jwks())["keys"]:
                    print(jwk["kid"])
        finally:
            await db.close()
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    import sys
    sys.exit(main())
