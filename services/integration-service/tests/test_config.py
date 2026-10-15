"""Test cấu hình của integration-service: mặc định hợp lệ, fail-fast khi sai/thiếu, không có secret mặc định."""

import pytest

from integration_service.config import ConfigError, load_settings, require_serving_config

GOOD = {"INTEGRATION_ENV": "test", "INTEGRATION_DATABASE_URL": "postgresql://u:p@h:5432/integration_db", "INTEGRATION_ISSUER": "http://identity.test",
        "INTEGRATION_IDENTITY_JWKS_URL": "http://identity.test/.well-known/jwks.json",
        "INTEGRATION_REVEAL_KEY": "hYPGsUcmXi1PEc93txnTNeynFkYtJ8-8u9mxfqh3lYc="}


def test_defaults_are_valid():
    s = load_settings({"INTEGRATION_ENV": "test"})
    assert s.port == 8431 and s.log_level == "INFO" and s.database_url == ""
    assert s.audience == "manager-api" and s.rate_limit_per_min == 600 and s.snapshot_ttl_s == 6 * 3600
    assert s.max_body_bytes == 2 * 1024 * 1024 and s.feed_keys == () and not s.provisioning_enabled
    assert s.reveal_key == ""              # không bắt buộc ở env=test, mặc định rỗng


def test_default_env_is_production_and_requires_reveal_key():
    # Không khai ENV => mặc định production (an toàn: không được tự coi là dev); production đòi reveal_key ngay
    # lúc load_settings (không chỉ lúc serving).
    with pytest.raises(ConfigError, match="REVEAL_KEY"):
        load_settings({})
    s = load_settings({"INTEGRATION_REVEAL_KEY": GOOD["INTEGRATION_REVEAL_KEY"]})
    assert s.env == "production"

def test_env_overrides():
    s = load_settings({"INTEGRATION_PORT": "9001", "INTEGRATION_LOG_LEVEL": "debug", "INTEGRATION_RATE_LIMIT_PER_MIN": "5",
                       "INTEGRATION_SNAPSHOT_TTL_S": "120", "INTEGRATION_AUDIENCE": "khac", **GOOD})
    assert s.port == 9001 and s.log_level == "DEBUG" and s.rate_limit_per_min == 5 and s.snapshot_ttl_s == 120
    assert s.audience == "khac" and s.issuer == "http://identity.test"


def test_feed_keys_are_collected_per_reader():
    s = load_settings({"INTEGRATION_FEED_KEY_ANALYTICS": "a" * 24, "INTEGRATION_FEED_KEY_IDENTITY": "b" * 30, **GOOD})
    assert s.feed_keys == (("analytics", "a" * 24), ("identity", "b" * 30))


def test_provisioning_is_off_unless_both_url_and_key_present():
    assert not load_settings({"INTEGRATION_ENV": "test", "INTEGRATION_IDENTITY_FEED_URL": "http://i:8430"}).provisioning_enabled
    assert not load_settings({"INTEGRATION_ENV": "test", "INTEGRATION_IDENTITY_FEED_KEY": "k" * 30}).provisioning_enabled
    s = load_settings({"INTEGRATION_ENV": "test", "INTEGRATION_IDENTITY_FEED_URL": "http://i:8430/",
                       "INTEGRATION_IDENTITY_FEED_KEY": "k" * 30})
    assert s.provisioning_enabled and s.identity_feed_url == "http://i:8430"


@pytest.mark.parametrize("env", [
    {"INTEGRATION_PORT": "abc"},
    {"INTEGRATION_PORT": "0"},
    {"INTEGRATION_PORT": "70000"},
    {"INTEGRATION_LOG_LEVEL": "LOUD"},
    {"INTEGRATION_ENV": "staging"},
    {"INTEGRATION_DATABASE_URL": "sqlite:///x.db"},
    {"INTEGRATION_DATABASE_URL": "mysql://u:p@h/db"},
    {"INTEGRATION_FEED_KEY_ANALYTICS": "ngan"},
    {"INTEGRATION_IDENTITY_FEED_KEY": "ngan"},
    {"INTEGRATION_IDENTITY_FEED_URL": "ftp://x"},
    {"INTEGRATION_IDENTITY_JWKS_URL": "file:///etc/passwd"},
    {"INTEGRATION_RATE_LIMIT_PER_MIN": "0"},
    {"INTEGRATION_SNAPSHOT_TTL_S": "5"},
    {"INTEGRATION_MAX_BODY_BYTES": "10"},
    {"INTEGRATION_PROVISIONING_POLL_S": "abc"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)


@pytest.mark.parametrize("drop,needle", [("INTEGRATION_DATABASE_URL", "DATABASE_URL"), ("INTEGRATION_ISSUER", "ISSUER"),
                                         ("INTEGRATION_IDENTITY_JWKS_URL", "JWKS")])
def test_serving_requires_database_issuer_and_jwks(drop, needle):
    env = {k: v for k, v in GOOD.items() if k != drop}
    with pytest.raises(ConfigError, match=needle):
        require_serving_config(load_settings(env))
    require_serving_config(load_settings(GOOD))


def test_production_requires_https_for_issuer_and_jwks():
    with pytest.raises(ConfigError, match="https"):
        require_serving_config(load_settings({**GOOD, "INTEGRATION_ENV": "production"}))
    with pytest.raises(ConfigError, match="https"):                    # không khai ENV => mặc định production
        require_serving_config(load_settings({k: v for k, v in GOOD.items() if k != "INTEGRATION_ENV"}))
    https = {**GOOD, "INTEGRATION_ENV": "production", "INTEGRATION_ISSUER": "https://id.example.com",
             "INTEGRATION_IDENTITY_JWKS_URL": "https://id.example.com/.well-known/jwks.json"}
    require_serving_config(load_settings(https))
    with pytest.raises(ConfigError, match="https"):
        require_serving_config(load_settings({**https, "INTEGRATION_IDENTITY_JWKS_URL": "http://id.example.com/j"}))


def test_there_are_no_default_secrets_in_settings():
    s = load_settings({"INTEGRATION_ENV": "test"})
    assert s.identity_feed_key == "" and s.feed_keys == () and s.database_url == "" and s.reveal_key == ""
