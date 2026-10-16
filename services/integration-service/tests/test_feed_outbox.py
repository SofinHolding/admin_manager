"""Feed nội bộ + Canonical Export: xác thực service key, phân trang, replay, không mất sự kiện dưới ghi đồng thời."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from conftest import FEED_KEY_ANALYTICS, FEED_KEY_IDENTITY
from helpers import canonical, commit, daily, event, ingest_full, new_snapshot, post, push, res

from integration_service.feed import repository as outbox

pytestmark = pytest.mark.needs_pg
KEY = {"X-Service-Key": FEED_KEY_ANALYTICS}


async def feed(env, **params):
    r = await env.http.get("/internal/v1/feed", params=params, headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()


# ── Xác thực ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/internal/v1/feed", "/internal/v1/canonical/resources",
                                  "/internal/v1/canonical/summary", "/internal/v1/canonical/content"])
async def test_internal_endpoints_reject_missing_or_wrong_service_key(env, path):
    tool = await env.tool()
    user = await env.verified_user(role="admin")
    for headers in ({}, {"X-Service-Key": ""}, {"X-Service-Key": "sai-khoa-" + "x" * 30},
                    {"X-Service-Key": FEED_KEY_ANALYTICS[:-1]}, {"X-Service-Key": FEED_KEY_ANALYTICS + "x"},
                    user["headers"], {"Authorization": f"Bearer {tool.api_key}"},
                    {"Authorization": f"Bearer {FEED_KEY_ANALYTICS}"}):
        r = await env.http.get(path, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID", headers
    assert (await env.http.get(path, headers={"X-Service-Key": FEED_KEY_ANALYTICS})).status_code == 200
    assert (await env.http.get(path, headers={"X-Service-Key": FEED_KEY_IDENTITY})).status_code == 200   # mỗi bên đọc một khoá


async def test_service_key_cannot_call_public_api_and_no_keys_configured_means_locked(env, make_env):
    r = await env.http.get("/v1/integrations/clients", headers=KEY)
    assert r.status_code == 401
    r = await env.http.post("/v1/integrations/handshake", headers=KEY, json={"installation": {"device_id": "d"}})
    assert r.status_code == 401
    locked, _ = await make_env(feed_keys=())
    for k in (KEY, {}, {"X-Service-Key": "x" * 40}):
        assert (await locked.http.get("/internal/v1/feed", headers=k)).status_code == 401


async def test_internal_endpoints_are_not_in_public_openapi(env):
    spec = (await env.http.get("/openapi.json")).json()
    assert not [p for p in spec["paths"] if p.startswith("/internal")]
    assert not [p for p in spec["paths"] if p.startswith("/health")]


# ── Feed ────────────────────────────────────────────────────────────────────────

async def test_feed_replay_pagination_and_shape(env):
    tool = await env.tool()
    start = (await feed(env))["head"]   # bỏ qua integration.client.provisioned phát lúc provision client cá nhân
    for i in range(5):
        await ingest_full(tool, resources=[res(f"r{i}", obs=i)], obs=i, type="delta")
    full = await feed(env, after=start)
    assert [e["type"] for e in full["events"]] == ["snapshot.committed"] * 5
    assert full["has_more"] is False and full["head"] == full["next_cursor"] == full["events"][-1]["seq"]
    e = full["events"][0]
    assert set(e) == {"seq", "id", "type", "subject", "time", "data_version", "data"}
    assert e["id"].startswith("evt_") and e["time"].endswith("Z") and e["data_version"] == 1
    seqs = [x["seq"] for x in full["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5
    # phân trang không trùng không sót
    got, cursor = [], start
    while True:
        page = await feed(env, after=cursor, limit=2)
        got += page["events"]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break

    assert [x["id"] for x in got] == [x["id"] for x in full["events"]]
    empty = await feed(env, after=full["head"])
    assert empty["events"] == [] and empty["next_cursor"] == full["head"] and empty["has_more"] is False
    assert (await env.http.get("/internal/v1/feed", params={"limit": 501}, headers=KEY)).status_code == 422
    assert (await env.http.get("/internal/v1/feed", params={"after": -1}, headers=KEY)).status_code == 422


async def test_feed_types_filter_and_event_catalogue(env):
    tool = await env.tool()
    await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "r1", "observed_at": "2026-10-05T10:00:00Z"}]})
    await ingest_full(tool, resources=[res("r2")], type="delta")
    owner = env.user(sub=tool.owner)
    await env.http.delete(f"/v1/integrations/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert {e["type"] for e in (await feed(env))["events"]} == {
        "integration.client.provisioned", "resource.changed", "snapshot.committed", "credential.revoked"}
    only = await feed(env, types="credential.revoked,khong.ton.tai")
    assert [e["type"] for e in only["events"]] == ["credential.revoked"]
    assert only["events"][0]["data"] == {"client_id": tool.client_id, "key_id": tool.key_id}
    text = str(await feed(env))
    assert "api_key" not in text and "secret" not in text and "hash" not in text and "@" not in text   # không PII/secret


# ── Canonical export ────────────────────────────────────────────────────────────

async def test_canonical_export_pagination_removed_and_only_committed(env):
    tool = await env.tool()
    pending = await new_snapshot(tool, type="delta")
    await push(tool, pending, "resources", [res("chua-commit")])
    assert (await canonical(env, "resources"))["items"] == []                # building không bao giờ lộ ra
    await ingest_full(tool, resources=[res(f"r{i}") for i in range(7)], content=[post(f"p{i}", res_ext="r0") for i in range(7)])
    await ingest_full(tool, resources=[res(f"r{i}", obs=5) for i in range(6)], content=[post("p0", res_ext="r0", obs=5)], obs=5)
    items, after = [], 0
    while True:
        page = await canonical(env, "resources", after=after, limit=3)
        items += page["items"]
        after = page["next_revision"]
        if not page["has_more"]:
            break
    revs = [i["revision"] for i in items]
    assert revs == sorted(revs) and len(set(revs)) == len(revs)
    removed = {i["external_id"] for i in items if i["removed"]}
    assert removed == {"r6"} and {i["external_id"] for i in items} == {f"r{i}" for i in range(7)}
    delta = await canonical(env, "resources", after=revs[-2])
    assert [i["revision"] for i in delta["items"]] == [revs[-1]] and delta["has_more"] is False
    assert delta["head"] >= revs[-1]
    for bad in ("khong-co", "documents"):
        assert (await env.http.get(f"/internal/v1/canonical/{bad}", headers=KEY)).status_code == 404
    assert (await env.http.get("/internal/v1/canonical/resources", params={"limit": 1001}, headers=KEY)).status_code == 422


async def test_canonical_item_fields_follow_the_contract(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1", display_name="P", country={"code": "BR", "name": "Brazil"}, managed=True)],
                      content=[post("p1", views=3, reactions=None)], metrics=[daily("r1", "2026-10-04", views=9)],
                      events=[{**event("e1"), "occurred_at": "2026-10-04T12:00:00Z"}])
    r = (await canonical(env, "resources"))["items"][0]
    assert {"id", "revision", "removed", "owner_account_id", "client_id", "platform", "resource_type", "external_id",
            "state", "observed_at", "display_name", "country"} <= set(r)
    assert r["owner_account_id"] == tool.owner and r["client_id"] == tool.client_id and r["platform"] == "facebook"
    assert r["country"] == {"code": "BR", "name": "Brazil"} and r["managed"] is True
    c = (await canonical(env, "content"))["items"][0]
    assert c["resource_external_id"] == "r1" and c["resource_id"] == r["id"] and c["published_date"] == "2026-10-04"
    assert c["metrics"] == {"views": 3, "reactions": None} and c["owner_account_id"] == tool.owner
    m = (await canonical(env, "metrics"))["items"][0]
    assert m["date"] == "2026-10-04" and m["resource_external_id"] == "r1" and m["metrics"] == {"views": 9}
    e = (await canonical(env, "events"))["items"][0]
    assert e["kind"] == "post_ok" and e["occurred_date"] == "2026-10-04" and "project_key" not in e
    assert "projects" not in r and "project_keys" not in r      # liên kết project đã gỡ khỏi hợp đồng canonical


def _n(v):
    return "n" if v is None else str(v)


def _line(kind: str, i: dict) -> str:
    """Dòng checksum theo HỢP ĐỒNG ĐỐI SOÁT (khoá tự nhiên, không phải id nội bộ) — viết độc lập với service."""
    o, p = i["owner_account_id"], i.get("platform")
    tail = f"|{i['revision']}|{1 if i['removed'] else 0}"
    if kind == "resources":
        return f"{o}|{p}|{i['resource_type']}|{i['external_id']}{tail}"
    base = f"{o}|{p}|{i['resource_type']}|{i['resource_external_id']}"
    if kind == "content":
        m = i["metrics"]
        return f"{base}|{i['external_id']}{tail}|{_n(m.get('views'))},{_n(m.get('reactions'))},{_n(m.get('comments'))}"
    if kind == "metrics":
        return f"{base}|{i['date']}{tail}|{_n(i['metrics'].get('views'))}"
    return f"{base}|{i['external_id']}{tail}"


def checksum(kind: str, items: list[dict]) -> str:
    return hashlib.sha256("\n".join(sorted(_line(kind, i) for i in items)).encode()).hexdigest()


async def test_summary_counts_and_checksum_let_a_consumer_reconcile(env):
    tool = await env.tool()
    empty = (await env.http.get("/internal/v1/canonical/summary", headers=KEY)).json()
    assert empty["head_revision"] == 0 and empty["counts"]["resources"] == {"total": 0, "active": 0}
    assert empty["checksum"]["resources"] == hashlib.sha256(b"").hexdigest()
    await ingest_full(tool, resources=[res("r1"), res("r2")], content=[post("p1"), post("p2")],
                      metrics=[daily("r1", "2026-10-01")], events=[event("e1")])
    await ingest_full(tool, resources=[res("r1", obs=5)], content=[post("p1", obs=5, views=7)], obs=5)
    s = (await env.http.get("/internal/v1/canonical/summary", headers=KEY)).json()
    for kind in ("resources", "content", "metrics", "events"):
        items = (await canonical(env, kind))["items"]
        assert s["counts"][kind] == {"total": len(items), "active": len([i for i in items if not i["removed"]])}, kind
        assert s["checksum"][kind] == checksum(kind, items), kind
    every = []
    for k in ("resources", "content", "metrics", "events"):
        every += [i["revision"] for i in (await canonical(env, k))["items"]]
    assert s["head_revision"] == max(every)
    # đổi dữ liệu => checksum đổi (Analytics phát hiện lệch)
    await ingest_full(tool, resources=[res("r1", obs=9, display_name="moi")], obs=9, type="delta")
    s2 = (await env.http.get("/internal/v1/canonical/summary", headers=KEY)).json()
    assert s2["checksum"]["resources"] != s["checksum"]["resources"] and s2["checksum"]["content"] == s["checksum"]["content"]


# ── Không mất sự kiện: khoá advisory + mutation ─────────────────────────────────────

async def ordering_scenario(db) -> tuple[set[str], list[int]]:
    """tx1 cấp seq trước nhưng commit SAU tx2. Consumer đọc theo con trỏ ở giữa. Trả (id đã thấy, danh sách seq)."""
    async with db.connection() as conn:
        await conn.execute("TRUNCATE outbox RESTART IDENTITY")
    seen: dict[str, int] = {}
    cursor = 0

    async def poll():
        nonlocal cursor
        async with db.connection() as conn:
            rows = await outbox.read(conn, after=cursor, limit=100)
        for r in rows:
            seen[r["data"]["tag"]] = r["seq"]
            cursor = max(cursor, r["seq"])

    holder = await db.pool.acquire()
    tx1 = holder.transaction()
    await tx1.start()
    await outbox.append(holder, type="t", subject="s", data={"tag": "A"})          # seq=1, CHƯA commit

    async def second():
        async with db.transaction() as c2:
            await outbox.append(c2, type="t", subject="s", data={"tag": "B"})      # seq=2

    task = asyncio.create_task(second())
    await asyncio.sleep(0.4)          # có khoá: B đang chờ A; không khoá: B đã commit xong
    await poll()                      # consumer đọc "ở giữa"
    await tx1.commit()
    await db.pool.release(holder)
    await task
    await poll()                      # đọc tiếp từ con trỏ
    return set(seen), sorted(seen.values())


async def test_consumer_never_misses_an_event_when_writers_interleave(db):
    seen, seqs = await ordering_scenario(db)
    assert seen == {"A", "B"}, "consumer đã MẤT sự kiện: seq không theo thứ tự commit"
    assert seqs == [1, 2]


async def test_mutation_without_the_advisory_lock_the_scenario_loses_an_event(db, monkeypatch):
    """Test của test: gỡ khoá thứ tự commit => kịch bản trên PHẢI làm mất sự kiện (nghĩa là test chính sẽ ĐỎ)."""
    async def no_lock(conn):
        return None
    monkeypatch.setattr(outbox, "acquire_order_lock", no_lock)
    seen, _ = await ordering_scenario(db)
    assert seen != {"A", "B"}, "bỏ khoá mà không mất sự kiện => test không phát hiện được lỗi"
    assert seen == {"B"}              # A (seq nhỏ, commit muộn) bị bỏ lỡ vĩnh viễn


async def revision_scenario(db) -> set[str]:
    """Tương tự cho `revision` canonical: consumer kéo theo after_revision không được bỏ lỡ bản ghi."""
    from integration_service.feed import canonical
    async with db.connection() as conn:
        await conn.execute("TRUNCATE resources, outbox RESTART IDENTITY CASCADE")
        await conn.execute("ALTER SEQUENCE canonical_revision_seq RESTART")
    seen: set[str] = set()
    cursor = 0

    async def insert(conn, key):
        (rev,) = await outbox.next_revisions(conn, 1)
        await conn.execute("INSERT INTO resources (id, owner_account_id, client_id, platform, resource_type, external_id, state, "
                           "observed_at, data, revision, created_at, updated_at) VALUES ($1,'o','c','facebook','page',$2,'active',"
                           "now(),'{}'::jsonb,$3,now(),now())", "res_" + key, key, rev)

    async def poll():
        nonlocal cursor
        async with db.connection() as conn:
            page = await canonical.export(conn, "resources", cursor, 100)
        seen.update(i["external_id"] for i in page["items"])
        cursor = page["next_revision"]

    holder = await db.pool.acquire()
    tx1 = holder.transaction()
    await tx1.start()
    await insert(holder, "a")

    async def second():
        async with db.transaction() as c2:
            await insert(c2, "b")

    task = asyncio.create_task(second())
    await asyncio.sleep(0.4)
    await poll()
    await tx1.commit()
    await db.pool.release(holder)
    await task
    await poll()
    return seen


async def test_canonical_revision_consumer_never_misses_a_row(db):
    assert await revision_scenario(db) == {"a", "b"}


async def test_mutation_without_the_lock_revision_scenario_loses_a_row(db, monkeypatch):
    async def no_lock(conn):
        return None
    monkeypatch.setattr(outbox, "acquire_order_lock", no_lock)
    assert await revision_scenario(db) == {"b"}


async def test_concurrent_writers_with_a_live_consumer_lose_nothing(env):
    """Nhiều Tool commit đồng thời trong lúc một consumer liên tục kéo feed + canonical theo con trỏ."""
    tools = [await env.tool() for _ in range(4)]
    prepared = []
    for i, t in enumerate(tools):
        for j in range(3):
            sid = await new_snapshot(t, type="delta", expected={"resources": 3})
            await push(t, sid, "resources", [res(f"t{i}j{j}r{k}") for k in range(3)])
            prepared.append((t, sid))
    stop = asyncio.Event()
    got_events: dict[int, str] = {}
    got_resources: dict[str, int] = {}

    async def consumer():
        ev_cursor = rev_cursor = 0
        while True:
            finished = stop.is_set()          # đọc lượt cuối SAU khi writer xong
            page = await feed(env, after=ev_cursor, limit=7)
            for e in page["events"]:
                got_events[e["seq"]] = e["id"]
            ev_cursor = page["next_cursor"]
            cpage = await canonical(env, "resources", after=rev_cursor, limit=5)
            for i in cpage["items"]:
                got_resources[i["id"]] = i["revision"]
            rev_cursor = cpage["next_revision"]
            if finished and not page["has_more"] and not cpage["has_more"]:
                return
            await asyncio.sleep(0)

    async def writers():
        rs = await asyncio.gather(*[t.post(f"/v1/integrations/snapshots/{sid}/commit", {}) for t, sid in prepared])
        stop.set()
        return rs

    task = asyncio.create_task(consumer())
    rs = await writers()
    await asyncio.wait_for(task, 30)
    assert all(r.status_code == 200 for r in rs)
    db_events = {r["seq"]: r["id"] for r in await env.rows("SELECT seq, id FROM outbox")}
    assert got_events == db_events and len(db_events) == 16   # 12 snapshot.committed + 4 integration.client.provisioned
    db_res = {r["id"]: r["revision"] for r in await env.rows("SELECT id, revision FROM resources")}
    assert got_resources == db_res and len(db_res) == 36
