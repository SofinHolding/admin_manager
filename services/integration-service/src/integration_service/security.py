"""Tiện ích bảo mật/định danh: thời gian, id, API key máy (`mpk_<key_id>.<secret>`).

DB chỉ lưu `key_id` + sha256(secret). Secret 256 bit ngẫu nhiên nên hash nhanh là đủ (không cần KDF chậm như mật khẩu).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timezone

API_KEY_RE = re.compile(r"^mpk_([A-Za-z0-9_-]{12})\.([A-Za-z0-9_-]{43})$")
# Hash giả để so sánh khi key_id không tồn tại: thời gian phản hồi không lộ key_id nào có thật.
_DUMMY_HASH = hashlib.sha256(b"khong-ton-tai").hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """(key_id, secret, api_key). `api_key` chỉ hiện đúng một lần."""
    key_id = secrets.token_urlsafe(9)      # 12 ký tự [A-Za-z0-9_-]
    secret = secrets.token_urlsafe(32)     # 43 ký tự
    return key_id, secret, f"mpk_{key_id}.{secret}"


def parse_api_key(authorization: str) -> tuple[str, str] | None:
    """Tách `Authorization: Bearer mpk_...`. Sai định dạng (kể cả JWT của user) => None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    m = API_KEY_RE.match(value.strip())
    return (m.group(1), m.group(2)) if m else None


def verify_secret(secret: str, stored_hash: str | None) -> bool:
    """So sánh hằng-thời-gian; luôn làm một phép so sánh kể cả khi không có bản ghi."""
    return hmac.compare_digest(sha256_hex(secret), stored_hash or _DUMMY_HASH) and stored_hash is not None
