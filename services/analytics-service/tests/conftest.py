"""Fixture dùng chung. Test cần DB chạy trên Postgres THẬT (user riêng `analytics_svc`, DB `analytics_db`) và Redis THẬT
(user `analytics_cache`, prefix `analytics:*`) qua `infra/scripts/devenv.py`. Thiếu hạ tầng -> SKIP có lý do (KHÔNG tính là
đã kiểm chứng). Secret test lấy từ `.env.dev` đã sinh ngẫu nhiên; không có secret nào nằm trong repo.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_harness import (PG_OK, REDIS_OK, TABLES, FakeClock, World, build_world, devenv, dsn,  # noqa: E402,F401
                               make_settings, reset_database)
from analytics_testsupport import FakeIdentityFeed, FakeIntegration, Issuer, seed_world  # noqa: E402

from analytics_service import migrate  # noqa: E402
from analytics_service.db import Database  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_pg: cần Postgres thật")
    config.addinivalue_line("markers", "needs_redis: cần Redis thật")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "needs_pg" in item.keywords and not PG_OK:
            item.add_marker(pytest.mark.skip(reason=devenv.SKIP_REASON))
        if "needs_redis" in item.keywords and not REDIS_OK:
            item.add_marker(pytest.mark.skip(reason=devenv.SKIP_REASON))


@pytest.fixture(scope="session")
def migrated() -> None:
    if not PG_OK:
        return

    async def _go() -> None:
        try:
            await migrate.run(dsn())
        except migrate.MigrationError:
            # DB test dùng riêng: migration đang phát triển có thể lệch checksum -> dựng lại từ đầu.
            import asyncpg
            conn = await asyncpg.connect(dsn())
            await conn.execute(f"DROP TABLE IF EXISTS {TABLES}, schema_migrations CASCADE")
            await conn.close()
            await migrate.run(dsn())

    asyncio.run(_go())


@pytest_asyncio.fixture
async def db(migrated):
    """Database đã migrate, rỗng (seal = 1) cho mỗi test."""
    database = Database(dsn(), max_size=8)
    await database.open()
    await reset_database(database)
    yield database
    await database.close()


@pytest.fixture
def db_clean(migrated):
    """Cho test ĐỒNG BỘ (tiến trình thật): dọn sạch bảng, seal = 1."""
    async def _go() -> None:
        database = Database(dsn(), max_size=2)
        try:
            await reset_database(database)
        finally:
            await database.close()

    asyncio.run(_go())


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="session")
def keys() -> dict:
    return {"integration": "feed-" + secrets.token_urlsafe(24), "identity": "feed-" + secrets.token_urlsafe(24)}


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@pytest.fixture
def integration(keys) -> FakeIntegration:
    return FakeIntegration(keys["integration"])


@pytest.fixture
def identity_feed(keys) -> FakeIdentityFeed:
    return FakeIdentityFeed(keys["identity"])


@pytest_asyncio.fixture
async def world(db, keys, issuer, integration, identity_feed, clock):
    """Không Redis: mọi truy vấn đọc thẳng PostgreSQL."""
    w = await build_world(db, keys, issuer, integration, identity_feed, clock)
    yield w
    await w.client.aclose()


@pytest_asyncio.fixture
async def seeded(world):
    """world + bộ dữ liệu tính tay đã được consumer đồng bộ."""
    seed_world(world.integration)
    await world.sync()
    return world


@pytest.fixture
def redis_url() -> str:
    if not REDIS_OK:
        return ""
    import redis
    url = devenv.redis_url()
    r = redis.Redis.from_url(url, decode_responses=True)
    for key in r.scan_iter("analytics:*"):
        r.delete(key)
    r.close()
    return url


@pytest_asyncio.fixture
async def world_redis(db, keys, issuer, integration, identity_feed, clock, redis_url):
    """world có Redis THẬT (user analytics_cache)."""
    w = await build_world(db, keys, issuer, integration, identity_feed, clock, redis_url=redis_url)
    yield w
    await w.cache.flush()
    await w.client.aclose()
