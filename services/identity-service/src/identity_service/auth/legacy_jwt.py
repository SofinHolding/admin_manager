"""JWT HS256 tương thích với admin-server/reward-service cũ (chung `JWT_SECRET`).

TẠM THỜI trong giai đoạn Strangler. Phase 1.4 thay bằng RS256 + JWKS (module `tokens`); cửa sổ chấp nhận cả hai
tối đa bằng TTL của access token (15 phút), sau đó xóa file này.
Payload giữ nguyên: sub, username, role, client_type, exp, iat.
"""

from __future__ import annotations

import time

import jwt

from identity_service.errors import DomainError

ALGORITHM = "HS256"


def create_access_token(secret: str, *, account_id: str, username: str, role: str,
                        client_type: str, ttl_s: int, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    return jwt.encode({"sub": account_id, "username": username, "role": role,
                       "client_type": client_type, "exp": issued + ttl_s, "iat": issued},
                      secret, algorithm=ALGORITHM)


def decode_access_token(secret: str, token: str) -> dict:
    """Giải JWT, ném DomainError(401) nếu hỏng hoặc hết hạn. Chỉ chấp nhận đúng HS256."""
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise DomainError(401, "Token hết hạn") from None
    except jwt.InvalidTokenError:
        raise DomainError(401, "Token không hợp lệ") from None
