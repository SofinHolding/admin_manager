"""Fixture dùng chung. Test cần DB chạy trên Postgres THẬT (user riêng `identity_svc`, DB `identity_db`).

Đặt TEST_PG_HOST (và TEST_PG_PORT) để bật; thiếu thì các test đó SKIP có lý do (KHÔNG tính là đã kiểm chứng).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3] / "infra" / "scripts"))
import devenv  # noqa: E402  (đọc secret DEV từ .env.dev; không có secret nào nằm trong repo)

from identity_service import migrate
from identity_service.app import create_app
from identity_service.config import Settings
from identity_service.db import Database

PG_REASON = devenv.SKIP_REASON
_TABLES = "auth_audit, refresh_tokens, accounts, invite_keys"


def _dsn() -> str | None:
    return devenv.pg_dsn("identity") if devenv.pg_ready() else None


def pytest_collection_modifyitems(config, items):
    if _dsn():
        return
    skip = pytest.mark.skip(reason=PG_REASON)
    for item in items:
        if "needs_pg" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_pg: cần Postgres thật")


TEST_JWT_SECRET = __import__("secrets").token_urlsafe(32)  # ngẫu nhiên mỗi lần chạy


class CapturingMailer:
    """Giữ lại mọi email 'đã gửi' để test đọc token/mã."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []
        self.fail = False

    async def _record(self, kind: str, email: str, secret: str, username: str) -> None:
        if self.fail:
            raise RuntimeError("SMTP hỏng (giả lập)")
        self.sent.append((kind, email, secret, username))

    async def send_verify(self, email, token, username):
        await self._record("verify", email, token, username)

    async def send_reset(self, email, token, username):
        await self._record("reset", email, token, username)

    async def send_change_code(self, email, code, username):
        await self._record("code", email, code, username)

    def last(self, kind: str) -> str:
        return [s for s in self.sent if s[0] == kind][-1][2]


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def settings() -> Settings:
    return Settings(port=8430, log_level="INFO", database_url=_dsn() or "postgresql://x:y@127.0.0.1:1/z",
                    env="test", jwt_compat_secret=TEST_JWT_SECRET,
                    login_fail_delay_s=0.0)


@pytest.fixture(scope="session")
def migrated() -> None:
    dsn = _dsn()
    if not dsn:
        return

    async def _go() -> None:
        try:
            await migrate.run(dsn)
        except migrate.MigrationError:
            # DB test dùng riêng: migration 0001 còn đang được phát triển, dựng lại từ đầu.
            import asyncpg
            conn = await asyncpg.connect(dsn)
            await conn.execute(f"DROP TABLE IF EXISTS {_TABLES}, schema_migrations CASCADE")
            await conn.close()
            await migrate.run(dsn)

    asyncio.run(_go())


@pytest_asyncio.fixture
async def db(migrated):
    """Database đã migrate và rỗng cho mỗi test."""
    database = Database(_dsn(), max_size=5)
    await database.open()
    async with database.connection() as conn:
        await conn.execute(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE")
    yield database
    await database.close()


@pytest.fixture
def mailer() -> CapturingMailer:
    return CapturingMailer()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest_asyncio.fixture
async def client(db, settings, mailer, clock, sleeps):
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    app = create_app(settings, database=db, mailer=mailer, now=clock, sleep=fake_sleep)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://identity.test",
                                 headers={"X-Real-IP": "10.0.0." + str(uuid.uuid4().int % 250)}) as c:
        c.app = app
        yield c


# ── Trợ giúp nghiệp vụ ──────────────────────────────────────────────────────────

async def make_key(db: Database, *, role: str = "viewer", max_uses: int = 5, key: str | None = None,
                   expires_at: datetime | None = None, status: str = "active",
                   clock: FakeClock | None = None) -> str:
    key = key or f"{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
    now = clock() if clock else datetime.now(timezone.utc)
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO invite_keys (key, role, max_uses, created_at, expires_at, status) "
            "VALUES ($1, $2, $3, $4, $5, $6)", key, role, max_uses, now, expires_at, status)
    return key


async def register(client, db, *, username: str, email: str | None = None, password: str = "mat-khau-12345",
                   role: str = "viewer", key: str | None = None, clock=None):
    key = key or await make_key(db, role=role, clock=clock)
    # Mỗi lần gọi một IP riêng để helper không vô tình chạm giới hạn 3 lần/giờ/IP (test giới hạn gọi thẳng client).
    return await client.post("/v1/auth/register", headers={"X-Real-IP": f"172.16.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"},
                             json={"key": key, "username": username, "password": password,
                                   "email": email or f"{username}@example.com"})


async def register_verified(client, db, mailer, *, username: str, role: str = "viewer",
                            password: str = "mat-khau-12345", clock=None) -> dict:
    r = await register(client, db, username=username, role=role, password=password, clock=clock)
    assert r.status_code == 200, r.text
    v = await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    assert v.status_code == 200, v.text
    return {"username": username, "password": password}


async def login(client, username: str, password: str = "mat-khau-12345", **headers) -> httpx.Response:
    return await client.post("/v1/auth/login", json={"username": username, "password": password},
                             headers=headers)


async def token_for(client, db, mailer, *, username: str, role: str = "viewer", clock=None) -> dict:
    await register_verified(client, db, mailer, username=username, role=role, clock=clock)
    body = (await login(client, username)).json()
    return {"access": body["access_token"], "refresh": body["refresh_token"], "id": body["user"]["id"],
            "auth": {"Authorization": f"Bearer {body['access_token']}"}}
