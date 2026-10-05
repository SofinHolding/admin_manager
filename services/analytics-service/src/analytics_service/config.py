"""Cấu hình từ biến môi trường (`ANALYTICS_*`). Sai cấu hình thì fail-fast bằng ConfigError.

Phân biệt: cấu hình SAI/THIẾU là lỗi khởi động (fail-fast); dependency tạm chết (DB, Redis, nguồn feed) thì service
vẫn sống: DB chết → ready=503 và API trả 503 nhanh; Redis chết → đọc thẳng PostgreSQL; nguồn feed chết → dashboard
vẫn đọc dữ liệu cũ kèm `stale`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    database_url: str
    redis_url: str
    issuer: str = ""
    audience: str = "manager-api"
    identity_jwks_url: str = ""
    integration_url: str = ""
    integration_feed_key: str = ""
    identity_url: str = ""
    identity_feed_key: str = ""
    stale_after_s: int = 3600
    cache_ttl_s: int = 300
    sync_interval_s: float = 5.0
    consumer_max_attempts: int = 3
    consumer_backoff_base_s: float = 0.5
    feed_page_size: int = 200
    canonical_page_size: int = 1000

    @property
    def integration_enabled(self) -> bool:
        return bool(self.integration_url and self.integration_feed_key)

    @property
    def identity_feed_enabled(self) -> bool:
        return bool(self.identity_url and self.identity_feed_key)

    @property
    def jwks_url(self) -> str:
        return self.identity_jwks_url or (self.issuer + "/.well-known/jwks.json" if self.issuer else "")


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get("ANALYTICS_" + name, default) or "").strip()


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"ANALYTICS_{name} phải là số nguyên") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"ANALYTICS_{name} phải trong khoảng {lo}..{hi}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = _env(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"ANALYTICS_{name} phải là số") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"ANALYTICS_{name} phải trong khoảng {lo}..{hi}")
    return value


def _url(env: Mapping[str, str], name: str) -> str:
    value = _env(env, name).rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise ConfigError(f"ANALYTICS_{name} phải là URL http(s)")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    port = _int(env, "PORT", 8432, 1, 65535)

    level = _env(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("ANALYTICS_LOG_LEVEL không hợp lệ")

    database_url = _env(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("ANALYTICS_DATABASE_URL phải là PostgreSQL")

    redis_url = _env(env, "REDIS_URL")
    if redis_url and not redis_url.startswith(("redis://", "rediss://")):
        raise ConfigError("ANALYTICS_REDIS_URL phải là redis:// hoặc rediss://")

    feed_keys = {}
    for name in ("INTEGRATION_FEED_KEY", "IDENTITY_FEED_KEY"):
        key = _env(env, name)
        if key and len(key) < 16:
            raise ConfigError(f"ANALYTICS_{name} quá ngắn (tối thiểu 16 ký tự)")
        feed_keys[name] = key

    return Settings(
        port=port, log_level=level, database_url=database_url, redis_url=redis_url,
        issuer=_url(env, "ISSUER"), audience=_env(env, "AUDIENCE", "manager-api") or "manager-api",
        identity_jwks_url=_url(env, "IDENTITY_JWKS_URL"),
        integration_url=_url(env, "INTEGRATION_URL"), integration_feed_key=feed_keys["INTEGRATION_FEED_KEY"],
        identity_url=_url(env, "IDENTITY_URL"), identity_feed_key=feed_keys["IDENTITY_FEED_KEY"],
        stale_after_s=_int(env, "STALE_AFTER_S", 3600, 1, 30 * 86400),
        cache_ttl_s=_int(env, "CACHE_TTL_S", 300, 1, 86400),
        sync_interval_s=_float(env, "SYNC_INTERVAL_S", 5.0, 0.05, 3600),
        consumer_max_attempts=_int(env, "CONSUMER_MAX_ATTEMPTS", 3, 1, 20),
        consumer_backoff_base_s=_float(env, "CONSUMER_BACKOFF_BASE_S", 0.5, 0.0, 60),
        feed_page_size=_int(env, "FEED_PAGE_SIZE", 200, 1, 500),
        canonical_page_size=_int(env, "CANONICAL_PAGE_SIZE", 1000, 1, 1000),
    )


def require_serving_config(settings: Settings) -> None:
    """Điều kiện BẮT BUỘC để phục vụ request (không bắt buộc khi chỉ chạy test cấu hình)."""
    if not settings.database_url:
        raise ConfigError("Thiếu ANALYTICS_DATABASE_URL")
    if not settings.issuer:
        raise ConfigError("Thiếu ANALYTICS_ISSUER (issuer của access token Identity)")
    if not settings.jwks_url:
        raise ConfigError("Thiếu ANALYTICS_IDENTITY_JWKS_URL")
