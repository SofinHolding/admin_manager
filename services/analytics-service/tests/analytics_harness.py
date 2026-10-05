"""Khung dựng một analytics-service đầy đủ (app thật + Postgres thật [+ Redis thật]) nối với Integration/Identity giả.

Tách khỏi conftest để `tests/contract/test_analytics_stats_parity.py` dùng lại. Cần `analytics_service` và các package dùng
chung trên sys.path (pytest.ini của service đã đặt; test contract tự thêm)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "infra" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import devenv  # noqa: E402

from analytics_testsupport import AUD, ISS, OWN_A  # noqa: E402

from analytics_service.app import create_app  # noqa: E402
from analytics_service.cache import ResultCache  # noqa: E402
from analytics_service.config import Settings  # noqa: E402
from manager_sdk import FeedClient, TokenVerifier  # noqa: E402

TABLES = ("sync_state, feed_cursor, processed_events, dead_letters, account_projection, read_projects, read_resources, "
          "read_content, read_daily_metrics, read_events, channel_group_members, channel_groups, country_aliases, "
          "country_canonicals")

PG_OK = devenv.pg_ready()
REDIS_OK = devenv.redis_ready()


def dsn() -> str:
    return devenv.pg_dsn("analytics")


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


async def reset_database(database) -> None:
    async with database.connection() as conn:
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        await conn.execute("INSERT INTO sync_state (key, num) VALUES ('seal', 1)")


def make_settings(keys, redis_url: str = "", **over) -> Settings:
    base = dict(port=8432, log_level="INFO", database_url=dsn() if PG_OK else "postgresql://x:y@127.0.0.1:1/z",
                redis_url=redis_url, issuer=ISS, audience=AUD, identity_jwks_url=ISS + "/.well-known/jwks.json",
                integration_url="http://integration.test", integration_feed_key=keys["integration"],
                identity_url="http://identity-feed.test", identity_feed_key=keys["identity"], stale_after_s=3600,
                cache_ttl_s=300, sync_interval_s=0.05, consumer_max_attempts=3, consumer_backoff_base_s=0.0,
                feed_page_size=200, canonical_page_size=1000)
    base.update(over)
    return Settings(**base)


class World:
    """Một analytics-service đầy đủ nối với Integration/Identity giả."""

    def __init__(self, app, client, db, integration, identity_feed, issuer, clock, cache):
        self.app, self.client, self.db = app, client, db
        self.integration, self.identity_feed, self.issuer, self.clock, self.cache = (
            integration, identity_feed, issuer, clock, cache)

    @property
    def consumer(self):
        return self.app.state.container.consumer

    async def sync(self) -> dict:
        return await self.consumer.run_once()

    def as_user(self, sub: str, role: str = "viewer", **over) -> dict:
        return self.issuer.headers(sub, role, **over)

    async def get(self, path: str, sub: str = OWN_A, role: str = "viewer", **params):
        return await self.client.get(path, params={k: v for k, v in params.items() if v not in (None, "")},
                                     headers=self.as_user(sub, role))

    async def fetch(self, sql: str, *args):
        async with self.db.connection() as conn:
            return await conn.fetch(sql, *args)

    async def seal(self) -> int:
        return int((await self.fetch("SELECT num FROM sync_state WHERE key='seal'"))[0]["num"])


async def build_world(db, keys, issuer, integration, identity_feed, clock, *, redis_url: str = "", **over) -> World:
    settings = make_settings(keys, redis_url, **over)
    cache = ResultCache(redis_url, ttl=settings.cache_ttl_s)

    async def _sleep(_):
        return None

    verifier = TokenVerifier(issuer=ISS, audience=AUD, jwks_url=settings.jwks_url, http_client=issuer.http_client())
    app = create_app(settings, database=db, cache=cache, verifier=verifier,
                     integration_client=FeedClient(settings.integration_url, keys["integration"],
                                                   http_client=integration.client(), retries=0),
                     identity_client=FeedClient(settings.identity_url, keys["identity"],
                                                http_client=identity_feed.client(), retries=0),
                     now=clock, start_consumer=False, sleep=_sleep)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://analytics.test")
    return World(app, client, db, integration, identity_feed, issuer, clock, cache)
