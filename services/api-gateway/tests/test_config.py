"""Test cấu hình của api-gateway: mặc định hợp lệ và fail-fast khi sai."""

import pytest

from api_gateway.config import ConfigError, load_settings


def test_defaults_are_valid():
    s = load_settings({})
    assert s.port == 8400 and s.log_level == "INFO" and s.database_url == ""


def test_env_overrides():
    s = load_settings({"API_GATEWAY_PORT": "9001", "API_GATEWAY_LOG_LEVEL": "debug",
                       "API_GATEWAY_DATABASE_URL": "postgresql://u:p@h:5432/—"})
    assert s.port == 9001 and s.log_level == "DEBUG"


@pytest.mark.parametrize("env", [
    {"API_GATEWAY_PORT": "abc"},
    {"API_GATEWAY_PORT": "0"},
    {"API_GATEWAY_PORT": "70000"},
    {"API_GATEWAY_LOG_LEVEL": "LOUD"},
    {"API_GATEWAY_DATABASE_URL": "sqlite:///x.db"},
    {"API_GATEWAY_DATABASE_URL": "mysql://u:p@h/db"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)
