"""Trợ giúp cho test OIDC: dựng account/client trực tiếp trong Postgres thật, chạy luồng authorize/token qua HTTP."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import jwt

from identity_service.security import hash_password, hash_token

PASSWORD = "mat-khau-12345"
_PW_HASH: str | None = None
REDIRECT = "https://app.example/cb"
ISSUER = "http://identity.test"


def pw_hash() -> str:
    global _PW_HASH
    if _PW_HASH is None:
        _PW_HASH = hash_password(PASSWORD)  # bcrypt cost 12 thật, tính một lần cho cả phiên test
    return _PW_HASH


async def make_account(db, username: str, *, role: str = "viewer", status: str = "active", verified: bool = True) -> str:
    account_id = uuid.uuid4().hex
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO accounts (id, username, email, password_hash, role, invite_key, email_verified, status) "
            "VALUES ($1,$2,$3,$4,$5,'TEST-KEY',$6,$7)",
            account_id, username, f"{username}@example.com", pw_hash(), role, verified, status)
    return account_id


async def make_client(db, client_id: str = "app1", *, ctype: str = "public", redirect_uris=(REDIRECT,),
                      first_party: bool = True, scopes=("openid", "profile", "email", "offline_access"),
                      audience: str = "app1-api", backchannel: str = "", post_logout=(),
                      secret: str | None = None, status: str = "active") -> str | None:
    """Trả secret rõ (chỉ confidential)."""
    if ctype == "confidential" and secret is None:
        secret = secrets.token_urlsafe(32)
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO oidc_clients (client_id, name, type, secret_hash, redirect_uris, post_logout_redirect_uris, "
            "backchannel_logout_uri, allowed_scopes, audience, first_party, status) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            client_id, f"Ung dung {client_id}", ctype, hash_token(secret) if ctype == "confidential" else "",
            list(redirect_uris), list(post_logout), backchannel, list(scopes), audience, first_party, status)
    return secret


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authz_params(client_id: str = "app1", *, challenge: str | None = None, scope: str = "openid profile email",
                 state: str = "st-123", redirect_uri: str = REDIRECT, nonce: str | None = "nonce-1", **extra) -> dict:
    p = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "state": state}
    if challenge is not None:
        p["code_challenge"] = challenge
        p["code_challenge_method"] = "S256"
    if nonce is not None:
        p["nonce"] = nonce
    p.update(extra)
    return {k: v for k, v in p.items() if v is not None}


async def sso_login(client, username: str, *, password: str = PASSWORD, authz: str = ""):
    """Đăng nhập qua trang hosted thật (CSRF double-submit). Cookie nằm trong jar của `client`."""
    page = await client.get("/oauth/login", params={"authz": authz} if authz else None)
    found = re.search(r'name="csrf" value="([^"]+)"', page.text)
    if not found:  # phiên SSO còn sống: trang login không hiện form nữa
        return page
    csrf = found.group(1)
    return await client.post("/oauth/login", data={"csrf": csrf, "authz": authz, "username": username,
                                                  "password": password})


def query_of(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(location).query, keep_blank_values=True).items()}


async def get_code(client, params: dict, *, consent: str = "allow") -> str:
    r = await client.get("/oauth/authorize", params=params)
    if r.status_code == 200 and 'name="decision"' in r.text:    # client không first-party: màn consent
        from html import unescape
        csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
        authz = unescape(re.search(r'name="authz" value="([^"]+)"', r.text).group(1))
        r = await client.post("/oauth/authorize", data={"csrf": csrf, "authz": authz, "decision": consent})
    assert r.status_code == 302, r.text
    q = query_of(r.headers["location"])
    assert "code" in q, q
    assert q["state"] == params["state"]
    return q["code"]


async def exchange(client, *, code: str, verifier: str | None, client_id: str = "app1", redirect_uri: str = REDIRECT,
                   secret: str | None = None, basic: bool = False, **extra):
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    if verifier is not None:
        data["code_verifier"] = verifier
    data.update(extra)
    headers = {}
    if secret is not None and basic:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    else:
        data["client_id"] = client_id
        if secret is not None:
            data["client_secret"] = secret
    return await client.post("/oauth/token", data=data, headers=headers)


async def full_login(client, db, username: str, *, client_id: str = "app1", scope: str = "openid profile email",
                     secret: str | None = None, nonce: str | None = "nonce-1", redirect_uri: str = REDIRECT) -> dict:
    """SSO login + authorize + token. Trả dict token và các tham số đã dùng."""
    await sso_login(client, username)
    verifier, challenge = pkce()
    params = authz_params(client_id, challenge=challenge, scope=scope, nonce=nonce, redirect_uri=redirect_uri)
    code = await get_code(client, params)
    r = await exchange(client, code=code, verifier=verifier, client_id=client_id, secret=secret,
                       redirect_uri=redirect_uri)
    assert r.status_code == 200, r.text
    return {**r.json(), "verifier": verifier, "code": code, "params": params}


async def jwks_key(client, token: str):
    jwks = (await client.get("/.well-known/jwks.json")).json()["keys"]
    kid = jwt.get_unverified_header(token)["kid"]
    return jwt.algorithms.RSAAlgorithm.from_jwk(next(k for k in jwks if k["kid"] == kid))


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def basic_header(client_id: str, secret: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def form_post(client, path: str, data: dict, headers: dict | None = None):
    return client.post(path, data=data, headers=headers or {})


__all__ = [n for n in dir() if not n.startswith("_")] + ["urlencode"]
