"""Test chassis của analytics-service: health, request-id, envelope lỗi, fail-fast cấu hình, DB chết."""

import time

import pytest
from fastapi.testclient import TestClient

from analytics_service import SERVICE_NAME
from analytics_service.app import create_app
from analytics_service.config import ConfigError, Settings, load_settings

DEAD_DSN = "postgresql://nobody:x@127.0.0.1:1/none"   # cổng 1: từ chối kết nối ngay


def _settings(**over) -> Settings:
    base = dict(port=8432, log_level="INFO", database_url=DEAD_DSN, redis_url="", issuer="http://identity.test",
                audience="manager-api")
    base.update(over)
    return Settings(**base)


def _client(**over) -> TestClient:
    return TestClient(create_app(_settings(**over), start_consumer=False), raise_server_exceptions=False)


def test_live_is_up_even_when_database_is_dead():
    c = _client()
    assert c.get("/health/live").json() == {"status": "live", "service": SERVICE_NAME}


def test_ready_is_503_when_database_is_dead_and_does_not_hang():
    c = _client()
    t0 = time.monotonic()
    r = c.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"database": False}
    assert time.monotonic() - t0 < 5


def test_ready_does_not_depend_on_redis_or_sources():
    # Redis/nguồn feed trỏ vào cổng chết, DB cũng chết: chỉ `database` xuất hiện trong checks.
    c = _client(redis_url="redis://127.0.0.1:1/0", integration_url="http://127.0.0.1:1",
                integration_feed_key="k" * 20)
    assert set(c.get("/health/ready").json()["checks"]) == {"database"}


def test_version_reports_contract_version():
    body = _client().get("/health/version").json()
    assert body["service"] == SERVICE_NAME and body["contract_version"] == "2026-10-01"


def test_unknown_route_uses_envelope_on_new_path_and_detail_on_legacy_path():
    c = _client()
    r = c.get("/v1/analytics/khong-ton-tai")
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["X-Request-Id"]
    legacy = c.get("/v1/viewer/khong-ton-tai")
    assert legacy.status_code == 404 and set(legacy.json()) == {"detail"}


async def test_api_returns_503_fast_when_database_is_dead():
    import httpx

    from analytics_testsupport import Issuer
    from manager_sdk import TokenVerifier
    issuer = Issuer()
    app = create_app(_settings(), verifier=TokenVerifier(issuer="http://identity.test", audience="manager-api",
                                                        jwks_url="http://identity.test/jwks",
                                                        http_client=issuer.http_client()), start_consumer=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://analytics.test") as c:
        t0 = time.monotonic()
        r = await c.get("/v1/analytics/summary", headers=issuer.headers("a" * 32))
        assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
        legacy = await c.get("/v1/viewer/summary", headers=issuer.headers("a" * 32))
        assert legacy.status_code == 503 and set(legacy.json()) == {"detail"}
        assert time.monotonic() - t0 < 8


def test_openapi_is_served_and_has_unique_operation_ids():
    spec = _client().get("/openapi.json").json()
    assert spec["info"]["title"] == SERVICE_NAME
    ops = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert len(ops) == len(set(ops))


@pytest.mark.parametrize("missing", ["ANALYTICS_DATABASE_URL", "ANALYTICS_ISSUER"])
def test_serving_without_required_config_fails_fast(missing):
    env = {"ANALYTICS_DATABASE_URL": "postgresql://u:p@h:5432/analytics_db", "ANALYTICS_ISSUER": "http://identity.test"}
    env.pop(missing)
    with pytest.raises(ConfigError):
        create_app(load_settings(env), start_consumer=False)
