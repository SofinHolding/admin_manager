"""Device binding (1 khoá : 1 thiết bị) ở mặt phẳng máy + connection-guide của Tool cá nhân."""

from __future__ import annotations

import pytest
from conftest import ts  # noqa: F401
from integration_service.guide.catalog import CATALOG

BASE = "/v1/integrations"
SNAP_BODY = {"platform": "facebook", "snapshot_type": "delta", "observed_at": "2026-10-05T00:00:00Z",
             "report_timezone": "UTC", "expected_counts": {}}


def test_every_machine_route_is_in_the_guide_catalog():
    """Thêm route máy mà quên khai trong CATALOG => connection-guide thiếu endpoint cho Tool."""
    from integration_service.app import create_app
    from integration_service.config import Settings

    app = create_app(Settings(port=1, log_level="INFO", database_url="postgresql://a:b@127.0.0.1:1/x", env="test",
                              issuer="http://identity.invalid", jwks_url="http://identity.invalid/jwks",
                              reveal_key="A" * 43 + "="))
    routes = {(m.upper(), path) for path, ops in app.openapi()["paths"].items()
              for m, op in ops.items() if "machine" in op.get("tags", [])}
    catalog = {(e.method, e.path) for e in CATALOG}
    assert routes == catalog, {"thiếu trong catalog": sorted(routes - catalog), "thừa": sorted(catalog - routes)}


# ── Device binding ─────────────────────────────────────────────────────────────────

@pytest.mark.needs_pg
async def test_credential_must_be_activated_by_handshake_before_any_other_call(env):
    tool = await env.tool(handshake=False)
    r = await tool.post(f"{BASE}/snapshots", SNAP_BODY)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CREDENTIAL_NOT_ACTIVATED"
    hs = await env.handshake(tool.api_key, platforms=["facebook"], device_id="pc-01")
    tool.installation_id = hs["installation_id"]
    assert (await tool.post(f"{BASE}/snapshots", SNAP_BODY)).status_code == 201


@pytest.mark.needs_pg
async def test_calls_without_or_with_foreign_installation_id_are_rejected(env):
    tool = await env.tool()
    other = await env.tool()
    bound = tool.installation_id
    tool.installation_id = None                       # header vắng
    r = await tool.post(f"{BASE}/snapshots", SNAP_BODY)
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSTALLATION_MISMATCH"
    tool.installation_id = other.installation_id      # installation của khoá khác
    r = await tool.post(f"{BASE}/snapshots", SNAP_BODY)
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSTALLATION_MISMATCH"
    tool.installation_id = bound
    assert (await tool.post(f"{BASE}/snapshots", SNAP_BODY)).status_code == 201


@pytest.mark.needs_pg
async def test_rotated_key_can_be_activated_on_a_new_device(env):
    tool = await env.tool(device_id="old-pc")
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=owner["headers"], json={})
    assert r.status_code == 201, r.text
    new_key = r.json()["data"]["api_key"]
    hs = await env.handshake(new_key, platforms=["facebook"], device_id="new-pc")
    assert hs["installation_id"] != tool.installation_id


@pytest.mark.needs_pg
async def test_shared_client_cannot_write_resources_without_delegation(env):
    shared = await env.tool(kind="fallback_tool")
    r = await shared.post(f"{BASE}/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "x", "observed_at": "2026-10-05T00:00:00Z"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SHARED_CLIENT_REQUIRES_DELEGATION"
    r = await shared.post(f"{BASE}/snapshots", SNAP_BODY)
    assert r.status_code == 403 and r.json()["error"]["code"] == "SHARED_CLIENT_REQUIRES_DELEGATION"


# ── Connection guide ───────────────────────────────────────────────────────────────

@pytest.mark.needs_pg
async def test_connection_guide_describes_the_bound_key_and_resolved_schemas(env):
    tool = await env.tool(device_id="pc-guide")
    owner = env.user(sub=tool.owner)
    r = await env.http.get(f"{BASE}/me/connection-guide", headers=owner["headers"])
    assert r.status_code == 200, r.text
    g = r.json()["data"]
    assert g["credential"]["key_id"] == tool.key_id and g["credential"]["bound_device_id"] == "pc-guide"
    assert g["credential"]["bound_installation_id"] == tool.installation_id
    paths = {(e["method"], e["path"]) for e in g["endpoints"]}
    assert ("POST", f"{BASE}/snapshots/{{snapshot_id}}/content") in paths
    assert not any(e["scope"].startswith("jobs:") for e in g["endpoints"])   # personal không có scope jobs
    content = next(e for e in g["endpoints"] if e["path"] == f"{BASE}/snapshots/{{snapshot_id}}/content")
    schema = content["request_schema"]
    assert "$ref" not in schema and "items" in schema["properties"]
    item = schema["properties"]["items"]["items"]
    assert {"external_id", "resource_external_id"} <= set(item["properties"])
    codes = {c["code"]: c["http_status"] for c in g["error_codes"]}
    assert codes["CREDENTIAL_DEVICE_BOUND"] == 409 and codes["INSTALLATION_MISMATCH"] == 403
    assert "api_key" not in r.text and "secret" not in g["credential"]


@pytest.mark.needs_pg
async def test_connection_guide_lists_only_endpoints_the_key_scopes_allow(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert r.status_code == 200, r.text
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"],
                            json={"scopes": ["integration:handshake", "resources:write"]})
    assert r.status_code == 201 and r.json()["data"]["api_key"], r.text
    g = (await env.http.get(f"{BASE}/me/connection-guide", headers=owner["headers"])).json()["data"]
    assert {e["scope"] for e in g["endpoints"]} == {"integration:handshake", "resources:write"}
    spec = (await env.http.get(f"{BASE}/me/openapi.json", headers=owner["headers"])).json()
    assert f"{BASE}/snapshots" not in spec["paths"] and f"{BASE}/resources/batch" in spec["paths"]


@pytest.mark.needs_pg
async def test_connection_guide_is_404_without_personal_tool(env):
    u = await env.verified_user()
    r = await env.http.get(f"{BASE}/me/connection-guide", headers=u["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"
