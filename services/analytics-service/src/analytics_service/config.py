"""Cấu hình từ biến môi trường (`ANALYTICS_*`). Sai cấu hình thì fail-fast bằng ConfigError."""

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


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get("ANALYTICS_" + name, default) or "").strip()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    raw_port = _env(env, "PORT", "8432")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError("ANALYTICS_PORT phải là số nguyên") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("ANALYTICS_PORT phải trong khoảng 1..65535")

    level = _env(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("ANALYTICS_LOG_LEVEL không hợp lệ")

    database_url = _env(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("ANALYTICS_DATABASE_URL phải là PostgreSQL")
    redis_url = _env(env, "REDIS_URL")

    return Settings(port=port, log_level=level, database_url=database_url, redis_url=redis_url)
