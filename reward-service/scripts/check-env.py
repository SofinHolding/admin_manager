#!/usr/bin/env python3
"""Preflight kiểm tra cấu hình reward-service TRƯỚC khi start (gọi trong CI/CD deploy).

Phase 4: Reward chạy trên DB RIÊNG và xác thực bằng JWKS của Identity — không còn secret JWT dùng chung. Kiểm:

  1. REWARD_TOKEN_KEY là Fernet key hợp lệ (mất/sai key = mất toàn bộ token Discord đã mã hoá).
  2. REWARD_DATABASE_URL kết nối được, trỏ vào DB RIÊNG của Reward: KHÔNG có bảng `accounts` của Identity
     (nếu có nghĩa là đang trỏ nhầm DB dùng chung) và migration reward_* chạy được.
  3. Cấu hình Identity đủ (REWARD_IDENTITY_URL, REWARD_ISSUER/REWARD_AUDIENCE) và JWKS lấy được (chỉ CẢNH BÁO nếu
     Identity chưa sống — Reward vẫn khởi động được, token verify được sau khi JWKS có).
  4. Cảnh báo nếu còn biến cũ (JWT_SECRET, ADMIN_API_BASE, DATABASE_URL) trong môi trường — chúng bị BỎ QUA.
  5. Cảnh báo nếu thiếu REWARD_IDENTITY_FEED_KEY (consumer feed tắt ⇒ thu quyền không có hiệu lực ngay).

Exit code != 0 → CI dừng deploy, KHÔNG restart pm2. Không có secret nào lọt ra stdout/stderr.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Cho phép import settings/store khi chạy từ thư mục reward-service.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fail(msg: str) -> None:
    print(f"[check-env] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"[check-env] OK: {msg}")


def _warn(msg: str) -> None:
    print(f"[check-env] WARN: {msg}")


def check_fernet_key() -> None:
    from cryptography.fernet import Fernet

    key = os.environ.get("REWARD_TOKEN_KEY", "").strip()
    if not key:
        _fail("REWARD_TOKEN_KEY chưa set — không mã hoá được token Discord của user.")
    try:
        Fernet(key.encode("utf-8"))
    except Exception:  # noqa: BLE001 — key sai định dạng
        _fail("REWARD_TOKEN_KEY không phải Fernet key hợp lệ (cần 44 ký tự base64 từ Fernet.generate_key()).")
    _ok("REWARD_TOKEN_KEY là Fernet key hợp lệ.")


def check_database() -> None:
    import asyncio

    import asyncpg

    dsn = os.environ.get("REWARD_DATABASE_URL", "").strip()
    if not dsn:
        _fail("REWARD_DATABASE_URL chưa set (DB riêng của Reward; không dùng DATABASE_URL cũ).")

    async def _probe() -> None:
        try:
            conn = await asyncpg.connect(dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001
            _fail(f"Không kết nối được REWARD_DATABASE_URL: {type(exc).__name__}")
        try:
            identity_tables = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name = ANY($1::text[])", ["accounts", "invite_keys", "refresh_tokens"])
            if identity_tables:
                _fail("DB có bảng của Identity (accounts/invite_keys/refresh_tokens) — đang trỏ NHẦM DB dùng chung; "
                      "Reward phải chạy trên DB riêng (reward_db).")
            _ok("Kết nối DB riêng thành công; không có bảng Identity.")
        finally:
            await conn.close()

    asyncio.run(_probe())


def check_identity() -> None:
    import httpx

    from settings import RewardConfigError, load_settings

    try:
        settings = load_settings()
    except RewardConfigError as exc:
        _fail(str(exc))
    _ok(f"Cấu hình hợp lệ (issuer={settings.issuer}, audience={settings.audience}).")
    try:
        resp = httpx.get(settings.identity_jwks_url, timeout=5.0)
        resp.raise_for_status()
        kids = [k.get("kid") for k in resp.json().get("keys", [])]
        if not kids:
            _warn("JWKS của Identity rỗng — token chưa verify được.")
        else:
            _ok(f"Lấy được JWKS của Identity ({len(kids)} khoá).")
    except Exception as exc:  # noqa: BLE001 — Identity chưa sống không được chặn deploy Reward
        _warn(f"Chưa lấy được JWKS ({type(exc).__name__}); Reward vẫn khởi động, verify được khi Identity sống.")
    if not settings.feed_enabled:
        _warn("Thiếu REWARD_IDENTITY_FEED_KEY — consumer feed TẮT: thu quyền/khoá chỉ có hiệu lực khi token hết hạn.")
    else:
        _ok("Consumer feed Identity được cấu hình.")


def check_legacy_env() -> None:
    for name in ("JWT_SECRET", "ADMIN_API_BASE", "DATABASE_URL"):
        if os.environ.get(name, "").strip():
            _warn(f"Biến cũ {name} còn trong môi trường — Reward KHÔNG đọc nó nữa, hãy gỡ khỏi .env.")


def main() -> None:
    check_fernet_key()
    check_database()
    check_identity()
    check_legacy_env()
    print("[check-env] Tất cả kiểm tra PASS — an toàn để migrate + start service.")


if __name__ == "__main__":
    main()
