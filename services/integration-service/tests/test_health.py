"""Test chassis của integration-service: health, request-id, envelope lỗi, readiness chỉ phụ thuộc DB của chính nó."""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from integration_service import SERVICE_NAME
from integration_service.app import create_app
from integration_service.config import ConfigError, Settings

DEAD = Settings(port=8431, log_level="INFO", database_url="postgresql://x:y@127.0.0.1:1/z", env="test",
                issuer="http://identity.test", jwks_url="http://identity.test/.well-known/jwks.json",
                reveal_key="hYPGsUcmXi1PEc93txnTNeynFkYtJ8-8u9mxfqh3lYc=")


def _client(settings: Settings = DEAD) -> TestClient:
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_live_is_up_even_when_database_and_identity_are_down():
    c = _client()
    assert c.get("/health/live").json() == {"status": "live", "service": SERVICE_NAME}


def test_ready_is_503_when_own_database_is_unreachable_and_never_leaks_config():
    r = _client().get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and r.json()["checks"] == {"database": False}
    assert "postgresql" not in r.text and "identity.test" not in r.text      # không lộ DSN/cấu hình


def test_version_reports_contract_version():
    body = _client().get("/health/version").json()
    assert body["service"] == SERVICE_NAME and body["contract_version"] == "2026-10-01"


def test_unknown_route_uses_stable_error_envelope():
    r = _client().get("/v1/khong-ton-tai")
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["X-Request-Id"]


def test_wrong_method_uses_stable_error_envelope():
    r = _client().delete("/v1/integrations/handshake")
    assert r.status_code == 405 and r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_openapi_is_served():
    spec = _client().get("/openapi.json").json()
    assert spec["info"]["title"] == SERVICE_NAME


def test_request_id_is_echoed_when_safe_and_replaced_when_not():
    c = _client()
    assert c.get("/health/live", headers={"X-Request-Id": "req-abc-12345678"}).headers["X-Request-Id"] == "req-abc-12345678"
    bad = c.get("/health/live", headers={"X-Request-Id": "bad id with spaces!"}).headers["X-Request-Id"]
    assert bad.startswith("req_") and " " not in bad


def test_cors_is_not_enabled_by_the_service_gateway_owns_it():
    r = _client().options("/v1/integrations/clients", headers={"Origin": "http://evil.test",
                                                                "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = _client().get("/health/live", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}      # tuyệt đối không có `*`


@pytest.mark.parametrize("drop", ["database_url", "issuer", "jwks_url"])
def test_app_refuses_to_build_without_required_config(drop):
    with pytest.raises(ConfigError):
        create_app(dataclasses.replace(DEAD, **{drop: ""}))


def test_unhandled_errors_do_not_leak_tracebacks(monkeypatch):
    from integration_service.credentials import service as cred

    async def boom(*a, **kw):
        raise RuntimeError("chi tiet noi bo bi lo")

    monkeypatch.setattr(cred.CredentialService, "authenticate", boom)
    r = _client().post("/v1/integrations/handshake", json={"installation": {"device_id": "d"}},
                       headers={"Authorization": "Bearer mpk_" + "a" * 12 + "." + "b" * 43})
    assert r.status_code == 500 and r.json()["error"]["code"] == "SERVER_ERROR"
    assert "chi tiet noi bo" not in r.text and "Traceback" not in r.text


@pytest.mark.needs_pg
async def test_ready_is_200_with_a_migrated_database(app, http):
    r = await http.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"database": True}
