"""Cấu hình từ biến môi trường (`INTEGRATION_*`). Sai cấu hình thì fail-fast bằng ConfigError.

Phân biệt: cấu hình SAI/THIẾU là lỗi khởi động (fail-fast); dependency tạm chết (DB, Identity) thì service vẫn
sống và báo `ready=503` / API 503, không crash.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_ENVS = {"dev", "test", "production"}
MAX_BATCH_ITEMS = 500  # trần cố định theo hợp đồng ingest (BATCH_TOO_LARGE)


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    database_url: str
    env: str = "production"
    # Xác thực người dùng (token RS256 do Identity ký, verify offline bằng JWKS).
    issuer: str = ""
    audience: str = "manager-api"
    jwks_url: str = ""
    # Khoá đọc feed/canonical của các bên đọc: INTEGRATION_FEED_KEY_<READER> (analytics, identity).
    feed_keys: tuple = ()
    # Consumer provisioning: kéo feed của Identity. Thiếu URL hoặc khoá => consumer tắt (service vẫn chạy).
    identity_feed_url: str = ""
    identity_feed_key: str = ""
    rate_limit_per_min: int = 600
    snapshot_ttl_s: int = 6 * 3600
    committing_timeout_s: int = 600
    max_body_bytes: int = 2 * 1024 * 1024
    max_clients_per_owner: int = 20
    max_credentials_per_client: int = 10
    credential_default_ttl_days: int = 0     # 0 = không hết hạn mặc định
    idempotency_ttl_s: int = 24 * 3600
    maintenance_interval_s: int = 60         # 0 = tắt worker bảo trì (test gọi tay)
    provisioning_poll_s: float = 5.0
    provisioning_max_attempts: int = 5
    provisioning_batch: int = 100
    # Jobs (Fallback Tool): lease có hạn, retry có giới hạn -> dead.
    job_lease_default_s: int = 300
    job_lease_max_s: int = 900               # trần của hợp đồng (integration-api-v1.md §4)
    job_max_attempts: int = 3
    job_backoff_base_s: int = 30
    job_backoff_max_s: int = 900
    job_max_pending_per_owner: int = 1000
    job_result_max_bytes: int = 64 * 1024
    legacy_redeem_per_window: int = 10       # pairing legacy: số lần đổi mã tối đa mỗi IP mỗi cửa sổ
    legacy_redeem_window_s: int = 300

    @property
    def provisioning_enabled(self) -> bool:
        return bool(self.identity_feed_url and self.identity_feed_key)


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get("INTEGRATION_" + name, default) or "").strip()


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"INTEGRATION_{name} phải là số nguyên") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"INTEGRATION_{name} phải trong khoảng {lo}..{hi}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = _env(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"INTEGRATION_{name} phải là số") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"INTEGRATION_{name} phải trong khoảng {lo}..{hi}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    port = _int(env, "PORT", 8431, 1, 65535)

    level = _env(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("INTEGRATION_LOG_LEVEL không hợp lệ")

    database_url = _env(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("INTEGRATION_DATABASE_URL phải là PostgreSQL")

    mode = _env(env, "ENV", "production").lower()
    if mode not in _ENVS:
        raise ConfigError("INTEGRATION_ENV phải là dev, test hoặc production")

    feed_keys = tuple(sorted(
        (k[len("INTEGRATION_FEED_KEY_"):].lower(), (v or "").strip())
        for k, v in env.items() if k.startswith("INTEGRATION_FEED_KEY_") and (v or "").strip()))
    for reader, key in feed_keys:
        if len(key) < 24:
            raise ConfigError(f"INTEGRATION_FEED_KEY_{reader.upper()} quá ngắn (tối thiểu 24 ký tự)")

    feed_url = _env(env, "IDENTITY_FEED_URL").rstrip("/")
    if feed_url and not feed_url.startswith(("http://", "https://")):
        raise ConfigError("INTEGRATION_IDENTITY_FEED_URL phải là http(s)://")
    feed_key = _env(env, "IDENTITY_FEED_KEY")
    if feed_key and len(feed_key) < 24:
        raise ConfigError("INTEGRATION_IDENTITY_FEED_KEY quá ngắn (tối thiểu 24 ký tự)")

    jwks_url = _env(env, "IDENTITY_JWKS_URL")
    if jwks_url and not jwks_url.startswith(("http://", "https://")):
        raise ConfigError("INTEGRATION_IDENTITY_JWKS_URL phải là http(s)://")

    return Settings(
        port=port, log_level=level, database_url=database_url, env=mode,
        issuer=_env(env, "ISSUER").rstrip("/"), audience=_env(env, "AUDIENCE", "manager-api"),
        jwks_url=jwks_url, feed_keys=feed_keys, identity_feed_url=feed_url, identity_feed_key=feed_key,
        rate_limit_per_min=_int(env, "RATE_LIMIT_PER_MIN", 600, 1, 1_000_000),
        snapshot_ttl_s=_int(env, "SNAPSHOT_TTL_S", 6 * 3600, 60, 30 * 86400),
        committing_timeout_s=_int(env, "COMMITTING_TIMEOUT_S", 600, 30, 86400),
        max_body_bytes=_int(env, "MAX_BODY_BYTES", 2 * 1024 * 1024, 1024, 64 * 1024 * 1024),
        max_clients_per_owner=_int(env, "MAX_CLIENTS_PER_OWNER", 20, 1, 10_000),
        max_credentials_per_client=_int(env, "MAX_CREDENTIALS_PER_CLIENT", 10, 1, 1000),
        credential_default_ttl_days=_int(env, "CREDENTIAL_DEFAULT_TTL_DAYS", 0, 0, 3650),
        idempotency_ttl_s=_int(env, "IDEMPOTENCY_TTL_S", 24 * 3600, 60, 30 * 86400),
        maintenance_interval_s=_int(env, "MAINTENANCE_INTERVAL_S", 60, 0, 86400),
        provisioning_poll_s=_float(env, "PROVISIONING_POLL_S", 5.0, 0.0, 3600.0),
        provisioning_max_attempts=_int(env, "PROVISIONING_MAX_ATTEMPTS", 5, 1, 100),
        provisioning_batch=_int(env, "PROVISIONING_BATCH", 100, 1, 500),
        job_lease_default_s=_int(env, "JOB_LEASE_DEFAULT_S", 300, 1, 900),
        job_lease_max_s=_int(env, "JOB_LEASE_MAX_S", 900, 1, 900),
        job_max_attempts=_int(env, "JOB_MAX_ATTEMPTS", 3, 1, 20),
        job_backoff_base_s=_int(env, "JOB_BACKOFF_BASE_S", 30, 0, 3600),
        job_backoff_max_s=_int(env, "JOB_BACKOFF_MAX_S", 900, 0, 86400),
        job_max_pending_per_owner=_int(env, "JOB_MAX_PENDING_PER_OWNER", 1000, 1, 1_000_000),
        job_result_max_bytes=_int(env, "JOB_RESULT_MAX_BYTES", 64 * 1024, 1024, 1024 * 1024),
        legacy_redeem_per_window=_int(env, "LEGACY_REDEEM_PER_WINDOW", 10, 1, 10_000),
        legacy_redeem_window_s=_int(env, "LEGACY_REDEEM_WINDOW_S", 300, 1, 86400),
    )


def require_serving_config(settings: Settings) -> None:
    """Điều kiện BẮT BUỘC để phục vụ request (không bắt buộc khi chỉ chạy migrate/test cấu hình)."""
    if not settings.database_url:
        raise ConfigError("Thiếu INTEGRATION_DATABASE_URL")
    if not settings.issuer:
        raise ConfigError("Thiếu INTEGRATION_ISSUER (issuer của access token Identity)")
    if not settings.jwks_url:
        raise ConfigError("Thiếu INTEGRATION_IDENTITY_JWKS_URL (JWKS để verify token RS256 offline)")
    if settings.env == "production":
        if not settings.issuer.startswith("https://"):
            raise ConfigError("INTEGRATION_ISSUER phải là https:// ở production")
        if not settings.jwks_url.startswith("https://"):
            raise ConfigError("INTEGRATION_IDENTITY_JWKS_URL phải là https:// ở production")
