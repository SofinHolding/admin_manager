"""Cấu hình biên của gateway: mặc định an toàn, fail-fast khi sai, không chứa secret."""

from __future__ import annotations

from pathlib import Path

import pytest
from api_gateway.config import ConfigError, load_settings


def test_secure_defaults():
    s = load_settings({})
    assert s.environment == "dev" and s.cors_origins == () and s.cors_allow_credentials is False
    assert s.max_body_bytes == 2 * 1024 * 1024 and s.rate_limit_enabled is True
    assert s.connect_timeout_s <= 3 and s.trusted_proxies == () and dict(s.upstream_overrides) == {}
    assert Path(s.route_map_path).is_file() and Path(s.registry_path).is_file()


def test_gateway_prefix_overrides_legacy_prefix_and_both_work():
    assert load_settings({"API_GATEWAY_PORT": "9001"}).port == 9001
    assert load_settings({"GATEWAY_PORT": "9002", "API_GATEWAY_PORT": "9001"}).port == 9002


def test_values_are_parsed():
    s = load_settings({
        "GATEWAY_CORS_ORIGINS": "https://A.example.com, https://b.example.com:8443", "GATEWAY_ENV": "production",
        "GATEWAY_MAX_BODY_BYTES": "4096", "GATEWAY_UPSTREAM_IDENTITY": "http://10.0.0.5:8430/",
        "GATEWAY_TRUSTED_PROXIES": "10.0.0.0/8,127.0.0.1", "GATEWAY_CORS_ALLOW_CREDENTIALS": "true",
        "GATEWAY_RATE_LIMIT_ENABLED": "no"})
    assert s.cors_origins == ("https://a.example.com", "https://b.example.com:8443")
    assert s.environment == "production" and s.max_body_bytes == 4096 and s.rate_limit_enabled is False
    assert dict(s.upstream_overrides) == {"identity": "http://10.0.0.5:8430"}
    assert s.trusted_proxies == ("10.0.0.0/8", "127.0.0.1") and s.cors_allow_credentials is True


@pytest.mark.parametrize("env", [
    {"GATEWAY_MAX_BODY_BYTES": "0"}, {"GATEWAY_MAX_BODY_BYTES": "abc"}, {"GATEWAY_ENV": "staging"},
    {"GATEWAY_UPSTREAM_IDENTITY": "ftp://x"}, {"GATEWAY_UPSTREAM_IDENTITY": "http://h:1/path"},
    {"GATEWAY_UPSTREAM_IDENTITY": "http://u:p@h:1"}, {"GATEWAY_UPSTREAM_IDENTITY": "not a url"},
    {"GATEWAY_TRUSTED_PROXIES": "banana"}, {"GATEWAY_CONNECT_TIMEOUT_S": "-1"},
    {"GATEWAY_CORS_ALLOW_CREDENTIALS": "maybe"}, {"GATEWAY_BREAKER_THRESHOLD": "0"},
    {"GATEWAY_RATE_LIMIT_STRICT_PER_MIN": "0"},
])
def test_invalid_values_fail_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_env_example_has_no_secrets_and_documents_every_gateway_variable():
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    for var in ("GATEWAY_CORS_ORIGINS", "GATEWAY_MAX_BODY_BYTES", "GATEWAY_UPSTREAM_IDENTITY",
                "GATEWAY_UPSTREAM_INTEGRATION", "GATEWAY_UPSTREAM_ANALYTICS", "GATEWAY_UPSTREAM_REWARD",
                "GATEWAY_TRUSTED_PROXIES", "GATEWAY_RATE_LIMIT_STRICT_PER_MIN", "GATEWAY_BREAKER_THRESHOLD"):
        assert var in text, var
    assert "*" not in [ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("GATEWAY_CORS_ORIGINS=")]
    for ln in text.splitlines():
        assert not any(w in ln.lower() for w in ("password", "secret", "token", "private")) or ln.startswith("#"), ln
