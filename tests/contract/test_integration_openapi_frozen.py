"""Hợp đồng công khai của integration-service: bản OpenAPI đóng băng phải hợp lệ, không lộ endpoint nội bộ, và phản ánh đúng
quy tắc an toàn của hợp đồng ingest (không có trường owner_*, mọi endpoint có envelope lỗi ổn định).

(So khớp với `/openapi.json` THỰC TẾ nằm ở services/integration-service/tests/test_openapi_contract.py.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "docs" / "api" / "openapi" / "integration-service.v1.json"
INGEST_SCHEMA = ROOT / "packages" / "contracts" / "ingest" / "2026-10-01" / "ingest.schema.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    assert FROZEN.exists(), "thiếu bản OpenAPI đóng băng của integration-service"
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def test_frozen_openapi_is_a_valid_3x_document_with_public_paths_only(spec):
    assert spec["openapi"].startswith("3.") and spec["info"]["title"] == "integration-service"
    assert spec["paths"], "OpenAPI rỗng"
    for path in spec["paths"]:
        assert path.startswith("/v1/integrations"), f"{path} không thuộc namespace chung /v1/integrations/*"
        assert "/internal" not in path and not path.startswith("/health")


def test_frozen_openapi_has_every_endpoint_required_by_the_integration_api(spec):
    required = {("post", "/v1/integrations/clients"), ("get", "/v1/integrations/clients"),
                ("get", "/v1/integrations/clients/{client_id}"), ("patch", "/v1/integrations/clients/{client_id}"),
                ("post", "/v1/integrations/clients/{client_id}/credentials"),
                ("post", "/v1/integrations/{client_id}/credentials"),
                ("post", "/v1/integrations/clients/{client_id}/credentials/{key_id}/rotate"),
                ("delete", "/v1/integrations/clients/{client_id}/credentials/{key_id}"),
                ("post", "/v1/integrations/handshake"), ("put", "/v1/integrations/projects/{project_key}"),
                ("post", "/v1/integrations/resources/batch"), ("post", "/v1/integrations/snapshots"),
                ("post", "/v1/integrations/snapshots/{snapshot_id}/resources"),
                ("post", "/v1/integrations/snapshots/{snapshot_id}/content"),
                ("post", "/v1/integrations/snapshots/{snapshot_id}/events"),
                ("post", "/v1/integrations/snapshots/{snapshot_id}/metrics"),
                ("post", "/v1/integrations/snapshots/{snapshot_id}/commit"),
                ("get", "/v1/integrations/snapshots/{snapshot_id}"), ("get", "/v1/integrations/audit"),
                ("put", "/v1/integrations/admin/platforms/{key}"), ("put", "/v1/integrations/admin/metrics/{key}"),
                # Phase 2b: jobs (Fallback Tool + user-plane)
                ("post", "/v1/integrations/jobs/claim"), ("post", "/v1/integrations/jobs/{job_id}/complete"),
                ("post", "/v1/integrations/jobs/{job_id}/fail"), ("post", "/v1/integrations/jobs/{job_id}/heartbeat"),
                ("post", "/v1/integrations/jobs"), ("get", "/v1/integrations/jobs"),
                ("get", "/v1/integrations/jobs/{job_id}"), ("post", "/v1/integrations/jobs/{job_id}/cancel")}
    have = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert required <= have, sorted(required - have)


def test_no_tool_specific_endpoint_or_name(spec):
    """CLAUDE.md luật 4-5: không endpoint riêng theo Tool, không hardcode tên Tool."""
    text = json.dumps(spec["paths"]).lower()
    for banned in ("itl", "sible", "facebook-itl", "tiktok-pet"):
        assert f"/{banned}" not in text


def test_ingest_bodies_forbid_owner_fields_in_the_frozen_contract(spec):
    schemas = spec["components"]["schemas"]
    for name in ("SnapshotCreate", "ResourceItem", "ContentItem", "MetricItem", "EventItem", "CommitRequest"):
        assert schemas[name]["additionalProperties"] is False
        assert not [p for p in schemas[name]["properties"] if p.startswith("owner")]


def test_openapi_ingest_models_follow_the_ingest_schema_property_names(spec):
    ingest = json.loads(INGEST_SCHEMA.read_text(encoding="utf-8"))["$defs"]
    pairs = {"ResourceItem": ingest["ResourceBatch"]["properties"]["items"]["items"],
             "ContentItem": ingest["ContentBatch"]["properties"]["items"]["items"],
             "MetricItem": ingest["MetricsBatch"]["properties"]["items"]["items"],
             "EventItem": ingest["EventBatch"]["properties"]["items"]["items"],
             "SnapshotCreate": ingest["SnapshotCreate"], "CommitRequest": ingest["CommitRequest"]}
    for name, contract in pairs.items():
        mine = spec["components"]["schemas"][name]
        assert set(mine["properties"]) == set(contract["properties"]), name
        assert set(mine.get("required", [])) == set(contract.get("required", [])), name


def test_every_error_response_uses_the_stable_error_envelope(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            for status, resp in op["responses"].items():
                if not status.startswith("2"):
                    ref = resp["content"]["application/json"]["schema"]["$ref"]
                    assert ref.endswith("/ErrorEnvelope"), (method, path, status)
    err = spec["components"]["schemas"]["ErrorEnvelope"]
    assert set(err["required"]) >= {"error", "meta"} and err["properties"]["ok"]["default"] is False
    assert "SNAPSHOT_INCOMPLETE" in spec["info"]["x-error-codes"]


def test_job_bodies_forbid_owner_fields_and_lease_is_capped(spec):
    """Job do SERVER gán owner (chủ resource); lease ≤ 900s theo hợp đồng integration-api-v1.md §4."""
    schemas = spec["components"]["schemas"]
    for name in ("JobCreate", "JobClaimRequest", "JobCompleteRequest", "JobFailRequest", "JobHeartbeatRequest"):
        assert schemas[name]["additionalProperties"] is False, name
        assert not [p for p in schemas[name]["properties"] if p.startswith("owner")], name
    assert schemas["JobClaimRequest"]["properties"]["lease_seconds"]["maximum"] == 900
    assert schemas["JobHeartbeatRequest"]["properties"]["lease_seconds"]["maximum"] == 900
