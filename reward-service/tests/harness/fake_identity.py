"""Fake Identity cho test reward-service (offline, `127.0.0.1`, cổng ngẫu nhiên) — KHÔNG import code Identity.

Sinh khoá RSA THẬT, ký access token RS256 THẬT, phục vụ JWKS; phục vụ API công khai mà Reward gọi
(`/v1/auth/{login,refresh,logout,me}`, `/v1/admin/users`, `PUT|DELETE /v1/admin/accounts/{id}/permissions/{perm}`)
và change feed `/internal/v1/feed` (đúng hợp đồng `docs/api/internal-feed-contract-v1.md`).

Điều khiển từ test: `add_user`, `token`, `emit`, `stop()` / `restart()` (Identity chết / sống lại), `hang`
(treo để thử timeout), `feed_failures` (trả 500 N lần), `requests` (nhật ký request nhận được).
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hmac
import itertools
import secrets
import time
import uuid
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ISSUER = "http://identity.test"
AUDIENCE = "manager-api"

# Sinh khoá RSA một lần mỗi phiên test (tốn ~0.2s); mỗi FakeIdentity dùng chung khoá, kid khác nhau theo instance.
_SESSION_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


def jwk_for(kid: str, key: rsa.RSAPrivateKey) -> dict[str, str]:
    nums = key.public_key().public_numbers()
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _b64(nums.n), "e": _b64(nums.e)}


def sign(claims: dict[str, Any], *, key: rsa.RSAPrivateKey = _SESSION_KEY, kid: str = "kid-test") -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid, "typ": "at+jwt"})


class FakeIdentity:
    def __init__(self) -> None:
        self.kid = f"kid-{uuid.uuid4().hex[:6]}"
        self.feed_key = secrets.token_urlsafe(24)
        self.users: dict[str, dict[str, Any]] = {}
        self.passwords: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self._seq = itertools.count(1)
        self.requests: list[dict[str, Any]] = []
        self.feed_failures = 0      # N lần kế tiếp trả 500
        self.hang_seconds = 0.0     # >0: mọi request (trừ JWKS) ngủ ngần này giây trước khi trả lời
        self.base_url = ""
        self.port = 0
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._app = self._build_app()

    # ── Dữ liệu ─────────────────────────────────────────────────────────────────

    def add_user(self, *, role: str = "viewer", status: str = "active", perms: tuple[str, ...] = (),
                 username: str | None = None, password: str | None = None, emit_event: bool = False) -> dict[str, Any]:
        uid = uuid.uuid4().hex
        username = username or f"user_{uid[:8]}"
        user = {"id": uid, "username": username, "email": f"{username}@example.test", "role": role,
                "status": status, "created_at": "2026-01-01T00:00:00Z", "last_login": "",
                "perms": set(perms), "sid": uuid.uuid4().hex}
        self.users[uid] = user
        if password:
            self.passwords[username] = password
        if emit_event:
            self.emit("account.registered", {"account_id": uid, "username": username, "role": role, "status": status})
        return user

    def effective_perms(self, user: dict[str, Any]) -> list[str]:
        implied = {"reward:access"} if user["role"] == "discord" else set()
        return sorted(user["perms"] | implied)

    def token(self, user: dict[str, Any], *, ttl: int = 900, **over: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER, "aud": [AUDIENCE], "sub": user["id"], "username": user["username"], "role": user["role"],
            "perms": self.effective_perms(user), "client_type": "web", "sid": user["sid"], "jti": uuid.uuid4().hex,
            "iat": now, "nbf": now, "exp": now + ttl}
        claims.update(over)
        return sign(claims, kid=self.kid)

    def bearer(self, user: dict[str, Any], **over: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(user, **over)}"}

    def emit(self, type_: str, data: dict[str, Any], *, subject: str | None = None, event_id: str | None = None,
             when: datetime.datetime | None = None) -> dict[str, Any]:
        seq = next(self._seq)
        when = when or datetime.datetime.now(datetime.timezone.utc)
        event = {"seq": seq, "id": event_id or f"evt_{uuid.uuid4().hex}", "type": type_,
                 "subject": subject or f"acc:{data.get('account_id', '')}",
                 "time": when.isoformat().replace("+00:00", "Z"), "data_version": 1, "data": data}
        self.events.append(event)
        return event

    # Thao tác kiểu Identity (đổi trạng thái + phát event như Identity thật)
    def grant_permission(self, account_id: str, permission: str) -> bool:
        user = self.users[account_id]
        changed = permission not in user["perms"]
        user["perms"].add(permission)
        if changed:
            self.emit("account.permission_changed",
                      {"account_id": account_id, "permission": permission, "granted": True})
        return changed

    def revoke_permission(self, account_id: str, permission: str) -> bool:
        user = self.users[account_id]
        changed = permission in user["perms"]
        user["perms"].discard(permission)
        if changed:
            self.emit("account.permission_changed",
                      {"account_id": account_id, "permission": permission, "granted": False})
        return changed

    def set_status(self, account_id: str, status: str) -> None:
        user = self.users[account_id]
        previous, user["status"] = user["status"], status
        self.emit("account.status_changed", {"account_id": account_id, "status": status, "previous": previous})
        if status == "suspended":
            self.emit("session.revoked", {"account_id": account_id})

    def delete_account(self, account_id: str) -> None:
        self.users.pop(account_id, None)
        self.emit("account.deleted", {"account_id": account_id})

    def calls(self, method: str, path_prefix: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == method and r["path"].startswith(path_prefix)]

    # ── HTTP ────────────────────────────────────────────────────────────────────

    def _claims(self, request: Request) -> dict[str, Any] | None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        try:
            return jwt.decode(auth[7:], _SESSION_KEY.public_key(), algorithms=["RS256"], audience=AUDIENCE,
                              issuer=ISSUER, options={"verify_exp": True})
        except jwt.InvalidTokenError:
            return None

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def log_requests(request: Request, call_next):
            self.requests.append({"method": request.method, "path": request.url.path, "query": dict(request.query_params),
                                  "x_real_ip": request.headers.get("x-real-ip"),
                                  "x_client_type": request.headers.get("x-client-type"),
                                  "authorization": request.headers.get("authorization"),
                                  "service_key": request.headers.get("x-service-key")})
            if self.hang_seconds and not request.url.path.startswith("/.well-known"):
                await asyncio.sleep(self.hang_seconds)
            return await call_next(request)

        @app.get("/.well-known/jwks.json")
        async def jwks() -> dict:
            return {"keys": [jwk_for(self.kid, _SESSION_KEY)]}

        @app.post("/v1/auth/login")
        async def login(request: Request) -> JSONResponse:
            body = await request.json()
            user = next((u for u in self.users.values() if u["username"] == body.get("username")), None)
            if not user or self.passwords.get(user["username"]) != body.get("password"):
                return JSONResponse({"detail": "Sai tên đăng nhập hoặc mật khẩu"}, status_code=401)
            return JSONResponse({"access_token": self.token(user), "refresh_token": f"rt-{user['id']}",
                                 "expires_in": 900, "user": {"id": user["id"], "username": user["username"],
                                                              "role": user["role"]}})

        @app.post("/v1/auth/refresh")
        async def refresh(request: Request) -> JSONResponse:
            body = await request.json()
            uid = str(body.get("refresh_token", ""))[3:]
            user = self.users.get(uid)
            if not user:
                return JSONResponse({"detail": "Refresh token không hợp lệ hoặc đã hết hạn"}, status_code=401)
            return JSONResponse({"access_token": self.token(user), "expires_in": 900})

        @app.post("/v1/auth/logout")
        async def logout(request: Request) -> JSONResponse:
            await request.json()
            return JSONResponse({"ok": True})

        @app.get("/v1/auth/me")
        async def me(request: Request) -> JSONResponse:
            claims = self._claims(request)
            user = self.users.get(claims["sub"]) if claims else None
            if not user:
                return JSONResponse({"detail": "Token không hợp lệ"}, status_code=401)
            return JSONResponse({"id": user["id"], "username": user["username"], "email": user["email"],
                                 "role": user["role"], "created_at": user["created_at"]})

        def _require_admin(request: Request) -> tuple[dict | None, JSONResponse | None]:
            claims = self._claims(request)
            if not claims:
                return None, JSONResponse({"detail": "Token không hợp lệ"}, status_code=401)
            if claims.get("role") not in ("admin", "dash_admin"):
                return None, JSONResponse({"detail": "Chỉ quản trị viên mới có quyền này"}, status_code=403)
            return claims, None

        @app.get("/v1/admin/users")
        async def list_users(request: Request) -> JSONResponse:
            _claims, err = _require_admin(request)
            if err:
                return err
            return JSONResponse({"users": [{k: v for k, v in u.items() if k not in ("perms", "sid")}
                                           for u in self.users.values()]})

        async def change(request: Request, account_id: str, permission: str, grant: bool) -> JSONResponse:
            claims, err = _require_admin(request)
            if err:
                return err
            if permission not in ("reward:access", "reward:admin"):
                return JSONResponse({"detail": "Permission không tồn tại"}, status_code=404)
            if permission == "reward:admin" and claims.get("role") != "admin":
                return JSONResponse({"detail": "Chỉ quản trị viên (admin) mới được cấp/thu quyền này"}, status_code=403)
            if account_id not in self.users:
                return JSONResponse({"detail": "Tài khoản không tồn tại"}, status_code=404)
            changed = self.grant_permission(account_id, permission) if grant else self.revoke_permission(
                account_id, permission)
            return JSONResponse({"ok": True, "account_id": account_id, "permission": permission, "granted": grant,
                                 "changed": changed})

        @app.put("/v1/admin/accounts/{account_id}/permissions/{permission}")
        async def grant(request: Request, account_id: str, permission: str) -> JSONResponse:
            return await change(request, account_id, permission, True)

        @app.delete("/v1/admin/accounts/{account_id}/permissions/{permission}")
        async def revoke(request: Request, account_id: str, permission: str) -> JSONResponse:
            return await change(request, account_id, permission, False)

        @app.get("/internal/v1/feed")
        async def feed(request: Request) -> JSONResponse:
            key = request.headers.get("x-service-key", "")
            if not hmac.compare_digest(key.encode(), self.feed_key.encode()):
                return JSONResponse({"detail": "service key không hợp lệ"}, status_code=401)
            if self.feed_failures > 0:
                self.feed_failures -= 1
                return JSONResponse({"detail": "lỗi giả lập"}, status_code=500)
            after = int(request.query_params.get("after", 0))
            limit = int(request.query_params.get("limit", 100))
            wanted = [t for t in request.query_params.get("types", "").split(",") if t]
            rows = [e for e in self.events if e["seq"] > after and (not wanted or e["type"] in wanted)]
            page = rows[:limit]
            head = self.events[-1]["seq"] if self.events else 0
            next_cursor = page[-1]["seq"] if page else after
            return JSONResponse({"events": page, "next_cursor": next_cursor, "has_more": len(rows) > limit,
                                 "head": head})

        return app

    # ── Vòng đời server ─────────────────────────────────────────────────────────

    async def start(self, port: int = 0) -> None:
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        sock = config.bind_socket()
        self.port = sock.getsockname()[1]
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        while not self._server.started:
            await asyncio.sleep(0.01)
        self.base_url = f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        """Giả lập Identity chết: đóng cổng — kết nối mới bị từ chối."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:  # noqa: BLE001 — dọn tài nguyên test
                self._task.cancel()
        self._server = self._task = None

    async def restart(self) -> None:
        await self.stop()
        await self.start(self.port)

    @property
    def running(self) -> bool:
        return self._server is not None


async def start_fake_identity() -> FakeIdentity:
    fake = FakeIdentity()
    await fake.start()
    return fake
