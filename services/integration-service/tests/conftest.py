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
from integration_service.clients import policy  # noqa: E402
from integration_service.config import Settings  # noqa: E402
from integration_service.db import Database  # noqa: E402
from integration_service.provisioning import handlers  # noqa: E402
from manager_sdk import TokenVerifier  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

PG_REASON = devenv.SKIP_REASON
ISSUER = "http://identity.test"
AUDIENCE = "manager-api"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
_TABLES = ("integration_clients, projects, resources, resource_project_bindings, content_items, daily_metrics, "
           "activity_events, snapshots, snapshot_items, idempotency_keys, audit_log, account_projection, outbox, "
           "feed_cursor, processed_events, dead_letters, jobs, user_channel_groups, legacy_ingest.operation_audit_log, "
           "legacy_ingest.operation_items, legacy_ingest.operation_batches, legacy_ingest.resource_assignments, "
           "legacy_ingest.resource_sources, legacy_ingest.account_links, legacy_ingest.link_codes")
LEGACY_POLICIES = (("facebook", True, True), ("tiktok", True, True), ("youtube", True, True), ("instagram", True, False))
SEED_PLATFORMS = ("facebook", "youtube", "tiktok", "instagram")
SEED_METRICS = ("views", "reactions", "comments", "likes", "shares", "followers", "interactions", "videos_posted")
FEED_KEY_ANALYTICS = secrets.token_urlsafe(32)   # ngẫu nhiên mỗi lần chạy
FEED_KEY_IDENTITY = secrets.token_urlsafe(32)
REVEAL_KEY = Fernet.generate_key().decode()


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
                    maintenance_interval_s=0, provisioning_poll_s=0.0, reveal_key=REVEAL_KEY,
                    credential_reveal_ttl_days=7, public_base_url="http://integration.test")


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
        await conn.execute("DELETE FROM platform_aliases")
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
    """Một Tool đã có credential + đã handshake (bind thiết bị): gọi API máy bằng API key, tự gửi
    `X-Installation-Id` (bắt buộc ngoài /handshake kể từ thiết kế tool-key 2026-10-14)."""
    http: httpx.AsyncClient
    client_id: str
    owner: str
    key_id: str
    api_key: str
    scopes: list[str]
    installation_id: str | None = None
    device_id: str | None = None
    headers: dict = field(default_factory=dict)
    counter: int = 0

    def h(self, **extra) -> dict:
        base = {"Authorization": f"Bearer {self.api_key}"}
        if self.installation_id:
            base["X-Installation-Id"] = self.installation_id
        return {**base, **extra}

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
    app: object = None   # FastAPI app (container cho provisioning trực tiếp trong tool())

    def user(self, sub: str | None = None, role: str = "viewer", **claims) -> dict:
        sub = sub or uuid.uuid4().hex
        # Mặc định có capability:tool_integration (gán lúc đăng ký, không phụ thuộc xác thực email).
        # Dùng env.user(perms=[]) để test trường hợp thiếu capability tường minh.
        claims.setdefault("perms", ["capability:tool_integration"])
        return {"sub": sub, "headers": {"Authorization": f"Bearer {self.identity.token(sub=sub, role=role, **claims)}"}}

    async def projection(self, account_id: str, *, verified: bool = True, status: str = "active") -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO account_projection (account_id, username, status, email_verified) VALUES ($1,'u',$2,$3) "
                "ON CONFLICT (account_id) DO UPDATE SET status = $2, email_verified = $3", account_id, status, verified)

    async def verified_user(self, role: str = "viewer", *, perms: list[str] | None = None) -> dict:
        """Tài khoản đã xác thực email. Mặc định có capability:tool_integration để tạo client/credential.
        Dùng `perms=[]` hoặc perms không có capability:tool_integration để test trường hợp bị từ chối."""
        default_perms = ["capability:tool_integration"]
        u = self.user(role=role, perms=perms if perms is not None else default_perms)
        await self.projection(u["sub"])
        return u

    async def grant_capability(self, account_id: str, *, granted: bool = True) -> None:
        """Mô phỏng `account.permission_changed` của Identity (provisioning THỰC, không tạo client bằng tay)."""
        container = self.app.state.container
        event = {"type": "account.permission_changed",
                 "data": {"account_id": account_id, "permission": "capability:tool_integration", "granted": granted}}
        async with self.db.transaction() as conn:
            await handlers.apply_event(conn, container.clients, container.credentials, event, self.clock())

    async def handshake(self, api_key: str, *, platforms, device_id: str | None = None,
                        capabilities: list[str] | None = None) -> dict:
        device_id = device_id or f"device-{uuid.uuid4().hex[:8]}"
        caps = capabilities if capabilities is not None else ["resources.push", "snapshots.push", "metrics.push",
                                                               "jobs.pull"]
        r = await self.http.post("/v1/integrations/handshake", headers={"Authorization": f"Bearer {api_key}"}, json={
            "installation": {"device_id": device_id, "hostname": "h", "os": "windows", "app_version": "1.0"},
            "platforms": list(platforms), "capabilities": caps})
        assert r.status_code == 200, r.text
        return {**r.json()["data"], "device_id": device_id}

    async def tool(self, *, platforms=("facebook",), kind: str = "primary_tool", scopes: list[str] | None = None,
                   user: dict | None = None, device_id: str | None = None, handshake: bool = True) -> Tool:
        """`kind=primary_tool`: provision qua capability (nguồn DUY NHẤT), chủ tự `reveal`.
        `kind=fallback_tool|service`: wizard admin 3 bước (create -> config -> activate) rồi admin cấp credential."""
        if kind == "primary_tool":
            user = user or await self.verified_user()
            await self.grant_capability(user["sub"])
            r = await self.http.post("/v1/integrations/me/credential/reveal", headers=user["headers"])
            assert r.status_code == 200, r.text
            d = r.json()["data"]
            owner = user["sub"]
        else:
            admin = await self.verified_user(role="admin")
            r = await self.http.post("/v1/integrations/clients", headers=admin["headers"], json={
                "tool_name": "Tool-" + uuid.uuid4().hex[:6], "client_kind": kind,
                "purpose": "test fixture", "platforms": list(platforms)})
            assert r.status_code == 201, r.text
            client_id = r.json()["data"]["client_id"]
            wanted_scopes = scopes if scopes is not None else policy.default_scopes(kind)
            r = await self.http.put(f"/v1/integrations/clients/{client_id}/config", headers=admin["headers"], json={
                "allowed_platforms": list(platforms), "allowed_scopes": wanted_scopes})
            assert r.status_code == 200, r.text
            tool_name = r.json()["data"]["tool_name"]
            r = await self.http.post(f"/v1/integrations/clients/{client_id}/activate", headers=admin["headers"],
                                     json={"confirm_tool_name": tool_name})
            assert r.status_code == 200, r.text
            r = await self.http.post(f"/v1/integrations/clients/{client_id}/credentials", headers=admin["headers"],
                                     json={})
            assert r.status_code == 201, r.text
            d = r.json()["data"]
            owner = admin["sub"]
        installation_id = None
        dev_id = device_id
        if handshake:
            hs = await self.handshake(d["api_key"], platforms=platforms, device_id=device_id)
            installation_id = hs["installation_id"]
            dev_id = hs["device_id"]
        return Tool(http=self.http, client_id=d["client_id"], owner=owner, key_id=d["key_id"], api_key=d["api_key"],
                   scopes=d["scopes"], installation_id=installation_id, device_id=dev_id)

    async def scalar(self, sql: str, *args):
        async with self.db.connection() as conn:
            return await conn.fetchval(sql, *args)

    async def rows(self, sql: str, *args) -> list[dict]:
        async with self.db.connection() as conn:
            return [dict(r) for r in await conn.fetch(sql, *args)]


@pytest_asyncio.fixture
async def env(http, db, identity, clock, app) -> Env:
    return Env(http=http, db=db, identity=identity, clock=clock, app=app)


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
        return Env(http=client, db=db, identity=identity, clock=clock, app=application), application

    yield factory
    for c in opened:
        await c.aclose()


def ts(minutes: int = 0, base: str = "2026-10-05T10:00:00") -> str:
    """Timestamp RFC 3339 UTC, lệch `minutes` so với mốc cố định (để so sánh observed_at cũ/mới)."""
    t = datetime.fromisoformat(base) + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
