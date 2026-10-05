"""Commit atomic: lỗi chèn GIỮA commit => canonical không đổi một bit; commit/ghi đồng thời không vỡ thứ tự."""

from __future__ import annotations

import asyncio

import pytest
from conftest import FEED_KEY_ANALYTICS
from helpers import FULL_SCOPE, canonical, commit, daily, event, ingest_full, new_snapshot, post, push, res

from integration_service.feed import events as feed_events
from integration_service.ingest import merge
from integration_service.snapshots import repository as snap_repo

pytestmark = pytest.mark.needs_pg
S = "/v1/integrations/snapshots"


async def fingerprint(env) -> dict:
    """Ảnh chụp toàn bộ trạng thái canonical + outbox + con trỏ current."""
    tables = ("projects", "resources", "resource_project_bindings", "content_items", "daily_metrics", "activity_events")
    snap: dict = {}
    for t in tables:
        snap[t] = await env.rows(f"SELECT * FROM {t} ORDER BY 1, 2")
    snap["outbox"] = await env.rows("SELECT seq, id, type FROM outbox ORDER BY seq")
    snap["current"] = await env.rows("SELECT id FROM snapshots WHERE is_current ORDER BY id")
    return snap


async def baseline(env, tool):
    await ingest_full(tool, resources=[res("r1", display_name="V1"), res("r2")],
                      content=[post("p1", views=100), post("p2", views=5)],
                      metrics=[daily("r1", "2026-10-01", views=1)], events=[event("e1")], obs=0)


async def second_snapshot(env, tool):
    """Snapshot sẽ sửa MỌI loại dữ liệu và xoá bớt (nếu commit được)."""
    sid = await new_snapshot(tool, obs=30, scope=FULL_SCOPE,
                             expected={"resources": 1, "content": 1, "metrics": 1, "events": 1})
    await push(tool, sid, "resources", [res("r1", obs=30, display_name="V2")])
    await push(tool, sid, "content", [post("p1", obs=30, views=9999)])
    await push(tool, sid, "metrics", [daily("r1", "2026-10-01", obs=30, views=2)])
    await push(tool, sid, "events", [event("e1")])
    return sid


@pytest.mark.parametrize("target,attr", [
    ("merge_children", "merge"),           # chen lỗi khi đã merge xong resources + content, đang merge metrics
    ("infer_removed_resources", "merge"),  # lỗi lúc suy luận removed
    ("flip_current", "snap_repo"),         # lỗi lúc lật con trỏ current
    ("snapshot_committed", "feed_events"), # lỗi lúc ghi outbox (bước cuối)
])
async def test_failure_in_the_middle_of_commit_leaves_canonical_untouched(env, monkeypatch, target, attr):
    tool = await env.tool()
    await baseline(env, tool)
    before = await fingerprint(env)
    sid = await second_snapshot(env, tool)

    module = {"merge": merge, "snap_repo": snap_repo, "feed_events": feed_events}[attr]
    original = getattr(module, target)
    calls = {"n": 0}

    async def boom(*a, **kw):
        calls["n"] += 1
        if target == "merge_children" and calls["n"] < 2:   # cho lần 1 (content) chạy thật rồi mới nổ ở lần 2
            return await original(*a, **kw)
        raise RuntimeError("lỗi giả lập giữa commit")

    monkeypatch.setattr(module, target, boom)
    r = await tool.post(f"{S}/{sid}/commit", {})
    assert r.status_code == 500 and r.json()["error"]["code"] == "SERVER_ERROR" and r.json()["ok"] is False
    assert "RuntimeError" not in r.text and "Traceback" not in r.text            # không lộ traceback
    monkeypatch.undo()

    assert await fingerprint(env) == before                                      # canonical + outbox + current: không đổi
    row = (await env.rows("SELECT status, failure_reason FROM snapshots WHERE id = $1", sid))[0]
    assert row["status"] == "failed" and row["failure_reason"].startswith("commit_error:RuntimeError")
    # Tool tạo snapshot mới thì mọi thứ vẫn chạy bình thường
    out = await ingest_full(tool, resources=[res("r1", obs=40, display_name="V3")], obs=40, type="delta")
    assert out["status"] == "committed"
    assert by_id(await canonical(env, "resources"))["r1"]["display_name"] == "V3"


def by_id(page: dict) -> dict:
    return {i["external_id"]: i for i in page["items"]}


async def test_commit_failure_does_not_burn_the_visible_state_even_if_revisions_were_allocated(env, monkeypatch):
    tool = await env.tool()
    await baseline(env, tool)
    head_before = (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()
    sid = await second_snapshot(env, tool)

    async def boom(*a, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(snap_repo, "mark_committed", boom)
    assert (await tool.post(f"{S}/{sid}/commit", {})).status_code == 500
    monkeypatch.undo()
    after = (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()
    assert after == head_before          # head, số lượng và checksum đều y nguyên (revision cấp rồi nhưng rollback)


async def test_database_error_midway_is_retryable_not_failed(env, monkeypatch):
    """Lỗi hạ tầng (DB chập chờn) không được giết snapshot: nó quay về building để Tool commit lại."""
    import asyncpg
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta", expected={"resources": 1})
    await push(tool, sid, "resources", [res("r1")])

    async def down(*a, **kw):
        raise asyncpg.ConnectionDoesNotExistError("mất kết nối")
    monkeypatch.setattr(merge, "merge_resources", down)
    r = await tool.post(f"{S}/{sid}/commit", {})
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
    monkeypatch.undo()
    assert (await env.rows("SELECT status FROM snapshots WHERE id = $1", sid))[0]["status"] == "building"
    assert (await commit(tool, sid))["status"] == "committed"


async def test_double_commit_race_commits_once(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta", expected={"resources": 2})
    await push(tool, sid, "resources", [res("r1"), res("r2")])
    rs = await asyncio.gather(*[tool.post(f"{S}/{sid}/commit", {}) for _ in range(6)])
    assert sorted(r.status_code for r in rs).count(200) == 1
    assert {r.json()["error"]["code"] for r in rs if r.status_code != 200} <= {"SNAPSHOT_ALREADY_COMMITTED",
                                                                              "SNAPSHOT_COMMIT_IN_PROGRESS"}
    assert await env.scalar("SELECT count(*) FROM outbox WHERE type = 'snapshot.committed'") == 1
    assert await env.scalar("SELECT count(*) FROM resources") == 2


async def test_concurrent_commits_keep_revisions_unique_and_ordered_like_the_outbox(env):
    tools = [await env.tool() for _ in range(3)]
    sids = []
    for i, t in enumerate(tools):
        for j in range(2):
            sid = await new_snapshot(t, type="delta", expected={"resources": 8, "content": 8})
            await push(t, sid, "resources", [res(f"t{i}r{k}", display_name=f"{j}") for k in range(8)])
            await push(t, sid, "content", [post(f"t{i}p{k}", res_ext=f"t{i}r{k}") for k in range(8)])
            sids.append((t, sid))
    rs = await asyncio.gather(*[commit_raw(t, sid) for t, sid in sids])
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs if r.status_code != 200]
    revs: list[int] = []
    for kind in ("resources", "content"):
        revs += [i["revision"] for i in (await canonical(env, kind))["items"]]
    assert len(revs) == len(set(revs)) == 48                      # không trùng revision
    events = await env.rows("SELECT seq, data FROM outbox WHERE type = 'snapshot.committed' ORDER BY seq")
    ranges = [(e["data"]["revision_from"], e["data"]["revision_to"]) for e in events]
    assert ranges == sorted(ranges) and len(ranges) == 6          # thứ tự outbox = thứ tự revision = thứ tự commit
    for (a1, b1), (a2, b2) in zip(ranges, ranges[1:]):
        assert b1 < a2                                            # khoảng revision của các snapshot không đan xen


async def commit_raw(tool, sid):
    return await tool.post(f"{S}/{sid}/commit", {})


async def test_snapshots_building_in_parallel_do_not_interfere_until_commit(env):
    tool = await env.tool()
    a = await new_snapshot(tool, type="delta", expected={"resources": 1})
    b = await new_snapshot(tool, type="delta", expected={"resources": 1})
    await push(tool, a, "resources", [res("ra", display_name="A")])
    await push(tool, b, "resources", [res("ra", display_name="B", obs=10)])
    assert await env.scalar("SELECT count(*) FROM resources") == 0          # staging không lọt sang canonical
    await commit(tool, b)
    await commit(tool, a)                                                   # a có observed_at cũ hơn b => b thắng
    assert by_id(await canonical(env, "resources"))["ra"]["display_name"] == "B"
