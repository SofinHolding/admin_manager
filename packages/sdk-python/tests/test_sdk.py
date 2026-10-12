"""SDK xác thực: verify offline, tấn công chữ ký, JWKS cache/xoay khoá/Identity chết, service key, feed client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI

from manager_sdk import (AuthError, FeedClient, FeedError, Principal, ServiceKeyAuth, TokenVerifier, backoff_delays,
                         install_auth_error_handler, make_dependencies)

ISS, AUD = "http://identity.test", "manager-api"


class Issuer:
    """Một 'Identity' giả trong test: sinh khoá RSA thật, ký token thật, phục vụ JWKS qua MockTransport."""

    def __init__(self, kid="kid-1"):
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.up = True
        self.hits = 0
        self.extra: dict[str, rsa.RSAPrivateKey] = {}

    @staticmethod
    def _b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    def jwk(self, kid, key):
        nums = key.public_key().public_numbers()
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": self._b64(nums.n), "e": self._b64(nums.e)}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hits += 1
        if not self.up:
            raise httpx.ConnectError("identity down")
        keys = [self.jwk(self.kid, self.key)] + [self.jwk(k, v) for k, v in self.extra.items()]
        return httpx.Response(200, json={"keys": keys})

    def token(self, *, key=None, kid=None, **over) -> str:
        now = int(time.time())
        payload = {"iss": ISS, "aud": [AUD], "sub": "a" * 32, "username": "u", "role": "viewer",
                   "perms": ["reward:access"], "client_type": "web", "sid": "s1", "jti": "j", "iat": now, "nbf": now,
                   "exp": now + 600, **over}
        return jwt.encode(payload, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def issuer():
    return Issuer()


@pytest.fixture
def clock():
    return Clock()


def verifier_for(issuer, clock, **kw) -> TokenVerifier:
    client = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    return TokenVerifier(issuer=ISS, audience=AUD, jwks_url=f"{ISS}/.well-known/jwks.json", http_client=client,
                         clock=clock, **kw)


# ── Verify ──────────────────────────────────────────────────────────────────────

async def test_valid_token_yields_principal(issuer, clock):
    p = await verifier_for(issuer, clock).verify(issuer.token())
    assert isinstance(p, Principal) and p.sub == "a" * 32 and p.role == "viewer"
    assert p.perms == frozenset({"reward:access"}) and p.sid == "s1" and p.client_type == "web"
    assert p.has_perm("reward:access") and not p.has_perm("reward:admin") and not p.is_admin


async def test_admin_role_has_every_permission_and_dash_admin_is_admin_but_not_superuser(issuer, clock):
    v = verifier_for(issuer, clock)
    admin = await v.verify(issuer.token(role="admin", perms=[]))
    dash = await v.verify(issuer.token(role="dash_admin", perms=[]))
    assert admin.has_perm("reward:admin") and admin.is_admin
    assert dash.is_admin and not dash.has_perm("reward:admin")


@pytest.mark.parametrize("over,why", [
    ({"aud": ["khac"]}, "audience"), ({"iss": "http://gia-mao.test"}, "issuer"), ({"exp": 1}, "hết hạn"),
    ({"nbf": 4_000_000_000}, "nbf tương lai"),
])
async def test_wrong_claims_are_rejected(issuer, clock, over, why):
    with pytest.raises(AuthError):
        await verifier_for(issuer, clock).verify(issuer.token(**over))


async def test_expired_has_distinct_message_and_leeway_is_applied(issuer, clock):
    v = verifier_for(issuer, clock)
    with pytest.raises(AuthError, match="hết hạn"):
        await v.verify(issuer.token(exp=int(time.time()) - 120))
    assert (await v.verify(issuer.token(exp=int(time.time()) - 10))).sub  # trong leeway 30s


async def test_missing_required_claim_is_rejected(issuer, clock):
    now = int(time.time())
    token = jwt.encode({"iss": ISS, "aud": [AUD], "sub": "x", "exp": now + 60}, issuer.key, algorithm="RS256",
                       headers={"kid": issuer.kid})  # thiếu iat/nbf
    with pytest.raises(AuthError):
        await verifier_for(issuer, clock).verify(token)


async def test_attacker_signed_token_with_real_kid_is_rejected(issuer, clock):
    evil = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        await verifier_for(issuer, clock).verify(issuer.token(key=evil))


async def test_alg_none_and_hs256_confusion_are_rejected(issuer, clock):
    v = verifier_for(issuer, clock)
    none_tok = jwt.encode({"sub": "x", "exp": int(time.time()) + 60}, key=None, algorithm="none")
    with pytest.raises(AuthError):
        await v.verify(none_tok)
    pub_pem = issuer.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    now = int(time.time())
    signing_input = (b64(json.dumps({"alg": "HS256", "kid": issuer.kid}).encode()) + "." +
                     b64(json.dumps({"iss": ISS, "aud": [AUD], "sub": "x", "exp": now + 60, "iat": now,
                                     "nbf": now}).encode()))
    forged = signing_input + "." + b64(hmac.new(pub_pem, signing_input.encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError):
        await v.verify(forged)


@pytest.mark.parametrize("garbage", ["", "rac", "a.b.c", "Bearer x"])
async def test_garbage_tokens_are_rejected(issuer, clock, garbage):
    with pytest.raises(AuthError):
        await verifier_for(issuer, clock).verify(garbage)


# ── JWKS: cache, xoay khoá, Identity chết ───────────────────────────────────────

async def test_jwks_is_cached_between_verifications(issuer, clock):
    v = verifier_for(issuer, clock)
    for _ in range(5):
        await v.verify(issuer.token())
    assert issuer.hits == 1


async def test_jwks_refreshes_after_interval(issuer, clock):
    v = verifier_for(issuer, clock, refresh_interval=300)
    await v.verify(issuer.token())
    clock.t += 301
    await v.verify(issuer.token())
    assert issuer.hits == 2


async def test_rotation_new_kid_triggers_refetch(issuer, clock):
    v = verifier_for(issuer, clock, min_refetch_interval=30)
    await v.verify(issuer.token())
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer.extra["kid-2"] = new_key
    clock.t += 31
    assert (await v.verify(issuer.token(key=new_key, kid="kid-2"))).sub
    assert issuer.hits == 2


async def test_unknown_kid_cannot_hammer_the_identity_service(issuer, clock):
    v = verifier_for(issuer, clock, min_refetch_interval=30)
    await v.verify(issuer.token())
    base = issuer.hits
    for _ in range(20):
        with pytest.raises(AuthError):
            await v.verify(issuer.token(kid="kid-la"))
    assert issuer.hits - base <= 1  # chặn kiểu tấn công làm cạn Identity bằng kid ngẫu nhiên


async def test_identity_down_cached_keys_keep_verifying_tokens(issuer, clock):
    v = verifier_for(issuer, clock, refresh_interval=300, min_refetch_interval=1)
    await v.verify(issuer.token())
    issuer.up = False
    clock.t += 3600   # quá hạn làm mới, Identity chết
    assert (await v.verify(issuer.token())).sub          # vẫn verify offline bằng khoá cache
    assert (await v.verify(issuer.token())).sub


async def test_identity_down_unknown_kid_is_rejected_not_crash(issuer, clock):
    v = verifier_for(issuer, clock, min_refetch_interval=1)
    await v.verify(issuer.token())
    issuer.up = False
    clock.t += 5
    with pytest.raises(AuthError):
        await v.verify(issuer.token(kid="kid-moi-chua-thay"))
    assert (await v.verify(issuer.token())).sub          # khoá cũ vẫn dùng được


async def test_stale_cache_expires_after_stale_max(issuer, clock):
    v = verifier_for(issuer, clock, refresh_interval=300, min_refetch_interval=1, stale_max=86400)
    await v.verify(issuer.token())
    issuer.up = False
    clock.t += 86400 + 10
    with pytest.raises(AuthError):
        await v.verify(issuer.token())


async def test_identity_never_reachable_at_startup_rejects_without_crash(issuer, clock):
    issuer.up = False
    with pytest.raises(AuthError):
        await verifier_for(issuer, clock).verify(issuer.token())


async def test_recovers_when_identity_comes_back(issuer, clock):
    issuer.up = False
    v = verifier_for(issuer, clock, min_refetch_interval=1)
    with pytest.raises(AuthError):
        await v.verify(issuer.token())
    issuer.up = True
    clock.t += 5
    assert (await v.verify(issuer.token())).sub


# ── prefetch ──────────────────────────────────────────────────────────────────

async def test_prefetch_loads_jwks_when_identity_is_up(issuer, clock):
    v = verifier_for(issuer, clock)
    hits_before = issuer.hits
    await v.prefetch()
    assert issuer.hits > hits_before       # đã gọi JWKS endpoint
    assert v._keys                         # khoá đã được cache
    assert v._fetched_at is not None
    # token verify được ngay, không cần fetch thêm
    hits_after = issuer.hits
    await v.verify(issuer.token())
    assert issuer.hits == hits_after       # không gọi thêm lần nào (dùng cache)


async def test_prefetch_does_not_crash_when_identity_is_down(issuer, clock):
    issuer.up = False
    v = verifier_for(issuer, clock)
    await v.prefetch(retries=2, backoff=0.0)   # không chờ, phải xong nhanh
    assert not v._keys                          # thất bại nhưng KHÔNG crash
    # token đầu tiên vẫn ném AuthError (không có khoá) — không crash service
    with pytest.raises(AuthError):
        await v.verify(issuer.token())


async def test_prefetch_retries_until_identity_available(issuer, clock):
    """prefetch với retry=1: Identity chết → fail; Identity sống → thành công."""
    issuer.up = False
    v = verifier_for(issuer, clock, min_refetch_interval=0)
    await v.prefetch(retries=1, backoff=0.0)   # 1 lần thất bại
    assert not v._keys
    issuer.up = True
    # lần prefetch thứ hai (gọi thủ công): Identity đã sống → load được
    await v.prefetch(retries=1, backoff=0.0)
    assert v._keys
    assert (await v.verify(issuer.token())).sub


async def test_prefetch_then_identity_down_tokens_still_verify_offline(issuer, clock):
    """Sau khi prefetch thành công, Identity chết → JWKS cache còn hạn → token vẫn verify."""
    v = verifier_for(issuer, clock, refresh_interval=300, stale_max=86400)
    await v.prefetch()
    issuer.up = False
    clock.t += 60   # JWKS vẫn fresh (300s)
    assert (await v.verify(issuer.token())).sub   # verify offline, không cần Identity


# ── FastAPI dependencies ────────────────────────────────────────────────────────

def make_app(issuer, clock, style="envelope") -> FastAPI:
    v = verifier_for(issuer, clock)
    deps = make_dependencies(v)
    app = FastAPI()
    install_auth_error_handler(app, style)

    @app.get("/me")
    async def me(p: Principal = Depends(deps.require_user)):
        return {"sub": p.sub}

    @app.get("/admin")
    async def admin(p: Principal = Depends(deps.require_admin)):
        return {"ok": True}

    @app.get("/reward")
    async def reward(p: Principal = Depends(deps.require_perm("reward:access"))):
        return {"ok": True}

    @app.get("/roles")
    async def roles(p: Principal = Depends(deps.require_role("dash_admin"))):
        return {"ok": True}

    return app


async def call(app, path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, headers=headers)


async def test_dependencies_enforce_auth_role_and_permission(issuer, clock):
    app = make_app(issuer, clock)
    assert (await call(app, "/me")).status_code == 401
    assert (await call(app, "/me", "rac")).status_code == 401
    assert (await call(app, "/me", issuer.token())).status_code == 200
    assert (await call(app, "/admin", issuer.token())).status_code == 403
    assert (await call(app, "/admin", issuer.token(role="dash_admin"))).status_code == 200
    assert (await call(app, "/reward", issuer.token())).status_code == 200
    assert (await call(app, "/reward", issuer.token(perms=[]))).status_code == 403
    assert (await call(app, "/reward", issuer.token(perms=[], role="admin"))).status_code == 200
    assert (await call(app, "/roles", issuer.token(role="admin"))).status_code == 403  # đúng role, không phải 'có mọi quyền'


async def test_error_styles(issuer, clock):
    r = await call(make_app(issuer, clock, "legacy"), "/me")
    assert r.status_code == 401 and r.json() == {"detail": "Thiếu token"}
    r = await call(make_app(issuer, clock, "envelope"), "/admin", issuer.token())
    body = r.json()
    assert r.status_code == 403 and body["ok"] is False and body["error"]["code"] == "SCOPE_DENIED"


# ── Service key ─────────────────────────────────────────────────────────────────

def test_service_key_matches_the_right_reader_only():
    auth = ServiceKeyAuth({"analytics": "k-analytics-0123456789abcdef", "reward": "k-reward-0123456789abcdefgh"})
    assert auth.match("k-analytics-0123456789abcdef") == "analytics"
    assert auth.match("k-reward-0123456789abcdefgh") == "reward"
    assert auth.match("k-analytics-0123456789abcdeX") is None and auth.match("") is None


async def test_service_key_dependency_rejects_missing_wrong_and_unconfigured():
    app = FastAPI()
    install_auth_error_handler(app, "legacy")
    guard = ServiceKeyAuth({"analytics": "k-analytics-0123456789abcdef"})
    unconfigured = ServiceKeyAuth({})

    @app.get("/ok")
    async def ok(reader: str = Depends(guard)):
        return {"reader": reader}

    @app.get("/none")
    async def none(reader: str = Depends(unconfigured)):
        return {"reader": reader}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/ok")).status_code == 401
        assert (await c.get("/ok", headers={"X-Service-Key": "sai"})).status_code == 401
        assert (await c.get("/ok", headers={"X-Service-Key": "k-analytics-0123456789abcdef"})).json() == {"reader": "analytics"}
        assert (await c.get("/none", headers={"X-Service-Key": "bat-ky"})).status_code == 401  # chưa cấu hình ⇒ từ chối hết


# ── Feed client ─────────────────────────────────────────────────────────────────

class Source:
    def __init__(self):
        self.calls = 0
        self.script: list = []
        self.seen_keys: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.seen_keys.append(request.headers.get("x-service-key", ""))
        item = self.script.pop(0) if self.script else httpx.Response(200, json={"events": [], "next_cursor": 0, "has_more": False, "head": 0})
        if isinstance(item, Exception):
            raise item
        return item


def feed_client(src, **kw):
    async def no_sleep(_):
        return None

    return FeedClient("http://src.test", "khoa-bi-mat-0123456789abcdef",
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(src.handler)), sleep=no_sleep, **kw)


async def test_feed_client_sends_key_and_parses_response():
    src = Source()
    src.script = [httpx.Response(200, json={"events": [{"seq": 1}], "next_cursor": 1, "has_more": False, "head": 1})]
    out = await feed_client(src).feed(after=0, limit=10, types=["a.b"])
    assert out["next_cursor"] == 1 and src.seen_keys == ["khoa-bi-mat-0123456789abcdef"]


async def test_feed_client_retries_transient_errors_then_succeeds():
    src = Source()
    src.script = [httpx.ConnectError("x"), httpx.Response(503), httpx.Response(200, json={"events": [], "next_cursor": 0, "has_more": False, "head": 0})]
    assert (await feed_client(src, retries=3).feed())["has_more"] is False
    assert src.calls == 3


async def test_feed_client_gives_up_with_feed_error_and_never_leaks_the_key():
    src = Source()
    src.script = [httpx.ConnectError("khoa-bi-mat-0123456789abcdef")] * 10
    with pytest.raises(FeedError) as exc:
        await feed_client(src, retries=2).feed()
    assert src.calls == 3 and "khoa-bi-mat" not in str(exc.value)


async def test_feed_client_does_not_retry_auth_failures():
    src = Source()
    src.script = [httpx.Response(401)]
    with pytest.raises(FeedError, match="từ chối khoá"):
        await feed_client(src, retries=3).feed()
    assert src.calls == 1


def test_backoff_is_exponential_capped_and_jittered():
    delays = list(backoff_delays(6, base=1.0, cap=10.0, jitter=0.0))
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    jittered = list(backoff_delays(4, base=1.0, cap=100.0, jitter=0.2))
    assert all(0.8 * 2 ** i <= d <= 1.2 * 2 ** i for i, d in enumerate(jittered))
