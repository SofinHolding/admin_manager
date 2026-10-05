"""Proxy xác thực MỎNG + endpoint chẩn đoán (`/v1/reward/auth/*`, `/v1/reward/health`).

reward-service KHÔNG ký/phát hành token và KHÔNG đọc bảng tài khoản. Các route `login/refresh/logout/me` GIỮ để
frontend cũ chạy nguyên, nhưng chỉ còn chuyển tiếp tới API CÔNG KHAI của Identity (`/v1/auth/*`) kèm `X-Real-IP`
(Identity rate-limit theo IP), có timeout + retry/backoff giới hạn (`identity_link/client.py`). Identity chết ⇒ 503
`UNAVAILABLE`. KHÔNG log mật khẩu/token. Các route này là DEPRECATED: client mới gọi thẳng Identity.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel

from identity_link.client import REWARD_ACCESS, IdentityClient
from security import Security, extract_token, try_extract_token
from settings import RewardSettings
from store import migrate
from store.pool import Pool
from store.repositories import feed as feed_repo

logger = logging.getLogger("reward.auth_proxy")

VERSION = "0.4.0-phase4"
_DEPRECATED = "DEPRECATED — dùng thẳng Identity: "


class LoginBody(BaseModel):
    username: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str


class ValidateBody(BaseModel):
    token: str | None = None


def _client_ip(request: Request) -> str:
    """Ưu tiên `X-Real-IP` của request gốc (do nginx gắn) — Identity rate-limit theo IP này."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _passthrough(resp) -> Response:
    """Trả nguyên status code + body JSON từ Identity — không diễn giải lại."""
    return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")


def make_router(
    settings: RewardSettings, security: Security, pool: Pool, identity: IdentityClient, feed_state=None,
) -> APIRouter:
    router = APIRouter(tags=["reward-auth"])

    async def _post(path: str, body: dict, request: Request) -> Response:
        extra = {}
        client_type = request.headers.get("x-client-type")
        if client_type:
            extra["X-Client-Type"] = client_type
        resp = await identity.request("POST", path, json=body, real_ip=_client_ip(request), extra_headers=extra)
        return _passthrough(resp)

    @router.post("/v1/reward/auth/login", summary=_DEPRECATED + "POST /v1/auth/login", deprecated=True)
    async def login(body: LoginBody, request: Request) -> Response:
        return await _post("/v1/auth/login", body.model_dump(), request)

    @router.post("/v1/reward/auth/refresh", summary=_DEPRECATED + "POST /v1/auth/refresh", deprecated=True)
    async def refresh(body: RefreshBody, request: Request) -> Response:
        return await _post("/v1/auth/refresh", body.model_dump(), request)

    @router.post("/v1/reward/auth/logout", summary=_DEPRECATED + "POST /v1/auth/logout", deprecated=True)
    async def logout(body: LogoutBody, request: Request) -> Response:
        return await _post("/v1/auth/logout", body.model_dump(), request)

    @router.get("/v1/reward/auth/me", summary=_DEPRECATED + "GET /v1/auth/me", deprecated=True)
    async def me(authorization: str = Header("")):
        token = extract_token(authorization)
        principal = await security.verify_token(token)  # offline: sai chữ ký/aud/hết hạn ⇒ 401, không gọi Identity
        resp = await identity.request("GET", "/v1/auth/me", authorization=f"Bearer {token}")
        if resp.status_code != 200:
            return _passthrough(resp)
        profile = resp.json()
        return {
            "id": principal.sub,
            "username": profile.get("username") or principal.username,
            "role": profile.get("role") or principal.role,
            "token_role": principal.role,
            "status": "active",
            "reward_access": principal.has_perm(REWARD_ACCESS),
            "has_credential": False,
            "credential_status": None,
            "discord_username": None,
        }

    @router.post("/v1/reward/auth/validate", summary="Chẩn đoán: verify access token RS256 offline (JWKS)")
    async def validate(body: ValidateBody, authorization: str = Header("")) -> dict:
        token = (body.token or "").strip() or try_extract_token(authorization)
        if not token:
            return {"valid": False, "claims": None, "reward_access": False, "reason": "Thiếu token"}
        try:
            principal = await security.verifier.verify(token)
        except Exception as exc:  # noqa: BLE001 — AuthError mang message an toàn, mọi lỗi khác coi như không hợp lệ
            reason = getattr(exc, "message", "Token không hợp lệ")
            return {"valid": False, "claims": None, "reward_access": False, "reason": reason}
        claims = {k: principal.claims.get(k) for k in ("sub", "username", "role", "exp", "iat")}
        claims["perms"] = sorted(principal.perms)
        return {"valid": True, "claims": claims, "reward_access": principal.has_perm(REWARD_ACCESS), "reason": None}

    @router.get("/v1/reward/health", summary="Health-check cho pm2/nginx/CI — không auth, KHÔNG gọi service khác")
    async def health() -> dict:
        db_ok = await pool.ping()
        migration_version = await migrate.current_version(pool) if db_ok else None
        cursor = None
        if db_ok:
            try:
                async with pool.acquire() as conn:
                    cursor = await feed_repo.get_cursor(conn, "identity")
            except Exception:  # noqa: BLE001 — health không được ném lỗi
                cursor = None
        return {
            "ok": db_ok,
            "db": "ok" if db_ok else "down",
            "migration_version": migration_version,
            "feed": {
                "enabled": settings.feed_enabled,
                "cursor": cursor,
                "last_error": getattr(feed_state, "last_error", None),
            },
            "version": VERSION,
        }

    return router
