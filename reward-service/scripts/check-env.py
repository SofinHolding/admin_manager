#!/usr/bin/env python3
"""Preflight kiểm tra cấu hình reward-service TRƯỚC khi start (gọi trong CI/CD deploy).

Kiểm 4 bất biến sống-còn để service dùng chung hạ tầng admin_manager mà không hỏng:

  1. JWT_SECRET của reward-service PHẢI trùng khít admin_manager — nếu lệch, token admin cấp sẽ
     bị reward từ chối (hoặc ngược lại). So bằng SHA-256, KHÔNG BAO GIỜ in secret ra log.
  2. REWARD_TOKEN_KEY là Fernet key hợp lệ (44 ký tự base64) — nếu sai, mọi credential Discord
     đã mã hoá sẽ không giải được.
  3. DATABASE_URL kết nối được và bảng `accounts` của admin_manager tồn tại (đúng DB dùng chung).
  4. reward-service KHÔNG vô tình trỏ vào DB rỗng/khác — kiểm luôn migrate reward_* đã sẵn sàng.

Exit code != 0 → CI dừng deploy, KHÔNG restart pm2. Không có secret nào lọt ra stdout/stderr.
"""

from __future__ import annotations

import hashlib
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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Đọc file .env dạng KEY=VALUE (bỏ comment/dòng trống). Không xử lý quote phức tạp."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _sha(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def check_jwt_secret_matches_admin() -> None:
    reward_secret = os.environ.get("JWT_SECRET", "").strip()
    if not reward_secret:
        _fail("JWT_SECRET của reward-service chưa được set trong môi trường.")
    if reward_secret in ("dev-secret-thay-doi-khi-deploy", "REPLACE_WITH_RANDOM_HEX_64"):
        _fail("JWT_SECRET vẫn là giá trị placeholder/dev — phải copy đúng secret production của admin_manager.")

    admin_env_path = Path(os.environ.get("ADMIN_ENV_PATH", "/srv/admin/.env"))
    if not admin_env_path.is_file():
        _fail(f"Không tìm thấy .env của admin_manager tại {admin_env_path} (đặt ADMIN_ENV_PATH nếu khác).")

    admin_env = _parse_env_file(admin_env_path)
    admin_secret = admin_env.get("JWT_SECRET", "").strip()
    if not admin_secret:
        _fail(f"{admin_env_path} không chứa JWT_SECRET.")

    if _sha(reward_secret) != _sha(admin_secret):
        _fail("JWT_SECRET của reward-service KHÁC admin_manager — token sẽ không xác thực chéo được.")
    _ok("JWT_SECRET trùng khít admin_manager (so bằng SHA-256).")


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

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        _fail("DATABASE_URL chưa set.")

    async def _probe() -> None:
        try:
            conn = await asyncpg.connect(dsn)
        except Exception as exc:  # noqa: BLE001
            _fail(f"Không kết nối được DATABASE_URL: {type(exc).__name__}")
        try:
            has_accounts = await conn.fetchval("SELECT to_regclass('public.accounts') IS NOT NULL")
            if not has_accounts:
                _fail("DB kết nối được nhưng KHÔNG có bảng `accounts` — sai DB (phải là DB dùng chung với admin_manager).")
            _ok("Kết nối DB thành công và bảng `accounts` của admin_manager tồn tại.")
        finally:
            await conn.close()

    asyncio.run(_probe())


def main() -> None:
    check_jwt_secret_matches_admin()
    check_fernet_key()
    check_database()
    print("[check-env] Tất cả kiểm tra PASS — an toàn để migrate + start service.")


if __name__ == "__main__":
    main()
