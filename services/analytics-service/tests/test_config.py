"""Test cấu hình của analytics-service: mặc định hợp lệ và fail-fast khi sai."""

import pytest

from analytics_service.config import ConfigError, load_settings


def test_defaults_are_valid():
    s = load_settings({})
    assert s.port == 8432 and s.log_level == "INFO" and s.database_url == ""


def test_env_overrides():
    s = load_settings({"ANALYTICS_PORT": "9001", "ANALYTICS_LOG_LEVEL": "debug",
                       "ANALYTICS_DATABASE_URL": "postgresql://u:p@h:5432/analytics_db"})
    assert s.port == 9001 and s.log_level == "DEBUG"


@pytest.mark.parametrize("env", [
    {"ANALYTICS_PORT": "abc"},
    {"ANALYTICS_PORT": "0"},
    {"ANALYTICS_PORT": "70000"},
    {"ANALYTICS_LOG_LEVEL": "LOUD"},
    {"ANALYTICS_DATABASE_URL": "sqlite:///x.db"},
    {"ANALYTICS_DATABASE_URL": "mysql://u:p@h/db"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_new_settings_defaults_and_overrides():
    s = load_settings({})
    assert s.stale_after_s == 3600 and s.cache_ttl_s == 300 and s.consumer_max_attempts == 3
    assert not s.integration_enabled and not s.identity_feed_enabled and s.audience == "manager-api"
    s = load_settings({"ANALYTICS_INTEGRATION_URL": "http://127.0.0.1:8431/", "ANALYTICS_INTEGRATION_FEED_KEY": "k" * 24,
                       "ANALYTICS_ISSUER": "http://127.0.0.1:8430", "ANALYTICS_STALE_AFTER_S": "60"})
    assert s.integration_enabled and s.integration_url == "http://127.0.0.1:8431" and s.stale_after_s == 60
    assert s.jwks_url == "http://127.0.0.1:8430/.well-known/jwks.json"


@pytest.mark.parametrize("env", [
    {"ANALYTICS_REDIS_URL": "http://x"},
    {"ANALYTICS_INTEGRATION_URL": "ftp://x"},
    {"ANALYTICS_INTEGRATION_FEED_KEY": "ngan"},
    {"ANALYTICS_STALE_AFTER_S": "0"},
    {"ANALYTICS_CACHE_TTL_S": "abc"},
])
def test_new_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)
