"""Module jobs: tạo (USER), claim/heartbeat/complete/fail (MÁY), lease, retry -> dead, ownership, tranh chấp, lỗi hạ tầng."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from conftest import ts
from helpers import claim, create_job, fallback_resource, handshake
from integration_service.clients import policy
from integration_service.clients.policy import SYSTEM_SCOPE

pytestmark = pytest.mark.needs_pg
JOBS = "/v1/integrations/jobs"


class World:
    """Một owner, một Fallback Tool đã handshake, một resource ở chế độ fallback_tool."""

    def __init__(self, env, user, tool, ins, rid, owner_tool):
        self.env, self.user, self.tool, self.ins, self.rid = env, user, tool, ins, rid
        self.owner_tool = owner_tool   # tool cá nhân của `user` — dùng để seed resource (ghi dữ liệu không qua delegation)

    async def job(self, **body) -> dict:
        return await create_job(self.env, self.user, self.rid, **body)

    async def state(self, job_id: str) -> dict:
        return (await self.env.rows("SELECT * FROM jobs WHERE id = $1", job_id))[0]


async def make_world(env, *, platforms=("facebook",), caps=("jobs.pull",), ext="fb1", system: bool = True) -> World:
    """`system=True` (mặc định): Fallback Tool có `jobs:claim:system` (đúng vai trò claim job của MỌI owner, vì
    client dùng chung thuộc về admin tạo ra nó, không thuộc về `user`). Test cố ý không có system scope
    (vd `test_system_scope_claims_across_owners_but_job_owner_stays_server_assigned`) truyền `system=False`."""
    user = await env.verified_user()
    owner_tool = await env.tool(user=user)   # client cá nhân của user: seed resource không bị chặn bởi delegation
    scopes = policy.default_scopes("fallback_tool") + ([SYSTEM_SCOPE] if system else [])
    tool = await env.tool(kind="fallback_tool", platforms=platforms, user=user, handshake=False, scopes=scopes)
    hs = await env.handshake(tool.api_key, platforms=platforms, capabilities=list(caps))
    tool.installation_id, tool.device_id = hs["installation_id"], hs["device_id"]
    ins = tool.installation_id
    rid = await fallback_resource(env, owner_tool, ext)
    return World(env, user, tool, ins, rid, owner_tool)


@pytest.fixture
async def w(env) -> World:
    return await make_world(env)


# ── Tạo job (mặt phẳng USER) ─────────────────────────────────────────────────────────

async def test_user_creates_job_for_own_fallback_resource(w):
    job = await w.job(payload={"mode": "refresh"}, priority=5)
    assert job["status"] == "pending" and job["owner_account_id"] == w.user["sub"] and job["platform"] == "facebook"
    assert job["attempts"] == 0 and job["max_attempts"] == 3 and job["created"] is True
    r = await w.env.http.get(f"{JOBS}/{job['job_id']}", headers=w.user["headers"])
    assert r.status_code == 200 and r.json()["data"]["payload"] == {"mode": "refresh"}


async def test_create_requires_fallback_mode_active_resource_and_ownership(w, env):
    other_rid = await fallback_resource(env, w.owner_tool, "primary-one", mode="primary_tool")
    r = await create_job(env, w.user, other_rid, expect=409)
    assert r["error"]["code"] == "JOB_NOT_ELIGIBLE"
    off = await fallback_resource(env, w.owner_tool, "disabled-one", state="disabled")
    assert (await create_job(env, w.user, off, expect=409))["error"]["code"] == "JOB_NOT_ELIGIBLE"
    stranger = await env.verified_user()
    assert (await create_job(env, stranger, w.rid, expect=404))["error"]["code"] == "NOT_FOUND"
    assert (await create_job(env, w.user, "res_khong_ton_tai", expect=404))["error"]["code"] == "NOT_FOUND"


async def test_admin_may_create_for_any_owner_but_owner_is_the_resource_owner(w, env):
    admin = await env.verified_user(role="admin")
    job = await create_job(env, admin, w.rid)
    assert job["owner_account_id"] == w.user["sub"] != admin["sub"]


@pytest.mark.parametrize("extra", [{"owner_account_id": "acc_gia_mao"}, {"owner": "x"}, {"unknown": 1}])
async def test_create_rejects_owner_and_unknown_fields(w, env, extra):
    r = await env.http.post(JOBS, headers=w.user["headers"], json={"resource_id": w.rid, **extra})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


async def test_create_needs_user_token_and_machine_key_is_refused(w, env):
    assert (await env.http.post(JOBS, json={"resource_id": w.rid})).status_code == 401
    r = await env.http.post(JOBS, headers={"Authorization": f"Bearer {w.tool.api_key}"}, json={"resource_id": w.rid})
    assert r.status_code == 401


async def test_dedupe_key_returns_existing_active_job(w):
    first = await w.job(dedupe_key="refresh-1")
    second = await create_job(w.env, w.user, w.rid, expect=200, dedupe_key="refresh-1")
    assert second["job_id"] == first["job_id"] and second["created"] is False
    assert await w.env.scalar("SELECT count(*) FROM jobs") == 1


async def test_pending_quota_per_owner(env, settings, make_env):
    import dataclasses  # noqa: F401
    e2, _ = await make_env(job_max_pending_per_owner=2)
    w2 = await make_world(e2)
    await w2.job()
    await w2.job()
    assert (await create_job(e2, w2.user, w2.rid, expect=409))["error"]["code"] == "LIMIT_EXCEEDED"


async def test_list_is_scoped_to_owner_and_admin_sees_all(w, env):
    job = await w.job()
    other = await make_world(env, ext="zz")
    await other.job()
    mine = (await env.http.get(JOBS, headers=w.user["headers"])).json()["data"]["jobs"]
    assert [j["job_id"] for j in mine] == [job["job_id"]]
    admin = await env.verified_user(role="dash_admin")
    assert len((await env.http.get(JOBS, headers=admin["headers"])).json()["data"]["jobs"]) == 2
    assert (await env.http.get(f"{JOBS}/{job['job_id']}", headers=other.user["headers"])).status_code == 404
    spoof = await env.http.get(JOBS, params={"owner": w.user["sub"]}, headers=other.user["headers"])
    assert spoof.json()["data"]["jobs"] == []


# ── Claim ───────────────────────────────────────────────────────────────────────────

async def test_claim_gives_job_with_lease_and_resource(w):
    job = await w.job()
    jobs = await claim(w.tool, w.ins, lease_seconds=120)
    assert [j["job_id"] for j in jobs] == [job["job_id"]]
    got = jobs[0]
    assert got["attempt"] == 1 and got["lease_token"] and got["resource"]["external_id"] == "fb1"
    assert got["owner_account_id"] == w.user["sub"]
    row = await w.state(job["job_id"])
    assert row["status"] == "leased" and row["claimed_by_client"] == w.tool.client_id
    assert (row["lease_until"] - w.env.clock.t).total_seconds() == 120
    assert await claim(w.tool, w.ins) == []   # đã giao, không giao lại


async def test_claim_respects_limit_priority_and_lease_cap(w):
    low = await w.job(priority=0)
    high = await w.job(priority=10)
    first = await claim(w.tool, w.ins, limit=1)
    assert [j["job_id"] for j in first] == [high["job_id"]]
    assert [j["job_id"] for j in await claim(w.tool, w.ins)] == [low["job_id"]]
    r = await w.tool.post(f"{JOBS}/claim", {"installation_id": w.ins, "lease_seconds": 901})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED"
    r = await w.tool.post(f"{JOBS}/claim", {"installation_id": w.ins, "limit": 0})
    assert r.status_code == 422


async def test_claim_never_returns_other_owners_jobs(env):
    a, b = await make_world(env, ext="a"), await make_world(env, ext="b")
    ja, jb = await a.job(), await b.job()
    assert [j["job_id"] for j in await claim(a.tool, a.ins, limit=1)] == [ja["job_id"]]
    assert [j["job_id"] for j in await claim(b.tool, b.ins, limit=1)] == [jb["job_id"]]
    assert (await b.state(ja["job_id"]))["claimed_by_client"] == a.tool.client_id


async def test_claim_rejects_owner_fields_and_foreign_installation(env):
    a, b = await make_world(env, ext="a"), await make_world(env, ext="b")
    await a.job()
    r = await a.tool.post(f"{JOBS}/claim", {"installation_id": a.ins, "owner_account_id": b.user["sub"]})
    assert r.status_code == 422
    r = await a.tool.post(f"{JOBS}/claim", {"installation_id": b.ins, "platforms": ["facebook"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSTALLATION_MISMATCH"   # installation của client khác
    r = await a.tool.post(f"{JOBS}/claim", {"installation_id": "ins_khong_hop_le"})
    assert r.status_code == 422


SCOPES = ["integration:handshake", "resources:write", "snapshots:write", "metrics:write", "jobs:claim", "jobs:complete"]


async def issue_sibling(env, w: World, *, device: str | None = None, caps=("jobs.pull",)):
    """Cấp credential THỨ HAI cho CÙNG client (owner không đổi), handshake với thiết bị khác — mô phỏng
    nhiều worker của cùng một Fallback Tool (1 credential : 1 thiết bị)."""
    admin = await env.verified_user(role="admin")
    r = await env.http.post(f"/v1/integrations/clients/{w.tool.client_id}/credentials", headers=admin["headers"], json={})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    sib = type(w.tool)(http=env.http, client_id=w.tool.client_id, owner=w.tool.owner, key_id=d["key_id"],
                       api_key=d["api_key"], scopes=d["scopes"])
    sib.installation_id = await handshake(sib, caps=caps, device=device)
    return sib


async def sibling_client(env, w: World, *, device: str | None = None):
    """Client DÙNG CHUNG KHÁC (client_id khác) nhưng CÙNG owner (admin) với `w.tool` — dùng để kiểm tra
    lease chỉ thuộc về đúng client_id, kể cả khi chung owner và cùng có system scope."""
    admin = env.user(sub=w.tool.owner, role="admin")
    r = await env.http.post("/v1/integrations/clients", headers=admin["headers"], json={
        "tool_name": "Sibling-" + (device or "x"), "client_kind": "fallback_tool",
        "purpose": "sibling client fixture", "platforms": ["facebook"]})
    assert r.status_code == 201, r.text
    client_id = r.json()["data"]["client_id"]
    scopes = policy.default_scopes("fallback_tool") + [SYSTEM_SCOPE]
    r = await env.http.put(f"/v1/integrations/clients/{client_id}/config", headers=admin["headers"],
                           json={"allowed_platforms": ["facebook"], "allowed_scopes": scopes})
    assert r.status_code == 200, r.text
    tool_name = r.json()["data"]["tool_name"]
    r = await env.http.post(f"/v1/integrations/clients/{client_id}/activate", headers=admin["headers"],
                            json={"confirm_tool_name": tool_name})
    assert r.status_code == 200, r.text
    r = await env.http.post(f"/v1/integrations/clients/{client_id}/credentials", headers=admin["headers"], json={})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    sib = type(w.tool)(http=env.http, client_id=d["client_id"], owner=w.tool.owner, key_id=d["key_id"],
                       api_key=d["api_key"], scopes=d["scopes"])
    sib.installation_id = await handshake(sib, device=device)
    return sib


async def grant_system(env, w: World):
    """Admin cấp `jobs:claim:system` cho client rồi cấp credential MỚI (credential cũ không tự có scope mới),
    handshake credential mới với thiết bị khác (1 credential : 1 thiết bị)."""
    admin = await env.verified_user(role="admin")
    r = await env.http.patch(f"/v1/integrations/clients/{w.tool.client_id}", headers=admin["headers"],
                             json={"allowed_scopes": SCOPES + ["jobs:claim:system"]})
    assert r.status_code == 200, r.text
    r = await env.http.post(f"/v1/integrations/clients/{w.tool.client_id}/credentials", headers=admin["headers"], json={})
    d = r.json()["data"]
    assert "jobs:claim:system" in d["scopes"]
    sys_tool = type(w.tool)(http=env.http, client_id=w.tool.client_id, owner=w.tool.owner, key_id=d["key_id"],
                            api_key=d["api_key"], scopes=d["scopes"])
    sys_tool.installation_id = await handshake(sys_tool, device="sys-device")
    return sys_tool, admin


async def test_system_scope_claims_across_owners_but_job_owner_stays_server_assigned(env):
    a, b = await make_world(env, ext="a", system=False), await make_world(env, ext="b")
    jb = await b.job()
    assert await claim(a.tool, a.ins) == []   # credential thường: không thấy job của owner khác
    sys_tool, _ = await grant_system(env, a)
    got = await claim(sys_tool, sys_tool.installation_id)
    assert [j["job_id"] for j in got] == [jb["job_id"]] and got[0]["owner_account_id"] == b.user["sub"]
    r = await sys_tool.post(f"{JOBS}/{jb['job_id']}/complete", {"result": {}})
    assert r.status_code == 200
    assert (await b.state(jb["job_id"]))["owner_account_id"] == b.user["sub"]


async def test_dropping_system_scope_stops_access_to_foreign_job(env):
    a, b = await make_world(env, ext="a"), await make_world(env, ext="b")
    jb = await b.job()
    sys_tool, admin = await grant_system(env, a)
    assert [j["job_id"] for j in await claim(sys_tool, sys_tool.installation_id)] == [jb["job_id"]]
    await env.http.patch(f"/v1/integrations/clients/{a.tool.client_id}", headers=admin["headers"],
                         json={"allowed_scopes": SCOPES})
    r = await sys_tool.post(f"{JOBS}/{jb['job_id']}/complete", {"result": {}})
    assert r.status_code == 404   # giữ lease từ trước nhưng đã mất quyền hệ thống: như không tồn tại
    assert (await b.state(jb["job_id"]))["status"] == "leased"


async def test_platform_scope_on_claim(env):
    w2 = await make_world(env, platforms=("facebook", "youtube"))
    await w2.job()
    r = await w2.tool.post(f"{JOBS}/claim", {"installation_id": w2.ins, "platforms": ["tiktok"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    assert await claim(w2.tool, w2.ins, platforms=["youtube"]) == []   # job là của facebook
    assert len(await claim(w2.tool, w2.ins, platforms=["facebook"])) == 1


async def test_capability_negotiation_is_declared_and_installed_intersection(env):
    w2 = await make_world(env, caps=("jobs.pull", "adapter.alpha"))
    job = await w2.job(required_capability="adapter.alpha")
    assert await claim(w2.tool, w2.ins, capabilities=["jobs.pull"]) == []            # claim không khai
    other = await issue_sibling(env, w2, device="other-device", caps=("jobs.pull",))
    assert await claim(other, other.installation_id, capabilities=["jobs.pull", "adapter.alpha"]) == []   # installation không có
    got = await claim(w2.tool, w2.ins, capabilities=["jobs.pull", "adapter.alpha"])
    assert [j["job_id"] for j in got] == [job["job_id"]]


async def test_claim_needs_jobs_scope(env):
    user = await env.verified_user()
    primary = await env.tool(kind="primary_tool", user=user, handshake=False)
    ins = await handshake(primary, caps=())
    r = await primary.post(f"{JOBS}/claim", {"installation_id": ins})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    assert (await env.http.post(f"{JOBS}/claim", json={"installation_id": ins})).status_code == 401


# ── Complete / fail ─────────────────────────────────────────────────────────────────

async def test_complete_is_idempotent_and_stores_result(w):
    job = await w.job()
    await claim(w.tool, w.ins)
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"fetched": 7}}, idem=True)
    assert r.status_code == 200 and r.json()["data"]["status"] == "done" and r.json()["data"]["replayed"] is False
    again = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"fetched": 999}})
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    row = await w.state(job["job_id"])
    assert row["status"] == "done" and row["result"] == {"fetched": 7} and row["lease_until"] is None


async def test_complete_same_idempotency_key_replays_exact_response(w):
    job = await w.job()
    await claim(w.tool, w.ins)
    key = "idem-job-complete-0001"
    first = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}}, idem=key)
    second = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}}, idem=key)
    assert second.status_code == 200 and second.headers["Idempotent-Replayed"] == "true" and second.content == first.content
    conflict = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"x": 1}}, idem=key)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_complete_unknown_unclaimed_and_malformed(w):
    job = await w.job()
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_INVALID_STATE"   # chưa claim
    for bad in ("job_" + "0" * 32, "khong-phai-id"):
        r = await w.tool.post(f"{JOBS}/{bad}/complete", {"result": {}})
        assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"
    await claim(w.tool, w.ins)
    big = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"blob": "x" * 70_000}})
    assert big.status_code == 422
    assert (await w.state(job["job_id"]))["status"] == "leased"


async def test_cross_owner_complete_and_fail_look_like_not_found(env):
    a, b = await make_world(env, ext="a"), await make_world(env, ext="b", system=False)
    ja = await a.job()
    await claim(a.tool, a.ins)
    for path, body in (("complete", {"result": {}}), ("fail", {"error_code": "X"}), ("heartbeat", {})):
        r = await b.tool.post(f"{JOBS}/{ja['job_id']}/{path}", body)
        assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND", path
    assert (await a.state(ja["job_id"]))["status"] == "leased"


async def test_other_client_of_same_owner_cannot_finish_my_lease(w, env):
    job = await w.job()
    await claim(w.tool, w.ins)
    sibling = await sibling_client(env, w, device="sibling-device")
    r = await sibling.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_LEASE_EXPIRED"
    assert (await w.state(job["job_id"]))["status"] == "leased"


async def test_wrong_lease_token_is_refused(w):
    job = await w.job()
    got = (await claim(w.tool, w.ins))[0]
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}, "lease_token": "sai-token"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_LEASE_EXPIRED"
    ok = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}, "lease_token": got["lease_token"]})
    assert ok.status_code == 200


async def test_fail_retryable_backs_off_then_dies_after_max_attempts(w):
    job = await w.job(max_attempts=2)
    jid = job["job_id"]
    await claim(w.tool, w.ins)
    r = await w.tool.post(f"{JOBS}/{jid}/fail", {"error_code": "NET", "message": "timeout", "retryable": True})
    assert r.status_code == 200 and r.json()["data"]["status"] == "pending"
    row = await w.state(jid)
    assert row["last_error_code"] == "NET" and (row["run_after"] - w.env.clock.t).total_seconds() == 30
    assert await claim(w.tool, w.ins) == []   # đang backoff
    w.env.clock.advance(seconds=31)
    assert [j["attempt"] for j in await claim(w.tool, w.ins)] == [2]
    r = await w.tool.post(f"{JOBS}/{jid}/fail", {"error_code": "NET", "retryable": True})
    assert r.json()["data"]["status"] == "dead"
    again = await w.tool.post(f"{JOBS}/{jid}/fail", {"error_code": "NET", "retryable": True})
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    late = await w.tool.post(f"{JOBS}/{jid}/complete", {"result": {}})
    assert late.status_code == 409 and late.json()["error"]["code"] == "JOB_INVALID_STATE"
    w.env.clock.advance(hours=1)
    assert await claim(w.tool, w.ins) == []   # dead không bao giờ quay lại


async def test_fail_not_retryable_goes_failed_immediately(w):
    job = await w.job()
    await claim(w.tool, w.ins)
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/fail", {"error_code": "BAD_TARGET", "retryable": False})
    assert r.json()["data"]["status"] == "failed"
    done = await w.job(dedupe_key="other")
    await claim(w.tool, w.ins)
    await w.tool.post(f"{JOBS}/{done['job_id']}/complete", {"result": {}})
    r = await w.tool.post(f"{JOBS}/{done['job_id']}/fail", {"error_code": "X"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_INVALID_STATE"


async def test_fail_validates_body(w):
    job = await w.job()
    await claim(w.tool, w.ins)
    for body in ({}, {"error_code": ""}, {"error_code": "co khoang trang"}, {"error_code": "X", "retryable": "yes"},
                 {"error_code": "X", "owner_account_id": "y"}):
        r = await w.tool.post(f"{JOBS}/{job['job_id']}/fail", body)
        assert r.status_code == 422, body


# ── Lease ───────────────────────────────────────────────────────────────────────────

async def test_expired_lease_returns_job_to_the_queue_and_old_holder_is_fenced(w, env):
    job = await w.job()
    first = (await claim(w.tool, w.ins, lease_seconds=60))[0]
    assert await claim(w.tool, w.ins) == []
    env.clock.advance(seconds=61)
    second = await claim(w.tool, w.ins)   # lazy reclaim ngay trong claim
    assert [j["job_id"] for j in second] == [job["job_id"]] and second[0]["attempt"] == 2
    assert second[0]["lease_token"] != first["lease_token"]
    stale = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}, "lease_token": first["lease_token"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "JOB_LEASE_EXPIRED"
    ok = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}, "lease_token": second[0]["lease_token"]})
    assert ok.status_code == 200


async def test_complete_after_expiry_but_before_anyone_reclaimed_still_works(w, env):
    job = await w.job()
    await claim(w.tool, w.ins, lease_seconds=60)
    env.clock.advance(seconds=61)
    assert (await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})).status_code == 200


async def test_complete_after_reclaim_without_new_claim_is_lease_expired(w, env):
    from integration_service import maintenance
    job = await w.job()
    await claim(w.tool, w.ins, lease_seconds=60)
    env.clock.advance(seconds=61)
    swept = (await maintenance.run_once(env.http._transport.app.state.container))["jobs"]
    assert swept["requeued"] == [job["job_id"]]
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_LEASE_EXPIRED"
    assert (await w.state(job["job_id"]))["status"] == "pending"


async def test_lease_expiry_with_no_attempts_left_means_dead(w, env):
    from integration_service import maintenance
    job = await w.job(max_attempts=1)
    await claim(w.tool, w.ins, lease_seconds=30)
    env.clock.advance(seconds=31)
    swept = (await maintenance.run_once(env.http._transport.app.state.container))["jobs"]
    assert swept["dead"] == [job["job_id"]]
    row = await w.state(job["job_id"])
    assert row["status"] == "dead" and row["last_error_code"] == "LEASE_EXPIRED" and row["completed_at"] is not None
    assert await claim(w.tool, w.ins) == []


async def test_heartbeat_extends_the_lease_so_it_is_not_reclaimed(w, env):
    job = await w.job()
    await claim(w.tool, w.ins, lease_seconds=60)
    env.clock.advance(seconds=50)
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/heartbeat", {"lease_seconds": 100})
    assert r.status_code == 200 and r.json()["data"]["status"] == "leased"
    env.clock.advance(seconds=60)   # quá hạn lease gốc nhưng chưa quá hạn lease mới
    assert await claim(w.tool, w.ins) == []
    env.clock.advance(seconds=100)
    assert len(await claim(w.tool, w.ins)) == 1   # hết hạn thật sự thì được giao lại


async def test_heartbeat_after_lease_lost_is_refused(w, env):
    job = await w.job()
    await claim(w.tool, w.ins, lease_seconds=60)
    env.clock.advance(seconds=61)
    await claim(w.tool, w.ins)   # chính client này giành lại, token mới
    other = await sibling_client(env, w, device="other-device")
    r = await other.post(f"{JOBS}/{job['job_id']}/heartbeat", {})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_LEASE_EXPIRED"


# ── Huỷ ──────────────────────────────────────────────────────────────────────────────

async def test_cancel_by_owner_blocks_claim_and_late_complete(w, env):
    pending = await w.job()
    r = await env.http.post(f"{JOBS}/{pending['job_id']}/cancel", headers=w.user["headers"])
    assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
    assert (await env.http.post(f"{JOBS}/{pending['job_id']}/cancel", headers=w.user["headers"])).status_code == 200
    assert await claim(w.tool, w.ins) == []
    running = await w.job(dedupe_key="b")
    await claim(w.tool, w.ins)
    await env.http.post(f"{JOBS}/{running['job_id']}/cancel", headers=w.user["headers"])
    late = await w.tool.post(f"{JOBS}/{running['job_id']}/complete", {"result": {}})
    assert late.status_code == 409 and late.json()["error"]["code"] == "JOB_INVALID_STATE"


async def test_cancel_by_stranger_is_not_found_and_done_job_cannot_be_cancelled(w, env):
    job = await w.job()
    stranger = await env.verified_user()
    assert (await env.http.post(f"{JOBS}/{job['job_id']}/cancel", headers=stranger["headers"])).status_code == 404
    await claim(w.tool, w.ins)
    await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})
    r = await env.http.post(f"{JOBS}/{job['job_id']}/cancel", headers=w.user["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_INVALID_STATE"


# ── Tranh chấp & lỗi hạ tầng ─────────────────────────────────────────────────────────

async def test_concurrent_claimers_never_get_the_same_job(env):
    """Thiết kế tool-key: 1 credential chỉ bind 1 thiết bị -> nhiều worker đồng thời = nhiều credential của CÙNG
    client (admin cấp thêm), mỗi credential tự handshake thiết bị riêng."""
    w = await make_world(env)
    ids = {(await w.job(dedupe_key=f"k{i}"))["job_id"] for i in range(12)}
    from conftest import Tool
    admin_headers = env.user(sub=w.tool.owner, role="admin")["headers"]
    siblings = [w.tool]
    for i in range(5):
        r = await env.http.post(f"/v1/integrations/clients/{w.tool.client_id}/credentials",
                                headers=admin_headers, json={})
        assert r.status_code == 201, r.text
        d = r.json()["data"]
        hs = await env.handshake(d["api_key"], platforms=["facebook"], capabilities=["jobs.pull"])
        siblings.append(Tool(env.http, w.tool.client_id, w.tool.owner, d["key_id"], d["api_key"], d["scopes"],
                             installation_id=hs["installation_id"], device_id=hs["device_id"]))
    batches = await asyncio.gather(*(claim(t, t.installation_id, limit=3) for t in siblings))
    got = [j["job_id"] for b in batches for j in b]
    assert len(got) == len(set(got)) == 12 and set(got) == ids
    rows = await env.rows("SELECT attempts, status FROM jobs")
    assert all(r["attempts"] == 1 and r["status"] == "leased" for r in rows)


async def test_claim_skips_rows_locked_by_another_transaction_instead_of_blocking(env, db):
    w = await make_world(env)
    a, b = await w.job(dedupe_key="a"), await w.job(dedupe_key="b")
    async with db.transaction() as conn:   # worker khác đang giữ khoá dòng job `a`
        await conn.fetchrow("SELECT * FROM jobs WHERE id = $1 FOR UPDATE", a["job_id"])
        got = await asyncio.wait_for(claim(w.tool, w.ins, limit=5), timeout=5)
        assert [j["job_id"] for j in got] == [b["job_id"]]


async def test_claim_is_atomic_when_the_transaction_fails_midway(env, monkeypatch):
    from integration_service.jobs import repository
    w = await make_world(env)
    job = await w.job()

    async def boom(conn, ids):
        raise asyncpg.InterfaceError("kết nối chập chờn")

    monkeypatch.setattr(repository, "resource_summaries", boom)
    r = await w.tool.post(f"{JOBS}/claim", {"installation_id": w.ins})
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
    row = await w.state(job["job_id"])
    assert row["status"] == "pending" and row["attempts"] == 0 and row["claimed_by_client"] is None   # rollback trọn vẹn
    monkeypatch.undo()
    assert len(await claim(w.tool, w.ins)) == 1


async def test_database_blip_on_complete_leaves_state_intact_and_retry_succeeds(env, db, monkeypatch):
    w = await make_world(env)
    job = await w.job()
    await claim(w.tool, w.ins)
    original = type(db).transaction
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("mất kết nối")
        return original(self)

    monkeypatch.setattr(type(db), "transaction", flaky)
    key = "idem-job-flaky-0001"
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"n": 1}}, idem=key)
    assert r.status_code == 503
    assert (await w.state(job["job_id"]))["status"] == "leased"
    r = await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"n": 1}}, idem=key)   # SDK retry cùng khoá
    assert r.status_code == 200 and r.json()["data"]["status"] == "done"


async def test_two_workers_completing_the_same_job_end_with_one_done(env):
    w = await make_world(env)
    job = await w.job()
    await claim(w.tool, w.ins)
    rs = await asyncio.gather(*(w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {"n": i}}) for i in range(4)))
    assert all(r.status_code == 200 for r in rs)
    assert sorted(r.json()["data"]["replayed"] for r in rs) == [False, True, True, True]
    assert (await w.state(job["job_id"]))["status"] == "done"


async def test_audit_trail_for_job_actions(w, env):
    job = await w.job()
    await claim(w.tool, w.ins)
    await w.tool.post(f"{JOBS}/{job['job_id']}/complete", {"result": {}})
    actions = {r["action"] for r in await env.rows("SELECT action FROM audit_log")}
    assert {"jobs.create", "jobs.claim", "jobs.complete"} <= actions
    assert "lease_token" not in str(await env.rows("SELECT detail FROM audit_log"))


async def test_job_status_values_cover_the_lifecycle(w):
    row = await w.env.rows("SELECT conname FROM pg_constraint WHERE conrelid = 'jobs'::regclass AND contype = 'c'")
    assert row   # CHECK status tồn tại: giá trị lạ bị DB từ chối
    with pytest.raises(asyncpg.CheckViolationError):
        await w.env.rows("UPDATE jobs SET status = 'lung_tung'")  # nhưng bảng rỗng -> không lỗi nếu 0 dòng
        await w.job()
        await w.env.rows("UPDATE jobs SET status = 'lung_tung'")
