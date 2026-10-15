"""Machine API mới: GET /v1/integrations/resources và /resource-owners.

fallback_tool (tool dùng chung): thấy resource của TẤT CẢ owner.
primary_tool / service: chỉ thấy resource của chính mình.
User JWT: bị từ chối (machine-only endpoint).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.needs_pg

BASE = "/v1/integrations"


async def _push_resource(env, tool, external_id: str = "page_123") -> None:
    """Đẩy một resource đơn giản bằng Tool đã cho."""
    r = await tool.post(f"{BASE}/resources/batch", json={"items": [{
        "platform": "facebook",
        "resource_type": "page",
        "external_id": external_id,
        "display_name": f"Page {external_id}",
        "observed_at": "2026-10-05T10:00:00Z",
    }]})
    assert r.status_code == 200, r.text


# ── GET /v1/integrations/resources ────────────────────────────────────────────


async def test_fallback_tool_sees_all_owners_resources(env):
    """fallback_tool (is_shared=True): trả resource của MỌI owner."""
    # Tạo 2 owner khác nhau với resource riêng
    owner_a = await env.verified_user()
    owner_b = await env.verified_user()
    tool_a = await env.tool(user=owner_a, kind="primary_tool")
    tool_b = await env.tool(user=owner_b, kind="primary_tool")

    await _push_resource(env, tool_a, "page_aaa")
    await _push_resource(env, tool_b, "page_bbb")

    # Tạo fallback_tool (tool dùng chung) với scopes: jobs:claim
    fallback = await env.tool(kind="fallback_tool", platforms=("facebook", "youtube", "tiktok"))

    r = await fallback.get(f"{BASE}/resources")
    assert r.status_code == 200, r.text
    d = r.json()["data"]

    # is_shared phải True
    assert d["is_shared"] is True, f"Kỳ vọng is_shared=True, nhận: {d}"

    # Phải thấy resource của cả hai owner
    external_ids = {item["external_id"] for item in d["items"]}
    assert "page_aaa" in external_ids, f"Không thấy page_aaa trong {external_ids}"
    assert "page_bbb" in external_ids, f"Không thấy page_bbb trong {external_ids}"


async def test_primary_tool_cannot_call_resources_endpoint(env):
    """primary_tool KHÔNG có scope jobs:claim → bị từ chối 403 SCOPE_DENIED.
    Endpoint /resources chỉ dành cho fallback_tool (operator).
    Kiểm tra access control hoạt động đúng — không phải bug."""
    owner_a = await env.verified_user()
    tool_a = await env.tool(user=owner_a, kind="primary_tool")
    await _push_resource(env, tool_a, "my_page_prim")

    # primary_tool không có jobs:claim → 403
    r = await tool_a.get(f"{BASE}/resources")
    assert r.status_code == 403, f"Kỳ vọng 403 SCOPE_DENIED, nhận: {r.status_code} {r.text}"
    assert r.json()["error"]["code"] in ("SCOPE_DENIED", "AUTH_INVALID"), r.text


async def test_resources_user_jwt_is_rejected(env):
    """User JWT không được dùng cho machine endpoint — phải nhận 401."""
    user = await env.verified_user()
    r = await env.http.get(f"{BASE}/resources", headers=user["headers"])
    assert r.status_code == 401, f"Kỳ vọng 401 cho user JWT, nhận: {r.status_code} {r.text}"


async def test_fallback_tool_can_filter_by_owner(env):
    """fallback_tool + ?owner_account_id=X chỉ trả resource của X."""
    owner_a = await env.verified_user()
    owner_b = await env.verified_user()
    tool_a = await env.tool(user=owner_a, kind="primary_tool")
    tool_b = await env.tool(user=owner_b, kind="primary_tool")

    await _push_resource(env, tool_a, "pa_filter")
    await _push_resource(env, tool_b, "pb_filter")

    fallback = await env.tool(kind="fallback_tool", platforms=("facebook", "youtube", "tiktok"))

    # Lọc chỉ lấy owner_a
    r = await fallback.get(f"{BASE}/resources", owner_account_id=owner_a["sub"])
    assert r.status_code == 200, r.text
    d = r.json()["data"]

    external_ids = {item["external_id"] for item in d["items"]}
    assert "pa_filter" in external_ids
    assert "pb_filter" not in external_ids, "Filter theo owner không hoạt động"


async def test_resources_returns_correct_fields(env):
    """Mỗi item phải có các trường cần thiết cho ChannelImportPage."""
    owner = await env.verified_user()
    personal = await env.tool(user=owner, kind="primary_tool")
    await _push_resource(env, personal, "field_check_page")
    fallback = await env.tool(kind="fallback_tool", platforms=("facebook",))

    r = await fallback.get(f"{BASE}/resources")
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert items, "Không có item nào trả về"

    item = items[0]
    # Các trường bắt buộc để frontend ChannelImportPage hiển thị được
    for field in ("id", "platform", "resource_type", "external_id", "display_name",
                  "state", "mapping_status", "execution_mode", "enabled"):
        assert field in item, f"Thiếu field {field!r} trong resource item"


# ── GET /v1/integrations/resource-owners ──────────────────────────────────────


async def test_fallback_tool_sees_all_resource_owners(env):
    """fallback_tool nhận danh sách TẤT CẢ owner có resource."""
    owner_a = await env.verified_user()
    owner_b = await env.verified_user()
    tool_a = await env.tool(user=owner_a, kind="primary_tool")
    tool_b = await env.tool(user=owner_b, kind="primary_tool")

    await _push_resource(env, tool_a, "owner_page_a")
    await _push_resource(env, tool_b, "owner_page_b")

    fallback = await env.tool(kind="fallback_tool", platforms=("facebook", "youtube", "tiktok"))

    r = await fallback.get(f"{BASE}/resource-owners")
    assert r.status_code == 200, r.text
    d = r.json()["data"]

    assert "users" in d, f"Thiếu key 'users' trong response: {d}"
    owner_ids = {u["id"] for u in d["users"]}
    assert owner_a["sub"] in owner_ids, f"Không thấy owner_a trong danh sách"
    assert owner_b["sub"] in owner_ids, f"Không thấy owner_b trong danh sách"


async def test_primary_tool_cannot_call_resource_owners_endpoint(env):
    """primary_tool KHÔNG có scope jobs:claim → 403 SCOPE_DENIED cho /resource-owners.
    Endpoint chỉ dành cho fallback_tool operator."""
    owner_a = await env.verified_user()
    tool_a = await env.tool(user=owner_a, kind="primary_tool")
    await _push_resource(env, tool_a, "solo_page_prim")

    r = await tool_a.get(f"{BASE}/resource-owners")
    assert r.status_code == 403, f"Kỳ vọng 403 SCOPE_DENIED, nhận: {r.status_code} {r.text}"
    assert r.json()["error"]["code"] in ("SCOPE_DENIED", "AUTH_INVALID"), r.text


async def test_resource_owners_user_jwt_rejected(env):
    """User JWT bị từ chối — machine-only endpoint."""
    user = await env.verified_user()
    r = await env.http.get(f"{BASE}/resource-owners", headers=user["headers"])
    assert r.status_code == 401, f"Kỳ vọng 401, nhận: {r.status_code}"


async def test_resource_owners_no_resources_returns_empty(env):
    """Owner không có resource: /resource-owners trả danh sách rỗng (fallback_tool)."""
    fallback = await env.tool(kind="fallback_tool", platforms=("facebook", "youtube", "tiktok"))
    # Không đẩy resource nào
    r = await fallback.get(f"{BASE}/resource-owners")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["users"] == []
