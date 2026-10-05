"""Unit test không cần DB: bảo mật, rate limit, JWT, cấu hình."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import jwt
import pytest

from identity_service.auth import legacy_jwt
from identity_service.auth.ratelimit import RateLimiter
from identity_service.config import ConfigError, load_settings
from identity_service.errors import DomainError
from identity_service.security import (hash_password, hash_token, iso, new_numeric_code, new_token,
                                       verify_password)

SECRET = __import__("secrets").token_urlsafe(32)


# ── security ────────────────────────────────────────────────────────────────────

def test_password_roundtrip_and_wrong_password():
    h = hash_password("mat-khau-12345")
    assert h.startswith("$2") and verify_password("mat-khau-12345", h)
    assert not verify_password("sai-mat-khau", h)


def test_bcrypt_hash_from_legacy_still_verifies():
    """Hash do admin-server cũ tạo (cost 12) phải verify được sau khi migrate."""
    import bcrypt
    legacy = bcrypt.hashpw(b"mat-khau-cu", bcrypt.gensalt(rounds=12)).decode()
    assert verify_password("mat-khau-cu", legacy)


@pytest.mark.parametrize("bad", ["", "khong-phai-hash", "$2b$12$short"])
def test_corrupt_hash_is_not_a_match_and_does_not_raise(bad):
    assert verify_password("x", bad) is False


def test_tokens_are_long_unique_and_hashed_deterministically():
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200 and all(len(t) == 48 for t in tokens)
    t = new_token()
    assert hash_token(t) == hash_token(t) and hash_token(t) != t and len(hash_token(t)) == 64


def test_numeric_code_is_six_digits():
    for _ in range(100):
        c = new_numeric_code()
        assert len(c) == 6 and c.isdigit()


def test_iso_matches_legacy_format():
    assert iso(datetime(2026, 10, 5, 3, 4, 5, 999, tzinfo=timezone.utc)) == "2026-10-05T03:04:05+00:00"
    assert iso(None) == ""


# ── rate limit ──────────────────────────────────────────────────────────────────

class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_rate_limit_blocks_after_max_hits_with_retry_after():
    clk = Clock()
    rl = RateLimiter(clk)
    for _ in range(3):
        rl.check("1.1.1.1", "login", 3, 60)
    with pytest.raises(DomainError) as exc:
        rl.check("1.1.1.1", "login", 3, 60)
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "60"


def test_rate_limit_is_per_ip_and_per_action_and_expires():
    clk = Clock()
    rl = RateLimiter(clk)
    for _ in range(3):
        rl.check("1.1.1.1", "login", 3, 60)
    rl.check("2.2.2.2", "login", 3, 60)      # IP khác
    rl.check("1.1.1.1", "register", 3, 60)   # hành động khác
    clk.t += 61
    rl.check("1.1.1.1", "login", 3, 60)      # cửa sổ đã trôi qua


# ── JWT tương thích ─────────────────────────────────────────────────────────────

def _tok(**kw):
    return legacy_jwt.create_access_token(SECRET, account_id="a1", username="u", role="viewer",
                                          client_type="web", ttl_s=900, **kw)


def test_jwt_payload_has_exact_legacy_claims():
    p = legacy_jwt.decode_access_token(SECRET, _tok())
    assert set(p) == {"sub", "username", "role", "client_type", "exp", "iat"}
    assert p["sub"] == "a1" and p["exp"] - p["iat"] == 900


def test_jwt_rejects_expired_tampered_wrong_secret_and_none_alg():
    with pytest.raises(DomainError) as e:
        legacy_jwt.decode_access_token(SECRET, _tok(now=int(time.time()) - 5000))
    assert e.value.detail == "Token hết hạn"
    for bad in (_tok()[:-3] + "xxx", "rac", ""):
        with pytest.raises(DomainError) as e:
            legacy_jwt.decode_access_token(SECRET, bad)
        assert e.value.detail == "Token không hợp lệ"
    with pytest.raises(DomainError):
        legacy_jwt.decode_access_token("secret-khac-hoan-toan-xx", _tok())
    forged = jwt.encode({"sub": "a1", "role": "admin", "exp": int(time.time()) + 99}, key=None,
                        algorithm="none")
    with pytest.raises(DomainError):
        legacy_jwt.decode_access_token(SECRET, forged)


def test_jwt_rejects_other_hmac_algorithms():
    other = jwt.encode({"sub": "a1", "exp": int(time.time()) + 99}, SECRET, algorithm="HS512")
    with pytest.raises(DomainError):
        legacy_jwt.decode_access_token(SECRET, other)


# ── cấu hình ────────────────────────────────────────────────────────────────────

def test_settings_parse_and_defaults():
    s = load_settings({"IDENTITY_ENV": "dev", "IDENTITY_JWT_COMPAT_SECRET": SECRET,
                       "IDENTITY_BASE_URL": "https://m.example.com/", "IDENTITY_LOGIN_FAIL_DELAY_S": "0.5"})
    assert s.env == "dev" and s.base_url == "https://m.example.com" and s.login_fail_delay_s == 0.5
    assert s.access_token_ttl_s == 900 and s.refresh_token_ttl_days == 30 and not s.smtp_configured


@pytest.mark.parametrize("env", [
    {"IDENTITY_ENV": "staging"}, {"IDENTITY_JWT_COMPAT_SECRET": "ngan"},
    {"IDENTITY_LOGIN_FAIL_DELAY_S": "-1"}, {"IDENTITY_LOGIN_FAIL_DELAY_S": "abc"},
    {"IDENTITY_ACCESS_TOKEN_TTL_S": "5"}, {"IDENTITY_SMTP_PORT": "0"},
])
def test_settings_reject_invalid(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_smtp_configured_requires_host_user_and_pass():
    assert load_settings({"IDENTITY_SMTP_HOST": "h", "IDENTITY_SMTP_USER": "u",
                          "IDENTITY_SMTP_PASS": "p"}).smtp_configured
    assert not load_settings({"IDENTITY_SMTP_HOST": "h", "IDENTITY_SMTP_USER": "u"}).smtp_configured
