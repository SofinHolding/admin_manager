"""Bảo mật của Legacy ACL: xác thực kiểu legacy, owner spoof, cross-owner, không ghi đè dữ liệu do Tool đồng bộ, injection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import ts
from test_legacy_acl import LNK, OPS, RES, L

pytestmark = pytest.mark.needs_pg
ROOT = Path(__file__).resolve().parents[3]
LEGACY = json.loads((ROOT / "docs" / "api" / "legacy" / "admin-server.openapi.json").read_text(encoding="utf-8"))
OWNED_PREFIXES = ("/v1/resources", "/v1/ops", "/v1/viewer/links")
PUBLIC = {("POST", "/v1/viewer/links/redeem")}


def owned_ops() -> list[tuple[str, str]]:
    return sorted((m.upper(), p) for p, ops in LEGACY["paths"].items() if p.startswith(OWNED_PREFIXES) for m in ops)


def concrete(path: str) -> str:
    out = path
    for part in [seg for seg in path.split("/") if seg.startswith("{")]:
        out = out.replace(part, "x")
    return out


@pytest.fixture
def lg(env) -> L:
    return L(env)


# ── Xác thực kiểu legacy ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [op for op in owned_ops() if op not in PUBLIC])
async def test_every_protected_operation_demands_a_token_with_legacy_message(lg, method, path):
    r = await lg.env.http.request(method, concrete(path), json={})
    assert r.status_code == 401 and r.json() == {"detail": "Thiếu token"}, (method, path)
    r = await lg.env.http.request(method, concrete(path), json={}, headers={"Authorization": "Bearer khong.phai.jwt"})
    assert r.status_code == 401 and r.json() == {"detail": "Token không hợp lệ"}
    r = await lg.env.http.request(method, concrete(path), json={}, headers={"Authorization": "Basic abc"})
    assert r.status_code == 401 and r.json() == {"detail": "Thiếu token"}


async def test_expired_forged_and_machine_credentials_are_refused(lg, env):
    expired = {"Authorization": "Bearer " + env.identity.token(exp=1, iat=0, nbf=0)}
    r = await env.http.get(RES, headers=expired)
    assert r.status_code == 401 and r.json() == {"detail": "Token hết hạn"}
    from cryptography.hazmat.primitives.asymmetric import rsa
    forged = {"Authorization": "Bearer " + env.identity.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))}
    assert (await env.http.get(RES, headers=forged)).json() == {"detail": "Token không hợp lệ"}
    tool = await env.tool()
    for path in (RES, f"{OPS}/inventory", LNK):   # API key máy KHÔNG phải credential user
        r = await env.http.get(path, headers={"Authorization": f"Bearer {tool.api_key}"})
        assert r.status_code == 401 and r.json() == {"detail": "Token không hợp lệ"}


@pytest.mark.parametrize("method,path", [(m, p) for m, p in owned_ops() if p.startswith("/v1/ops")])
async def test_ops_routes_need_admin_roles(lg, method, path):
    viewer = await lg.user("viewer")
    r = await lg.env.http.request(method, concrete(path), json={}, headers=viewer["headers"])
    expected = "Chỉ admin được thao tác này" if (method == "PATCH" and ("platform-policies" in path or "execution-mode" in path)) \
        else "Chỉ admin/dash_admin được truy cập"
    assert r.status_code == 403 and r.json() == {"detail": expected}, (method, path)
    if expected.startswith("Chỉ admin được"):
        dash = await lg.user("dash_admin")
        r = await lg.env.http.request(method, concrete(path), json={}, headers=dash["headers"])
        assert r.status_code == 403 and r.json() == {"detail": expected}


# ── Owner spoof ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["owner_account_id", "owner", "Owner_Id"])
async def test_legacy_bodies_with_owner_fields_are_rejected_and_write_nothing(lg, env, field):
    victim, attacker, ops, admin = await lg.user(), await lg.user(), await lg.user("dash_admin"), await lg.user("admin")
    own_source = await lg.source(attacker)
    own_res = await lg.resource(attacker, own_source, "mine")
    rows_before = await env.scalar("SELECT count(*) FROM resources")
    spoof = {field: victim["sub"]}
    attempts = [
        ("POST", f"{RES}/sources", attacker, {"name": "x", "platform": "facebook", **spoof}),
        ("PATCH", f"{RES}/sources/{own_source['id']}", attacker, {"name": "y", **spoof}),
        ("POST", RES, attacker, {"source_instance_id": own_source["id"], "platform": "facebook", "input_ref": "z", **spoof}),
        ("POST", f"{RES}/import", attacker, {"source_instance_id": own_source["id"], "platform": "facebook",
                                              "lines": ["a"], **spoof}),
        ("PATCH", f"{RES}/{own_res['id']}", attacker, {"display_name": "y", **spoof}),
        ("POST", f"{OPS}/batches", ops, {"platform": "facebook", **spoof}),
        ("POST", f"{OPS}/batches/none/items", ops, {"resource_ids": ["a"], **spoof}),
        ("PATCH", f"{OPS}/platform-policies/facebook", admin, {"fallback_enabled": True, **spoof}),
        ("PATCH", f"{OPS}/resources/{own_res['id']}/execution-mode", admin, {"execution_mode": "disabled", **spoof}),
        ("POST", f"{LNK}/redeem", None, {"code": "ABCD1234", "tool_user_id": "tool-user-12345", **spoof}),
    ]
    for method, path, who, body in attempts:
        r = await env.http.request(method, path, headers=who["headers"] if who else None, json=body)
        assert r.status_code == 422, (method, path, r.text)
        detail = r.json()["detail"]
        assert detail[0]["type"] == "extra_forbidden" and "input" not in detail[0], (method, path)
        assert victim["sub"] not in r.text   # không phản chiếu giá trị đầu vào
    assert await env.scalar("SELECT count(*) FROM resources") == rows_before
    assert await env.scalar("SELECT count(*) FROM resources WHERE owner_account_id = $1", victim["sub"]) == 0
    assert (await lg.canon(own_res["id"]))["owner_account_id"] == attacker["sub"]
    assert await env.scalar("SELECT count(*) FROM legacy_ingest.resource_sources WHERE owner_account_id = $1",
                            victim["sub"]) == 0


async def test_owner_is_always_the_token_subject_even_for_admin(lg, env):
    admin = await lg.user("admin")
    src = await lg.source(admin)
    res = await lg.resource(admin, src, "admin-made")
    assert res["owner_account_id"] == admin["sub"]
    assert (await lg.canon(res["id"]))["owner_account_id"] == admin["sub"]


# ── Cross-owner ──────────────────────────────────────────────────────────────────────────

async def test_other_users_cannot_touch_or_see_my_data(lg, env):
    a, b, ops = await lg.user(), await lg.user(), await lg.user("dash_admin")
    src = await lg.source(a)
    res = await lg.resource(a, src, "private")
    assert (await lg.call("GET", RES, b)).json() == {"items": []}
    assert (await lg.call("GET", f"{RES}/sources", b)).json() == {"items": []}
    r = await lg.call("POST", RES, b, expect=403, json={"source_instance_id": src["id"], "platform": "facebook",
                                                       "input_ref": "chen-ngang"})
    assert r.json() == {"detail": "Không có quyền thao tác nguồn này"}
    await lg.call("PATCH", f"{RES}/{res['id']}", b, expect=403, json={"display_name": "hack"})
    await lg.call("DELETE", f"{RES}/{res['id']}", b, expect=403)
    await lg.call("PATCH", f"{RES}/sources/{src['id']}", b, expect=403, json={"name": "hack"})
    assert (await lg.canon(res["id"]))["removed"] is False and (await lg.canon(res["id"]))["data"].get("display_name") == "private"
    assert await env.scalar("SELECT count(*) FROM resources") == 1
    seen = (await lg.call("GET", f"{OPS}/inventory", ops)).json()["items"]   # vai trò ops thấy toàn hệ thống (đúng legacy)
    assert [i["id"] for i in seen] == [res["id"]]


async def test_same_input_for_two_owners_gives_two_independent_resources(lg, env):
    a, b = await lg.user(), await lg.user()
    ra = await lg.resource(a, await lg.source(a), "shared-ref")
    rb = await lg.resource(b, await lg.source(b), "shared-ref")
    assert ra["id"] != rb["id"] and await env.scalar("SELECT count(*) FROM resources") == 2
    await lg.call("DELETE", f"{RES}/{ra['id']}", a)
    assert (await lg.call("GET", RES, b)).json()["items"][0]["id"] == rb["id"]


async def test_pairing_codes_and_links_are_account_scoped(lg, env):
    a, b = await lg.user(), await lg.user()
    code = (await lg.call("POST", f"{LNK}/code", a)).json()["code"]
    await lg.call("POST", f"{LNK}/redeem", None, json={"code": code, "tool_user_id": "tool-user-aaaa"})
    assert (await lg.call("GET", LNK, a)).json()["items"][0]["tool_user_id"] == "tool-user-aaaa"
    assert (await lg.call("GET", LNK, b)).json() == {"items": []}
    await lg.call("DELETE", f"{LNK}/tool-user-aaaa", b, expect=404)
    assert len((await lg.call("GET", LNK, a)).json()["items"]) == 1
    # mã cũ không dùng lại được để gắn Tool khác vào account của a
    await lg.call("POST", f"{LNK}/redeem", None, expect=400, json={"code": code, "tool_user_id": "tool-user-bbbb"})
    assert await env.scalar("SELECT count(*) FROM legacy_ingest.account_links") == 1


# ── Không ghi đè dữ liệu do Tool đồng bộ ──────────────────────────────────────────────────

async def test_legacy_cannot_overwrite_or_see_tool_synced_resources(lg, env):
    owner = await lg.user()
    tool = await env.tool(user=owner)
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "tool-page", "display_name": "Do Tool",
         "observed_at": ts(0)}]})
    rid = r.json()["data"]["items"][0]["resource_id"]
    src = await lg.source(owner)
    rejected = await lg.call("POST", RES, owner, expect=409,
                             json={"source_instance_id": src["id"], "platform": "facebook", "resource_type": "page",
                                   "input_ref": "tool-page", "display_name": "Chiếm"})
    assert "do Tool đồng bộ" in rejected.json()["detail"]
    out = (await lg.call("POST", f"{RES}/import", owner, json={
        "source_instance_id": src["id"], "platform": "facebook", "resource_type": "page", "lines": ["tool-page"]})).json()
    assert out["created_count"] == 0 and out["skipped"] == [{"input_ref": "tool-page", "reason": "managed_by_tool"}]
    assert (await lg.call("GET", RES, owner)).json() == {"items": []}   # legacy không thấy resource của Tool
    await lg.call("PATCH", f"{RES}/{rid}", owner, expect=404, json={"display_name": "Chiếm"})
    await lg.call("DELETE", f"{RES}/{rid}", owner, expect=404)
    admin = await lg.user("admin")
    await lg.call("PATCH", f"{OPS}/resources/{rid}/execution-mode", admin, expect=404, json={"execution_mode": "disabled"})
    row = (await env.rows("SELECT * FROM resources WHERE id = $1", rid))[0]
    assert row["data"]["display_name"] == "Do Tool" and row["origin"] == "tool" and row["removed"] is False
    assert row["execution_mode"] == "primary_tool" and row["client_id"] == tool.client_id


async def test_tool_snapshot_full_scope_does_not_remove_web_entered_resources(lg, env):
    from helpers import ingest_full, res
    owner = await lg.user()
    web = await lg.resource(owner, await lg.source(owner), "web-only")
    tool = await env.tool(user=owner)
    await ingest_full(tool, resources=[res("tool-1")])
    assert (await lg.canon(web["id"]))["removed"] is False   # khác client => ngoài phạm vi suy luận removed
    assert [r["id"] for r in (await lg.call("GET", RES, owner)).json()["items"]] == [web["id"]]


# ── Injection / dữ liệu xấu ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("evil", ["'; DROP TABLE resources; --", "x' OR '1'='1", "%", "_", "\\", "{0}", "{}"])
async def test_filters_and_ids_are_parameterised(lg, env, evil):
    u = await lg.user()
    src = await lg.source(u)
    res = await lg.resource(u, src, "innocent", display_name="Innocent")
    ops = await lg.user("dash_admin")
    for params in ({"q": evil}, {"platform": evil}, {"country": evil}, {"source_instance_id": evil}, {"mapping_status": evil}):
        r = await lg.call("GET", RES, u, params=params)
        assert r.status_code == 200
        if "q" not in params or evil not in ("%", "_"):
            assert r.json()["items"] == []
    assert (await lg.call("GET", f"{OPS}/inventory", ops, params={"owner_account_id": evil})).json() == {"items": []}
    await lg.call("PATCH", f"{RES}/{evil}", u, expect=404, json={"display_name": "x"})
    await lg.call("DELETE", f"{LNK}/{evil}", u, expect=404)
    assert await env.scalar("SELECT count(*) FROM resources WHERE id = $1", res["id"]) == 1


async def test_nul_characters_and_oversized_refs_do_not_cause_500(lg, env):
    u = await lg.user()
    src = await lg.source(u)
    body = {"source_instance_id": src["id"], "platform": "facebook"}
    r = await env.http.post(RES, headers=u["headers"], json={**body, "input_ref": "a\u0000b"})
    assert r.status_code in (422,) and "detail" in r.json()
    long_ref = "https://example.test/" + "x" * 450   # > 200: rút gọn bằng hash để vẫn hợp lệ ở canonical
    res = await lg.resource(u, src, long_ref)
    assert res["input_ref"] == long_ref
    row = await lg.canon(res["id"])
    assert row["external_id"].startswith("sha256:") and len(row["external_id"]) <= 200
    assert (await lg.resource(u, src, long_ref))["id"] == res["id"]   # cùng input => cùng danh tính


async def test_unicode_and_odd_resource_types_are_normalised_but_returned_as_typed(lg, env):
    u = await lg.user()
    src = await lg.source(u)
    res = await lg.resource(u, src, "kênh-việt", resource_type="Fan Page!", display_name="Trang Việt")
    assert res["resource_type"] == "Fan Page!" and res["display_name"] == "Trang Việt"
    assert (await lg.canon(res["id"]))["resource_type"] == "fan_page"
    blank = await lg.resource(u, src, "no-type")
    assert blank["resource_type"] == "" and (await lg.canon(blank["id"]))["resource_type"] == "unspecified"


async def test_concurrent_legacy_edits_and_tool_upserts_on_one_resource_never_deadlock(lg, env):
    """Thứ tự khoá thống nhất: khoá thứ tự commit (advisory) TRƯỚC khoá dòng. Đảo thứ tự => deadlock giữa sửa tay và Tool."""
    import asyncio
    owner = await lg.user()
    web = await lg.resource(owner, await lg.source(owner), "contended", resource_type="page")
    tool = await env.tool(user=owner)

    async def legacy_edit(i):
        return (await env.http.patch(f"{RES}/{web['id']}", headers=owner["headers"], json={"display_name": f"edit-{i}"})).status_code

    async def tool_push(i):
        r = await tool.post("/v1/integrations/resources/batch", {"items": [
            {"platform": "facebook", "resource_type": "page", "external_id": "contended", "display_name": f"tool-{i}",
             "observed_at": ts(i + 1)}]})
        return r.status_code

    codes = await asyncio.gather(*([legacy_edit(i) for i in range(8)] + [tool_push(i) for i in range(8)]))
    assert set(codes) == {200}, codes
