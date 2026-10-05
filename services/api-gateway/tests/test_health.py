"""Test chassis của api-gateway: health, request-id, envelope lỗi."""

from fastapi.testclient import TestClient

from api_gateway import SERVICE_NAME
from api_gateway.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_live_and_ready():
    c = _client()
    assert c.get("/health/live").json() == {"status": "live", "service": SERVICE_NAME}
    assert c.get("/health/ready").status_code == 200


def test_version_reports_contract_version():
    body = _client().get("/health/version").json()
    assert body["service"] == SERVICE_NAME and body["contract_version"] == "2026-10-01"


def test_unknown_route_uses_stable_error_envelope():
    r = _client().get("/v1/khong-ton-tai")
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["X-Request-Id"]


def test_openapi_is_served():
    spec = _client().get("/openapi.json").json()
    assert spec["info"]["title"] == SERVICE_NAME
