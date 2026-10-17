"""Lớp ánh xạ endpoint hệ thống của tool (system-endpoints.json + system_api.py). Không cần mạng/DB."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import system_api  # noqa: E402

SERVER_CATALOG = TOOL.parents[1] / "services" / "integration-service" / "src" / "integration_service" / "guide" / "catalog.py"


def test_tool_ships_with_the_current_systems_address():
    assert system_api.DEFAULT_GATEWAY == "http://192.168.1.70:8480"
    assert json.loads((TOOL / "system-endpoints.json").read_text(encoding="utf-8"))["default_gateway"] == system_api.DEFAULT_GATEWAY


@pytest.mark.skipif(not SERVER_CATALOG.exists(), reason="chạy ngoài monorepo")
def test_tool_maps_every_machine_endpoint_the_server_exposes_and_nothing_else():
    """Server thêm/đổi/bỏ một endpoint máy mà tool không theo kịp => đỏ ở đây (không đợi tới lúc chạy thật)."""
    server = set(re.findall(r'EndpointDoc\("(GET|POST|PUT|PATCH|DELETE)",\s*"([^"]+)"', SERVER_CATALOG.read_text(encoding="utf-8")))
    tool = {(e["method"], e["path"]) for e in system_api.ENDPOINTS.values() if e["name"] != "health"}
    assert server, "không đọc được danh mục server"
    assert tool == server, (sorted(server - tool), sorted(tool - server))


def test_every_call_in_the_client_goes_through_a_mapped_endpoint():
    used = set(re.findall(r'_call\(\s*f?"([a-z_{}]+)"', (TOOL / "system_api.py").read_text(encoding="utf-8")))
    used.discard("snapshot_{kind}")
    used |= {f"snapshot_{k}" for k in system_api.BATCH_KINDS}
    assert used <= set(system_api.ENDPOINTS), sorted(used - set(system_api.ENDPOINTS))


def test_path_params_are_url_encoded():
    assert system_api.endpoint_path("job_fail", job_id="job_1") == "/v1/integrations/jobs/job_1/fail"
    assert system_api.endpoint_path("worker_heartbeat", worker_id="a/b c") == "/v1/integrations/workers/a%2Fb%20c/heartbeat"


def _api(handler) -> system_api.SystemApi:
    a = system_api.SystemApi("http://gw", "mpk_a.b")
    a._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    return a


def test_read_endpoints_hit_the_mapped_paths_with_filters():
    seen: list[tuple[str, str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params)))
        if req.url.path.endswith("resource-owners"):
            return httpx.Response(200, json={"ok": True, "data": {"users": [{"id": "u1", "username": "a", "resource_count": 2}]}})
        return httpx.Response(200, json={"ok": True, "data": {"items": [{"id": "r1"}], "is_shared": True, "owner_account_id": "u1"}})

    async def go():
        api = _api(handler)
        return await api.list_resource_owners(), await api.list_resources("u1", platform="youtube")

    owners, res = asyncio.run(go())
    assert owners[0]["id"] == "u1" and res["items"] == [{"id": "r1"}]
    assert seen == [("GET", "/v1/integrations/resource-owners", {}),
                    ("GET", "/v1/integrations/resources", {"owner_account_id": "u1", "platform": "youtube"})]


def test_worker_register_and_heartbeat_use_the_workers_contract():
    seen: list[tuple[str, str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "data": {"registration_id": "wkr_1", "heartbeat_interval_s": 30,
                                                              "ok": True, "next_heartbeat_in_s": 30}})

    async def go():
        api = _api(handler)
        await api.register_worker(["youtube"])
        await api.worker_heartbeat("collector-01")

    asyncio.run(go())
    assert seen[0][1] == "/v1/integrations/workers/register" and seen[0][2]["platforms"] == ["youtube"]
    assert seen[0][2]["worker_id"] == system_api.device_id()[:64]
    assert seen[1][1] == "/v1/integrations/workers/collector-01/heartbeat" and seen[1][2] == {}


def test_batches_are_split_at_500_and_always_carry_the_delegated_job():
    seen: list[tuple[str, int, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, len(json.loads(req.content)["items"]), req.headers.get("x-delegated-job", "")))
        return httpx.Response(200, json={"ok": True, "data": {"received": 1, "rejected": 0}})

    asyncio.run(_api(handler).send_batch("snp_1", "events", [{"i": n} for n in range(1001)], "job_9"))
    assert seen == [("/v1/integrations/snapshots/snp_1/events", n, "job_9") for n in (500, 500, 1)]
    with pytest.raises(ValueError):
        asyncio.run(_api(handler).send_batch("snp_1", "bogus", [{}], "job_9"))


def _probe(handler, key="mpk_a.b"):
    return asyncio.run(system_api.probe_all("http://gw", key, transport=httpx.MockTransport(handler)))


def _good(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/v1/health":
        return httpx.Response(200, json={"ok": True, "service": "gateway"})
    if req.url.path.endswith("/handshake"):
        return httpx.Response(200, json={"ok": True, "data": {"installation_id": "ins_1"}})
    return httpx.Response(200, json={"ok": True, "data": []})


def test_probe_never_touches_endpoints_with_side_effects_and_reuses_the_installation():
    calls: list[tuple[str, str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("x-installation-id", "")))
        return _good(req)

    res = {r["name"]: r for r in _probe(handler)}
    side_effects = {n for n, e in system_api.ENDPOINTS.items() if e["probe"] != "safe"}
    assert side_effects and all(res[n]["status"] == "skipped" for n in side_effects)
    requested = {p for _, p, _ in calls}
    assert "/v1/integrations/jobs/claim" not in requested and "/v1/integrations/snapshots" not in requested
    assert all(r["status"] == "ok" for n, r in res.items() if n not in side_effects)
    after_handshake = [c for c in calls if c[1] not in ("/v1/health", "/v1/integrations/handshake")]
    assert after_handshake and all(c[2] == "ins_1" for c in after_handshake)   # device binding: mọi request sau gửi X-Installation-Id


def test_probe_without_key_only_checks_health():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return _good(req)

    res = {r["name"]: r for r in _probe(handler, key="")}
    assert calls == ["/v1/health"] and res["health"]["status"] == "ok"
    assert res["platforms"]["status"] == "skipped" and "API key" in res["platforms"]["detail"]


def test_probe_flags_a_wrong_address_that_answers_html_instead_of_api_json():
    """Đúng lỗi cũ: `/health/ready` trả trang web 200 nên nhìn như 'khoẻ' dù không phải API."""
    res = {r["name"]: r for r in _probe(lambda req: httpx.Response(200, text="<!doctype html><html></html>"))}
    assert res["health"]["status"] == "fail" and "JSON" in res["health"]["detail"]
    assert res["handshake"]["status"] == "fail"
    assert res["platforms"]["status"] == "skipped" and "handshake" in res["platforms"]["detail"]


def test_probe_reports_unauthorized_key_and_network_errors_per_endpoint():
    def unauthorized(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"ok": False, "error": {"code": "AUTH_INVALID", "message": "Thiếu hoặc sai API key"}})

    res = {r["name"]: r for r in _probe(unauthorized)}
    assert res["health"]["status"] == "ok"
    assert res["handshake"]["status"] == "fail" and res["handshake"]["http"] == 401 and "AUTH_INVALID" in res["handshake"]["detail"]

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    res = {r["name"]: r for r in _probe(down)}
    assert res["health"]["status"] == "fail" and res["health"]["http"] is None and "refused" in res["health"]["detail"]
