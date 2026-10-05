"""SQL luôn tham số hoá (đầu vào độc hại chỉ là dữ liệu), ký tự NUL không gây 500, và DB user không chạm DB khác."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import devenv
from helpers import canonical, commit, new_snapshot, post, push, res

pytestmark = pytest.mark.needs_pg
SRC = Path(__file__).resolve().parents[1] / "src" / "integration_service"
INJECTION = "x'); DROP TABLE resources; --"
TABLES = ("integration_clients", "integration_credentials", "resources", "content_items", "snapshots", "outbox",
          "projects", "idempotency_keys", "audit_log")


async def tables_intact(env) -> None:
    for t in TABLES:
        assert await env.scalar(f"SELECT to_regclass('{t}')") is not None, t


async def test_hostile_strings_in_every_input_are_stored_as_data(env):
    tool = await env.tool()
    user = env.user(sub=tool.owner)
    r = await env.http.post("/v1/integrations/clients", headers=user["headers"], json={
        "tool_name": INJECTION, "client_kind": "primary_tool", "platforms": ["facebook"]})
    assert r.status_code == 201 and r.json()["data"]["tool_name"] == INJECTION
    assert (await tool.put("/v1/integrations/projects/itl", {"name": INJECTION, "metadata": {"k": INJECTION}})).status_code == 200
    sid = await new_snapshot(tool, type="delta", expected={"resources": 1, "content": 1})
    await push(tool, sid, "resources", [res(INJECTION, display_name=INJECTION, username=INJECTION, url=INJECTION)])
    await push(tool, sid, "content", [post(INJECTION, res_ext=INJECTION)])
    await commit(tool, sid)
    item = (await canonical(env, "resources"))["items"][0]
    assert item["external_id"] == INJECTION and item["display_name"] == INJECTION
    assert (await canonical(env, "content"))["items"][0]["external_id"] == INJECTION
    # query / path / header độc hại
    admin = await env.verified_user(role="admin")
    for params in ({"owner": INJECTION}, {"status": INJECTION}, {"limit": "1; DROP TABLE x"}):
        r = await env.http.get("/v1/integrations/clients", headers=admin["headers"], params=params)
        assert r.status_code in (200, 422)
    for path in (f"/v1/integrations/clients/{INJECTION}", f"/v1/integrations/audit?client_id={INJECTION}&action={INJECTION}"):
        r = await env.http.get(path, headers=admin["headers"])
        assert r.status_code in (200, 404, 422)
    await env.http.get("/internal/v1/feed", params={"types": INJECTION}, headers={"X-Service-Key": "x"})
    await tool.post("/v1/integrations/handshake", {"installation": {"device_id": INJECTION}}, headers={
        "X-Installation-Id": INJECTION, "X-Contract-Version": INJECTION})
    await tables_intact(env)


async def test_nul_characters_never_cause_a_server_error(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    responses = [
        await env.http.get("/v1/integrations/clients/abc%00def", headers=admin["headers"]),
        await env.http.get("/v1/integrations/clients", headers=admin["headers"], params={"owner": "a\x00b"}),
        await env.http.get("/v1/integrations/audit", headers=admin["headers"], params={"action": "a\x00b"}),
        await tool.put("/v1/integrations/projects/itl", {"name": "a\u0000b"}),
        await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "a\u0000b"}}),
        await env.http.get("/internal/v1/canonical/resources", params={"after_revision": "1\x00"},
                           headers={"X-Service-Key": "x"}),
    ]
    for r in responses:
        assert r.status_code < 500, (r.request.url, r.status_code, r.text)
    assert await env.scalar("SELECT count(*) FROM projects") == 0 and await env.scalar("SELECT count(*) FROM installations") == 0


def test_only_known_safe_modules_build_sql_with_f_strings():
    """Mọi chỗ ghép chuỗi SQL bằng f-string phải nằm trong danh sách đã duyệt (tên bảng/cột là HẰNG trong code)."""
    approved = {"ingest/merge.py", "feed/canonical.py", "clients/repository.py", "audit/repository.py"}
    offenders = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                text = "".join(p.value for p in node.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
                if any(k in text.upper() for k in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", " FROM ", "WHERE ")):
                    offenders.setdefault(str(path.relative_to(SRC)).replace("\\", "/"), []).append(node.lineno)
    assert set(offenders) <= approved, f"f-string SQL ngoài danh sách duyệt: {offenders}"


def test_dynamic_identifiers_come_only_from_code_constants():
    """update_fields chỉ nhận cột trong whitelist; canonical/merge chỉ ghép tên bảng/cột từ hằng ChildSpec/_TABLE."""
    from integration_service.clients import repository
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(repository.update_fields(None, "cli_x", {"status; DROP TABLE x": 1}, None))   # type: ignore[arg-type]
    from integration_service.feed import canonical
    assert set(canonical._SELECT) == set(canonical.KINDS) and set(canonical._TABLE) == set(canonical.KINDS)


async def test_service_role_cannot_touch_other_services_databases():
    import asyncpg
    for other in ("identity", "analytics", "reward"):
        dsn = devenv.pg_dsn("integration", database=f"{other}_db")
        with pytest.raises(asyncpg.PostgresError):          # permission denied / không được CONNECT
            conn = await asyncpg.connect(dsn, timeout=5)
            try:
                await conn.fetch("SELECT 1 FROM information_schema.tables LIMIT 1")
                await conn.fetch("SELECT * FROM accounts")
            finally:
                await conn.close()


async def test_integration_role_is_not_superuser_and_owns_only_its_database(db):
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        assert row["rolsuper"] is False and row["rolcreaterole"] is False
        assert await conn.fetchval("SELECT current_database()") == "integration_db"
