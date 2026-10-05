"""Test cấu hình của integration-service: mặc định hợp lệ và fail-fast khi sai."""

import pytest

from integration_service.config import ConfigError, load_settings


def test_defaults_are_valid():
    s = load_settings({})
    assert s.port == 8431 and s.log_level == "INFO" and s.database_url == ""


def test_env_overrides():
    s = load_settings({"INTEGRATION_PORT": "9001", "INTEGRATION_LOG_LEVEL": "debug",
                       "INTEGRATION_DATABASE_URL": "postgresql://u:p@h:5432/integration_db"})
    assert s.port == 9001 and s.log_level == "DEBUG"


@pytest.mark.parametrize("env", [
    {"INTEGRATION_PORT": "abc"},
    {"INTEGRATION_PORT": "0"},
    {"INTEGRATION_PORT": "70000"},
    {"INTEGRATION_LOG_LEVEL": "LOUD"},
    {"INTEGRATION_DATABASE_URL": "sqlite:///x.db"},
    {"INTEGRATION_DATABASE_URL": "mysql://u:p@h/db"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)
