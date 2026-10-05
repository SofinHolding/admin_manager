"""Xác thực + phân quyền cho reward-service (Phase 4: hết JWT secret chung).

Access token là RS256 do Identity ký. Reward verify OFFLINE bằng `manager_sdk.TokenVerifier` (JWKS cache trong bộ
nhớ, kiểm `iss`/`aud`/`exp`/`nbf`) — Identity chết thì token còn hạn vẫn được phục vụ bằng khoá đã cache.

Phân quyền thực thi CỤC BỘ từ claim `perms` (không gọi Identity trên đường request):
    route người dùng  → `reward:access`  (role `admin` được coi là có mọi quyền, xem Principal.has_perm)
    route quản trị    → `reward:admin`   (role `admin` hoặc permission `reward:admin`)
Sau đó đối chiếu denylist CỤC BỘ (`account_denylist`, nuôi bởi feed Identity) để chặn NGAY tài khoản vừa bị thu quyền /
khoá / xoá / thu hồi phiên mà access token (≈15 phút) chưa hết hạn.

Lỗi giữ kiểu legacy `{"detail": ...}` để frontend cũ không đổi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from fastapi import Header, HTTPException, Request, status

import sdk_path  # noqa: F401  — trước khi import manager_sdk
from manager_sdk import AuthError, Principal, TokenVerifier
from store.pool import Pool
from store.repositories import projection as proj

logger = logging.getLogger("reward.security")

REWARD_ACCESS = "reward:access"
REWARD_ADMIN = "reward:admin"

_DENIAL_RESPONSE = {
    "suspended": (status.HTTP_403_FORBIDDEN, "Tài khoản không hoạt động"),
    "deleted": (status.HTTP_403_FORBIDDEN, "Tài khoản không hoạt động"),
    "permission_revoked": (status.HTTP_403_FORBIDDEN, "Tài khoản chưa được cấp quyền reward"),
    proj.SESSION_REASON: (status.HTTP_401_UNAUTHORIZED, "Phiên đã bị thu hồi"),
}


@dataclass(frozen=True)
class AuthContext:
    account_id: str
    username: str
    token_role: str
    perms: frozenset[str] = field(default_factory=frozenset)
    sid: str = ""
    iat: int = 0
    is_reward_admin: bool = False


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


def _context(principal: Principal) -> AuthContext:
    return AuthContext(
        account_id=principal.sub, username=principal.username, token_role=principal.role,
        perms=principal.perms, sid=principal.sid, iat=int(principal.claims.get("iat", 0) or 0),
        is_reward_admin=principal.has_perm(REWARD_ADMIN))


class Security:
    """Đóng gói `TokenVerifier` + pool (denylist cục bộ) để cung cấp các FastAPI dependency phân quyền."""

    def __init__(self, pool: Pool, verifier: TokenVerifier) -> None:
        self._pool = pool
        self.verifier = verifier

    async def verify_token(self, token: str) -> Principal:
        try:
            return await self.verifier.verify(token)
        except AuthError as exc:
            raise HTTPException(exc.status_code, exc.message) from None

    async def _authenticate(self, request: Request, authorization: str) -> tuple[Principal, AuthContext]:
        """Verify chữ ký/claim + denylist cục bộ. Kết quả cache trên `request.state` (một request chạy nhiều dependency)."""
        cached = getattr(request.state, "reward_auth", None)
        if cached is not None and cached[0] == authorization:
            return cached[1], cached[2]
        principal = await self.verify_token(extract_token(authorization))
        ctx = _context(principal)
        async with self._pool.acquire() as conn:
            reason = await proj.is_denied(conn, account_id=ctx.account_id, sid=ctx.sid, iat=ctx.iat)
        if reason:
            code, message = _DENIAL_RESPONSE.get(reason, (status.HTTP_403_FORBIDDEN, "Tài khoản không hoạt động"))
            raise HTTPException(code, message)
        request.state.reward_auth = (authorization, principal, ctx)
        return principal, ctx

    # ── Dependency ──────────────────────────────────────────────────────────────

    async def require_reward_user(self, request: Request, authorization: str = Header("")) -> AuthContext:
        """Cần `reward:access` (hoặc role admin) và không bị denylist."""
        principal, ctx = await self._authenticate(request, authorization)
        if not principal.has_perm(REWARD_ACCESS):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản chưa được cấp quyền reward")
        return ctx

    async def require_admin_role(self, request: Request, authorization: str = Header("")) -> AuthContext:
        """Cần `reward:admin` (hoặc role admin) và không bị denylist — dùng cho `/v1/reward/admin/*`."""
        principal, ctx = await self._authenticate(request, authorization)
        if not principal.has_perm(REWARD_ADMIN):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên mới được phép")
        return ctx

    async def require_active_account(self, request: Request, authorization: str = Header("")) -> AuthContext:
        """Token hợp lệ + tài khoản KHÔNG nằm trong denylist (bị khoá/xoá/thu quyền/thu hồi phiên) — bắt buộc trước
        mọi endpoint GHI. Không kiểm permission (đi kèm `require_reward_user`/`require_admin_role`)."""
        _principal, ctx = await self._authenticate(request, authorization)
        return ctx
