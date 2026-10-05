"""IntegrationClient: hành vi phía client (chia lô, idempotency ổn định, retry an toàn, không rò API key)."""

from __future__ import annotations

import json

import httpx
import pytest

from manager_sdk.integration_client import MAX_BATCH, IntegrationClient, IntegrationError, _stable_key

KEY = "mpk_AAAAAAAAAAAA.secret-secret-secret-secret-secret-0123456"


class Server:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.script: list = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0) if self.script else httpx.Response(200, json={"ok": True, "data": {"snapshot_id": "snp_1", "status": "building"}})
        if isinstance(item, Exception):
            raise item
        return item


def client(server: Server, **kw) -> IntegrationClient:
    async def no_sleep(_):
        return None

    return IntegrationClient("http://integration.test", KEY, http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(server.handler)), sleep=no_sleep, **kw)


def body(req: httpx.Request) -> dict:
    return json.loads(req.content)


async def test_headers_carry_key_contract_version_and_request_id():
    s = Server()
    await client(s).create_snapshot(platform="facebook", snapshot_type="full", observed_at="2026-10-05T00:00:00Z",
                                    report_timezone="UTC", expected_counts={})
    h = s.requests[0].headers
    assert h["authorization"] == f"Bearer {KEY}" and h["x-contract-version"] == "2026-10-01"
    assert h["x-request-id"].startswith("sdk_") and h["idempotency-key"].startswith("sdk-")


async def test_idempotency_key_is_stable_for_same_body_and_different_for_different_body():
    s = Server()
    c = client(s)
    for _ in range(2):
        await c.put_project("p1", name="A", platform="facebook")
    await c.put_project("p1", name="B", platform="facebook")
    keys = [r.headers["idempotency-key"] for r in s.requests]
    assert keys[0] == keys[1] != keys[2]


async def test_retry_reuses_the_same_key_and_body():
    s = Server()
    s.script = [httpx.ConnectError("x"), httpx.Response(503), httpx.Response(200, json={"ok": True, "data": {"snapshot_id": "snp_9"}})]
    out = await client(s).create_snapshot(platform="facebook", snapshot_type="delta", observed_at="2026-10-05T00:00:00Z",
                                          report_timezone="UTC", expected_counts={"resources": 1})
    assert out["snapshot_id"] == "snp_9" and len(s.requests) == 3
    assert len({r.headers["idempotency-key"] for r in s.requests}) == 1
    assert len({r.content for r in s.requests}) == 1


async def test_4xx_is_not_retried_and_exposes_the_stable_error_code():
    s = Server()
    s.script = [httpx.Response(403, json={"ok": False, "error": {"code": "PLATFORM_NOT_ALLOWED", "message": "no", "details": {"platform": "tiktok"}}})]
    with pytest.raises(IntegrationError) as exc:
        await client(s).resources_batch([{"external_id": "1"}])
    assert exc.value.code == "PLATFORM_NOT_ALLOWED" and exc.value.status == 403 and exc.value.details == {"platform": "tiktok"}
    assert len(s.requests) == 1


async def test_gives_up_after_retries_and_never_leaks_the_api_key():
    s = Server()
    s.script = [httpx.ConnectError(KEY)] * 10
    with pytest.raises(IntegrationError) as exc:
        await client(s, retries=2).commit("snp_1")
    assert exc.value.code == "UNAVAILABLE" and len(s.requests) == 3
    assert KEY not in str(exc.value) and "secret-secret" not in repr(exc.value)


async def test_429_honours_retry_after_then_succeeds():
    s = Server()
    slept = []

    async def record_sleep(sec):
        slept.append(sec)

    s.script = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": True, "data": {"x": 1}})]
    c = IntegrationClient("http://i.test", KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(s.handler)),
                          sleep=record_sleep)
    assert (await c.commit("snp_1"))["x"] == 1 and slept == [7.0]


async def test_uploads_are_chunked_to_the_contract_batch_limit():
    s = Server()
    items = [{"external_id": str(i)} for i in range(MAX_BATCH * 2 + 17)]
    await client(s).add_content("snp_1", items)
    sizes = [len(body(r)["items"]) for r in s.requests]
    assert sizes == [MAX_BATCH, MAX_BATCH, 17] and all(r.url.path.endswith("/content") for r in s.requests)
    assert len({r.headers["idempotency-key"] for r in s.requests}) == 3


async def test_push_snapshot_flow_computes_expected_counts_and_commits_last():
    s = Server()
    await client(s).push_snapshot(platform="facebook", observed_at="2026-10-05T00:00:00Z", report_timezone="UTC",
                                  resources=[{"external_id": "r"}], content=[{"external_id": "c1"}, {"external_id": "c2"}],
                                  events=[{"external_id": "e"}], scope={"resources": True})
    paths = [r.url.path for r in s.requests]
    assert paths == ["/v1/integrations/snapshots", "/v1/integrations/snapshots/snp_1/resources",
                     "/v1/integrations/snapshots/snp_1/content", "/v1/integrations/snapshots/snp_1/events",
                     "/v1/integrations/snapshots/snp_1/commit"]
    assert body(s.requests[0])["expected_counts"] == {"resources": 1, "content": 2, "events": 1}
    assert body(s.requests[0])["scope"] == {"resources": True}


async def test_push_snapshot_stops_before_commit_when_an_upload_fails():
    s = Server()
    s.script = [httpx.Response(200, json={"ok": True, "data": {"snapshot_id": "snp_1"}}),
                httpx.Response(422, json={"ok": False, "error": {"code": "RECORD_VALIDATION_FAILED", "message": "x"}})]
    with pytest.raises(IntegrationError):
        await client(s).push_snapshot(platform="facebook", observed_at="2026-10-05T00:00:00Z", report_timezone="UTC",
                                      content=[{"external_id": "bad"}])
    assert not any(r.url.path.endswith("/commit") for r in s.requests)  # lỗi giữa chừng ⇒ không commit


async def test_client_never_sends_owner_fields():
    s = Server()
    await client(s).handshake(device_id="d", hostname="h", os_name="windows", app_version="1", tool_name="Tool X",
                              platforms=["facebook"], capabilities=["snapshots.push"])
    assert "owner" not in json.dumps(body(s.requests[0]))


async def test_job_calls_and_claim_is_not_idempotent_keyed():
    s = Server()
    s.script = [httpx.Response(200, json={"ok": True, "data": {"jobs": [{"job_id": "job_1"}]}}),
                httpx.Response(200, json={"ok": True, "data": {"ok": True}}),
                httpx.Response(200, json={"ok": True, "data": {"ok": True}})]
    c = client(s)
    jobs = await c.claim_jobs(installation_id="ins_1", platforms=["facebook"], capabilities=["resource.check"])
    await c.complete_job("job_1", {"views": 1})
    await c.fail_job("job_2", error_code="ADAPTER_ERROR", message="boom")
    assert jobs == [{"job_id": "job_1"}]
    assert "idempotency-key" not in s.requests[0].headers  # mỗi lần claim phải lấy job mới
    assert s.requests[1].headers["idempotency-key"] and s.requests[2].headers["idempotency-key"]


def test_stable_key_is_order_insensitive_for_dicts_and_sensitive_to_values():
    assert _stable_key("a", {"x": 1, "y": 2}) == _stable_key("a", {"y": 2, "x": 1})
    assert _stable_key("a", {"x": 1}) != _stable_key("a", {"x": 2})
