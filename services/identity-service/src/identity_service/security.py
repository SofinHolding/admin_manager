"""Primitive bảo mật: băm mật khẩu, sinh và băm token. Không có nghiệp vụ.

- Mật khẩu: bcrypt cost 12 (cùng legacy, nên hash cũ verify được sau khi migrate).
- Token (verify email, reset password, refresh): sinh ngẫu nhiên mạnh, DB chỉ lưu sha256. Lộ DB không lộ
  được token còn hiệu lực. Token có entropy cao nên sha256 (không cần salt/KDF) là đủ.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import bcrypt

BCRYPT_ROUNDS = 12
BCRYPT_MAX_BYTES = 72  # bcrypt chỉ xét 72 byte đầu của mật khẩu (UTF-8)


def password_too_long(password: str) -> bool:
    """Mật khẩu vượt giới hạn 72 BYTE (không phải ký tự) của bcrypt. Dùng ở schema để trả 422 thay vì 500."""
    return len(password.encode()) > BCRYPT_MAX_BYTES


def _bcrypt_input(password: str) -> bytes:
    """Cắt về 72 byte đúng như bcrypt<5 vẫn âm thầm làm (bcrypt>=5 ném ValueError thay vì cắt).

    Hash legacy tạo bởi bcrypt cũ từ mật khẩu dài vẫn verify được nhờ cắt tương thích này.
    """
    return password.encode()[:BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Schema đã chặn mật khẩu >72 byte; cắt ở đây chỉ là phòng thủ chiều sâu (không để thành 500)."""
    return bcrypt.hashpw(_bcrypt_input(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(password), password_hash.encode())
    except (ValueError, TypeError):  # hash hỏng/không đúng định dạng -> không khớp, không ném lỗi
        return False


def new_token() -> str:
    """Token 192 bit, dạng hex (an toàn khi nhúng vào URL)."""
    return secrets.token_hex(24)


def new_numeric_code() -> str:
    """Mã 6 chữ số cho bước xác nhận đổi mật khẩu."""
    return f"{secrets.randbelow(10**6):06d}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str:
    """Định dạng thời gian đúng như legacy (`2026-10-05T10:00:00+00:00`); không có giá trị -> ''."""
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")
