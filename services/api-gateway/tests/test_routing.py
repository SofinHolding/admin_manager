"""Định tuyến: MỌI route trong route-map + OpenAPI legacy + public_prefixes của registry đến đúng service.

Danh sách route được sinh tự động từ chính các tệp nguồn sự thật (không hardcode) và đếm số lượng.
Kỳ vọng được tính bằng bộ giải ĐỘC LẬP (khớp tiền tố theo thứ tự route-map), không dùng code của gateway.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from api_gateway.routing.table import RouteConfigError, RouteTable, load_route_table
from conftest import client_for

ROOT = Path(__file__).resolve().parents[3]
ROUTE_MAP = json.loads((ROOT / "infra/gateway/route-map.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "infra/service-registry.json").read_text(encoding="utf-8"))
LEGACY = sorted((ROOT / "docs/api/legacy").glob("*.openapi.json"))


def _expected_target(path: str) -> str | None:
    for rule in ROUTE_MAP["rules"]:  # thứ tự = ưu tiên (cụ thể trước)
        pre = rule["legacy_prefix"]
        if path == pre or path.startswith(pre + "/"):
            return rule["target"]
    for name, svc in REGISTRY["services"].items():
        for pre in svc.get("public_prefixes") or []:
            if path == pre or path.startswith(pre + "/"):
                return name
    return None


def _legacy_ops() -> list[tuple[str, str]]:
    ops = []
    for f in LEGACY:
        spec = json.loads(f.read_text(encoding="utf-8"))
        for path, methods in spec["paths"].items():
            for method in methods:
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    ops.append((method.upper(), re.sub(r"\{[^}]+\}", "x1", path)))
    ops.append(("GET", "/v1/health"))
    return ops


LEGACY_OPS = _legacy_ops()
PREFIX_OPS = [("GET", pre + "/probe") for svc in REGISTRY["services"].values()
              for pre in (svc.get("public_prefixes") or [])]
RULE_OPS = [("GET", r["legacy_prefix"]) for r in ROUTE_MAP["rules"]]
SERVICE_OF = {"identity-service": "identity", "integration-service": "integration",
              "analytics-service": "analytics", "reward-service": "reward"}


def test_route_sources_are_complete_and_counted():
    assert len(LEGACY_OPS) >= 100, "OpenAPI legacy phải có >=100 thao tác (baseline 70 + 40 + health)"
    assert len(RULE_OPS) == len(ROUTE_MAP["rules"]) and len(PREFIX_OPS) >= 9
    assert LEGACY and {f.name for f in LEGACY} == {"admin-server.openapi.json", "reward-service.openapi.json"}


@pytest.mark.parametrize("method,path", LEGACY_OPS + PREFIX_OPS + RULE_OPS,
                         ids=[f"{m} {p}" for m, p in LEGACY_OPS + PREFIX_OPS + RULE_OPS])
async def test_every_route_reaches_the_right_service(make_gateway, ups, method, path):
    expected = _expected_target(path)
    assert expected is not None, f"route không có đích trong nguồn sự thật: {path}"
    async with client_for(make_gateway()) as c:
        r = await c.request(method, path)
    if expected == "gateway":
        assert r.status_code == 200 and r.json()["service"] == "gateway"
        return
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service"] == SERVICE_OF[expected], f"{path} → {body['service']}, kỳ vọng {expected}"
    assert body["path"] == path and body["method"] == method


def test_routes_cover_all_new_service_prefixes_from_the_brief():
    table = load_route_table(ROOT / "infra/gateway/route-map.json", ROOT / "infra/service-registry.json")
    cases = {"/v1/auth/login": "identity", "/v1/accounts/me": "identity", "/v1/admin/accounts/x": "identity",
             "/.well-known/jwks.json": "identity", "/oauth/token": "identity",
             "/v1/integrations/handshake": "integration", "/v1/resources/import": "integration",
             "/v1/analytics/summary": "analytics", "/v1/rewards/x": "reward", "/v1/reward/jobs": "reward",
             "/v1/viewer/summary": "analytics", "/v1/viewer/links/code": "integration",
             "/v1/ops/batches": "integration", "/v1/admin/cache/flush": "analytics",
             "/v1/admin/keys": "identity", "/v1/admin/users/u1/status": "identity"}
    for path, up in cases.items():
        r = table.resolve(path)
        assert r is not None and r.upstream == up, path


def test_default_upstream_urls_follow_registry_ports():
    table = load_route_table(ROOT / "infra/gateway/route-map.json", ROOT / "infra/service-registry.json")
    assert table.upstream_urls == {"identity": "http://127.0.0.1:8430", "integration": "http://127.0.0.1:8431",
                                   "analytics": "http://127.0.0.1:8432", "reward": "http://127.0.0.1:8422"}


@pytest.mark.parametrize("path", ["/", "/v1", "/v1/khong-ton-tai", "/v1/authx/login", "/v1/viewerx",
                                  "/admin", "/v1/admin", "/v1/admin/other", "/robots.txt", "/health", "/health/x"])
async def test_unknown_paths_are_404_envelope(make_gateway, ups, path):
    async with client_for(make_gateway()) as c:
        r = await c.get(path)
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["x-request-id"]
    assert ups.all_calls() == [], "đường dẫn không có route không được chạm upstream nào"


async def test_prefix_requires_segment_boundary(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        assert (await c.get("/v1/viewer")).status_code == 200
        assert (await c.get("/v1/viewer/")).status_code == 200
        assert (await c.get("/v1/viewerx/summary")).status_code == 404
        assert (await c.get("/v1/rewardx")).status_code == 404


async def test_method_query_body_and_authorization_pass_through_untouched(make_gateway, ups):
    token = "Bearer eyJhbGciOiJSUzI1NiJ9.e30.sig-not-verified-by-gateway"
    async with client_for(make_gateway()) as c:
        r = await c.post("/v1/auth/login?next=%2Fhome&x=1&x=2", content=b'{"u":"a"}',
                         headers={"Authorization": token, "Content-Type": "application/json",
                                  "X-Contract-Version": "2026-10-01", "Idempotency-Key": "k-1"})
    rec = r.json()
    assert rec["service"] == "identity" and rec["method"] == "POST"
    assert rec["query"] == "next=%2Fhome&x=1&x=2"
    assert rec["body_len"] == 9
    h = rec["headers"]
    assert h["authorization"] == token, "Authorization phải được chuyển NGUYÊN VẸN (gateway không xác thực nghiệp vụ)"
    assert h["x-contract-version"] == "2026-10-01" and h["idempotency-key"] == "k-1"
    assert h["content-type"] == "application/json"


async def test_upstream_status_and_body_pass_through(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/resources/__status?code=409")
        assert r.status_code == 409
        r = await c.get("/v1/resources/__status?code=401")
        assert r.status_code == 401


async def test_forwarded_path_is_normalized_and_matches_routed_path(make_gateway, ups):
    """Gateway định tuyến và chuyển tiếp CÙNG một chuỗi ⇒ không có khác biệt phân tích với upstream."""
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/./summary")
    assert r.json()["path"] == "/v1/viewer/summary"


def test_conflicting_prefixes_are_rejected():
    from api_gateway.routing.table import Route

    with pytest.raises(RouteConfigError):
        RouteTable([Route("/v1/x", "a-service", "a"), Route("/v1/x", "b-service", "b")],
                   {"a": "http://x", "b": "http://y"})


def test_route_table_never_accepts_internal_prefix(tmp_path):
    rm = tmp_path / "rm.json"
    rm.write_text(json.dumps({"rules": [{"legacy_prefix": "/internal/v1", "target": "identity-service"}]}))
    with pytest.raises(RouteConfigError):
        load_route_table(rm, ROOT / "infra/service-registry.json")


def test_unknown_target_is_rejected(tmp_path):
    rm = tmp_path / "rm.json"
    rm.write_text(json.dumps({"rules": [{"legacy_prefix": "/v1/zzz", "target": "ghost-service"}]}))
    with pytest.raises(RouteConfigError):
        load_route_table(rm, ROOT / "infra/service-registry.json")


async def test_env_override_changes_upstream(make_gateway, ups):
    gw = make_gateway(GATEWAY_UPSTREAM_ANALYTICS=ups.servers["reward"].url)
    async with client_for(gw) as c:
        r = await c.get("/v1/analytics/x")
    assert r.json()["service"] == "reward"


async def test_redirects_are_not_followed(make_gateway, ups):
    # Upstream trả 302 phải đến nguyên vẹn client (gateway không tự theo).
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/resources/__status?code=302", follow_redirects=False)
    assert r.status_code == 302
    assert isinstance(r, httpx.Response)
