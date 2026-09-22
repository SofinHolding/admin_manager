"""Đọc + validate cấu hình từ biến môi trường cho reward-service.

Mọi lỗi cấu hình (thiếu biến bắt buộc, định dạng sai) ném `RewardConfigError` — KHÔNG BAO GIỜ gọi
`sys.exit()` trong module này (chỉ entry point `main.py` mới được phép dừng tiến trình).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet


class RewardConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class RewardSettings:
    jwt_secret: str
    database_url: str
    admin_api_base: str
    reward_token_key: str
    reward_port: int
    cors_origins: list[str]
    db_migrate_on_boot: bool
    max_concurrent_jobs: int
    http_timeout_s: float
    confirm_timeout_s: float
    confirm_poll_interval_s: float
    unknown_pause_threshold: int
    discord_api_base: str
    discord_gateway_url: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RewardConfigError(f"Thiếu biến môi trường bắt buộc: {name}")
    return value


def _parse_bool(raw: str, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def load_settings() -> RewardSettings:
    """Nạp + validate toàn bộ cấu hình. Ném `RewardConfigError` nếu thiếu/sai — không thoát tiến trình."""
    jwt_secret = _require("JWT_SECRET")
    database_url = _require("DATABASE_URL")
    admin_api_base = _require("ADMIN_API_BASE").rstrip("/")
    reward_token_key = _require("REWARD_TOKEN_KEY")

    # Phase 1 chỉ validate ĐỊNH DẠNG Fernet (chưa dùng để mã hoá token Discord — đó là Phase 3).
    try:
        Fernet(reward_token_key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — mọi lỗi định dạng Fernet đều là lỗi cấu hình
        raise RewardConfigError(f"REWARD_TOKEN_KEY không đúng định dạng Fernet: {exc}") from exc

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
    discord_api_base = os.environ.get("REWARD_API_BASE", "https://discord.com/api/v9").rstrip("/")
    discord_gateway_url = os.environ.get(
        "REWARD_GATEWAY_URL", "wss://gateway.discord.gg/?v=9&encoding=json")

    return RewardSettings(
        jwt_secret=jwt_secret,
        database_url=database_url,
        admin_api_base=admin_api_base,
        reward_token_key=reward_token_key,
        reward_port=reward_port,
        cors_origins=cors_origins,
        db_migrate_on_boot=db_migrate_on_boot,
        max_concurrent_jobs=max_concurrent_jobs,
        http_timeout_s=http_timeout_s,
        confirm_timeout_s=confirm_timeout_s,
        confirm_poll_interval_s=confirm_poll_interval_s,
        unknown_pause_threshold=unknown_pause_threshold,
        discord_api_base=discord_api_base,
        discord_gateway_url=discord_gateway_url,
    )
