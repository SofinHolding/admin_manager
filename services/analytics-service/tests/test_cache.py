"""Cache-Aside Redis THẬT: HIT/MISS, vô hiệu NGAY sau apply revision (không chờ TTL), khoá có scope người gọi,
Redis chết giữa chừng / không cấu hình / không với tới được vẫn đúng và `/health/ready` không phụ thuộc Redis."""

import asyncio
from urllib.parse import urlsplit, urlunsplit

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B, post
from conftest import build_world

pytestmark = [pytest.mark.needs_pg, pytest.mark.needs_redis]

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}


def redis_keys(url: str) -> dict[str, int]:
    import redis
    r = redis.Redis.from_url(url, decode_responses=True)
    try:
        return {k: r.ttl(k) for k in r.scan_iter("analytics:*")}
    finally:
        r.close()


async def seed(w):
    from analytics_testsupport import seed_world
    seed_world(w.integration)
    await w.sync()


async def test_miss_then_hit_and_keys_have_ttl_and_scope(world_redis, redis_url):
    w = world_redis
    await seed(w)
    r1 = await w.get("/v1/analytics/summary", **RANGE)
    r2 = await w.get("/v1/analytics/summary", **RANGE)
    assert (r1.headers["X-Cache"], r2.headers["X-Cache"]) == ("MISS", "HIT")
    a, b = r1.json(), r2.json()
    for body in (a, b):
        body.pop("freshness")
    assert a == b and a["total_views"] == 560
    keys = redis_keys(redis_url)
    assert len(keys) == 1
    key, ttl = next(iter(keys.items()))
    seal = await w.seal()
    assert key.startswith(f"analytics:v1:{seal}:own:{OWN_A}:summary:") and 0 < ttl <= 300     # LUÔN có TTL
    assert w.cache.hits == 1 and w.cache.misses == 1


async def test_every_cached_key_has_a_ttl(world_redis, redis_url):
    w = world_redis
    await seed(w)
    for ep in ("summary", "traffic", "top-channels", "views-by-country", "country-tags", "channel-breakdown",
               "compare/platform", "metrics", "platform-tags"):
        assert (await w.get("/v1/analytics/" + ep, **RANGE)).status_code == 200
    keys = redis_keys(redis_url)
    assert len(keys) == 9 and all(0 < ttl <= 300 for ttl in keys.values())


async def test_cache_is_invalidated_immediately_after_applying_a_revision(world_redis):
    """Không dựa TTL: sau khi áp revision mới, request kế tiếp là MISS và thấy số mới ngay."""
    w = world_redis
    await seed(w)
    first = await w.get("/v1/analytics/summary", **RANGE)
    assert first.headers["X-Cache"] == "MISS" and first.json()["total_views"] == 560
    assert (await w.get("/v1/analytics/summary", **RANGE)).headers["X-Cache"] == "HIT"
    seal_before = await w.seal()
    w.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                              external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
    await w.sync()
    assert await w.seal() > seal_before
    after = await w.get("/v1/analytics/summary", **RANGE)
    assert after.headers["X-Cache"] == "MISS" and after.json()["total_views"] == 860
    again = await w.get("/v1/analytics/summary", **RANGE)
    assert again.headers["X-Cache"] == "HIT" and again.json()["total_views"] == 860


async def test_group_and_alias_edits_invalidate_cache(world_redis):
    w = world_redis
    await seed(w)
    path = "/v1/analytics/views-by-country"
    assert (await w.get(path, **RANGE)).headers["X-Cache"] == "MISS"
    assert (await w.get(path, **RANGE)).headers["X-Cache"] == "HIT"
    g = (await w.client.post("/v1/analytics/groups", json={"name": "G"}, headers=w.as_user(OWN_A))).json()
    await w.client.post(f"/v1/analytics/groups/{g['id']}/members", headers=w.as_user(OWN_A),
                        json={"channel_ref": "pg1", "country_override": "Chile"})
    r = await w.get(path, **RANGE)
    assert r.headers["X-Cache"] == "MISS" and r.json()["totals"]["Chile"] == 350          # thấy override NGAY
    assert (await w.get(path, **RANGE)).headers["X-Cache"] == "HIT"
    admin = w.as_user(ADMIN, "admin")
    await w.client.post("/v1/analytics/admin/country-canonicals", json={"name": "Chilito"}, headers=admin)
    assert (await w.get(path, **RANGE)).headers["X-Cache"] == "MISS"        # nạp lại sau khi tạo tên chuẩn
    assert (await w.get(path, **RANGE)).headers["X-Cache"] == "HIT"
    await w.client.post("/v1/analytics/admin/country-aliases", json={"alias": "chile", "canonical": "Chilito"},
                        headers=admin)
    r = await w.get(path, **RANGE)
    assert r.headers["X-Cache"] == "MISS" and "Chilito" in r.json()["totals"] and "Chile" not in r.json()["totals"]


async def test_cache_never_leaks_between_users(world_redis, redis_url):
    w = world_redis
    await seed(w)
    a = await w.get("/v1/analytics/summary", sub=OWN_A, **RANGE)
    b = await w.get("/v1/analytics/summary", sub=OWN_B, **RANGE)
    assert a.headers["X-Cache"] == b.headers["X-Cache"] == "MISS"       # B KHÔNG nhận kết quả đã cache của A
    assert (a.json()["total_views"], b.json()["total_views"]) == (560, 999)
    # B xin owner của A: bị ép về B, vẫn là HIT của chính B
    forged = await w.get("/v1/analytics/summary", sub=OWN_B, owner_account_id=OWN_A, **RANGE)
    assert forged.json()["total_views"] == 999 and forged.headers["X-Cache"] == "HIT"
    # admin (xuyên owner) có phạm vi riêng, không dùng chung khoá với viewer
    adm = await w.get("/v1/analytics/summary", sub=ADMIN, role="admin", **RANGE)
    assert adm.headers["X-Cache"] == "MISS" and adm.json()["total_views"] == 1559
    assert (await w.get("/v1/analytics/summary", sub=OWN_A, **RANGE)).json()["total_views"] == 560
    scopes = {k.split(":")[3] + ":" + k.split(":")[4] for k in redis_keys(redis_url)}
    assert scopes == {f"own:{OWN_A}", f"own:{OWN_B}", "all:summary"}


async def test_filter_values_are_part_of_the_key(world_redis):
    w = world_redis
    await seed(w)
    a = await w.get("/v1/analytics/summary", country="Peru", **RANGE)
    b = await w.get("/v1/analytics/summary", country="Brazil", **RANGE)
    assert (a.json()["total_views"], b.json()["total_views"]) == (110, 380)
    assert a.headers["X-Cache"] == b.headers["X-Cache"] == "MISS"


async def test_flush_endpoint_removes_keys_and_requires_admin(world_redis, redis_url):
    w = world_redis
    await seed(w)
    await w.get("/v1/analytics/summary", **RANGE)
    await w.get("/v1/analytics/traffic", **RANGE)
    assert len(redis_keys(redis_url)) == 2
    assert (await w.client.post("/v1/admin/cache/flush", headers=w.as_user(OWN_A))).status_code == 403
    r = await w.client.post("/v1/admin/cache/flush", headers=w.as_user(ADMIN, "admin"))
    assert r.status_code == 200 and r.json() == {"ok": True, "removed": 2, "enabled": True}
    assert redis_keys(redis_url) == {}
    assert (await w.get("/v1/analytics/summary", **RANGE)).headers["X-Cache"] == "MISS"


# ── Redis không có / chết ───────────────────────────────────────────────────────

async def test_without_redis_everything_works_and_ready_is_ok(seeded):
    r = await seeded.get("/v1/analytics/summary", **RANGE)
    assert r.status_code == 200 and r.headers["X-Cache"] == "OFF" and r.json()["total_views"] == 560
    assert (await seeded.client.get("/health/ready")).status_code == 200
    assert seeded.cache.configured is False


async def test_unreachable_redis_at_startup_falls_back_to_postgres(db, keys, issuer, integration, identity_feed, clock):
    w = await build_world(db, keys, issuer, integration, identity_feed, clock, redis_url="redis://127.0.0.1:1/0")
    try:
        await seed(w)
        for _ in range(3):
            r = await w.get("/v1/analytics/summary", **RANGE)
            assert r.status_code == 200 and r.json()["total_views"] == 560 and r.headers["X-Cache"] == "MISS"
        assert w.cache.errors >= 1
        assert (await w.client.get("/health/ready")).status_code == 200      # ready KHÔNG phụ thuộc Redis
    finally:
        await w.client.aclose()


class TcpProxy:
    """Proxy TCP tới Redis thật; `stop()` cắt MỌI kết nối như Redis chết giữa chừng."""

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.server = None
        self.writers: list = []
        self.port = 0

    async def start(self, port: int = 0) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def _handle(self, reader, writer):
        try:
            up_r, up_w = await asyncio.open_connection(*self.target)
        except OSError:
            writer.close()
            return
        self.writers += [writer, up_w]

        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()
        self.writers.clear()
        await self.server.wait_closed()


async def test_redis_dying_midway_keeps_answers_correct_and_recovers(db, keys, issuer, integration, identity_feed,
                                                                      clock, redis_url):
    parts = urlsplit(redis_url)
    proxy = TcpProxy(parts.hostname, parts.port)
    port = await proxy.start()
    proxied = urlunsplit(parts._replace(netloc=f"{parts.username}:{parts.password}@127.0.0.1:{port}"))
    w = await build_world(db, keys, issuer, integration, identity_feed, clock, redis_url=proxied)
    w.cache._retry_after = 0.2
    try:
        await seed(w)
        assert (await w.get("/v1/analytics/summary", **RANGE)).headers["X-Cache"] == "MISS"
        assert (await w.get("/v1/analytics/summary", **RANGE)).headers["X-Cache"] == "HIT"
        await proxy.stop()                                               # Redis "chết" giữa chừng
        for _ in range(3):
            r = await w.get("/v1/analytics/summary", **RANGE)
            assert r.status_code == 200 and r.json()["total_views"] == 560      # vẫn đúng, đọc thẳng PostgreSQL
        # ghi trong lúc Redis chết: không lỗi, không lộ số cũ
        w.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                                  external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
        await w.sync()
        r = await w.get("/v1/analytics/summary", **RANGE)
        assert r.status_code == 200 and r.json()["total_views"] == 860
        assert (await w.client.get("/health/ready")).status_code == 200
        # Redis sống lại: cache hoạt động trở lại và KHÔNG có số cũ
        await proxy.start(port)
        await asyncio.sleep(0.4)
        r1 = await w.get("/v1/analytics/summary", **RANGE)
        r2 = await w.get("/v1/analytics/summary", **RANGE)
        assert r1.json()["total_views"] == r2.json()["total_views"] == 860
        assert r2.headers["X-Cache"] == "HIT"
    finally:
        await w.cache.flush()
        await w.client.aclose()
        try:
            await proxy.stop()
        except Exception:  # noqa: BLE001
            pass


async def test_corrupt_cache_value_is_ignored(world_redis, redis_url):
    import redis
    w = world_redis
    await seed(w)
    await w.get("/v1/analytics/summary", **RANGE)
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    key = next(iter(redis_keys(redis_url)))
    r.set(key, "{không phải json", ex=60)
    r.close()
    res = await w.get("/v1/analytics/summary", **RANGE)
    assert res.status_code == 200 and res.json()["total_views"] == 560


async def test_new_commit_from_another_owner_also_changes_seal_for_everyone(world_redis):
    """Seal là toàn cục: commit của B làm mọi khoá cũ (cả của A) vô hiệu — an toàn hơn là dựa vào đoán phạm vi ảnh hưởng."""
    w = world_redis
    await seed(w)
    await w.get("/v1/analytics/summary", sub=OWN_A, **RANGE)
    w.integration.commit(owner=OWN_B, platform="facebook", content=[post("pb2", "pgB", "2026-10-02", 1)])
    await w.sync()
    assert (await w.get("/v1/analytics/summary", sub=OWN_A, **RANGE)).headers["X-Cache"] == "MISS"
