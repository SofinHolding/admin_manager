"""Fixture pytest cho reward-service — Postgres THẬT (`reward_db`, user `reward_svc`) + FAKE IDENTITY.

Phase 4 (gỡ coupling):
  * DB test = DB RIÊNG `reward_db` lấy qua `infra/scripts/devenv.py` (secret DEV sinh ngẫu nhiên, không có trong repo).
    Thiếu hạ tầng DEV ⇒ test cần DB SKIP có lý do (SKIP KHÔNG phải PASS). `REWARD_TEST_DATABASE_URL` đè được.
  * Không còn DDL `accounts`/`invite_keys`/`refresh_tokens` và không còn JWT secret: access token là RS256 THẬT do
    `FakeIdentity` ký, Reward verify bằng JWKS của nó. Fake Identity cũng phục vụ API grant/revoke/users + feed.
  * `insert_account` / `sign_token` giữ tên cũ để test cũ đọc như trước, nhưng tạo người dùng TRÊN fake Identity
    (không có bảng accounts nào).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent / "infra" / "scripts"))

import importlib
import os
import secrets
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

import sdk_path  # noqa: E402,F401
import devenv  # noqa: E402  (đọc secret DEV từ .env.dev)
from store import migrate as store_migrate  # noqa: E402
from store.pool import Pool  # noqa: E402
from tests.harness.fake_identity import AUDIENCE, ISSUER, FakeIdentity, start_fake_identity  # noqa: E402

PG_REASON = devenv.SKIP_REASON
TEST_DATABASE_URL = os.environ.get("REWARD_TEST_DATABASE_URL") or (
    devenv.pg_dsn("reward") if devenv.pg_ready() else "")
HAVE_DB = bool(TEST_DATABASE_URL)

REWARD_TABLES = (
    "reward_distributions", "reward_job_events", "reward_runner_lock", "reward_user_map", "reward_role_audit",
    "reward_jobs", "reward_discord_credentials", "reward_discord_tokens",
    "account_denylist", "account_projection", "processed_events", "dead_letters", "feed_cursor",
)


def _set_test_env() -> None:
    # Biến cũ KHÔNG còn ý nghĩa — gỡ để chắc chắn không có đường nào còn đọc chúng.
    os.environ.pop("JWT_SECRET", None)
    os.environ.pop("ADMIN_API_BASE", None)
    os.environ.pop("DATABASE_URL", None)
    os.environ["REWARD_DATABASE_URL"] = TEST_DATABASE_URL or "postgresql://reward_svc@127.0.0.1:1/reward_db"
    os.environ.setdefault("REWARD_TOKEN_KEY", Fernet.generate_key().decode())
    os.environ["REWARD_PORT"] = "8422"
    os.environ["REWARD_DB_MIGRATE_ON_BOOT"] = "1"
    os.environ["REWARD_CORS_ORIGINS"] = "http://localhost:5175"
    # Giá trị tạm để `import main` (tạo `app` cấp module) không lỗi; fixture `app_client` đặt lại theo fake Identity.
    os.environ["REWARD_IDENTITY_URL"] = "http://127.0.0.1:9"
    os.environ["REWARD_ISSUER"] = ISSUER
    os.environ["REWARD_AUDIENCE"] = AUDIENCE
    os.environ.pop("REWARD_IDENTITY_FEED_KEY", None)
    os.environ.pop("REWARD_IDENTITY_FEED_URL", None)
    os.environ.pop("REWARD_IDENTITY_JWKS_URL", None)


_set_test_env()


def pytest_collection_modifyitems(config, items):
    if HAVE_DB:
        return
    skip = pytest.mark.skip(reason=PG_REASON)
    for item in items:
        fixtures = set(getattr(item, "fixturenames", ()))
        if fixtures & {"db_conn", "pool", "client"}:
            item.add_marker(skip)


# ── Fake Identity ────────────────────────────────────────────────────────────────

_CURRENT: dict[str, FakeIdentity] = {}


@pytest_asyncio.fixture
async def identity():
    fake = await start_fake_identity()
    _CURRENT["identity"] = fake
    try:
        yield fake
    finally:
        _CURRENT.pop("identity", None)
        await fake.stop()


def _identity() -> FakeIdentity:
    try:
        return _CURRENT["identity"]
    except KeyError:
        raise RuntimeError("Test cần fixture `identity` (fake Identity) trước khi tạo tài khoản") from None


async def insert_account(_conn=None, *, role: str, status: str = "active", username: str | None = None,
                         perms: tuple[str, ...] = ()) -> dict:
    """Tạo tài khoản TRÊN FAKE IDENTITY (không có bảng accounts). Tham số đầu giữ cho tương thích chữ ký cũ."""
    user = _identity().add_user(role=role, status=status, username=username, perms=perms)
    return {"id": user["id"], "username": user["username"], "role": role, "status": status}


def sign_token(account: dict, *, ttl_s: int = 900, role: str | None = None, **over) -> str:
    """Access token RS256 THẬT do fake Identity ký (đủ claim như Identity: iss/aud/sub/role/perms/sid/jti/iat/nbf/exp)."""
    ident = _identity()
    user = ident.users[account["id"]]
    if role is not None:
        over["role"] = role
    return ident.token(user, ttl=ttl_s, **over)


@pytest.fixture
def bearer():
    def _bearer(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}
    return _bearer


# ── Database ─────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_conn():
    boot = Pool(TEST_DATABASE_URL)
    await boot.open()
    try:
        await store_migrate.run(boot)
    finally:
        await boot.close()
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        yield conn
    finally:
        # Dọn dữ liệu test tạo ra — KHÔNG DROP bảng. distributions trước items (RESTRICT), jobs CASCADE items/attempts.
        for table in REWARD_TABLES:
            await conn.execute(f"DELETE FROM {table}")
        await conn.close()


@pytest_asyncio.fixture
async def pool(db_conn):
    """`Pool` đã mở + migrate — dùng cho test engine (`job_runner`, `recovery`, repositories)."""
    p = Pool(TEST_DATABASE_URL)
    await p.open()
    await store_migrate.run(p)
    try:
        yield p
    finally:
        await p.close()


# ── App ──────────────────────────────────────────────────────────────────────────

def configure_identity_env(ident: FakeIdentity, *, feed: str = "off", poll_interval: str = "0.1", **env: str) -> None:
    os.environ["REWARD_IDENTITY_URL"] = ident.base_url
    os.environ["REWARD_ISSUER"] = ISSUER
    os.environ["REWARD_AUDIENCE"] = AUDIENCE
    os.environ["REWARD_IDENTITY_TIMEOUT_S"] = "2"
    os.environ["REWARD_IDENTITY_RETRIES"] = "1"
    os.environ["REWARD_IDENTITY_BACKOFF_S"] = "0.01"
    if feed == "auto":  # consumer chạy nền thật
        os.environ["REWARD_IDENTITY_FEED_KEY"] = ident.feed_key
        os.environ["REWARD_FEED_POLL_INTERVAL_S"] = poll_interval
        os.environ["REWARD_FEED_EVENT_BACKOFF_S"] = "0.01"
    else:  # test tự điều khiển consumer bằng `make_consumer`
        os.environ.pop("REWARD_IDENTITY_FEED_KEY", None)
    for key, value in env.items():
        os.environ[key] = value


@asynccontextmanager
async def app_client(ident: FakeIdentity, *, feed: str = "off", poll_interval: str = "0.1", **env: str):
    """ASGI client tới app reward (đã reload với env mới) + lifespan thật. Trả `(client)`; app ở `client.app_`."""
    configure_identity_env(ident, feed=feed, poll_interval=poll_interval, **env)
    import main as main_module
    importlib.reload(main_module)
    app = main_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            ac.app_ = app  # type: ignore[attr-defined]
            yield ac


def make_consumer(client, ident: FakeIdentity, *, max_attempts: int = 3, **kw):
    """Consumer điều khiển tay (poll_once) dùng chung pool + WorkerManager của app đang chạy."""
    from manager_sdk import FeedClient
    from identity_link.consumer import FeedConsumer
    app = client.app_
    feed = FeedClient(ident.base_url, ident.feed_key, timeout=2, retries=kw.pop("retries", 0), backoff_base=0.01)
    on_pause = kw.pop("on_pause", app.state.worker_manager.pause_account_jobs)
    return FeedConsumer(pool=app.state.pool, feed=feed, on_pause=on_pause, max_attempts=max_attempts,
                        backoff_base=0.01, **kw)


@pytest_asyncio.fixture
async def client(db_conn, identity):
    """Client mặc định (không consumer nền). Test cần Discord giả/biến khác tự định nghĩa lại `client` bằng `app_client`."""
    async with app_client(identity) as ac:
        yield ac


async def seed_active_account_with_credential(
    conn: asyncpg.Connection, *, role: str = "discord", credential_status: str = "valid",
) -> str:
    """Chèn 1 `reward_discord_credentials` `status='valid'` cho một account_id mới — dùng cho test gọi
    `JobRunner.run()` trực tiếp. Không còn bảng accounts: quyền chạy do denylist cục bộ quyết định (mặc định không
    bị chặn). `role` giữ cho tương thích chữ ký cũ. Trả `account_id`."""
    account_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO reward_discord_credentials (account_id, status) VALUES ($1, $2)", account_id, credential_status)
    return account_id


async def seed_job(
    conn: asyncpg.Connection, *, account_id: str, rows: list[dict],
    channel_id: str = "C", guild_id: str = "G", command_name: str = "give-xp",
    confirm_mode: str = "reply", success_pattern: str | None = "has been given",
    failure_pattern: str | None = None, leveling_bot_id: str | None = None,
    delay_ms: int = 0, jitter_ms: int = 0, max_item_retries: int = 3,
    unknown_pause_threshold: int = 5, status: str = "validated",
) -> int:
    """Tạo 1 job + N item trực tiếp trong DB (thay cho API job chưa có ở P2 — xem D.5/Contract).

    `rows`: `[{"raw_username":..., "point":...}, ...]` — `normalized_username`/`idempotency_key` tự
    sinh bằng `domain.parser`/`domain.idempotency` (đúng công thức B.2.3)."""
    from domain.idempotency import item_key
    from domain.parser import normalize_username

    job_id = await conn.fetchrow(
        """
        INSERT INTO reward_jobs
            (account_id, name, status, guild_id, channel_id, command_name, confirm_mode,
             success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
             max_item_retries, unknown_pause_threshold, total_items)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        RETURNING id
        """,
        account_id, f"job-{uuid.uuid4().hex[:8]}", status, guild_id, channel_id, command_name,
        confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
        max_item_retries, unknown_pause_threshold, len(rows),
    )
    job_id = job_id["id"]
    for i, r in enumerate(rows):
        normalized = normalize_username(r["raw_username"])
        key = item_key(job_id=job_id, row_index=i, normalized_username=normalized, point=r["point"])
        await conn.execute(
            "INSERT INTO reward_items "
            "(job_id, row_index, raw_username, normalized_username, point, status, idempotency_key) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            job_id, i, r["raw_username"], normalized, r["point"], r.get("status", "pending"), key,
        )
    return job_id


def random_secret() -> str:
    return secrets.token_urlsafe(24)
