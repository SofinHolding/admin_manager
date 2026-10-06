"""Đối soát XUYÊN HAI SERVICE bằng dữ liệu Integration THẬT (không giả nguồn).

Trước đây Integration băm `id:revision:removed` (id nội bộ) còn Analytics băm `<khoá tự nhiên>|revision|removed(|số liệu)`:
hai bên không bao giờ khớp nên `POST /v1/analytics/admin/reconcile` luôn báo lệch, và test của Analytics không bắt được vì
tự giả nguồn theo thuật toán của chính nó. Test này chạy cả hai service THẬT trong một tiến trình:

  Tool --API key--> integration-service (app thật, integration_db) --(ASGI)--> FeedConsumer THẬT của analytics-service
      (kéo /internal/v1/feed + /canonical/* + /summary thật) --> analytics_db --> `reconcile()` PHẢI báo ok.

Test chéo hệ thống nên được phép import code của cả hai service (service thì không bao giờ import nhau).
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
INTEG = ROOT / "services" / "integration-service"
ANALYTICS = ROOT / "services" / "analytics-service"
for p in (INTEG / "src", ANALYTICS / "src", ANALYTICS / "tests", ROOT / "packages" / "observability" / "src",
          ROOT / "packages" / "contracts" / "python", ROOT / "packages" / "sdk-python", ROOT / "infra" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import devenv  # noqa: E402

pytestmark = pytest.mark.skipif(not devenv.pg_ready(), reason=devenv.SKIP_REASON)

# conftest của integration-service nạp bằng tên riêng để không đụng `conftest` của pytest.
_spec = importlib.util.spec_from_file_location("integration_tests_conftest", INTEG / "tests" / "conftest.py")
iconf = importlib.util.module_from_spec(_spec)
sys.modules["integration_tests_conftest"] = iconf
_spec.loader.exec_module(iconf)

import asyncpg  # noqa: E402
from analytics_harness import FakeClock as AClock  # noqa: E402
from analytics_harness import build_world, reset_database  # noqa: E402
from analytics_harness import dsn as analytics_dsn  # noqa: E402
from analytics_testsupport import FakeIdentityFeed, Issuer  # noqa: E402

from analytics_service import migrate as analytics_migrate  # noqa: E402
from analytics_service.db import Database as AnalyticsDb  # noqa: E402
from integration_service import migrate as integration_migrate  # noqa: E402
from integration_service.app import create_app as create_integration_app  # noqa: E402
from integration_service.db import Database as IntegrationDb  # noqa: E402

KINDS = ("projects", "resources", "content", "metrics", "events")


class IntegrationOverAsgi:
    """Cho `build_world`: client httpx đi thẳng vào app Integration THẬT (không có route giả)."""

    def __init__(self, app) -> None:
        self.app = app

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                 base_url="http://integration.test")


async def _migrate_integration(dsn: str) -> None:
    try:
        await integration_migrate.run(dsn)
    except integration_migrate.MigrationError:
        conn = await asyncpg.connect(dsn)
        for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"):
            await conn.execute(f'DROP TABLE IF EXISTS "{r["tablename"]}" CASCADE')
        await conn.execute("DROP SCHEMA IF EXISTS legacy_ingest CASCADE")
        await conn.execute("DROP SEQUENCE IF EXISTS canonical_revision_seq")
        await conn.close()
        await integration_migrate.run(dsn)


@pytest_asyncio.fixture
async def stack():
    idn_dsn = devenv.pg_dsn("integration")
    await _migrate_integration(idn_dsn)
    idb = IntegrationDb(idn_dsn, max_size=6)
    await idb.open()
    async with idb.connection() as conn:
        await conn.execute(f"TRUNCATE {iconf._TABLES} RESTART IDENTITY CASCADE")
        await conn.execute("ALTER SEQUENCE canonical_revision_seq RESTART")
        await conn.execute("DELETE FROM platform_registry WHERE key <> ALL($1::text[])", list(iconf.SEED_PLATFORMS))
        await conn.execute("DELETE FROM metric_registry WHERE key <> ALL($1::text[])", list(iconf.SEED_METRICS))
        await conn.execute("UPDATE platform_registry SET enabled = true")
        await conn.execute("UPDATE metric_registry SET enabled = true")
        await conn.execute("DELETE FROM legacy_ingest.platform_policies")
        await conn.executemany(
            "INSERT INTO legacy_ingest.platform_policies (platform, web_entry_enabled, fallback_enabled) VALUES ($1,$2,$3)",
            iconf.LEGACY_POLICIES)
    identity = iconf.FakeIdentity()
    clock, mono = iconf.FakeClock(), iconf.FakeMono()
    settings = iconf.Settings(
        port=8431, log_level="INFO", database_url=idn_dsn, env="test", issuer=iconf.ISSUER, audience=iconf.AUDIENCE,
        jwks_url=iconf.JWKS_URL, feed_keys=(("analytics", iconf.FEED_KEY_ANALYTICS), ("identity", iconf.FEED_KEY_IDENTITY)),
        maintenance_interval_s=0, provisioning_poll_s=0.0)
    iapp = create_integration_app(settings, database=idb, verifier=iconf.build_verifier(identity), now=clock, clock=mono)
    ihttp = httpx.AsyncClient(transport=httpx.ASGITransport(app=iapp, raise_app_exceptions=False),
                              base_url="http://integration.test", headers={"X-Real-IP": "10.9.0.1"})
    env = iconf.Env(http=ihttp, db=idb, identity=identity, clock=clock)

    await analytics_migrate.run(analytics_dsn())
    adb = AnalyticsDb(analytics_dsn(), max_size=6)
    await adb.open()
    await reset_database(adb)
    keys = {"integration": iconf.FEED_KEY_ANALYTICS, "identity": "feed-" + secrets.token_urlsafe(24)}
    world = await build_world(adb, keys, Issuer(), IntegrationOverAsgi(iapp), FakeIdentityFeed(keys["identity"]), AClock())
    yield env, world
    await world.client.aclose()
    await ihttp.aclose()
    await adb.close()
    await idb.close()


# ── Dữ liệu qua đường Tool THẬT ─────────────────────────────────────────────────

SCOPE = {"resources": True, "content_window": {"from": "2026-09-01", "to": "2026-10-31"},
         "metrics_window": {"from": "2026-09-01", "to": "2026-10-31"},
         "events_window": {"from": "2026-09-01", "to": "2026-10-31"}}


async def ingest(tool, *, platform="facebook", obs=0, scope=SCOPE, type="full", resources=(), content=(), metrics=(),
                 events=()) -> dict:
    expected = {k: len(v) for k, v in (("resources", resources), ("content", content), ("metrics", metrics),
                                       ("events", events)) if v}
    body = {"platform": platform, "snapshot_type": type, "observed_at": iconf.ts(obs), "report_timezone": "UTC",
            "expected_counts": expected}
    if scope is not None and type == "full":
        body["scope"] = scope
    r = await tool.post("/v1/integrations/snapshots", body)
    assert r.status_code == 201, r.text
    sid = r.json()["data"]["snapshot_id"]
    for kind, items in (("resources", resources), ("content", content), ("metrics", metrics), ("events", events)):
        if items:
            r = await tool.post(f"/v1/integrations/snapshots/{sid}/{kind}", {"items": list(items)})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["rejected"] == 0, r.text
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/commit")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def res(ext, rtype="page", obs=0, **kw):
    return {"external_id": ext, "resource_type": rtype, "observed_at": iconf.ts(obs), **kw}


def post(ext, resource, date, obs=0, **metrics):
    return {"external_id": ext, "resource_external_id": resource, "published_date": date,
            "metrics": metrics, "observed_at": iconf.ts(obs)}


def daily(resource, date, obs=0, **metrics):
    return {"resource_external_id": resource, "date": date, "metrics": metrics, "observed_at": iconf.ts(obs)}


def evt(ext, resource, date, kind="post_ok", **kw):
    return {"external_id": ext, "resource_external_id": resource, "kind": kind, "occurred_date": date, **kw}


async def seed(tool):
    """Dữ liệu đa dạng: chủ đề, hai resource_type cùng external_id, null ≠ 0, số 0, sự kiện trùng id trên hai kênh."""
    await tool.put("/v1/integrations/projects/itl", {"name": "ITL", "country_code": "BR"})
    await ingest(
        tool, obs=0,
        resources=[res("r1", display_name="One", country={"code": "BR", "name": "Brazil"}, managed=True,
                       projects=[{"project_key": "itl", "enabled": True}]),
                   res("r2", display_name="Two"),
                   res("dup", "page", display_name="Dup page"), res("dup", "profile", display_name="Dup profile")],
        content=[post("p1", "r1", "2026-10-01", views=100, reactions=None, comments=2),
                 post("p2", "r1", "2026-10-02", views=0, reactions=0, comments=0),
                 post("p1", "r2", "2026-10-03", views=7)],
        metrics=[daily("r1", "2026-10-01", views=40), daily("r2", "2026-10-01", views=0)],
        events=[evt("e1", "r1", "2026-10-01", project_key="itl"), evt("e1", "r2", "2026-10-02"),
                evt("e2", "r1", "2026-10-02", kind="post_fail")])


def assert_reconciled(rec: dict) -> None:
    assert rec["ok"] is True, rec["differences"]
    for kind in KINDS:
        assert rec["kinds"][kind]["checksum_match"] and rec["kinds"][kind]["count_match"], (kind, rec["kinds"][kind])


async def test_reconcile_is_ok_after_real_ingest_and_real_consumer(stack):
    env, world = stack
    tool = await env.tool()
    await seed(tool)
    sync = await world.sync()
    assert sync["integration"]["ok"], sync
    rec = await world.consumer.reconcile()
    assert_reconciled(rec)
    # không tầm thường: có dữ liệu thật ở mọi loại bản ghi, và checksum không phải của chuỗi rỗng
    assert rec["kinds"]["content"]["local"]["counts"]["total"] == 3
    assert rec["kinds"]["events"]["local"]["counts"]["total"] == 3          # 'e1' trên hai kênh vẫn là hai bản ghi
    assert rec["kinds"]["resources"]["local"]["counts"]["total"] == 4
    assert {k: rec["kinds"][k]["source"]["checksum"] for k in KINDS}["content"] != \
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


async def test_reconcile_stays_ok_through_removed_and_updates(stack):
    env, world = stack
    tool = await env.tool()
    await seed(tool)
    await world.sync()
    # snapshot full mới: 'r2', 'dup/profile' và bài p2 vắng mặt => removed; r1/p1 đổi số liệu
    await ingest(tool, obs=10,
                 resources=[res("r1", obs=10, display_name="One", managed=True, country={"code": "BR", "name": "Brazil"},
                                projects=[{"project_key": "itl", "enabled": True}]),
                            res("dup", "page", obs=10, display_name="Dup page")],
                 content=[post("p1", "r1", "2026-10-01", obs=10, views=250, reactions=3, comments=2)],
                 metrics=[daily("r1", "2026-10-01", obs=10, views=55)],
                 events=[evt("e1", "r1", "2026-10-01", project_key="itl")])
    await world.sync()
    assert_reconciled(await world.consumer.reconcile())
    removed = await world.fetch("SELECT count(*) AS n FROM read_resources WHERE removed")
    assert removed[0]["n"] >= 2          # có hàng removed trong read model: lịch sử vẫn nằm trong checksum


async def test_reconcile_detects_real_divergence(stack):
    """Đối soát không được rỗng nghĩa: sửa lén read model thì phải báo lệch."""
    env, world = stack
    tool = await env.tool()
    await seed(tool)
    await world.sync()
    assert_reconciled(await world.consumer.reconcile())
    await world.fetch("UPDATE read_content SET views = views + 1 WHERE external_id = 'p1' AND resource_external_id = 'r1'")
    rec = await world.consumer.reconcile()
    assert rec["ok"] is False and rec["kinds"]["content"]["checksum_match"] is False
    assert rec["kinds"]["resources"]["checksum_match"] is True


async def test_rebuild_then_reconcile_ok(stack):
    env, world = stack
    tool = await env.tool()
    await seed(tool)
    await world.sync()
    await world.consumer.rebuild()
    assert_reconciled(await world.consumer.reconcile())
