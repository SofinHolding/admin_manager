"""Nhiều consumer cùng chạy (nhiều instance) và khối lượng đủ lớn để phát hiện hồi quy hiệu năng thô."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from analytics_testsupport import OWN_A, post, res, seed_world
from analytics_service.feed_consumer.consumer import FeedConsumer
from analytics_service.readmodels.apply import apply_canonical
from manager_sdk import FeedClient

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}


async def test_two_consumers_running_at_once_produce_one_consistent_result(world, keys):
    seed_world(world.integration)
    settings = world.app.state.container.settings
    second = FeedConsumer(world.db, settings, now=world.clock,
                          integration=FeedClient(settings.integration_url, keys["integration"], retries=0,
                                                 http_client=world.integration.client()),
                          identity=FeedClient(settings.identity_url, keys["identity"], retries=0,
                                              http_client=world.identity_feed.client()))
    results = await asyncio.gather(world.sync(), second.run_once(), world.sync(), second.run_once())
    assert all(r["integration"]["ok"] for r in results)
    assert (await world.get("/v1/analytics/summary", **RANGE)).json()["total_views"] == 560
    assert (await world.fetch("SELECT count(*) AS n FROM processed_events"))[0]["n"] == 3
    assert (await world.fetch("SELECT count(*) AS n FROM read_content"))[0]["n"] == 8
    rep = await world.client.post("/v1/analytics/admin/reconcile", headers=world.as_user("d" * 32, "admin"))
    assert rep.json()["ok"] is True


async def test_summary_over_twenty_thousand_posts_is_fast_and_exact(world):
    world.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("seed", "pg1", "2026-09-01", 1)])
    await world.sync()
    items = [{"owner_account_id": OWN_A, "platform": "facebook", "resource_external_id": "pg1", "external_id": f"x{i}",
              "published_date": f"2026-10-{1 + i % 3:02d}", "metrics": {"views": 2, "reactions": 1, "comments": 0},
              "revision": 2, "removed": False} for i in range(20_000)]
    async with world.db.transaction() as conn:
        await apply_canonical(conn, {"content": items})
    t0 = time.perf_counter()
    r = await world.get("/v1/analytics/traffic", **RANGE)
    elapsed = time.perf_counter() - t0
    body = r.json()
    assert body["totals"]["views"] == 40_000 and body["totals"]["interactions"] == 20_000
    assert [d["videos"] for d in body["days"]] == [6667, 6667, 6666]
    assert elapsed < 8, f"truy vấn 20k bài mất {elapsed:.1f}s"


async def test_published_openapi_snapshot_is_still_honoured(world):
    """Chặn breaking change: mọi path/method/query-param trong docs/api/openapi/analytics-service.v1.json vẫn còn."""
    snapshot = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi" / "analytics-service.v1.json"
    if not snapshot.exists():
        pytest.skip("chưa xuất OpenAPI snapshot")
    old = json.loads(snapshot.read_text(encoding="utf-8"))
    live = (await world.client.get("/openapi.json")).json()
    for path, item in old["paths"].items():
        assert path in live["paths"], f"mất path {path}"
        for method, op in item.items():
            assert method in live["paths"][path], f"mất {method} {path}"
            have = {p["name"] for p in live["paths"][path][method].get("parameters", [])}
            want = {p["name"] for p in op.get("parameters", [])}
            assert want <= have, f"{method} {path} mất tham số {want - have}"
