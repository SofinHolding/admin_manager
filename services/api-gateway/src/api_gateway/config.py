"""Cấu hình từ biến môi trường. Sai cấu hình thì fail-fast bằng ConfigError.

Tiền tố: `GATEWAY_*` (khuyến nghị) hoặc `API_GATEWAY_*` (tương thích chassis cũ); `GATEWAY_*` thắng.
Gateway KHÔNG có database và KHÔNG giữ secret nghiệp vụ: không có biến nào ở đây là secret.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$|^https?://\[[0-9A-Fa-f:]+\](:\d{1,5})?$")
_UPSTREAM_NAME_RE = re.compile(r"^GATEWAY_UPSTREAM_([A-Z0-9_]+)$")


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    database_url: str
    environment: str = "dev"                      # dev | production (production bật HSTS)
    cors_origins: tuple[str, ...] = ()            # allowlist exact-match; rỗng = không CORS
    cors_allow_credentials: bool = False
    max_body_bytes: int = 2 * 1024 * 1024
    max_header_bytes: int = 16 * 1024
    max_header_count: int = 100
    max_url_bytes: int = 8 * 1024
    upstream_overrides: Mapping[str, str] = field(default_factory=dict)   # {"identity": "http://..."}
    connect_timeout_s: float = 2.0
    read_timeout_s: float = 30.0
    body_timeout_s: float = 30.0                  # tối đa chờ client gửi tiếp thân request (chống slowloris)
    breaker_threshold: int = 5
    breaker_cooldown_s: float = 10.0
    rate_limit_enabled: bool = True
    rate_limit_strict_per_min: int = 10           # login/register/reset/...
    rate_limit_auth_per_min: int = 60             # phần còn lại của auth/oauth
    rate_limit_default_per_min: int = 600
    trusted_proxies: tuple[str, ...] = ()         # IP/CIDR của reverse proxy tin cậy (nginx) đặt X-Real-IP
    route_map_path: str = ""
    registry_path: str = ""


def _first(env: Mapping[str, str], name: str, default: str = "") -> str:
    for prefix in ("GATEWAY_", "API_GATEWAY_"):
        if prefix + name in env:
            return (env.get(prefix + name) or "").strip()
    return default


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _first(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"GATEWAY_{name} phải là số nguyên") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"GATEWAY_{name} phải trong khoảng {lo}..{hi}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = _first(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"GATEWAY_{name} phải là số") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"GATEWAY_{name} phải trong khoảng {lo}..{hi}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _first(env, name, "true" if default else "false").lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"GATEWAY_{name} phải là true/false")


def _csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def default_infra_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [here.parents[4] / "infra", here.parents[2] / "infra"]


def _resolve_path(explicit: str, relative: str) -> str:
    if explicit:
        return explicit
    for base in default_infra_candidates():
        if (base / relative).is_file():
            return str(base / relative)
    return str(default_infra_candidates()[0] / relative)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    raw_port = _first(env, "PORT", "8400")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError("API_GATEWAY_PORT phải là số nguyên") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("API_GATEWAY_PORT phải trong khoảng 1..65535")

    level = _first(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("API_GATEWAY_LOG_LEVEL không hợp lệ")

    database_url = _first(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("API_GATEWAY_DATABASE_URL phải là PostgreSQL")

    environment = _first(env, "ENV", "dev").lower()
    if environment not in {"dev", "production"}:
        raise ConfigError("GATEWAY_ENV phải là dev hoặc production")

    origins: list[str] = []
    for origin in _csv(_first(env, "CORS_ORIGINS")):
        # Không wildcard, không "null", không đường dẫn: chỉ origin chính xác scheme://host[:port].
        if origin == "*" or not _ORIGIN_RE.match(origin):
            raise ConfigError("GATEWAY_CORS_ORIGINS chỉ nhận origin chính xác (scheme://host[:port]), không dùng '*'")
        origins.append(origin.rstrip("/").lower())
    credentials = _bool(env, "CORS_ALLOW_CREDENTIALS", False)

    overrides: dict[str, str] = {}
    for key, value in env.items():
        m = _UPSTREAM_NAME_RE.match(key)
        if not m or not (value or "").strip():
            continue
        url = value.strip().rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.path not in {"", "/"} \
                or parts.query or parts.fragment or parts.username or parts.password:
            raise ConfigError(f"{key} phải là URL http(s)://host[:port] không có đường dẫn")
        overrides[m.group(1).lower()] = url

    proxies = _csv(_first(env, "TRUSTED_PROXIES"))
    for item in proxies:
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ConfigError("GATEWAY_TRUSTED_PROXIES phải là danh sách IP/CIDR") from exc

    return Settings(
        port=port, log_level=level, database_url=database_url, environment=environment,
        cors_origins=tuple(origins), cors_allow_credentials=credentials,
        max_body_bytes=_int(env, "MAX_BODY_BYTES", 2 * 1024 * 1024, 1, 1024 * 1024 * 1024),
        max_header_bytes=_int(env, "MAX_HEADER_BYTES", 16 * 1024, 512, 1024 * 1024),
        max_header_count=_int(env, "MAX_HEADER_COUNT", 100, 8, 1000),
        max_url_bytes=_int(env, "MAX_URL_BYTES", 8 * 1024, 128, 65536),
        upstream_overrides=overrides,
        connect_timeout_s=_float(env, "CONNECT_TIMEOUT_S", 2.0, 0.05, 30),
        read_timeout_s=_float(env, "READ_TIMEOUT_S", 30.0, 0.1, 600),
        body_timeout_s=_float(env, "BODY_TIMEOUT_S", 30.0, 0.05, 600),
        breaker_threshold=_int(env, "BREAKER_THRESHOLD", 5, 1, 1000),
        breaker_cooldown_s=_float(env, "BREAKER_COOLDOWN_S", 10.0, 0.05, 3600),
        rate_limit_enabled=_bool(env, "RATE_LIMIT_ENABLED", True),
        rate_limit_strict_per_min=_int(env, "RATE_LIMIT_STRICT_PER_MIN", 10, 1, 1_000_000),
        rate_limit_auth_per_min=_int(env, "RATE_LIMIT_AUTH_PER_MIN", 60, 1, 1_000_000),
        rate_limit_default_per_min=_int(env, "RATE_LIMIT_DEFAULT_PER_MIN", 600, 1, 1_000_000),
        trusted_proxies=tuple(proxies),
        route_map_path=_resolve_path(_first(env, "ROUTE_MAP_PATH"), "gateway/route-map.json"),
        registry_path=_resolve_path(_first(env, "REGISTRY_PATH"), "service-registry.json"),
    )
