"""Fixture dùng chung. Test cần DB chạy trên Postgres THẬT (user riêng `integration_svc`, DB `integration_db`).

Thiếu hạ tầng DEV thì các test `needs_pg` SKIP có lý do (KHÔNG tính là đã kiểm chứng). Không có secret cố định nào
trong repo: khoá RSA, khoá feed, token đều sinh ngẫu nhiên mỗi lần chạy.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "infra" / "scripts"))
import devenv  # noqa: E402  (đọc secret DEV từ .env.dev; không có secret nào nằm trong repo)

from integration_service import migrate  # noqa: E402
from integration_service.app import create_app  # noqa: E402
from integration_service.config import Settings  # noqa: E402
from integration_service.db import Database  # noqa: E402
from manager_sdk import TokenVerifier  # noqa: E402

PG_REASON = devenv.SKIP_REASON
ISSUER = "http://identity.test"
AUDIENCE = "manager-api"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
_TABLES = ("integration_clients, projects, resources, resource_project_bindings, content_items, daily_metrics, "
           "activity_events, snapshots, snapshot_items, idempotency_keys, audit_log, account_projection, outbox, "
           "feed_cursor, processed_events, dead_letters, jobs, legacy_ingest.operation_audit_log, "
           "legacy_ingest.operation_items, legacy_ingest.operation_batches, legacy_ingest.resource_assignments, "
           "legacy_ingest.resource_sources, legacy_ingest.account_links, legacy_ingest.link_codes")
LEGACY_POLICIES = (("facebook", True, True), ("tiktok", True, True), ("youtube", True, True), ("instagram", True, False))
SEED_PLATFORMS = ("facebook", "youtube", "tiktok", "instagram")
SEED_METRICS = ("views", "reactions", "comments", "likes", "shares", "followers", "interactions", "videos_posted")
FEED_KEY_ANALYTICS = secrets.token_urlsafe(32)   # ngẫu nhiên mỗi lần chạy
FEED_KEY_IDENTITY = secrets.token_urlsafe(32)


def _dsn() -> str | None:
    return devenv.pg_dsn("integration") if devenv.pg_ready() else None


def pytest_collection_modifyitems(config, items):
    if _dsn():
        return
    skip = pytest.mark.skip(reason=PG_REASON)
    for item in items:
        if "needs_pg" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_pg: cần Postgres thật")


# ── Identity giả: khoá RSA thật, token thật, JWKS thật (qua MockTransport) ─────────────

class FakeIdentity:
    def __init__(self, kid: str = "kid-test-1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.up = True

    @staticmethod
    def _b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    def jwks(self) -> dict:
        nums = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
                          "n": self._b64(nums.n), "e": self._b64(nums.e)}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("identity down")
        return httpx.Response(200, json=self.jwks())

    def token(self, *, sub: str | None = None, role: str = "viewer", key=None, kid: str | None = None,
              **over) -> str:
        now = int(time.time())
        payload = {"iss": ISSUER, "aud": [AUDIENCE], "sub": sub or uuid.uuid4().hex, "username": "u", "role": role,
                   "perms": [], "client_type": "web", "sid": "s1", "jti": uuid.uuid4().hex, "iat": now, "nbf": now,
                   "exp": now + 600, **over}
        return jwt.encode(payload, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid, "typ": "at+jwt"})


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


class FakeMono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(scope="session")
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def settings() -> Settings:
    return Settings(port=8431, log_level="INFO", database_url=_dsn() or "postgresql://x:y@127.0.0.1:1/z", env="test",
                    issuer=ISSUER, audience=AUDIENCE, jwks_url=JWKS_URL,
                    feed_keys=(("analytics", FEED_KEY_ANALYTICS), ("identity", FEED_KEY_IDENTITY)),
                    maintenance_interval_s=0, provisioning_poll_s=0.0)


@pytest.fixture(scope="session")
def migrated() -> None:
    dsn = _dsn()
    if not dsn:
        return

    async def _go() -> None:
        import asyncpg
        try:
            await migrate.run(dsn)
        except migrate.MigrationError:
            # DB test dùng riêng: nếu migration đã đổi trong lúc phát triển thì dựng lại từ đầu.
            conn = await asyncpg.connect(dsn)
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            for r in rows:
                await conn.execute(f'DROP TABLE IF EXISTS "{r["tablename"]}" CASCADE')
            await conn.execute("DROP SCHEMA IF EXISTS legacy_ingest CASCADE")
            await conn.execute("DROP SEQUENCE IF EXISTS canonical_revision_seq")
            await conn.close()
            await migrate.run(dsn)

    asyncio.run(_go())


@pytest_asyncio.fixture
async def db(migrated):
    """Database đã migrate, dữ liệu rỗng, registry về đúng seed."""
    database = Database(_dsn(), max_size=8)
    await database.open()
    async with database.connection() as conn:
        await conn.execute(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE")
        await conn.execute("ALTER SEQUENCE canonical_revision_seq RESTART")
        await conn.execute("DELETE FROM platform_registry WHERE key <> ALL($1::text[])", list(SEED_PLATFORMS))
        await conn.execute("DELETE FROM metric_registry WHERE key <> ALL($1::text[])", list(SEED_METRICS))
        await conn.execute("UPDATE platform_registry SET enabled = true")
        await conn.execute("UPDATE metric_registry SET enabled = true")
        await conn.execute("DELETE FROM legacy_ingest.platform_policies")
        await conn.executemany(
            "INSERT INTO legacy_ingest.platform_policies (platform, web_entry_enabled, fallback_enabled) VALUES ($1,$2,$3)",
            LEGACY_POLICIES)
    yield database
    await database.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mono() -> FakeMono:
    return FakeMono()


def build_verifier(identity: FakeIdentity) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_url=JWKS_URL,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(identity.handler)))


@pytest_asyncio.fixture
async def app(db, settings, identity, clock, mono):
    return create_app(settings, database=db, verifier=build_verifier(identity), now=clock, clock=mono)


@pytest_asyncio.fixture
async def http(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://integration.test",
                                 headers={"X-Real-IP": "10.1.0.1"}) as c:
        yield c


# ── Trợ giúp nghiệp vụ ──────────────────────────────────────────────────────────────

@dataclass
class Tool:
    """Một Tool đã có credential: gọi API máy bằng API key (không dùng token user)."""
    http: httpx.AsyncClient
    client_id: str
    owner: str
    key_id: str
    api_key: str
    scopes: list[str]
    headers: dict = field(default_factory=dict)
    counter: int = 0

    def h(self, **extra) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", **extra}

    def idem(self) -> str:
        self.counter += 1
        return f"idem-{uuid.uuid4().hex}"

    async def post(self, path: str, json=None, *, idem: str | bool | None = None, headers: dict | None = None,
                   content: bytes | None = None) -> httpx.Response:
        hdr = self.h(**(headers or {}))
        if idem:
            hdr["Idempotency-Key"] = idem if isinstance(idem, str) else self.idem()
        if content is not None:
            return await self.http.post(path, content=content, headers=hdr)
        return await self.http.post(path, json=json, headers=hdr)

    async def put(self, path: str, json=None, *, idem: str | bool | None = None) -> httpx.Response:
        hdr = self.h()
        if idem:
            hdr["Idempotency-Key"] = idem if isinstance(idem, str) else self.idem()
        return await self.http.put(path, json=json, headers=hdr)

    async def get(self, path: str, **params) -> httpx.Response:
        return await self.http.get(path, headers=self.h(), params=params)


@dataclass
class Env:
    http: httpx.AsyncClient
    db: Database
    identity: FakeIdentity
    clock: FakeClock

    def user(self, sub: str | None = None, role: str = "viewer", **claims) -> dict:
        sub = sub or uuid.uuid4().hex
        return {"sub": sub, "headers": {"Authorization": f"Bearer {self.identity.token(sub=sub, role=role, **claims)}"}}

    async def projection(self, account_id: str, *, verified: bool = True, status: str = "active") -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO account_projection (account_id, username, status, email_verified) VALUES ($1,'u',$2,$3) "
                "ON CONFLICT (account_id) DO UPDATE SET status = $2, email_verified = $3", account_id, status, verified)

    async def verified_user(self, role: str = "viewer") -> dict:
        u = self.user(role=role)
        await self.projection(u["sub"])
        return u

    async def tool(self, *, platforms=("facebook",), kind: str = "primary_tool", scopes: list[str] | None = None,
                   user: dict | None = None) -> Tool:
        user = user or await self.verified_user()
        r = await self.http.post("/v1/integrations/clients", headers=user["headers"], json={
            "tool_name": "Tool-" + uuid.uuid4().hex[:6], "client_kind": kind, "platforms": list(platforms)})
        assert r.status_code == 201, r.text
        client_id = r.json()["data"]["client_id"]
        body = {"scopes": scopes} if scopes is not None else {}
        r = await self.http.post(f"/v1/integrations/clients/{client_id}/credentials", headers=user["headers"], json=body)
        assert r.status_code == 201, r.text
        d = r.json()["data"]
        return Tool(http=self.http, client_id=client_id, owner=user["sub"], key_id=d["key_id"], api_key=d["api_key"],
                    scopes=d["scopes"])

    async def scalar(self, sql: str, *args):
        async with self.db.connection() as conn:
            return await conn.fetchval(sql, *args)

    async def rows(self, sql: str, *args) -> list[dict]:
        async with self.db.connection() as conn:
            return [dict(r) for r in await conn.fetch(sql, *args)]


@pytest_asyncio.fixture
async def env(http, db, identity, clock) -> Env:
    return Env(http=http, db=db, identity=identity, clock=clock)


@pytest_asyncio.fixture
async def make_env(db, settings, identity, clock, mono):
    """Dựng thêm một app với cấu hình khác (vd rate limit thấp). Trả (Env, app)."""
    import dataclasses
    opened: list[httpx.AsyncClient] = []

    async def factory(**overrides):
        application = create_app(dataclasses.replace(settings, **overrides), database=db,
                                 verifier=build_verifier(identity), now=clock, clock=mono)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://integration.test",
                                   headers={"X-Real-IP": "10.2.0.1"})
        opened.append(client)
        return Env(http=client, db=db, identity=identity, clock=clock), application

    yield factory
    for c in opened:
        await c.aclose()


def ts(minutes: int = 0, base: str = "2026-10-05T10:00:00") -> str:
    """Timestamp RFC 3339 UTC, lệch `minutes` so với mốc cố định (để so sánh observed_at cũ/mới)."""
    t = datetime.fromisoformat(base) + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
