"""Chassis của identity-service. Không cần DB: DB được trỏ vào cổng chết để kiểm tra khả năng chịu lỗi."""

import httpx
import pytest

from identity_service import SERVICE_NAME
from identity_service.app import create_app
from identity_service.config import ConfigError, Settings
from identity_service.db import Database


def _settings(**kw) -> Settings:
    base = dict(port=8430, log_level="INFO", database_url="postgresql://identity_svc:x@127.0.0.1:1/identity_db",
                env="test", jwt_compat_secret="test-secret-at-least-16-chars")
    base.update(kw)
    return Settings(**base)


def _client(app=None):
    app = app or create_app(_settings(), database=Database(_settings().database_url))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_live_ok_even_when_database_is_down():
    async with _client() as c:
        r = await c.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "live", "service": SERVICE_NAME}


async def test_ready_is_503_not_crash_when_database_is_down():
    async with _client() as c:
        r = await c.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"database": False}


async def test_api_returns_503_not_500_when_database_is_down():
    """DB chết: người dùng thấy 503 'tạm thời không khả dụng', tiến trình không crash."""
    async with _client() as c:
        r = await c.post("/v1/auth/check-username", json={"username": "abc"})
        again = await c.get("/health/live")
    assert r.status_code == 503 and r.json() == {"detail": "Dịch vụ tạm thời không khả dụng"}
    assert again.status_code == 200


async def test_version_and_openapi():
    async with _client() as c:
        assert (await c.get("/health/version")).json()["service"] == SERVICE_NAME
        spec = (await c.get("/openapi.json")).json()
    paths = set(spec["paths"])
    assert {"/v1/auth/login", "/v1/auth/register", "/v1/admin/keys", "/v1/admin/users"} <= paths


async def test_unknown_route_has_legacy_shape():
    async with _client() as c:
        r = await c.get("/v1/khong-ton-tai")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_serving_config_is_required_to_start():
    with pytest.raises(ConfigError, match="IDENTITY_DATABASE_URL"):
        create_app(_settings(database_url=""))
    with pytest.raises(ConfigError, match="IDENTITY_JWT_COMPAT_SECRET"):
        create_app(_settings(jwt_compat_secret=""))
    with pytest.raises(ConfigError, match="dev"):
        create_app(_settings(env="production", jwt_compat_secret="dev-secret-thay-doi-khi-deploy"))
