"""Tool dùng chung tự xin job thu thập cho kênh ĐÃ KÉO theo lịch của người vận hành (`POST /jobs/enqueue`) và chỉ nhận job của
các kênh đó (`resource_ids` ở `/jobs/claim`). Server chỉ tự tạo MỘT job khi user thêm kênh — không có hai đường này thì kênh chỉ
được thu đúng một lần, không có số liệu theo ngày."""

from __future__ import annotations

import pytest
from helpers import claim, create_job, fallback_resource
from test_jobs import make_world

pytestmark = pytest.mark.needs_pg
JOBS = "/v1/integrations/jobs"
CAPS = ("data.collect",)


async def enqueue(w, ids, expect: int = 200):
    r = await w.tool.post(f"{JOBS}/enqueue", {"resource_ids": ids})
    assert r.status_code == expect, r.text
    return r.json()["data"] if expect == 200 else r.json()


async def test_enqueue_creates_a_collect_job_owned_by_the_channels_owner_not_by_the_client(env):
    w = await make_world(env, caps=CAPS)
    out = await enqueue(w, [w.rid])
    assert out == {"created": 1, "already_active": 0, "skipped": []}
    job = (await env.rows("SELECT * FROM jobs"))[0]
    assert job["kind"] == "collect" and job["status"] == "pending" and job["required_capability"] == "data.collect"
    assert job["owner_account_id"] == w.user["sub"] and job["resource_owner_id"] == w.user["sub"]   # delegated ingestion
    assert job["dedupe_key"] == f"collect:{w.rid}" and job["created_by"] == w.tool.client_id
    got = await claim(w.tool, w.ins, capabilities=list(CAPS))
    assert [g["job_id"] for g in got] == [job["id"]] and got[0]["resource"]["resource_id"] == w.rid


async def test_calling_again_while_a_job_is_active_does_not_pile_up_jobs(env):
    w = await make_world(env, caps=CAPS)
    await enqueue(w, [w.rid])
    assert await enqueue(w, [w.rid, w.rid]) == {"created": 0, "already_active": 1, "skipped": []}   # lặp id trong đợt cũng gộp
    assert await env.scalar("SELECT count(*) FROM jobs") == 1


async def test_next_day_run_enqueues_again_once_the_previous_job_is_finished(env):
    """Đây là cơ chế cho số liệu HẰNG NGÀY: job xong thì đợt thu thập tiếp theo tạo job mới cho cùng kênh."""
    w = await make_world(env, caps=CAPS)
    await enqueue(w, [w.rid])
    job = (await claim(w.tool, w.ins, capabilities=list(CAPS)))[0]
    done = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}, "lease_token": job["lease_token"]})
    assert done.status_code == 200
    assert await enqueue(w, [w.rid]) == {"created": 1, "already_active": 0, "skipped": []}
    assert await env.scalar("SELECT count(*) FROM jobs") == 2


async def test_skipped_channels_are_reported_with_a_reason_and_do_not_break_the_batch(env):
    w = await make_world(env, caps=CAPS)
    personal = await fallback_resource(env, w.owner_tool, "dung-tool-rieng", mode="primary_tool")
    disabled = await fallback_resource(env, w.owner_tool, "da-tat", state="disabled")
    other_platform = await fallback_resource(env, w.owner_tool, "tt1", platform="tiktok")     # key chỉ được facebook
    out = await enqueue(w, ["res_khong_ton_tai", personal, disabled, other_platform, w.rid])
    assert out["created"] == 1 and out["already_active"] == 0
    assert {s["resource_id"]: s["reason"] for s in out["skipped"]} == {
        "res_khong_ton_tai": "NOT_FOUND", personal: "NOT_ELIGIBLE", disabled: "NOT_ELIGIBLE",
        other_platform: "PLATFORM_NOT_ALLOWED"}


async def test_owner_quota_blocks_only_that_channel(make_env):
    e2, _ = await make_env(job_max_pending_per_owner=1)
    w = await make_world(e2, caps=CAPS)
    second = await fallback_resource(e2, w.owner_tool, "fb2")
    out = await enqueue(w, [w.rid, second])
    assert out["created"] == 1 and out["skipped"] == [{"resource_id": second, "reason": "LIMIT_EXCEEDED"}]


async def test_a_client_without_the_system_scope_cannot_enqueue_for_other_owners(env):
    w = await make_world(env, caps=CAPS, system=False)
    r = await enqueue(w, [w.rid], expect=403)
    assert r["error"]["code"] == "SCOPE_DENIED"
    assert await env.scalar("SELECT count(*) FROM jobs") == 0


@pytest.mark.parametrize("body", [{"resource_ids": []}, {"resource_ids": ["r"] * 201}, {}, {"resource_ids": ["a"], "owner": "x"}])
async def test_enqueue_rejects_bad_bodies(env, body):
    w = await make_world(env, caps=CAPS)
    r = await w.tool.post(f"{JOBS}/enqueue", body)
    assert r.status_code == 422 and await env.scalar("SELECT count(*) FROM jobs") == 0


async def test_claim_with_resource_ids_only_returns_jobs_of_those_channels(env):
    w = await make_world(env, caps=CAPS)
    other = await fallback_resource(env, w.owner_tool, "fb-khac")
    await create_job(env, w.user, w.rid)
    await create_job(env, w.user, other)
    got = await claim(w.tool, w.ins, capabilities=list(CAPS), resource_ids=[other])
    assert [g["resource"]["resource_id"] for g in got] == [other]
    rest = await claim(w.tool, w.ins, capabilities=list(CAPS))                    # không lọc => nhận phần còn lại
    assert [g["resource"]["resource_id"] for g in rest] == [w.rid]
