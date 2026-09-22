"""Verify JWT phát hành bởi `admin_manager` (auth.py) — TỰ GIẢI MÃ CỤC BỘ, KHÔNG gọi :8421.

`JWT_SECRET`, thuật toán `HS256` và payload `{sub, username, role, exp, iat}` phải khớp CHÍNH XÁC với
`admin_manager/admin-server/auth.py:_create_access_token` — không có `aud`/`iss`, nên KHÔNG được truyền
`audience=`/`issuer=`/`options={"require": [...]}` vào `jwt.decode`.

Hai tầng phân quyền (xem plan A.3.3):
    T1 — token: chữ ký + `exp` + `role` trong payload (rẻ, không chạm DB).
    T2 — DB: `SELECT role, status FROM accounts WHERE id = $sub` — bắt buộc cho mọi endpoint GHI,
         vì JWT sống 15 phút nhưng role/status có thể đã đổi trong lúc đó.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, status

from settings import RewardSettings
from store.pool import Pool

logger = logging.getLogger("reward.security")

# Thứ bậc quyền để so sánh "role trong DB có còn đủ với role token đã claim hay không" (T2).
_TIER = {"viewer": 0, "discord": 1, "admin": 2}


@dataclass(frozen=True)
class AuthContext:
    account_id: str
    username: str
    token_role: str


def decode_access_token(token: str, jwt_secret: str) -> dict:
    """Giải JWT bằng `JWT_SECRET` dùng chung với admin_manager. Ném 401 nếu hỏng hoặc hết hạn."""
    try:
        return jwt.decode(token, jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token hết hạn") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token không hợp lệ") from exc


def extract_token(authorization: str) -> str:
    """Lấy token thô từ header `Authorization: Bearer <token>`. Ném 401 nếu thiếu/hỏng định dạng."""
    token = try_extract_token(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
    return token


def try_extract_token(authorization: str) -> str | None:
    """Như `extract_token` nhưng trả `None` thay vì ném lỗi — dùng cho endpoint chẩn đoán."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class Security:
    """Đóng gói `JWT_SECRET` + pool DB để cung cấp các FastAPI dependency phân quyền."""

    def __init__(self, settings: RewardSettings, pool: Pool) -> None:
        self._jwt_secret = settings.jwt_secret
        self._pool = pool

    def decode_access_token(self, token: str) -> dict:
        return decode_access_token(token, self._jwt_secret)

    def _context_from_authorization(self, authorization: str) -> AuthContext:
        token = extract_token(authorization)
        payload = self.decode_access_token(token)
        sub = payload.get("sub")
        role = payload.get("role")
        if not sub or not role:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token không hợp lệ")
        return AuthContext(account_id=str(sub), username=str(payload.get("username", "")), token_role=str(role))

    # ── T1 — chỉ kiểm chữ ký + role trong token ─────────────────────────────────

    async def require_reward_user(self, authorization: str = Header("")) -> AuthContext:
        """Role trong token phải thuộc `{discord, admin}` — viewer bị 403 ngay ở tầng token."""
        ctx = self._context_from_authorization(authorization)
        if ctx.token_role not in ("discord", "admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản chưa được cấp quyền reward")
        return ctx

    async def require_discord(self, authorization: str = Header("")) -> AuthContext:
        """Chỉ role token đúng `discord` mới qua (không tự động cho `admin` — dùng cho thao tác cá nhân)."""
        ctx = self._context_from_authorization(authorization)
        if ctx.token_role != "discord":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ tài khoản discord mới được phép")
        return ctx

    async def require_admin_role(self, authorization: str = Header("")) -> AuthContext:
        """Chỉ role token đúng `admin` mới qua — dùng cho `/v1/reward/admin/*`."""
        ctx = self._context_from_authorization(authorization)
        if ctx.token_role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên mới được phép")
        return ctx

    # ── T2 — đối chiếu DB: status active + role hiện tại còn đủ quyền ───────────

    async def require_active_account(self, authorization: str = Header("")) -> AuthContext:
        """`SELECT role, status FROM accounts WHERE id = $1` — bắt buộc trước mọi endpoint GHI.

        `status != 'active'` ⇒ 403. Role trong DB đã bị hạ xuống thấp hơn role token đã claim ⇒ 403
        (chặn trường hợp admin bị thu quyền giữa lúc access token 15 phút vẫn còn hạn)."""
        ctx = self._context_from_authorization(authorization)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT role, status FROM accounts WHERE id = $1", ctx.account_id)
        if row is None or row["status"] != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản không hoạt động")
        if _TIER.get(row["role"], 0) < _TIER.get(ctx.token_role, 0):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản không còn đủ quyền")
        return AuthContext(account_id=ctx.account_id, username=ctx.username, token_role=row["role"])
