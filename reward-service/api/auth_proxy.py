"""Proxy xác thực + endpoint chẩn đoán JWT (`/v1/reward/auth/*`, `/v1/reward/health`).

reward-service KHÔNG BAO GIỜ ký/phát hành JWT — chỉ verify cục bộ bằng `JWT_SECRET` dùng chung với
admin_manager (`security.py`), và chuyển tiếp NGUYÊN TRẠNG các thao tác đăng nhập/refresh/logout sang
`ADMIN_API_BASE` (:8421) bằng `httpx`, kèm header `X-Real-IP` (bắt buộc — xem plan A.3.4, rate limit của
`admin_manager/admin-server/auth.py:_client_ip` tính theo IP này). KHÔNG log password, KHÔNG log token.
"""

from __future__ import annotations

import hashlib
import logging

import httpx
import jwt
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

from security import Security, extract_token, try_extract_token
from settings import RewardSettings
from store import migrate
from store.pool import Pool
from store.repositories import accounts as accounts_repo

logger = logging.getLogger("reward.auth_proxy")

VERSION = "0.1.0-phase1"


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
    """Cùng logic `admin_manager/admin-server/auth.py:_client_ip` — ưu tiên `X-Real-IP` của request gốc."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _passthrough(resp: httpx.Response) -> Response:
    """Trả nguyên status code + body JSON từ admin_manager — không diễn giải lại."""
    return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")


def make_router(settings: RewardSettings, security: Security, pool: Pool, http_client: httpx.AsyncClient) -> APIRouter:
    router = APIRouter(tags=["reward-auth"])

    async def _proxy(path: str, body: dict, real_ip: str) -> httpx.Response:
        try:
            return await http_client.post(path, json=body, headers={"X-Real-IP": real_ip})
        except httpx.HTTPError:
            logger.warning("admin_manager không phản hồi khi proxy %s", path)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Dịch vụ xác thực không sẵn sàng")

    @router.post("/v1/reward/auth/login", summary="Proxy đăng nhập tới admin_manager")
    async def login(body: LoginBody, request: Request) -> Response:
        resp = await _proxy("/v1/auth/login", body.model_dump(), _client_ip(request))
        return _passthrough(resp)

    @router.post("/v1/reward/auth/refresh", summary="Proxy refresh token tới admin_manager")
    async def refresh(body: RefreshBody, request: Request) -> Response:
        resp = await _proxy("/v1/auth/refresh", body.model_dump(), _client_ip(request))
        return _passthrough(resp)

    @router.post("/v1/reward/auth/logout", summary="Proxy đăng xuất tới admin_manager")
    async def logout(body: LogoutBody, request: Request) -> Response:
        resp = await _proxy("/v1/auth/logout", body.model_dump(), _client_ip(request))
        return _passthrough(resp)

    @router.get("/v1/reward/auth/me", summary="Verify token cục bộ + làm giàu từ DB")
    async def me(authorization: str = Header("")) -> dict:
        token = extract_token(authorization)
        payload = security.decode_access_token(token)
        account_id = str(payload.get("sub", ""))
        row = await accounts_repo.get_by_id(pool, account_id)
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản không tồn tại")
        role = row["role"]
        return {
            "id": account_id,
            "username": row["username"],
            "role": role,
            "token_role": payload.get("role"),
            "status": row["status"],
            "reward_access": role in ("discord", "admin"),
            "has_credential": False,
            "credential_status": None,
            "discord_username": None,
        }

    @router.post("/v1/reward/auth/validate", summary="Công cụ chẩn đoán: verify JWT bằng JWT_SECRET cục bộ")
    async def validate(body: ValidateBody, authorization: str = Header("")) -> dict:
        token = (body.token or "").strip() or try_extract_token(authorization)
        if not token:
            return {"valid": False, "claims": None, "reward_access": False, "reason": "Thiếu token"}
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return {"valid": False, "claims": None, "reward_access": False, "reason": "Token hết hạn"}
        except jwt.InvalidTokenError:
            return {"valid": False, "claims": None, "reward_access": False, "reason": "Token không hợp lệ"}
        claims = {k: payload.get(k) for k in ("sub", "username", "role", "exp", "iat")}
        return {
            "valid": True,
            "claims": claims,
            "reward_access": payload.get("role") in ("discord", "admin"),
            "reason": None,
        }

    @router.get("/v1/reward/health", summary="Health-check cho pm2/nginx/CI — không auth")
    async def health() -> dict:
        db_ok = await pool.ping()
        migration_version = await migrate.current_version(pool) if db_ok else None
        admin_api_ok = await _check_admin_api()
        fingerprint = hashlib.sha256(settings.jwt_secret.encode("utf-8")).hexdigest()[:8]
        return {
            "ok": db_ok,
            "db": "ok" if db_ok else "down",
            "migration_version": migration_version,
            "admin_api": admin_api_ok,
            "jwt_secret_fingerprint": fingerprint,
            "version": VERSION,
        }

    async def _check_admin_api() -> bool:
        try:
            resp = await http_client.get("/v1/health", timeout=3.0)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    return router
