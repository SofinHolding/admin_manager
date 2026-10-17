"""Bảo mật mặt phẳng MÁY: định dạng key, không dò được key, scope/platform, rate limit, contract version, log sạch."""

from __future__ import annotations

import logging
import re

import pytest
from conftest import Tool
from integration_service.clients import policy

pytestmark = pytest.mark.needs_pg
HS = "/v1/integrations/handshake"
SNAP = "/v1/integrations/snapshots"


def hs_body(**over):
    return {"installation": {"device_id": "d1"}, **over}


def snap_body(platform="facebook", **over):
    return {"platform": platform, "snapshot_type": "delta", "observed_at": "2026-10-05T10:00:00Z",
            "report_timezone": "UTC", "expected_counts": {}, **over}


async def raw(env, authorization: str | None, path=HS, **headers):
    h = dict(headers)
    if authorization is not None:
        h["Authorization"] = authorization
    return await env.http.post(path, headers=h, json=hs_body())


# ── Sai định dạng / không dò được key ───────────────────────────────────────────

async def test_malformed_or_unknown_keys_all_give_identical_auth_invalid(env):
    tool = await env.tool()
    secret = tool.api_key.split(".")[1]
    attempts = [
        None, "", "Bearer", "Bearer ", "Basic abc", "mpk_" + "a" * 12 + "." + "b" * 43,           # thiếu scheme
        "Bearer mpk_short.xxx", "Bearer " + "x" * 80, "Bearer mpk_" + "a" * 12 + "." + "b" * 43,    # key_id không tồn tại
        f"Bearer mpk_{tool.key_id}.{'z' * 43}",                                                    # key_id ĐÚNG, secret sai
        f"Bearer mpk_{tool.key_id}.{secret[:-1]}",                                                 # thiếu 1 ký tự
        f"Bearer mpk_{tool.key_id}.{secret}x", f"Bearer mpk_{tool.key_id[:-1]}.{secret}",
        f"Bearer MPK_{tool.key_id}.{secret}", f"Bearer mpk_{tool.key_id}.{secret}\x00",
    ]
    seen = set()
    for a in attempts:
        try:
            r = await raw(env, a)
        except Exception:  # noqa: BLE001 — header chứa NUL bị httpx từ chối trước khi gửi: coi như bị chặn
            continue
        assert r.status_code == 401, a
        body = r.json()
        assert body["error"]["code"] == "AUTH_INVALID"
        seen.add((body["error"]["message"], tuple(sorted(body["error"]["details"]))))
    assert len(seen) == 1, f"thông điệp khác nhau cho từng kiểu sai => dò được key: {seen}"


async def test_revoked_state_is_only_disclosed_with_the_correct_secret(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    await env.http.delete(f"/v1/integrations/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    wrong = await raw(env, f"Bearer mpk_{tool.key_id}.{'q' * 43}")
    assert wrong.json()["error"]["code"] == "AUTH_INVALID"          # không lộ "đã thu hồi" cho người không có secret
    right = await raw(env, f"Bearer {tool.api_key}")
    assert right.json()["error"]["code"] == "AUTH_REVOKED"


async def test_repeated_bad_keys_from_one_ip_get_rate_limited(env):
    tool = await env.tool(handshake=False)
    bad = "Bearer mpk_" + "a" * 12 + "." + "b" * 43
    codes = [(await raw(env, bad)).status_code for _ in range(31)]
    assert codes[:30] == [401] * 30 and codes[30] == 429
    r = await raw(env, f"Bearer {tool.api_key}")        # cùng IP: bị chặn tạm thời, kể cả key đúng
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED" and "retry-after" in r.headers
    other_ip = await env.http.post(HS, headers={"Authorization": f"Bearer {tool.api_key}", "X-Real-IP": "10.9.9.9"},
                                   json=hs_body())
    assert other_ip.status_code == 200                  # IP khác không bị ảnh hưởng


# ── Scope / platform ────────────────────────────────────────────────────────────

async def test_scope_denial_for_every_endpoint_family(env):
    only_hs = await env.tool(kind="service", scopes=["integration:handshake"], handshake=False)
    assert (await only_hs.post(HS, hs_body())).status_code == 200
    cases = [("post", "/v1/integrations/resources/batch", {"items": [{"x": 1}]}),
             ("post", SNAP, snap_body()),
             ("post", "/v1/integrations/snapshots/snp_" + "0" * 32 + "/commit", {}),
             ("post", "/v1/integrations/snapshots/snp_" + "0" * 32 + "/resources", {"items": [{}]}),
             ("post", "/v1/integrations/snapshots/snp_" + "0" * 32 + "/metrics", {"items": [{}]}),
             ("get", "/v1/integrations/snapshots/snp_" + "0" * 32, None)]
    for method, path, body in cases:
        r = await (only_hs.get(path) if method == "get" else getattr(only_hs, method)(path, body))
        assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED", path
        assert r.json()["error"]["details"]["required"]
    only_snap = await env.tool(kind="service", scopes=["snapshots:write"], handshake=False)
    assert (await only_snap.post(HS, hs_body())).json()["error"]["code"] == "SCOPE_DENIED"
    r = await only_snap.post("/v1/integrations/snapshots/snp_" + "0" * 32 + "/metrics", {"items": [{}]})
    assert r.json()["error"]["code"] == "SCOPE_DENIED"           # metrics cần metrics:write riêng
    assert (await env.rows("SELECT count(*) AS n FROM audit_log WHERE action = 'auth.scope_denied'"))[0]["n"] >= 8


async def test_platform_denial_on_every_entry_point(env):
    tool = await env.tool(platforms=["facebook"], device_id="d1")
    admin = await env.verified_user(role="admin")
    r = await env.http.patch(f"/v1/integrations/clients/{tool.client_id}", headers=admin["headers"],
                             json={"allowed_platforms": ["facebook"]})
    assert r.status_code == 200, r.text
    r = await tool.post(SNAP, snap_body(platform="youtube"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "ok"},
        {"platform": "youtube", "resource_type": "channel", "external_id": "no"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    assert await env.scalar("SELECT count(*) FROM resources") == 0            # từ chối CẢ request, không ghi phần nào
    r = await tool.post(HS, hs_body(platforms=["facebook", "tiktok"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    assert (await tool.post(HS, hs_body(platforms=["facebook"]))).status_code == 200


async def test_platform_disabled_in_registry_is_denied_even_if_client_allows_it(env):
    tool = await env.tool(platforms=["facebook"])
    await env.rows("UPDATE platform_registry SET enabled = false WHERE key = 'facebook'")
    r = await tool.post(SNAP, snap_body())
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"


async def test_snapshot_operations_recheck_platform_after_client_is_narrowed(env):
    tool = await env.tool(platforms=["facebook", "youtube"])
    sid = (await tool.post(SNAP, snap_body(platform="youtube"))).json()["data"]["snapshot_id"]
    admin = await env.verified_user(role="admin")
    await env.http.patch(f"/v1/integrations/clients/{tool.client_id}", headers=admin["headers"],
                         json={"allowed_platforms": ["facebook"]})
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/resources",
                        {"items": [{"external_id": "c", "resource_type": "channel", "observed_at": "2026-10-05T10:00:00Z"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    assert (await tool.post(f"/v1/integrations/snapshots/{sid}/commit", {})).status_code == 403


async def test_a_tool_cannot_touch_another_tools_snapshot(env):
    a, b = await env.tool(), await env.tool()
    sid = (await a.post(SNAP, snap_body())).json()["data"]["snapshot_id"]
    admin_same = env.user(sub=a.owner, role="admin")
    r = await env.http.post("/v1/integrations/clients", headers=admin_same["headers"], json={
        "tool_name": "Same-owner-service", "client_kind": "service",
        "purpose": "same owner diff client fixture", "platforms": ["facebook"]})
    assert r.status_code == 201, r.text
    client_id = r.json()["data"]["client_id"]
    r = await env.http.put(f"/v1/integrations/clients/{client_id}/config", headers=admin_same["headers"],
                           json={"allowed_platforms": ["facebook"], "allowed_scopes": policy.default_scopes("service")})
    assert r.status_code == 200, r.text
    tool_name = r.json()["data"]["tool_name"]
    r = await env.http.post(f"/v1/integrations/clients/{client_id}/activate", headers=admin_same["headers"],
                            json={"confirm_tool_name": tool_name})
    assert r.status_code == 200, r.text
    r = await env.http.post(f"/v1/integrations/clients/{client_id}/credentials", headers=admin_same["headers"], json={})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    same_owner = Tool(http=env.http, client_id=d["client_id"], owner=a.owner, key_id=d["key_id"],
                      api_key=d["api_key"], scopes=d["scopes"])
    hs = await env.handshake(same_owner.api_key, platforms=["facebook"])
    same_owner.installation_id, same_owner.device_id = hs["installation_id"], hs["device_id"]
    assert (await b.get(f"/v1/integrations/snapshots/{sid}")).status_code == 404
    assert (await b.post(f"/v1/integrations/snapshots/{sid}/commit", {})).json()["error"]["code"] == "SNAPSHOT_NOT_FOUND"
    r = await b.post(f"/v1/integrations/snapshots/{sid}/resources", {"items": [{"external_id": "x"}]})
    assert r.status_code == 404
    # Client dùng chung khác, CÙNG owner (admin tạo thay a.owner): GET vẫn lọc theo client_id nên không thấy;
    # thao tác GHI bị chặn SỚM HƠN vì client dùng chung luôn cần X-Delegated-Job — cách ly còn chặt hơn nữa.
    assert (await same_owner.get(f"/v1/integrations/snapshots/{sid}")).status_code == 404
    r = await same_owner.post(f"/v1/integrations/snapshots/{sid}/commit", {})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SHARED_CLIENT_REQUIRES_DELEGATION"
    r = await same_owner.post(f"/v1/integrations/snapshots/{sid}/resources", {"items": [{"external_id": "x"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SHARED_CLIENT_REQUIRES_DELEGATION"


# ── Contract version ────────────────────────────────────────────────────────────

async def test_contract_version_header(env):
    tool = await env.tool(handshake=False)
    bad = await tool.post(HS, hs_body(), headers={"X-Contract-Version": "2025-01-01"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "CONTRACT_VERSION_UNSUPPORTED"
    ok = await tool.post(HS, hs_body(), headers={"X-Contract-Version": "2026-10-01"})
    assert ok.status_code == 200 and ok.json()["meta"]["contract_version"] == "2026-10-01"
    r = await tool.post(HS, hs_body(tool={"contract_version": "1999-01-01"}))
    assert r.status_code == 400 and r.json()["error"]["code"] == "CONTRACT_VERSION_UNSUPPORTED"


# ── Rate limit ──────────────────────────────────────────────────────────────────

async def test_rate_limit_per_client_with_retry_after(make_env, mono):
    env, _ = await make_env(rate_limit_per_min=3)
    t1, t2 = await env.tool(handshake=False), await env.tool(handshake=False)
    assert [(await t1.post(HS, hs_body())).status_code for _ in range(3)] == [200, 200, 200]
    r = await t1.post(HS, hs_body())
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert (await t2.post(HS, hs_body())).status_code == 200            # client khác không bị ảnh hưởng
    mono.t += 61
    assert (await t1.post(HS, hs_body())).status_code == 200            # cửa sổ mới


# ── Log sạch ────────────────────────────────────────────────────────────────────

async def test_api_key_and_authorization_never_reach_logs_or_responses(env, caplog):
    caplog.set_level(logging.DEBUG)
    tool = await env.tool()
    secret = tool.api_key.split(".")[1]
    responses = [await tool.post(HS, hs_body()), await raw(env, f"Bearer mpk_{tool.key_id}.{'w' * 43}"),
                 await tool.post(SNAP, snap_body(platform="youtube")), await tool.post(SNAP, {"bogus": 1})]
    blob = "\n".join(f"{r.getMessage()} {getattr(r, 'args', '')} {r.__dict__}" for r in caplog.records)
    for needle in (tool.api_key, secret, f"Bearer {tool.api_key}"):
        assert needle not in blob
        assert all(needle not in r.text for r in responses)
    assert re.search(r"mpk_[A-Za-z0-9_-]{12}\.", blob) is None


async def test_validation_errors_never_echo_input_values(env):
    tool = await env.tool()
    marker = "GIA_TRI_NHAY_CAM_12345"
    r = await tool.post(SNAP, snap_body(platform=marker))
    assert r.status_code == 422 and marker not in r.text
    r = await tool.post(HS, {"installation": {"device_id": marker, "bogus": marker}})
    assert r.status_code == 422 and marker not in r.text


async def test_inactive_client_blocks_its_keys_even_if_credentials_were_not_revoked(env):
    """Phòng thủ nhiều lớp: trạng thái client được kiểm ở MỖI request, không chỉ dựa vào việc thu hồi key."""
    tool = await env.tool(handshake=False)
    for status in ("suspended", "deleted", "pending_email_verification"):
        await env.rows("UPDATE integration_clients SET status = $2 WHERE id = $1", tool.client_id, status)
        r = await tool.post(HS, hs_body())
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED", status
    await env.rows("UPDATE integration_clients SET status = 'active' WHERE id = $1", tool.client_id)
    assert (await tool.post(HS, hs_body())).status_code == 200
