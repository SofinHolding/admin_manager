"""RS256 + JWKS: phát hành, verify, xoay khoá, và các kiểu tấn công vào chữ ký."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import timedelta

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from identity_service.tokens.keys import KeyStore, generate_signing_key

from conftest import ISSUER, TEST_FERNET_KEY, claims, login, token_for

pytestmark = pytest.mark.needs_pg


def container(client):
    return client.app.state.container


def forge(payload_overrides: dict | None = None, *, key=None, kid=None, alg="RS256", headers=None) -> str:
    """Tạo token giả để thử tấn công. Mặc định ký bằng một khoá RSA KHÁC (kẻ tấn công)."""
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": ["manager-api"], "sub": "a" * 32, "username": "x", "role": "admin",
               "perms": [], "client_type": "web", "sid": "s" * 32, "jti": "j", "iat": now, "nbf": now,
               "exp": now + 600, **(payload_overrides or {})}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid or "kid-gia", **(headers or {})})


async def me(client, token: str):
    return await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})


# ── JWKS ────────────────────────────────────────────────────────────────────────

async def test_jwks_is_public_cacheable_and_has_no_private_material(client):
    r = await client.get("/.well-known/jwks.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    keys = r.json()["keys"]
    assert len(keys) == 1
    k = keys[0]
    assert {"kty", "use", "alg", "n", "e", "kid"} <= set(k) and k["kty"] == "RSA" and k["alg"] == "RS256"
    assert not ({"d", "p", "q", "dp", "dq", "qi"} & set(k)), "JWKS lộ thành phần khoá private"


async def test_issued_token_header_and_claims_match_contract(client, db, mailer):
    t = await token_for(client, db, mailer, username="hop_dong")
    header = jwt.get_unverified_header(t["access"])
    assert header["alg"] == "RS256" and header["typ"] == "at+jwt" and header["kid"]
    c = await claims(client, t["access"])
    assert set(c) == {"iss", "aud", "sub", "username", "role", "perms", "client_type", "sid", "jti", "iat", "nbf", "exp"}
    jwks_kid = (await client.get("/.well-known/jwks.json")).json()["keys"][0]["kid"]
    assert header["kid"] == jwks_kid


async def test_jti_is_unique_per_token(client, db, mailer):
    await token_for(client, db, mailer, username="nhieu_token")
    jtis = set()
    for _ in range(3):
        body = (await login(client, "nhieu_token")).json()
        jtis.add((await claims(client, body["access_token"]))["jti"])
    assert len(jtis) == 3


# ── Khoá private được bảo vệ ────────────────────────────────────────────────────

async def test_private_key_is_stored_encrypted_and_needs_the_external_fernet_key(client, db, mailer):
    await token_for(client, db, mailer, username="khoa_ma_hoa")
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT private_encrypted, public_jwk FROM signing_keys")
    assert "PRIVATE KEY" not in row["private_encrypted"]
    pem = Fernet(TEST_FERNET_KEY.encode()).decrypt(row["private_encrypted"].encode())
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    with pytest.raises(Exception):
        Fernet(Fernet.generate_key()).decrypt(row["private_encrypted"].encode())  # sai khoá ngoài DB -> không giải mã được


async def test_only_one_active_signing_key_is_allowed(client, db, mailer):
    import asyncpg
    await token_for(client, db, mailer, username="mot_khoa")
    extra = generate_signing_key()
    async with db.connection() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("INSERT INTO signing_keys (kid, public_jwk, private_encrypted, status) "
                               "VALUES ($1, $2::jsonb, 'x', 'active')", extra.kid, json.dumps(extra.public_jwk))


async def test_concurrent_first_use_creates_exactly_one_key(db, settings):
    async with db.connection() as conn:
        await conn.execute("TRUNCATE signing_keys")
    stores = [KeyStore(db, settings.key_encryption_key) for _ in range(6)]  # 6 "instance" khởi động cùng lúc
    keys = await asyncio.gather(*[s.active_key() for s in stores])
    assert len({k.kid for k in keys}) == 1
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM signing_keys") == 1


# ── Từ chối token giả / sai ─────────────────────────────────────────────────────

async def test_token_signed_by_attacker_key_with_real_kid_is_rejected(client, db, mailer):
    await token_for(client, db, mailer, username="nan_nhan_kid")
    real_kid = (await client.get("/.well-known/jwks.json")).json()["keys"][0]["kid"]
    r = await me(client, forge(kid=real_kid))
    assert (r.status_code, r.json()["detail"]) == (401, "Token không hợp lệ")


async def test_alg_none_is_rejected(client):
    token = jwt.encode({"sub": "a" * 32, "role": "admin", "iss": ISSUER, "aud": ["manager-api"],
                        "exp": int(time.time()) + 600, "iat": 1, "nbf": 1}, key=None, algorithm="none")
    assert (await me(client, token)).status_code == 401


async def test_hs256_signed_with_the_public_key_is_rejected(client, db, mailer):
    """Tấn công 'nhầm thuật toán': ký HS256 bằng chính khoá công khai (công khai trong JWKS) làm secret."""
    await token_for(client, db, mailer, username="nham_thuat_toan")
    jwk = (await client.get("/.well-known/jwks.json")).json()["keys"][0]
    n = int.from_bytes(base64.urlsafe_b64decode(jwk["n"] + "=" * (-len(jwk["n"]) % 4)), "big")
    e = int.from_bytes(base64.urlsafe_b64decode(jwk["e"] + "=" * (-len(jwk["e"]) % 4)), "big")
    pub_pem = rsa.RSAPublicNumbers(e, n).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    # PyJWT từ chối tự tạo token kiểu này, nên kẻ tấn công phải tự tính HMAC với PEM công khai làm secret.
    import hashlib
    import hmac as hmac_mod

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    now = int(time.time())
    signing_input = (b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": jwk["kid"]}).encode()) + "." +
                     b64(json.dumps({"iss": ISSUER, "aud": ["manager-api"], "sub": "a" * 32, "role": "admin",
                                     "exp": now + 600, "iat": now, "nbf": now}).encode()))
    token = signing_input + "." + b64(hmac_mod.new(pub_pem, signing_input.encode(), hashlib.sha256).digest())
    assert (await me(client, token)).status_code == 401


async def test_hs256_tokens_are_not_accepted_in_rs256_mode(client):
    token = jwt.encode({"sub": "a" * 32, "role": "admin", "exp": int(time.time()) + 600},
                       "bat-ky-secret-nao-do-0123456789abcdef", algorithm="HS256")
    assert (await me(client, token)).status_code == 401


@pytest.mark.parametrize("override,why", [
    ({"aud": ["service-khac"]}, "sai audience"),
    ({"iss": "http://ke-gia-mao.test"}, "sai issuer"),
    ({"exp": 1}, "hết hạn"),
    ({"nbf": 4_000_000_000}, "chưa có hiệu lực (nbf)"),
])
async def test_valid_signature_but_wrong_claims_is_rejected(client, db, mailer, override, why):
    """Chữ ký ĐÚNG (ký bằng khoá thật của service) nhưng claim sai thì vẫn bị từ chối."""
    await token_for(client, db, mailer, username=f"claim_sai_{abs(hash(why)) % 10000}")
    key = await container(client).keystore.active_key()
    token = forge(override, key=serialization.load_pem_private_key(key.private_pem, None), kid=key.kid)
    r = await me(client, token)
    assert r.status_code == 401, why


async def test_missing_required_claims_is_rejected(client, db, mailer):
    await token_for(client, db, mailer, username="thieu_claim")
    key = await container(client).keystore.active_key()
    priv = serialization.load_pem_private_key(key.private_pem, None)
    now = int(time.time())
    token = jwt.encode({"sub": "a" * 32, "role": "admin", "aud": ["manager-api"], "iss": ISSUER, "exp": now + 99},
                       priv, algorithm="RS256", headers={"kid": key.kid})  # thiếu iat, nbf
    assert (await me(client, token)).status_code == 401


async def test_tampered_payload_is_rejected(client, db, mailer):
    t = await token_for(client, db, mailer, username="bi_sua")
    head, payload, sig = t["access"].split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["role"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    assert (await me(client, f"{head}.{forged}.{sig}")).status_code == 401


async def test_unknown_kid_is_rejected_without_crash(client, db, mailer):
    await token_for(client, db, mailer, username="kid_la")
    assert (await me(client, forge(kid="khong-co-trong-jwks"))).status_code == 401


# ── Xoay khoá ───────────────────────────────────────────────────────────────────

async def test_rotation_keeps_old_tokens_valid_during_grace_then_drops_the_old_key(client, db, mailer, settings):
    t = await token_for(client, db, mailer, username="xoay_khoa")
    old_kid = jwt.get_unverified_header(t["access"])["kid"]
    new_key = await container(client).keystore.rotate()
    assert new_key.kid != old_kid
    kids = {k["kid"] for k in (await client.get("/.well-known/jwks.json")).json()["keys"]}
    assert kids == {old_kid, new_key.kid}                       # khoá cũ vẫn publish trong thời gian ân hạn
    assert (await me(client, t["access"])).status_code == 200   # token đã phát vẫn dùng được
    fresh = (await login(client, "xoay_khoa")).json()["access_token"]
    assert jwt.get_unverified_header(fresh)["kid"] == new_key.kid  # token mới ký bằng khoá mới
    # hết thời gian ân hạn: khoá cũ rời JWKS
    async with db.connection() as conn:
        await conn.execute("UPDATE signing_keys SET retired_at = now() - interval '2 hours' WHERE status='retired'")
    kids = {k["kid"] for k in (await client.get("/.well-known/jwks.json")).json()["keys"]}
    assert kids == {new_key.kid}


async def test_rotation_leaves_exactly_one_active_key(client, db, mailer):
    await token_for(client, db, mailer, username="mot_active")
    async with db.connection() as conn:
        before = await conn.fetchval("SELECT count(*) FROM signing_keys")
    await container(client).keystore.rotate()
    await container(client).keystore.rotate()
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM signing_keys WHERE status='active'") == 1
        assert await conn.fetchval("SELECT count(*) FROM signing_keys") == before + 2


# ── Chế độ HS256 tương thích ────────────────────────────────────────────────────

async def test_hs256_compat_mode_issues_legacy_tokens_and_publishes_no_keys(client_hs256, db, mailer):
    t = await token_for(client_hs256, db, mailer, username="che_do_cu")
    assert jwt.get_unverified_header(t["access"])["alg"] == "HS256"
    assert (await client_hs256.get("/.well-known/jwks.json")).json() == {"keys": []}
    assert (await client_hs256.get("/v1/auth/me", headers=t["auth"])).status_code == 200
