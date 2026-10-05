"""Hợp đồng OpenAPI: `/openapi.json` thực tế phải KHỚP bản đóng băng `docs/api/openapi/integration-service.v1.json`.

Thay đổi tương thích (thêm endpoint/field) cũng làm test đỏ cho tới khi cố ý sinh lại bản đóng băng
(`python -m integration_service.openapi_dump --write`) — thay đổi PHÁ VỠ thì phải là contract version mới. Phần
`breaking_changes` + các ca đột biến chứng minh test bắt được từng loại phá vỡ.
"""

from __future__ import annotations

import copy
import json

import pytest

from integration_service.openapi_dump import FROZEN, build_spec, render

PUBLIC_PREFIXES = ("/v1/integrations",)
WRITE_METHODS = {"post", "put", "patch"}


@pytest.fixture(scope="module")
def spec() -> dict:
    return build_spec()


@pytest.fixture(scope="module")
def frozen() -> dict:
    assert FROZEN.exists(), "chưa có bản đóng băng: chạy `python -m integration_service.openapi_dump --write`"
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def breaking_changes(old: dict, new: dict) -> list[str]:
    """Liệt kê thay đổi PHÁ VỠ từ `old` sang `new` (thêm tính năng tương thích thì không tính)."""
    out: list[str] = []
    for path, ops in old["paths"].items():
        for method, op in ops.items():
            cur = new["paths"].get(path, {}).get(method)
            if cur is None:
                out.append(f"mất endpoint {method.upper()} {path}")
                continue
            for status in op["responses"]:
                if status not in cur["responses"]:
                    out.append(f"mất response {status} của {method.upper()} {path}")
            old_params = {(p["in"], p["name"]): p for p in op.get("parameters", [])}
            new_params = {(p["in"], p["name"]): p for p in cur.get("parameters", [])}
            for key, p in old_params.items():
                if key not in new_params:
                    out.append(f"mất tham số {key} của {method.upper()} {path}")
                elif new_params[key].get("required") and not p.get("required"):
                    out.append(f"tham số {key} thành bắt buộc ở {method.upper()} {path}")
            for key, p in new_params.items():
                if key not in old_params and p.get("required"):
                    out.append(f"thêm tham số BẮT BUỘC {key} ở {method.upper()} {path}")
            if ("requestBody" in cur) and ("requestBody" not in op):
                out.append(f"thêm body ở {method.upper()} {path}")
            if "security" in op and cur.get("security") != op["security"]:
                out.append(f"đổi security của {method.upper()} {path}")
    old_s, new_s = old["components"]["schemas"], new["components"]["schemas"]
    for name, schema in old_s.items():
        if name not in new_s:
            out.append(f"mất schema {name}")
            continue
        cur = new_s[name]
        for prop in schema.get("properties", {}):
            if prop not in cur.get("properties", {}):
                out.append(f"mất field {name}.{prop}")
            else:
                if schema["properties"][prop].get("enum") and not set(schema["properties"][prop]["enum"]) <= set(
                        cur["properties"][prop].get("enum", schema["properties"][prop]["enum"])):
                    out.append(f"thu hẹp enum {name}.{prop}")
                if schema["properties"][prop].get("type") != cur["properties"][prop].get("type"):
                    out.append(f"đổi kiểu {name}.{prop}")
        for req in cur.get("required", []):
            if req not in schema.get("required", []) and name.endswith(("Create", "Request", "Put", "Patch", "Upsert",
                                                                       "Batch")):
                out.append(f"field {name}.{req} thành bắt buộc")
        if schema.get("additionalProperties") is False and cur.get("additionalProperties") is not False:
            out.append(f"nới additionalProperties của {name}")
    for code in old["info"].get("x-error-codes", []):
        if code not in new["info"].get("x-error-codes", []):
            out.append(f"mất mã lỗi {code}")
    return out


# ── So khớp với bản đóng băng ───────────────────────────────────────────────────

def test_live_openapi_equals_frozen_contract(spec, frozen):
    assert render(spec) == render(frozen), (
        "OpenAPI thực tế khác bản đóng băng. Nếu thay đổi là CỐ Ý và tương thích: sinh lại bằng "
        "`python -m integration_service.openapi_dump --write`; nếu phá vỡ: tạo contract version mới.")


def test_no_breaking_change_against_frozen(spec, frozen):
    assert breaking_changes(frozen, spec) == []


def test_served_openapi_json_is_the_same_document(app_with_docs):
    import httpx

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_with_docs), base_url="http://x") as c:
            return (await c.get("/openapi.json")).json()
    import asyncio
    assert asyncio.run(fetch()) == json.loads(render(build_spec()))


@pytest.fixture
def app_with_docs():
    from integration_service.app import create_app
    from integration_service.config import Settings
    return create_app(Settings(port=1, log_level="INFO", database_url="postgresql://a:b@127.0.0.1:1/x", env="test",
                               issuer="http://identity.invalid", jwks_url="http://identity.invalid/jwks"))


# ── Test của test: bắt được từng kiểu phá vỡ ────────────────────────────────────

def mutated(spec: dict, fn) -> dict:
    clone = copy.deepcopy(spec)
    fn(clone)
    return clone


@pytest.mark.parametrize("name,fn,expect", [
    ("xoá endpoint", lambda s: s["paths"].pop("/v1/integrations/handshake"), "mất endpoint POST /v1/integrations/handshake"),
    ("xoá method", lambda s: s["paths"]["/v1/integrations/clients"].pop("post"), "mất endpoint POST /v1/integrations/clients"),
    ("xoá response 409", lambda s: s["paths"]["/v1/integrations/snapshots"]["post"]["responses"].pop("409"),
     "mất response 409"),
    ("xoá tham số", lambda s: s["paths"]["/v1/integrations/handshake"]["post"]["parameters"].pop(0), "mất tham số"),
    ("tham số thành bắt buộc", lambda s: s["paths"]["/v1/integrations/handshake"]["post"]["parameters"][0].update(required=True),
     "thành bắt buộc"),
    ("xoá schema", lambda s: s["components"]["schemas"].pop("CommitOut"), "mất schema CommitOut"),
    ("xoá field phản hồi", lambda s: s["components"]["schemas"]["CommitOut"]["properties"].pop("revision_to"),
     "mất field CommitOut.revision_to"),
    ("đổi kiểu field", lambda s: s["components"]["schemas"]["CommitOut"]["properties"]["rejected_total"].update(type="string"),
     "đổi kiểu CommitOut.rejected_total"),
    ("thu hẹp enum", lambda s: s["components"]["schemas"]["ClientCreate"]["properties"]["client_kind"].update(
        enum=["primary_tool"]) if "enum" in s["components"]["schemas"]["ClientCreate"]["properties"]["client_kind"]
        else s["components"]["schemas"].pop("ClientCreate"), "ClientCreate"),
    ("nới additionalProperties (cho phép owner_*)", lambda s: s["components"]["schemas"]["SnapshotCreate"].pop(
        "additionalProperties"), "nới additionalProperties của SnapshotCreate"),
    ("xoá mã lỗi", lambda s: s["info"]["x-error-codes"].remove("IDEMPOTENCY_CONFLICT"), "mất mã lỗi"),
])
def test_breaking_change_detector_catches(spec, name, fn, expect):
    found = breaking_changes(spec, mutated(spec, fn))
    assert found and any(expect in f for f in found), (name, found)


def test_additive_changes_are_not_breaking_but_still_fail_equality(spec):
    def add(s):
        s["paths"]["/v1/integrations/moi"] = {"get": {"responses": {"200": {"description": "x"}}}}
        s["components"]["schemas"]["CommitOut"]["properties"]["extra"] = {"type": "string"}
    changed = mutated(spec, add)
    assert breaking_changes(spec, changed) == []          # tương thích
    assert render(changed) != render(spec)                 # nhưng phải có chủ đích sinh lại bản đóng băng


# ── Chất lượng tài liệu: mọi endpoint công khai ─────────────────────────────────

def operations(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            yield path, method, op


def test_every_public_endpoint_is_documented_with_errors_security_and_envelope(spec):
    ids = set()
    seen = 0
    for path, method, op in operations(spec):
        seen += 1
        assert path.startswith(PUBLIC_PREFIXES), path                     # không lộ /internal, /health
        assert op.get("summary") and len(op.get("description", "")) > 40, (method, path)
        assert op["operationId"] not in ids
        ids.add(op["operationId"])
        statuses = set(op["responses"])
        assert statuses & {"200", "201"}, (method, path)
        assert {"401", "422", "503"} <= statuses, (method, path)         # lỗi xác thực / validation / dependency chết
        if op.get("security") or any("machine" in t for t in op.get("tags", [])):
            assert "429" in statuses or method == "get" or "admin" in op.get("tags", []), (method, path)
        for status, resp in op["responses"].items():
            schema = resp["content"]["application/json"]["schema"]["$ref"]
            if status.startswith("2"):
                assert schema.startswith("#/components/schemas/Envelope_"), (method, path, status)
                example = resp["content"]["application/json"]["example"]
                assert example["ok"] is True and "data" in example and "request_id" in example["meta"], (method, path)
            else:
                assert schema.endswith("/ErrorEnvelope"), (method, path, status)       # không phải HTTPValidationError
                err = resp["content"]["application/json"]["example"]["error"]
                assert err["code"] and err["message"]
        assert op.get("security") or "machine" in op.get("tags", []), (method, path)
    assert seen >= 24


def test_machine_write_endpoints_document_body_example_and_idempotency_header(spec):
    for path, method, op in operations(spec):
        if "machine" not in op.get("tags", []) or method not in WRITE_METHODS:
            continue
        names = {p["name"] for p in op["parameters"]}
        assert "Idempotency-Key" in names, (method, path)
        body = op["requestBody"]["content"]["application/json"]
        assert body["schema"]["$ref"].startswith("#/components/schemas/"), (method, path)
        ref = body["schema"]["$ref"].rsplit("/", 1)[1]
        assert ref in spec["components"]["schemas"], ref
    # các body ingest quan trọng có ví dụ
    for path in ("/v1/integrations/snapshots/{snapshot_id}/content", "/v1/integrations/resources/batch"):
        assert "example" in spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]


def test_security_schemes_separate_user_and_machine_planes(spec):
    schemes = spec["components"]["securitySchemes"]
    assert set(schemes) == {"UserAccessToken", "MachineApiKey"}
    assert schemes["MachineApiKey"]["bearerFormat"].startswith("mpk_")
    machine_ops = [op for _, _, op in operations(spec) if "machine" in op.get("tags", [])]
    user_ops = [op for _, _, op in operations(spec) if "machine" not in op.get("tags", [])]
    assert machine_ops and user_ops
    assert all(list(op["security"][0]) == ["MachineApiKey"] for op in machine_ops)
    assert all(list(op["security"][0]) == ["UserAccessToken"] for op in user_ops)


def test_ingest_models_in_openapi_forbid_extra_properties(spec):
    schemas = spec["components"]["schemas"]
    for name in ("SnapshotCreate", "ResourceItem", "ContentItem", "MetricItem", "EventItem", "CommitRequest",
                 "ResourceUpsertItem", "HandshakeRequest", "ProjectUpsert", "ClientCreate"):
        assert schemas[name].get("additionalProperties") is False, f"{name} cho phép trường lạ (owner spoof)"
        assert not [p for p in schemas[name].get("properties", {}) if p.startswith("owner")], name


def test_error_code_catalogue_covers_every_code_the_service_can_return(spec):
    from manager_contracts import ErrorCode

    from integration_service.errors import Extra
    codes = set(spec["info"]["x-error-codes"])
    assert {c.value for c in ErrorCode} <= codes and {c.value for c in Extra} <= codes
