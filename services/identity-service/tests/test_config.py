"""Test cấu hình của identity-service: mặc định hợp lệ và fail-fast khi sai."""

import pytest

from identity_service.config import ConfigError, load_settings


def test_defaults_are_valid():
    s = load_settings({})
    assert s.port == 8430 and s.log_level == "INFO" and s.database_url == ""


def test_env_overrides():
    s = load_settings({"IDENTITY_PORT": "9001", "IDENTITY_LOG_LEVEL": "debug",
                       "IDENTITY_DATABASE_URL": "postgresql://u:p@h:5432/identity_db"})
    assert s.port == 9001 and s.log_level == "DEBUG"


@pytest.mark.parametrize("env", [
    {"IDENTITY_PORT": "abc"},
    {"IDENTITY_PORT": "0"},
    {"IDENTITY_PORT": "70000"},
    {"IDENTITY_LOG_LEVEL": "LOUD"},
    {"IDENTITY_DATABASE_URL": "sqlite:///x.db"},
    {"IDENTITY_DATABASE_URL": "mysql://u:p@h/db"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)
