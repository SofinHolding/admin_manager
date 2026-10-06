"""Cấu hình từ biến môi trường (`IDENTITY_*`). Sai cấu hình thì fail-fast bằng ConfigError.

Phân biệt: cấu hình SAI/THIẾU là lỗi khởi động (fail-fast); dependency tạm chết (DB, SMTP) thì service vẫn
sống và báo `ready=503`, không crash.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_ENVS = {"dev", "test", "production"}


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    database_url: str
    env: str = "production"
    # Khóa HS256 CHỈ để cùng tồn tại với legacy/admin-server/reward-service legacy trong giai đoạn Strangler
    # (access_token_alg="HS256"). Mặc định hệ thống dùng RS256 + JWKS, KHÔNG cần secret chung.
    jwt_compat_secret: str = ""
    access_token_alg: str = "RS256"
    issuer: str = "http://127.0.0.1:8430"
    audience: str = "manager-api"
    key_encryption_key: str = ""        # Fernet: mã hoá khoá ký private trong DB
    jwks_grace_seconds: int = 3600      # khoá retired vẫn publish thêm khoảng này
    feed_keys: tuple = ()               # ((reader, key), ...) từ IDENTITY_FEED_KEY_<READER>
    integration_feed_url: str = ""      # nguồn feed provisioning (tuỳ chọn)
    integration_feed_key: str = ""
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_days: int = 30
    login_fail_delay_s: float = 1.0
    base_url: str = "http://localhost:8421"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    # ── OIDC (Phase 1.6) ──
    sso_ttl_s: int = 8 * 3600           # tuổi thọ cookie/phiên SSO của trang đăng nhập hosted
    auth_code_ttl_s: int = 60           # authorization code: RFC 6749 khuyến nghị rất ngắn
    web_redirect_uris: tuple = ("http://127.0.0.1:5174/admin/callback",)  # client first-party manager-web
    web_post_logout_uris: tuple = ()
    backchannel_enabled: bool = True
    backchannel_interval_s: float = 2.0
    backchannel_timeout_s: float = 5.0
    backchannel_max_attempts: int = 6
    backchannel_backoff_base_s: float = 5.0
    backchannel_backoff_cap_s: float = 900.0
    backchannel_allow_private: bool = False   # cho phép RP trong dải mạng nội bộ (RFC1918); mặc định chặn
    # ── Consumer feed Integration (Phase 1.7) ──
    feed_poll_interval_s: float = 3.0
    feed_max_attempts: int = 5
    feed_backoff_base_s: float = 1.0
    feed_backoff_cap_s: float = 60.0

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get("IDENTITY_" + name, default) or "").strip()


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"IDENTITY_{name} phải là số nguyên") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"IDENTITY_{name} phải trong khoảng {lo}..{hi}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _env(env, name, "true" if default else "false").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"IDENTITY_{name} phải là true/false")


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(_env(env, name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"IDENTITY_{name} phải là số") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"IDENTITY_{name} phải trong khoảng {lo}..{hi}")
    return value


def _csv(env: Mapping[str, str], name: str, default: tuple) -> tuple:
    raw = _env(env, name)
    if not raw:
        return default
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    port = _int(env, "PORT", 8430, 1, 65535)

    level = _env(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("IDENTITY_LOG_LEVEL không hợp lệ")

    database_url = _env(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("IDENTITY_DATABASE_URL phải là PostgreSQL")

    mode = _env(env, "ENV", "production").lower()
    if mode not in _ENVS:
        raise ConfigError("IDENTITY_ENV phải là dev, test hoặc production")

    secret = _env(env, "JWT_COMPAT_SECRET")
    if secret and len(secret) < 16:
        raise ConfigError("IDENTITY_JWT_COMPAT_SECRET quá ngắn (tối thiểu 16 ký tự)")

    try:
        delay = float(_env(env, "LOGIN_FAIL_DELAY_S", "1.0"))
    except ValueError as exc:
        raise ConfigError("IDENTITY_LOGIN_FAIL_DELAY_S phải là số") from exc
    if delay < 0:
        raise ConfigError("IDENTITY_LOGIN_FAIL_DELAY_S không được âm")

    alg = _env(env, "ACCESS_TOKEN_ALG", "RS256").upper()
    if alg not in ("RS256", "HS256"):
        raise ConfigError("IDENTITY_ACCESS_TOKEN_ALG phải là RS256 hoặc HS256")
    feed_keys = tuple(sorted((k[len("IDENTITY_FEED_KEY_"):].lower(), (v or "").strip())
                             for k, v in env.items() if k.startswith("IDENTITY_FEED_KEY_") and (v or "").strip()))
    for reader, key in feed_keys:
        if len(key) < 24:
            raise ConfigError(f"IDENTITY_FEED_KEY_{reader.upper()} quá ngắn (tối thiểu 24 ký tự)")

    return Settings(
        access_token_alg=alg, issuer=_env(env, "ISSUER", "http://127.0.0.1:8430").rstrip("/"),
        audience=_env(env, "AUDIENCE", "manager-api"), key_encryption_key=_env(env, "KEY_ENCRYPTION_KEY"),
        jwks_grace_seconds=_int(env, "JWKS_GRACE_SECONDS", 3600, 300, 7 * 86400), feed_keys=feed_keys,
        integration_feed_url=_env(env, "INTEGRATION_FEED_URL").rstrip("/"),
        integration_feed_key=_env(env, "INTEGRATION_FEED_KEY"),
        port=port, log_level=level, database_url=database_url, env=mode, jwt_compat_secret=secret,
        access_token_ttl_s=_int(env, "ACCESS_TOKEN_TTL_S", 15 * 60, 60, 24 * 3600),
        refresh_token_ttl_days=_int(env, "REFRESH_TOKEN_TTL_DAYS", 30, 1, 365),
        login_fail_delay_s=delay,
        base_url=_env(env, "BASE_URL", "http://localhost:8421").rstrip("/"),
        smtp_host=_env(env, "SMTP_HOST"), smtp_port=_int(env, "SMTP_PORT", 587, 1, 65535),
        smtp_user=_env(env, "SMTP_USER"), smtp_pass=_env(env, "SMTP_PASS"),
        smtp_from=_env(env, "SMTP_FROM") or _env(env, "SMTP_USER"),
        sso_ttl_s=_int(env, "SSO_TTL_S", 8 * 3600, 300, 30 * 86400),
        auth_code_ttl_s=_int(env, "AUTH_CODE_TTL_S", 60, 10, 600),
        web_redirect_uris=_csv(env, "WEB_REDIRECT_URIS", ("http://127.0.0.1:5174/admin/callback",)),
        web_post_logout_uris=_csv(env, "WEB_POST_LOGOUT_URIS", ()),
        backchannel_enabled=_bool(env, "BACKCHANNEL_ENABLED", True),
        backchannel_interval_s=_float(env, "BACKCHANNEL_INTERVAL_S", 2.0, 0.05, 300),
        backchannel_timeout_s=_float(env, "BACKCHANNEL_TIMEOUT_S", 5.0, 0.2, 30),
        backchannel_max_attempts=_int(env, "BACKCHANNEL_MAX_ATTEMPTS", 6, 1, 20),
        backchannel_backoff_base_s=_float(env, "BACKCHANNEL_BACKOFF_BASE_S", 5.0, 0.0, 3600),
        backchannel_backoff_cap_s=_float(env, "BACKCHANNEL_BACKOFF_CAP_S", 900.0, 0.0, 86400),
        backchannel_allow_private=_bool(env, "BACKCHANNEL_ALLOW_PRIVATE", False),
        feed_poll_interval_s=_float(env, "FEED_POLL_INTERVAL_S", 3.0, 0.05, 300),
        feed_max_attempts=_int(env, "FEED_MAX_ATTEMPTS", 5, 1, 20),
        feed_backoff_base_s=_float(env, "FEED_BACKOFF_BASE_S", 1.0, 0.0, 600),
        feed_backoff_cap_s=_float(env, "FEED_BACKOFF_CAP_S", 60.0, 0.0, 3600),
    )


def require_serving_config(settings: Settings) -> None:
    """Điều kiện BẮT BUỘC để phục vụ request (không bắt buộc khi chỉ chạy migrate/test cấu hình)."""
    if not settings.database_url:
        raise ConfigError("Thiếu IDENTITY_DATABASE_URL")
    if settings.access_token_alg == "HS256":
        if not settings.jwt_compat_secret:
            raise ConfigError("Thiếu IDENTITY_JWT_COMPAT_SECRET (bắt buộc khi IDENTITY_ACCESS_TOKEN_ALG=HS256)")
    else:
        if not settings.key_encryption_key:
            raise ConfigError("Thiếu IDENTITY_KEY_ENCRYPTION_KEY (khoá Fernet mã hoá khoá ký RS256)")
        try:
            from cryptography.fernet import Fernet
            Fernet(settings.key_encryption_key.encode())
        except Exception as exc:  # noqa: BLE001
            raise ConfigError("IDENTITY_KEY_ENCRYPTION_KEY không phải khoá Fernet hợp lệ") from exc
    if settings.integration_feed_url and not settings.integration_feed_url.startswith(("http://", "https://")):
        raise ConfigError("IDENTITY_INTEGRATION_FEED_URL phải là http(s)://")
    if settings.integration_feed_url and settings.env == "production" and             not settings.integration_feed_url.startswith("https://"):
        raise ConfigError("IDENTITY_INTEGRATION_FEED_URL phải là https:// ở production")
    if settings.jwt_compat_secret and settings.env == "production" and settings.jwt_compat_secret.startswith("dev-"):
        raise ConfigError("IDENTITY_JWT_COMPAT_SECRET kiểu dev không được dùng ở production")
    if settings.env == "production" and not settings.issuer.startswith("https://"):
        raise ConfigError("IDENTITY_ISSUER phải là https:// ở production")
