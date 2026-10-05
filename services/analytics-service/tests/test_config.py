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
