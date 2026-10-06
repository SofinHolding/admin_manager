"""Đọc + validate cấu hình từ biến môi trường cho reward-service.

Mọi lỗi cấu hình (thiếu biến bắt buộc, định dạng sai) ném `RewardConfigError` — KHÔNG BAO GIỜ gọi
`sys.exit()` trong module này (chỉ entry point `main.py` mới được phép dừng tiến trình).

Xác thực (Phase 4): KHÔNG còn JWT secret dùng chung. Access token RS256 do Identity ký, Reward verify OFFLINE
bằng JWKS (`REWARD_IDENTITY_JWKS_URL`), kiểm `iss`/`aud` theo `REWARD_ISSUER`/`REWARD_AUDIENCE`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet


class RewardConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class RewardSettings:
    database_url: str
    reward_token_key: str
    reward_port: int
    cors_origins: list[str]
    db_migrate_on_boot: bool
    max_concurrent_jobs: int
    http_timeout_s: float
    confirm_timeout_s: float
    confirm_poll_interval_s: float
    unknown_pause_threshold: int
    reconcile_grace_s: float
    discord_api_base: str
    discord_gateway_url: str
    # ── Identity (xác thực + quyền) ──
    identity_url: str
    identity_jwks_url: str
    issuer: str
    audience: str
    identity_timeout_s: float
    identity_retries: int
    identity_backoff_base_s: float
    # ── Feed consumer (tuỳ chọn: thiếu key ⇒ tắt, service vẫn chạy) ──
    identity_feed_url: str
    identity_feed_key: str
    feed_poll_interval_s: float
    feed_batch_size: int
    feed_event_max_attempts: int
    feed_event_backoff_base_s: float

    @property
    def feed_enabled(self) -> bool:
        return bool(self.identity_feed_url and self.identity_feed_key)


def _first(*names: str) -> str:
    """Giá trị của biến đầu tiên có set — dùng để vẫn đọc tên biến cũ (tương thích) sau tên mới."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _require(*names: str) -> str:
    value = _first(*names)
    if not value:
        raise RewardConfigError(f"Thiếu biến môi trường bắt buộc: {names[0]}")
    return value


def _parse_bool(raw: str, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _http_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RewardConfigError(f"{name} phải là URL http(s) hợp lệ")
    return value.rstrip("/")


def load_settings() -> RewardSettings:
    """Nạp + validate toàn bộ cấu hình. Ném `RewardConfigError` nếu thiếu/sai — không thoát tiến trình."""
    # Biến cũ (secret JWT chung, địa chỉ admin_manager, DSN dùng chung) KHÔNG còn được đọc (CLAUDE.md luật 2 và 8).
    # KHÔNG fallback sang `DATABASE_URL` cũ: biến đó từng trỏ vào DB dùng chung `ufsync` — đọc nó sẽ âm thầm duy trì coupling.
    database_url = _require("REWARD_DATABASE_URL")
    reward_token_key = _require("REWARD_TOKEN_KEY")

    try:
        Fernet(reward_token_key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — mọi lỗi định dạng Fernet đều là lỗi cấu hình
        raise RewardConfigError(f"REWARD_TOKEN_KEY không đúng định dạng Fernet: {exc}") from exc

    identity_url = _http_url("REWARD_IDENTITY_URL", _require("REWARD_IDENTITY_URL"))
    identity_jwks_url = _http_url(
        "REWARD_IDENTITY_JWKS_URL",
        _first("REWARD_IDENTITY_JWKS_URL") or f"{identity_url}/.well-known/jwks.json")
    issuer = _first("REWARD_ISSUER") or identity_url
    audience = _first("REWARD_AUDIENCE") or "manager-api"

    feed_key = _first("REWARD_IDENTITY_FEED_KEY")
    feed_url = _first("REWARD_IDENTITY_FEED_URL") or (identity_url if feed_key else "")
    if feed_url:
        feed_url = _http_url("REWARD_IDENTITY_FEED_URL", feed_url)

    port_raw = os.environ.get("REWARD_PORT", "8422").strip()
    try:
        reward_port = int(port_raw)
    except ValueError as exc:
        raise RewardConfigError(f"REWARD_PORT không hợp lệ: {port_raw!r}") from exc
    if not (1 <= reward_port <= 65535):
        raise RewardConfigError(f"REWARD_PORT ngoài phạm vi hợp lệ: {reward_port}")

    cors_raw = os.environ.get("REWARD_CORS_ORIGINS", "")
    cors_origins = [origin.strip() for origin in cors_raw.split(",") if origin.strip()]

    db_migrate_on_boot = _parse_bool(os.environ.get("REWARD_DB_MIGRATE_ON_BOOT", "1"), default=True)

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise RewardConfigError(f"{name} không hợp lệ: {raw!r}") from exc

    def _float(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise RewardConfigError(f"{name} không hợp lệ: {raw!r}") from exc

    max_concurrent_jobs = _int("REWARD_MAX_CONCURRENT_JOBS", 3)
    http_timeout_s = _float("REWARD_HTTP_TIMEOUT_S", 15.0)
    confirm_timeout_s = _float("REWARD_CONFIRM_TIMEOUT_S", 15.0)
    confirm_poll_interval_s = _float("REWARD_CONFIRM_POLL_INTERVAL_S", 2.0)
    unknown_pause_threshold = _int("REWARD_UNKNOWN_PAUSE_THRESHOLD", 5)
    # Item `unknown` chỉ bị kết luận "bot không trả lời" khi đã gửi ≥ ngần này giây mà kênh vẫn không có reply khớp.
    reconcile_grace_s = _float("REWARD_RECONCILE_GRACE_S", 45.0)
    discord_api_base = os.environ.get("REWARD_API_BASE", "https://discord.com/api/v9").rstrip("/")
    discord_gateway_url = os.environ.get(
        "REWARD_GATEWAY_URL", "wss://gateway.discord.gg/?v=9&encoding=json")

    return RewardSettings(
        database_url=database_url,
        reward_token_key=reward_token_key,
        reward_port=reward_port,
        cors_origins=cors_origins,
        db_migrate_on_boot=db_migrate_on_boot,
        max_concurrent_jobs=max_concurrent_jobs,
        http_timeout_s=http_timeout_s,
        confirm_timeout_s=confirm_timeout_s,
        confirm_poll_interval_s=confirm_poll_interval_s,
        unknown_pause_threshold=unknown_pause_threshold,
        reconcile_grace_s=reconcile_grace_s,
        discord_api_base=discord_api_base,
        discord_gateway_url=discord_gateway_url,
        identity_url=identity_url,
        identity_jwks_url=identity_jwks_url,
        issuer=issuer,
        audience=audience,
        identity_timeout_s=_float("REWARD_IDENTITY_TIMEOUT_S", 5.0),
        identity_retries=_int("REWARD_IDENTITY_RETRIES", 2),
        identity_backoff_base_s=_float("REWARD_IDENTITY_BACKOFF_S", 0.3),
        identity_feed_url=feed_url,
        identity_feed_key=feed_key,
        feed_poll_interval_s=_float("REWARD_FEED_POLL_INTERVAL_S", 2.0),
        feed_batch_size=_int("REWARD_FEED_BATCH_SIZE", 100),
        feed_event_max_attempts=_int("REWARD_FEED_EVENT_MAX_ATTEMPTS", 4),
        feed_event_backoff_base_s=_float("REWARD_FEED_EVENT_BACKOFF_S", 0.2),
    )
