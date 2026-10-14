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
# Thứ tự xóa phải tôn trọng FK: bảng con trước, cha sau.
# Không dùng TRUNCATE (AccessExclusiveLock) vì xung đột với dev-stack chạy song song cùng identity_db
# (backchannel giữ RowShareLock trên oidc_cursor qua SELECT FOR UPDATE → deadlock).
# DELETE dùng RowExclusiveLock, tương thích với RowShareLock của dev-stack.
_LEAF_TABLES = (
    # Bảng không có FK con — xóa trực tiếp
    "outbox", "auth_audit", "invite_keys",
    "oidc_cursor", "feed_cursor", "processed_events", "dead_letters",
    # OIDC child tables — phụ thuộc vào oidc_clients hoặc sessions, xóa trước cascade roots
    "oidc_backchannel_deliveries", "oidc_session_clients",
    "oidc_auth_codes", "oidc_refresh_tokens", "sso_sessions",
)
# accounts cascade → sessions, refresh_tokens, account_permissions, integration_provisioning
# oidc_clients cascade → oidc_auth_codes, oidc_refresh_tokens, sso_sessions, oidc_backchannel_deliveries
_CASCADE_ROOTS = ("accounts", "oidc_clients")
# Danh sách bằng phẳng dùng cho DROP TABLE trong migrated (recovery một lần khi migration lỗi).
_TABLES = "outbox, auth_audit, account_permissions, sessions, refresh_tokens, accounts, invite_keys, oidc_clients, oidc_cursor, feed_cursor, processed_events, dead_letters"


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
TEST_FERNET_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
ISSUER = "http://identity.test"


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
        # Xa đủ vào tương lai để dev-stack đang chạy (real time ~2026) không pick up
        # test deliveries qua next_attempt_at — vì dev-stack dùng real clock còn test dùng clock giả.
        self.t = datetime(2077, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def settings() -> Settings:
    """Cấu hình mặc định: RS256 + JWKS (không có secret chung).

    backchannel_enabled=False: tắt background task để tránh race condition giữa dev-stack
    (đang chạy cùng identity_db) và test — test_feed_consumer dùng backchannel_enabled=True tường minh.

    key_encryption_key: dùng IDENTITY_KEY_ENCRYPTION_KEY từ .env.dev (nếu có) để tránh
    InvalidToken khi dev stack đang chạy cùng identity_db và tạo lại signing keys.
    Fallback về TEST_FERNET_KEY khi không có (CI / chạy độc lập).
    """
    kek = os.environ.get("IDENTITY_KEY_ENCRYPTION_KEY") or TEST_FERNET_KEY
    return Settings(port=8430, log_level="INFO", database_url=_dsn() or "postgresql://x:y@127.0.0.1:1/z",
                    env="test", login_fail_delay_s=0.0, issuer=ISSUER, audience="manager-api",
                    key_encryption_key=kek, backchannel_enabled=False,
                    feed_keys=(("analytics", "feed-key-analytics-0123456789abcdef"),
                               ("integration", "feed-key-integration-0123456789abc")))


@pytest.fixture
def settings_hs256(settings) -> Settings:
    """Chế độ cùng tồn tại với legacy/admin-server/reward legacy: HS256 dùng chung secret."""
    from dataclasses import replace
    return replace(settings, access_token_alg="HS256", jwt_compat_secret=TEST_JWT_SECRET)


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

    async def _reset_keys() -> None:
        import asyncpg
        conn = await asyncpg.connect(dsn)
        await conn.execute("TRUNCATE signing_keys")  # khoá của lần chạy trước mã hoá bằng Fernet key khác
        await conn.close()

    asyncio.run(_reset_keys())


@pytest_asyncio.fixture
async def db(migrated):
    """Database đã migrate và rỗng cho mỗi test."""
    database = Database(_dsn(), max_size=5)
    await database.open()
    async with database.connection() as conn:
        # Xóa theo thứ tự FK: leaf tables trước (RowExclusiveLock, tương thích với dev-stack),
        # sau đó cascade roots (accounts / oidc_clients tự cascade xuống con).
        for t in _LEAF_TABLES:
            await conn.execute(f"DELETE FROM {t}")
        for t in _CASCADE_ROOTS:
            await conn.execute(f"DELETE FROM {t}")
        # Reset sequence của 2 bảng có bigserial (outbox.seq, auth_audit.id).
        for tbl, col in (("outbox", "seq"), ("auth_audit", "id")):
            await conn.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), 1, false)")
        # Catalog permission không bị TRUNCATE: đưa về đúng trạng thái seed sau mỗi test.
        # Import từ capabilities.py để conftest tự động cập nhật khi thêm capability mới.
        from identity_service.permissions.capabilities import ALL_CAPABILITIES_DESC
        _cap_rows = [
            f"('{name}', '{desc}', 'identity', false)"
            for name, desc in ALL_CAPABILITIES_DESC.items()
        ]
        _all_rows = [
            "('reward:access', 'Dùng module Reward (tạo/chạy job phân phối điểm)', 'reward-service', false)",
            "('reward:admin',  'Quản trị module Reward (xem mọi job/tài khoản)',   'reward-service', true)",
        ] + _cap_rows
        _names = ", ".join(f"'{r.split(chr(39))[1]}'" for r in _all_rows)
        await conn.execute(f"DELETE FROM permissions WHERE name NOT IN ({_names})")
        await conn.execute(
            "INSERT INTO permissions (name, description, owner_service, admin_only) VALUES "
            + ", ".join(_all_rows)
            + " ON CONFLICT (name) DO UPDATE SET admin_only = EXCLUDED.admin_only")
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


async def _make_client(db, settings, mailer, clock, sleeps):
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    app = create_app(settings, database=db, mailer=mailer, now=clock, sleep=fake_sleep)
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://identity.test",
                          headers={"X-Real-IP": "10.0.0." + str(uuid.uuid4().int % 250)})
    c.app = app
    return c


@pytest_asyncio.fixture
async def client(db, settings, mailer, clock, sleeps):
    async with await _make_client(db, settings, mailer, clock, sleeps) as c:
        yield c


@pytest_asyncio.fixture
async def client_hs256(db, settings_hs256, mailer, clock, sleeps):
    async with await _make_client(db, settings_hs256, mailer, clock, sleeps) as c:
        yield c


async def claims(client, token: str) -> dict:
    """Verify token RS256 bằng JWKS công khai của chính service (như một service khác sẽ làm) và trả claims."""
    import jwt
    jwks = (await client.get("/.well-known/jwks.json")).json()["keys"]
    kid = jwt.get_unverified_header(token)["kid"]
    jwk = next(k for k in jwks if k["kid"] == kid)
    key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    # verify_iat=False: FakeClock đặt thời gian tương lai (2077) nên iat > real_now; PyJWT 2.15 kiểm iat.
    return jwt.decode(token, key, algorithms=["RS256"], audience="manager-api", issuer=ISSUER,
                      options={"verify_iat": False})


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
