"""Parity với OpenAPI legacy (docs/api/legacy/admin-server.openapi.json) cho MỌI operation do integration-service sở hữu
theo route-map: cùng đường dẫn, method, operationId, tham số, ràng buộc body, mã 200; và mỗi operation đều có test hành vi
(status + shape) đăng ký bằng `@covers` ở test_legacy_acl.py / test_legacy_security.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from conftest import REVEAL_KEY
from legacy_cover import COVERED

from integration_service.legacy_acl.router import build_routers

ROOT = Path(__file__).resolve().parents[3]
LEGACY = json.loads((ROOT / "docs" / "api" / "legacy" / "admin-server.openapi.json").read_text(encoding="utf-8"))
ROUTE_MAP = json.loads((ROOT / "infra" / "gateway" / "route-map.json").read_text(encoding="utf-8"))["rules"]
TARGET = "integration-service"
SCHEMA_KEYS = ("type", "minLength", "maxLength", "pattern", "default", "minItems", "maxItems")


def route_target(path: str) -> str | None:
    for rule in ROUTE_MAP:
        prefix = rule["legacy_prefix"]
        if path == prefix or path.startswith(prefix + "/"):
            return rule["target"]
    return None


def owned_operations() -> list[tuple[str, str]]:
    return sorted((m.upper(), p) for p, ops in LEGACY["paths"].items() if route_target(p) == TARGET for m in ops)


def mine() -> dict:
    app = FastAPI()
    for router in build_routers(include_in_schema=True):
        app.include_router(router)
    return app.openapi()


MINE = mine()


def resolve(spec: dict, schema: dict) -> dict:
    if "$ref" in schema:
        return spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def param_type(schema: dict):
    """Kiểu + default của tham số (nhánh khác null với `X | None`)."""
    kinds = [schema.get("type")] if "type" in schema else [a.get("type") for a in schema.get("anyOf", [])
                                                           if a.get("type") != "null"]
    return (kinds[0] if kinds else None, schema.get("default"))


def body_schema(spec: dict, op: dict) -> dict | None:
    body = op.get("requestBody")
    if not body:
        return None
    return resolve(spec, body["content"]["application/json"]["schema"])


def prop_shape(spec: dict, prop: dict) -> dict:
    prop = resolve(spec, prop)
    shape = {k: prop[k] for k in SCHEMA_KEYS if k in prop}
    for variant in prop.get("anyOf", []):   # field tuỳ chọn (str | None): so ràng buộc của nhánh str
        shape.update({k: variant[k] for k in SCHEMA_KEYS if k in variant and variant.get("type") != "null"})
    return shape


def test_the_owned_set_is_what_the_route_map_says():
    ops = owned_operations()
    assert len(ops) == 26, ops   # 4 viewer/links + 9 resources + 13 ops
    assert {p.split("/")[2] for _, p in ops} == {"viewer", "resources", "ops"}


@pytest.mark.parametrize("method,path", owned_operations())
def test_operation_matches_the_legacy_contract(method, path):
    legacy_op = LEGACY["paths"][path][method.lower()]
    assert path in MINE["paths"] and method.lower() in MINE["paths"][path], f"thiếu {method} {path}"
    mine_op = MINE["paths"][path][method.lower()]
    assert mine_op["operationId"] == legacy_op["operationId"]
    assert "200" in mine_op["responses"] and "200" in legacy_op["responses"]
    legacy_params = {(p["in"], p["name"]): (p.get("required", False), param_type(p["schema"]))
                     for p in legacy_op.get("parameters", [])}
    my_params = {(p["in"], p["name"]): (p.get("required", False), param_type(p["schema"]))
                 for p in mine_op.get("parameters", [])}
    if "authorization" not in {n for _, n in legacy_params}:
        my_params = {k: v for k, v in my_params.items() if k[1] != "authorization"}
    assert my_params == legacy_params, path
    legacy_body, my_body = body_schema(LEGACY, legacy_op), body_schema(MINE, mine_op)
    assert (legacy_body is None) == (my_body is None)
    if legacy_body:
        assert set(my_body["properties"]) == set(legacy_body["properties"])
        assert set(my_body.get("required", [])) == set(legacy_body.get("required", []))
        for name, prop in legacy_body["properties"].items():
            assert prop_shape(MINE, my_body["properties"][name]) == prop_shape(LEGACY, prop), (path, name)


def test_every_owned_operation_has_a_behaviour_test():
    # nạp các module test để các decorator @covers chạy (collection có thể chưa import chúng khi chạy riêng file này)
    import test_legacy_acl  # noqa: F401
    import test_legacy_security  # noqa: F401
    owned = {f"{m} {p}" for m, p in owned_operations()}
    assert owned <= COVERED, f"operation legacy chưa có test hành vi: {sorted(owned - COVERED)}"
    assert COVERED <= owned, f"@covers khai operation không thuộc integration-service: {sorted(COVERED - owned)}"


async def test_the_service_serves_every_owned_operation_and_hides_them_from_public_openapi():
    import httpx
    from integration_service.app import create_app
    from integration_service.config import Settings
    app = create_app(Settings(port=1, log_level="INFO", database_url="postgresql://x:y@127.0.0.1:1/z", env="test",
                              issuer="http://i.invalid", jwks_url="http://i.invalid/jwks", reveal_key=REVEAL_KEY))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        for method, path in owned_operations():
            r = await client.request(method, path.replace("{", "").replace("}", ""), json={})
            assert r.status_code in (401, 422), (method, path, r.status_code)   # tồn tại: đòi token / sai body
            assert set(r.json()) == {"detail"}, (method, path)                   # lỗi kiểu legacy
        assert (await client.get("/v1/ops/khong-co")).status_code == 404
    # Route legacy (resources/ops/viewer-links) phải ẨN khỏi OpenAPI công khai; `/v1/user/*` là API công khai không-legacy.
    assert not [p for p in app.openapi()["paths"] if p.startswith(("/v1/resources", "/v1/ops", "/v1/viewer/links"))]
