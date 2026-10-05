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
    # Khóa HS256 CHỈ để tương thích với admin-server/reward-service cũ trong giai đoạn Strangler.
    # Phase 1.4 thay bằng RS256 + JWKS; sau cửa sổ chuyển tiếp thì xóa khóa này.
    jwt_compat_secret: str = ""
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_days: int = 30
    login_fail_delay_s: float = 1.0
    base_url: str = "http://localhost:8421"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""

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

    return Settings(
        port=port, log_level=level, database_url=database_url, env=mode, jwt_compat_secret=secret,
        access_token_ttl_s=_int(env, "ACCESS_TOKEN_TTL_S", 15 * 60, 60, 24 * 3600),
        refresh_token_ttl_days=_int(env, "REFRESH_TOKEN_TTL_DAYS", 30, 1, 365),
        login_fail_delay_s=delay,
        base_url=_env(env, "BASE_URL", "http://localhost:8421").rstrip("/"),
        smtp_host=_env(env, "SMTP_HOST"), smtp_port=_int(env, "SMTP_PORT", 587, 1, 65535),
        smtp_user=_env(env, "SMTP_USER"), smtp_pass=_env(env, "SMTP_PASS"),
        smtp_from=_env(env, "SMTP_FROM") or _env(env, "SMTP_USER"),
    )


def require_serving_config(settings: Settings) -> None:
    """Điều kiện BẮT BUỘC để phục vụ request (không bắt buộc khi chỉ chạy migrate/test cấu hình)."""
    if not settings.database_url:
        raise ConfigError("Thiếu IDENTITY_DATABASE_URL")
    if not settings.jwt_compat_secret:
        raise ConfigError("Thiếu IDENTITY_JWT_COMPAT_SECRET")
    if settings.env == "production" and settings.jwt_compat_secret.startswith("dev-"):
        raise ConfigError("IDENTITY_JWT_COMPAT_SECRET kiểu dev không được dùng ở production")
