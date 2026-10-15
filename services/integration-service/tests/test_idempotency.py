"""Idempotency-Key: cùng key + cùng body => phát lại ĐÚNG phản hồi cũ (không ghi lần 2); khác body => 409."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import FEED_KEY_ANALYTICS, ts
from helpers import canonical, commit, new_snapshot, post, push, res

pytestmark = pytest.mark.needs_pg
S = "/v1/integrations/snapshots"
RB = "/v1/integrations/resources/batch"


def key() -> str:
    return "k-" + uuid.uuid4().hex


def snap_body(**over):
    return {"platform": "facebook", "snapshot_type": "delta", "observed_at": ts(), "report_timezone": "UTC",
            "expected_counts": {}, **over}


async def head(env) -> int:
    return (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()["head_revision"]


async def test_same_key_same_body_replays_exact_response_without_second_write(env):
    tool = await env.tool()
    k = key()
    first = await tool.post(S, snap_body(), idem=k)
    second = await tool.post(S, snap_body(), idem=k)
    assert first.status_code == second.status_code == 201
    assert second.content == first.content                                  # từng byte, kể cả snapshot_id và request_id
    assert second.headers.get("idempotent-replayed") == "true" and "idempotent-replayed" not in first.headers
    assert await env.scalar("SELECT count(*) FROM snapshots") == 1


async def test_replay_does_not_write_again_for_batches_projects_and_commit(env):
    tool = await env.tool()
    body = {"items": [{"platform": "facebook", "resource_type": "page", "external_id": "r1", "observed_at": ts()}]}
    k = key()
    a = await tool.post(RB, body, idem=k)
    h1 = await head(env)
    b = await tool.post(RB, body, idem=k)
    assert b.content == a.content and a.json()["data"]["created"] == 1       # replay vẫn nói "created=1", không phải "unchanged"
    assert await head(env) == h1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE type = 'resource.changed'") == 1

    pk = key()
    p1 = await tool.put("/v1/integrations/projects/itl", {"name": "A"}, idem=pk)
    p2 = await tool.put("/v1/integrations/projects/itl", {"name": "A"}, idem=pk)
    assert p2.content == p1.content and p1.json()["data"]["created"] is True       # lần 2 thật sự sẽ là created=False

    sid = await new_snapshot(tool, type="delta", idem=key())
    bk = key()
    r1 = await tool.post(f"{S}/{sid}/resources", {"items": [res("x1")]}, idem=bk)
    r2 = await tool.post(f"{S}/{sid}/resources", {"items": [res("x1")]}, idem=bk)
    assert r2.content == r1.content and r1.json()["data"]["created"] == 1
    ck = key()
    c1 = await tool.post(f"{S}/{sid}/commit", {}, idem=ck)
    c2 = await tool.post(f"{S}/{sid}/commit", {}, idem=ck)
    assert c1.status_code == c2.status_code == 200 and c2.content == c1.content
    assert await env.scalar("SELECT count(*) FROM outbox WHERE type = 'snapshot.committed'") == 1
    # retry do timeout sau khi snapshot đã commit vẫn nhận lại phản hồi cũ, không phải lỗi
    late = await tool.post(f"{S}/{sid}/resources", {"items": [res("x1")]}, idem=bk)
    assert late.status_code == 200 and late.content == r1.content


async def test_same_key_different_body_is_a_conflict_and_changes_nothing(env):
    tool = await env.tool()
    k = key()
    first = await tool.post(S, snap_body(), idem=k)
    other = await tool.post(S, snap_body(report_timezone="Asia/Tokyo"), idem=k)
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await env.scalar("SELECT count(*) FROM snapshots") == 1
    again = await tool.post(S, snap_body(), idem=k)                          # bản gốc vẫn phát lại được
    assert again.content == first.content
    # khác độ dài/khoảng trắng cũng là "khác body" (so theo sha256 của byte thật)
    spaced = await tool.post(S, content=b" " + first.request.content, idem=k,
                             headers={"Content-Type": "application/json"})
    assert spaced.status_code == 409


async def test_commit_conflict_on_changed_options(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    k = key()
    assert (await tool.post(f"{S}/{sid}/commit", {"accept_partial": False}, idem=k)).status_code == 200
    r = await tool.post(f"{S}/{sid}/commit", {"accept_partial": True}, idem=k)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # không key hoặc key khác: trạng thái snapshot quyết định
    for kw in ({}, {"idem": key()}):
        r = await tool.post(f"{S}/{sid}/commit", {"accept_partial": False}, **kw)
        assert r.status_code == 409 and r.json()["error"]["code"] == "SNAPSHOT_ALREADY_COMMITTED"


async def test_keys_are_scoped_by_client_method_and_path(env):
    a, b = await env.tool(), await env.tool()
    k = key()
    ra = await a.post(S, snap_body(), idem=k)
    rb_ = await b.post(S, snap_body(), idem=k)                                # client khác: độc lập
    assert ra.json()["data"]["snapshot_id"] != rb_.json()["data"]["snapshot_id"] and rb_.status_code == 201
    sid = ra.json()["data"]["snapshot_id"]
    other_path = await a.post(f"{S}/{sid}/resources", {"items": [res("r")]}, idem=k)   # path khác: độc lập
    assert other_path.status_code == 200 and "idempotent-replayed" not in other_path.headers


async def test_failed_requests_are_not_stored_so_a_fixed_retry_works(env):
    tool = await env.tool()
    k = key()
    bad = await tool.post(S, {"platform": "facebook"}, idem=k)
    assert bad.status_code == 422
    good = await tool.post(S, snap_body(), idem=k)                           # cùng key, body đã sửa => chạy bình thường
    assert good.status_code == 201 and "idempotent-replayed" not in good.headers
    denied = await tool.post(S, snap_body(platform="threads"), idem=key())   # platform ngoài registry
    assert denied.status_code == 403
    assert await env.scalar("SELECT count(*) FROM idempotency_keys") == 1


async def test_invalid_key_format_is_rejected(env):
    tool = await env.tool()
    for bad in ("short", "x" * 129, "co khoang trang 1", "a;b;c;d;e;f;g;h"):
        r = await tool.post(S, snap_body(), idem=bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED", bad
    assert await env.scalar("SELECT count(*) FROM snapshots") == 0


async def test_concurrent_identical_requests_execute_exactly_once(env):
    tool = await env.tool()
    k = key()
    rs = await asyncio.gather(*[tool.post(S, snap_body(), idem=k) for _ in range(8)])
    assert {r.status_code for r in rs} == {201}
    assert len({r.content for r in rs}) == 1
    assert await env.scalar("SELECT count(*) FROM snapshots") == 1
    rs = await asyncio.gather(*[tool.post(S, snap_body(), idem=key()) for _ in range(5)])
    assert len({r.json()["data"]["snapshot_id"] for r in rs}) == 5


async def test_concurrent_same_key_different_bodies_one_wins_rest_conflict(env):
    tool = await env.tool()
    k = key()
    bodies = [snap_body(report_timezone=tz) for tz in ("UTC", "Asia/Tokyo", "Europe/Paris", "Asia/Seoul")]
    rs = await asyncio.gather(*[tool.post(S, b, idem=k) for b in bodies])
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409, 409, 409]
    assert await env.scalar("SELECT count(*) FROM snapshots") == 1


async def test_idempotency_rows_expire_and_key_becomes_reusable(env, clock):
    from integration_service import maintenance
    tool = await env.tool()
    k = key()
    first = await tool.post(S, snap_body(), idem=k)
    clock.advance(hours=25)
    await tool.post(S, snap_body(), idem=key())            # request khác để bảo đảm client còn hoạt động
    purged = (await maintenance.run_once(env.http._transport.app.state.container))["idempotency_purged"]
    assert purged >= 1
    third = await tool.post(S, snap_body(), idem=k)
    assert third.status_code == 201 and third.json()["data"]["snapshot_id"] != first.json()["data"]["snapshot_id"]


async def test_replayed_commit_and_canonical_are_consistent(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta", expected={"resources": 1, "content": 1})
    await push(tool, sid, "resources", [res("r1")])
    await push(tool, sid, "content", [post("p1", views=3)])
    k = key()
    out = await commit(tool, sid, idem=k)
    again = await tool.post(f"{S}/{sid}/commit", None, idem=k)
    assert again.status_code == 200 and again.json()["data"] == out
    assert len((await canonical(env, "content"))["items"]) == 1
