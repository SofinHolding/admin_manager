"""Parity identity-service (Postgres) <-> admin-server legacy (SQLite): CÙNG kịch bản, so từng phản hồi.

Đây là bằng chứng Phase 1.2: frontend cũ không phải đổi gì. Test nằm ở tests/contract (được phép chạm cả hai bên);
service không bao giờ import code legacy.

Khác biệt CÓ CHỦ ĐÍCH (đều có test hồi quy riêng ở services/identity-service/tests/test_security_regressions.py)
KHÔNG nằm trong kịch bản so sánh này:
  - key hết hạn không đăng ký được; link xác thực không mở khoá tài khoản bị suspended;
  - mã đổi mật khẩu tách khỏi token reset (có giới hạn thử); lỗi validation 422 không phản chiếu giá trị đầu vào.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "admin-server", ROOT / "services" / "identity-service" / "src",
          ROOT / "packages" / "observability" / "src", ROOT / "packages" / "contracts" / "python"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pg_host = os.environ.get("TEST_PG_HOST")
pytestmark = pytest.mark.skipif(
    not pg_host, reason="cần Postgres (TEST_PG_HOST/TEST_PG_PORT). SKIP KHÔNG phải là đã kiểm chứng.")

SECRET = "parity-secret-at-least-16-chars"
TABLES = "auth_audit, refresh_tokens, accounts, invite_keys"


def _dsn() -> str:
    return (f"postgresql://identity_svc:dev_identity_change_me@{pg_host}:"
            f"{os.environ.get('TEST_PG_PORT', '5432')}/identity_db")


# ── Hai phía ────────────────────────────────────────────────────────────────────

class Side:
    def __init__(self, name: str, client: httpx.AsyncClient) -> None:
        self.name, self.client = name, client
        self.verify: list[str] = []
        self.reset: list[str] = []
        self.codes: list[str] = []

    async def make_key(self, role="viewer", max_uses=5) -> str: raise NotImplementedError
    async def set_status(self, username: str, status: str) -> None: raise NotImplementedError
    async def account(self, username: str) -> dict: raise NotImplementedError


class LegacySide(Side):
    def __init__(self, client, db):
        super().__init__("legacy", client)
        self.db = db

    async def make_key(self, role="viewer", max_uses=5):
        return (await self.db.create_invite_key(role=role, label="", max_uses=max_uses, expires_days=7))["key"]

    async def set_status(self, username, status):
        acc = await self.db.get_account_by_username(username)
        await self.db.set_account_status(acc["id"], status)

    async def account(self, username):
        return await self.db.get_account_by_username(username)


class NewSide(Side):
    def __init__(self, client, db):
        super().__init__("identity", client)
        self.db = db

    async def make_key(self, role="viewer", max_uses=5):
        import uuid
        key = f"{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
        async with self.db.connection() as conn:
            await conn.execute("INSERT INTO invite_keys (key, role, max_uses, expires_at) "
                               "VALUES ($1, $2, $3, now() + interval '7 days')", key, role, max_uses)
        return key

    async def set_status(self, username, status):
        async with self.db.connection() as conn:
            await conn.execute("UPDATE accounts SET status=$2 WHERE username=$1", username, status)

    async def account(self, username):
        async with self.db.connection() as conn:
            row = await conn.fetchrow("SELECT * FROM accounts WHERE username=$1", username)
        return dict(row) if row else None


@pytest.fixture
def legacy_modules(monkeypatch):
    auth = importlib.import_module("auth")
    admin_api = importlib.import_module("admin_api")
    legacy_db = importlib.import_module("db")

    async def instant(_seconds):  # legacy ngủ 1s khi đăng nhập sai; bỏ qua để test nhanh
        return None

    monkeypatch.setattr(auth, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth, "asyncio", types.SimpleNamespace(sleep=instant))
    auth._rate.clear()
    return auth, admin_api, legacy_db


@pytest_asyncio.fixture
async def legacy(legacy_modules, tmp_path):
    from fastapi import FastAPI
    auth, admin_api, legacy_db = legacy_modules
    db = legacy_db.SqliteDb(str(tmp_path / "legacy.db"))
    await db.setup()
    side_ref: dict = {}

    async def send_verify(email, token, username): side_ref["s"].verify.append(token)
    async def send_reset(email, token, username):
        side_ref["s"].reset.append(token)
        side_ref["s"].codes.append(token)  # legacy gửi mã 6 số qua cùng hàm send_reset

    app = FastAPI()
    app.include_router(auth.make_router(db, send_verify_email=send_verify, send_reset_email=send_reset))
    app.include_router(admin_api.make_router(db, decode_token=auth._decode_access_token,
                                             cache=types.SimpleNamespace()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        side = LegacySide(c, db)
        side_ref["s"] = side
        yield side


@pytest_asyncio.fixture
async def identity():
    from identity_service import migrate
    from identity_service.app import create_app
    from identity_service.config import Settings
    from identity_service.db import Database
    from manager_contracts import ErrorCode  # noqa: F401  (xác nhận package dùng chung nạp được)

    try:
        await migrate.run(_dsn())
    except migrate.MigrationError:
        import asyncpg
        conn = await asyncpg.connect(_dsn())
        await conn.execute(f"DROP TABLE IF EXISTS {TABLES}, schema_migrations CASCADE")
        await conn.close()
        await migrate.run(_dsn())
    db = Database(_dsn(), max_size=5)
    await db.open()
    async with db.connection() as conn:
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")

    holder: dict = {}

    class Mailer:
        async def send_verify(self, email, token, username): holder["s"].verify.append(token)
        async def send_reset(self, email, token, username): holder["s"].reset.append(token)
        async def send_change_code(self, email, code, username): holder["s"].codes.append(code)

    async def instant(_seconds): return None

    settings = Settings(port=8430, log_level="INFO", database_url=_dsn(), env="test",
                        jwt_compat_secret=SECRET, login_fail_delay_s=0.0)
    app = create_app(settings, database=db, mailer=Mailer(), sleep=instant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        side = NewSide(c, db)
        holder["s"] = side
        yield side
    await db.close()


# ── Chuẩn hoá để so sánh ────────────────────────────────────────────────────────

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_KEY = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
_TS_KEYS = {"created_at", "last_login", "expires_at"}
_TOKEN_KEYS = {"access_token", "refresh_token"}


def norm(value, key=""):
    if isinstance(value, dict):
        return {k: norm(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        items = [norm(v) for v in value]
        return sorted(items, key=lambda x: repr(x))
    if key in _TOKEN_KEYS:
        return "<token>"
    if key in _TS_KEYS:
        return "<ts>" if value else ""
    if key == "email_verified":
        return bool(value)  # SQLite lưu 0/1, Postgres lưu boolean
    if isinstance(value, str):
        if _HEX32.match(value):
            return "<id>"
        if _KEY.match(value) or key == "key":
            return "<key>"
        return re.sub(r"\d+ giây", "<n> giây", value)
    return value


def shape(response: httpx.Response):
    if response.status_code == 422:  # chỉ so loc/type: phía mới cố ý bỏ `input`/`ctx`/`url`
        return sorted((tuple(e["loc"]), e["type"]) for e in response.json()["detail"])
    try:
        return norm(response.json())
    except ValueError:
        return response.text


class Recorder:
    def __init__(self, side: Side) -> None:
        self.side, self.log = side, []

    async def call(self, label: str, method: str, path: str, **kw) -> httpx.Response:
        r = await self.side.client.request(method, path, **kw)
        self.log.append((label, r.status_code, shape(r)))
        return r


def ip(n: int) -> dict:
    return {"X-Real-IP": f"10.9.0.{n}"}


async def bearer(r: httpx.Response) -> dict:
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── Kịch bản ────────────────────────────────────────────────────────────────────

async def scenario_auth(side: Side) -> list:
    rec = Recorder(side)
    c = rec.call
    admin_key, vkey = await side.make_key("admin", 3), await side.make_key("viewer", 2)

    await c("validate ok", "POST", "/v1/auth/validate-key", json={"key": vkey}, headers=ip(1))
    await c("validate bogus", "POST", "/v1/auth/validate-key", json={"key": "NOPE-NOPE"}, headers=ip(1))
    await c("username free", "POST", "/v1/auth/check-username", json={"username": "admin_one"}, headers=ip(1))
    await c("email free", "POST", "/v1/auth/check-email", json={"email": "admin.one@example.com"}, headers=ip(1))

    reg = lambda u, k, n, e=None: dict(json={"key": k, "username": u, "password": "mat-khau-12345",  # noqa: E731
                                              "email": e or f"{u}@example.com"}, headers=ip(n))
    await c("register bad key", "POST", "/v1/auth/register", **reg("admin_one", "ZZZZ-ZZZZ", 2))
    await c("register admin", "POST", "/v1/auth/register", **reg("admin_one", admin_key, 3))
    await c("register dup username", "POST", "/v1/auth/register", **reg("admin_one", vkey, 4))
    await c("register dup email", "POST", "/v1/auth/register",
            **reg("khac_ten", vkey, 5, "ADMIN_ONE@example.com"))
    await c("username taken", "POST", "/v1/auth/check-username", json={"username": "admin_one"}, headers=ip(1))
    await c("email taken (case)", "POST", "/v1/auth/check-email",
            json={"email": "ADMIN.ONE@example.com"}, headers=ip(1))
    await c("login unverified", "POST", "/v1/auth/login",
            json={"username": "admin_one", "password": "mat-khau-12345"}, headers=ip(6))
    await c("resend generic", "POST", "/v1/auth/resend-verify", json={"email": "admin_one@example.com"}, headers=ip(7))
    await c("resend unknown", "POST", "/v1/auth/resend-verify", json={"email": "ai.do@example.com"}, headers=ip(7))
    await c("verify bogus", "GET", "/v1/auth/verify-email", params={"token": "khong-co"})
    await c("verify admin", "GET", "/v1/auth/verify-email", params={"token": side.verify[-1]})
    await c("verify twice", "GET", "/v1/auth/verify-email", params={"token": side.verify[-1]})

    la = await c("login admin", "POST", "/v1/auth/login",
                 json={"username": "admin_one", "password": "mat-khau-12345"}, headers=ip(8))
    ah = await bearer(la)
    await c("me", "GET", "/v1/auth/me", headers=ah)
    await c("me no token", "GET", "/v1/auth/me")
    await c("me garbage", "GET", "/v1/auth/me", headers={"Authorization": "Bearer rac"})
    await c("refresh", "POST", "/v1/auth/refresh", json={"refresh_token": la.json()["refresh_token"]})
    await c("refresh bogus", "POST", "/v1/auth/refresh", json={"refresh_token": "khong-co"})
    await c("login wrong pw", "POST", "/v1/auth/login",
            json={"username": "admin_one", "password": "sai-mat-khau-1"}, headers=ip(9))
    await c("login unknown", "POST", "/v1/auth/login",
            json={"username": "khong_ton_tai", "password": "sai-mat-khau-1"}, headers=ip(9))

    await c("register viewer", "POST", "/v1/auth/register", **reg("nguoi_xem", vkey, 10))
    await c("viewer verify", "GET", "/v1/auth/verify-email", params={"token": side.verify[-1]})
    lv = await c("login viewer", "POST", "/v1/auth/login",
                 json={"username": "nguoi_xem", "password": "mat-khau-12345"}, headers=ip(11))
    vh = await bearer(lv)
    await c("fallback tool viewer", "POST", "/v1/auth/login", headers={**ip(12), "X-Client-Type": "local_fallback_tool"},
            json={"username": "nguoi_xem", "password": "mat-khau-12345"})
    await c("fallback tool admin", "POST", "/v1/auth/login", headers={**ip(12), "X-Client-Type": "local_fallback_tool"},
            json={"username": "admin_one", "password": "mat-khau-12345"})

    await c("profile none", "PATCH", "/v1/auth/profile", headers=vh, json={})
    await c("profile dup username", "PATCH", "/v1/auth/profile", headers=vh, json={"username": "admin_one"})
    await c("profile dup email", "PATCH", "/v1/auth/profile", headers=vh, json={"email": "Admin_One@example.com"})
    await c("profile update", "PATCH", "/v1/auth/profile", headers=vh,
            json={"username": "nguoi_xem_moi", "email": "Moi@Example.com"})
    await c("me after profile", "GET", "/v1/auth/me", headers=vh)

    await c("forgot unknown", "POST", "/v1/auth/forgot-password", json={"email": "ai.do@example.com"}, headers=ip(13))
    await c("forgot known", "POST", "/v1/auth/forgot-password", json={"email": "moi@example.com"}, headers=ip(13))
    await c("reset bogus", "POST", "/v1/auth/reset-password",
            json={"token": "khong-co", "new_password": "mat-khau-moi-999"}, headers=ip(14))
    await c("reset ok", "POST", "/v1/auth/reset-password",
            json={"token": side.reset[-1], "new_password": "mat-khau-moi-999"}, headers=ip(14))
    await c("reset twice", "POST", "/v1/auth/reset-password",
            json={"token": side.reset[-1], "new_password": "mat-khau-khac-1"}, headers=ip(14))
    await c("old pw fails", "POST", "/v1/auth/login",
            json={"username": "nguoi_xem_moi", "password": "mat-khau-12345"}, headers=ip(15))
    lv2 = await c("new pw works", "POST", "/v1/auth/login",
                  json={"username": "nguoi_xem_moi", "password": "mat-khau-moi-999"}, headers=ip(15))
    vh2 = await bearer(lv2)
    await c("refresh revoked by reset", "POST", "/v1/auth/refresh",
            json={"refresh_token": lv.json()["refresh_token"]})

    await c("send-code no auth", "POST", "/v1/auth/change-password/send-code")
    await c("change without code", "POST", "/v1/auth/change-password", headers=vh2,
            json={"code": "123456", "new_password": "mat-khau-cuoi-1"})
    await c("send-code", "POST", "/v1/auth/change-password/send-code", headers=vh2, )
    real = side.codes[-1]
    wrong = "000000" if real != "000000" else "111111"
    await c("code wrong", "POST", "/v1/auth/change-password", headers=vh2,
            json={"code": wrong, "new_password": "mat-khau-cuoi-1"})
    await c("code bad format", "POST", "/v1/auth/change-password", headers=vh2,
            json={"code": "12ab", "new_password": "mat-khau-cuoi-1"})
    await c("code ok", "POST", "/v1/auth/change-password", headers=vh2,
            json={"code": real, "new_password": "mat-khau-cuoi-1"})
    await c("login after change", "POST", "/v1/auth/login",
            json={"username": "nguoi_xem_moi", "password": "mat-khau-cuoi-1"}, headers=ip(16))

    await c("logout", "POST", "/v1/auth/logout", json={"refresh_token": lv2.json()["refresh_token"]})
    await c("logout bogus", "POST", "/v1/auth/logout", json={"refresh_token": "khong-co"})
    await c("refresh after logout", "POST", "/v1/auth/refresh", json={"refresh_token": lv2.json()["refresh_token"]})

    # suspended
    await side.set_status("admin_one", "suspended")
    await c("login suspended", "POST", "/v1/auth/login",
            json={"username": "admin_one", "password": "mat-khau-12345"}, headers=ip(17))
    await c("me suspended", "GET", "/v1/auth/me", headers=ah)
    await c("refresh suspended", "POST", "/v1/auth/refresh", json={"refresh_token": la.json()["refresh_token"]})
    await c("forgot suspended", "POST", "/v1/auth/forgot-password", json={"email": "admin_one@example.com"}, headers=ip(18))

    # đầu vào sai
    for label, body in [("short user", {"username": "ab"}), ("bad chars", {"username": "co dau cach"}),
                        ("short pw", {"password": "ngan"}), ("bad email", {"email": "khong-phai-email"})]:
        await c(f"422 {label}", "POST", "/v1/auth/register", headers=ip(19), json={
            "key": vkey, "username": "hop_le", "password": "mat-khau-12345", "email": "ok@example.com", **body})
    await c("422 verify no token", "GET", "/v1/auth/verify-email")
    return rec.log


async def scenario_admin(side: Side) -> list:
    rec = Recorder(side)
    c = rec.call
    akey, dkey, vkey = (await side.make_key("admin", 5), await side.make_key("dash_admin", 5),
                        await side.make_key("viewer", 9))

    async def user(name, key, n):
        await side.client.post("/v1/auth/register", headers=ip(n), json={
            "key": key, "username": name, "password": "mat-khau-12345", "email": f"{name}@example.com"})
        await side.client.get("/v1/auth/verify-email", params={"token": side.verify[-1]})
        r = await side.client.post("/v1/auth/login", headers=ip(n),
                                   json={"username": name, "password": "mat-khau-12345"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]["id"]

    ah, aid = await user("quan_tri", akey, 40)
    dh, did = await user("dash_tri", dkey, 41)
    vh, vid = await user("nguoi_thuong", vkey, 42)

    for label, h in [("anon", {}), ("viewer", vh)]:
        await c(f"keys list {label}", "GET", "/v1/admin/keys", headers=h)
        await c(f"users list {label}", "GET", "/v1/admin/users", headers=h)
        await c(f"key create {label}", "POST", "/v1/admin/keys", headers=h, json={})

    k = await c("key create", "POST", "/v1/admin/keys", headers=ah,
                json={"role": "viewer", "label": "Team BR", "max_uses": 3, "expires_days": 30})
    await c("key create defaults", "POST", "/v1/admin/keys", headers=ah, json={})
    await c("key create dash->admin", "POST", "/v1/admin/keys", headers=dh, json={"role": "admin"})
    await c("key create dash->viewer", "POST", "/v1/admin/keys", headers=dh, json={"role": "viewer"})
    for label, body in [("role", {"role": "root"}), ("max0", {"max_uses": 0}), ("days0", {"expires_days": 0})]:
        await c(f"key 422 {label}", "POST", "/v1/admin/keys", headers=ah, json=body)
    await c("keys list", "GET", "/v1/admin/keys", headers=ah)
    await c("key revoke", "DELETE", f"/v1/admin/keys/{k.json()['key']}", headers=ah)
    await c("key revoke 404", "DELETE", "/v1/admin/keys/KHONG-TON", headers=ah)

    await c("users list", "GET", "/v1/admin/users", headers=ah)
    await c("suspend self", "PATCH", f"/v1/admin/users/{aid}/status", headers=ah, json={"status": "suspended"})
    await c("suspend bad value", "PATCH", f"/v1/admin/users/{vid}/status", headers=ah, json={"status": "pending"})
    await c("suspend 404", "PATCH", "/v1/admin/users/khong-co/status", headers=ah, json={"status": "active"})
    await c("suspend viewer", "PATCH", f"/v1/admin/users/{vid}/status", headers=ah, json={"status": "suspended"})
    await c("suspended viewer me", "GET", "/v1/auth/me", headers=vh)
    await c("unsuspend viewer", "PATCH", f"/v1/admin/users/{vid}/status", headers=ah, json={"status": "active"})

    await c("update dup username", "PATCH", f"/v1/admin/users/{vid}", headers=ah, json={"username": "dash_tri"})
    await c("update dup email", "PATCH", f"/v1/admin/users/{vid}", headers=ah, json={"email": "DASH_TRI@example.com"})
    await c("update dash->admin", "PATCH", f"/v1/admin/users/{vid}", headers=dh, json={"role": "admin"})
    await c("update self demote", "PATCH", f"/v1/admin/users/{aid}", headers=ah, json={"role": "viewer"})
    await c("update role discord 422", "PATCH", f"/v1/admin/users/{vid}", headers=ah, json={"role": "discord"})
    await c("update ok", "PATCH", f"/v1/admin/users/{vid}", headers=ah,
            json={"username": "da_doi_ten", "email": "Da.Doi@Example.com", "role": "dash_admin"})
    await c("update none", "PATCH", f"/v1/admin/users/{vid}", headers=ah, json={})
    await c("update 404", "PATCH", "/v1/admin/users/khong-co", headers=ah, json={"role": "viewer"})
    await c("delete self", "DELETE", f"/v1/admin/users/{aid}", headers=ah)
    await c("delete user", "DELETE", f"/v1/admin/users/{did}", headers=ah)
    await c("delete 404", "DELETE", f"/v1/admin/users/{did}", headers=ah)
    await c("users after", "GET", "/v1/admin/users", headers=ah)
    return rec.log


async def scenario_rate_limits(side: Side) -> list:
    rec = Recorder(side)
    for i in range(7):
        await rec.call(f"login {i}", "POST", "/v1/auth/login", headers=ip(60),
                       json={"username": "khong_co", "password": "sai-mat-khau-1"})
    for i in range(12):
        await rec.call(f"validate {i}", "POST", "/v1/auth/validate-key", headers=ip(61), json={"key": "x"})
    for i in range(5):
        await rec.call(f"register {i}", "POST", "/v1/auth/register", headers=ip(62), json={
            "key": "NOPE-NOPE", "username": "abcde", "password": "mat-khau-12345", "email": "a@example.com"})
    for i in range(4):
        await rec.call(f"forgot {i}", "POST", "/v1/auth/forgot-password", headers=ip(63),
                       json={"email": "ai.do@example.com"})
    for i in range(7):
        await rec.call(f"resend {i}", "POST", "/v1/auth/resend-verify", headers=ip(64),
                       json={"email": "ai.do@example.com"})
    return rec.log


def diff(a: list, b: list) -> list[str]:
    out = []
    for x, y in zip(a, b):
        if x != y:
            out.append(f"[{x[0]}]\n   legacy  : {x[1]} {x[2]}\n   identity: {y[1]} {y[2]}")
    if len(a) != len(b):
        out.append(f"độ dài khác nhau: {len(a)} vs {len(b)}")
    return out


# ── Test ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scenario", [scenario_auth, scenario_admin, scenario_rate_limits],
                         ids=lambda f: f.__name__)
async def test_same_scenario_gives_same_responses(scenario, legacy, identity):
    old = await scenario(legacy)
    new = await scenario(identity)
    assert len(old) > 5
    problems = diff(old, new)
    assert not problems, "identity-service khác legacy ở:\n" + "\n".join(problems)


async def test_scenarios_actually_exercise_error_and_success_paths(legacy):
    """Chống 'parity rỗng': kịch bản phải thật sự đi qua cả thành công lẫn lỗi (kiểm trên legacy)."""
    log = await scenario_auth(legacy)
    codes = {status for _, status, _ in log}
    assert {200, 400, 401, 403, 409, 422} <= codes
    assert sum(1 for _, s, _ in log if s == 200) >= 25


async def test_rate_limit_scenario_really_hits_429(identity):
    log = await scenario_rate_limits(identity)
    # login 7/5 -> 2, validate 12/10 -> 2, register 5/3 -> 2, forgot 4/3 -> 1, resend 7/5 -> 2
    assert sum(1 for _, s, _ in log if s == 429) == 9


# ── Dùng chéo token và hash giữa hai bên (điều kiện để chuyển dần không đăng xuất ai) ──

async def test_tokens_issued_by_each_side_are_accepted_by_the_other(legacy, identity):
    old_key, new_key = await legacy.make_key("admin", 1), await identity.make_key("admin", 1)
    tokens = {}
    for side, key in ((legacy, old_key), (identity, new_key)):
        await side.client.post("/v1/auth/register", headers=ip(80), json={
            "key": key, "username": "admin_x", "password": "mat-khau-12345", "email": "admin_x@example.com"})
        await side.client.get("/v1/auth/verify-email", params={"token": side.verify[-1]})
        r = await side.client.post("/v1/auth/login", headers=ip(81),
                                   json={"username": "admin_x", "password": "mat-khau-12345"})
        tokens[side.name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # token do identity cấp -> legacy chấp nhận (admin API chỉ giải JWT); và ngược lại
    assert (await legacy.client.get("/v1/admin/keys", headers=tokens["identity"])).status_code == 200
    assert (await identity.client.get("/v1/admin/keys", headers=tokens["legacy"])).status_code == 200


async def test_legacy_account_row_can_be_migrated_and_logs_in_on_identity(legacy, identity):
    """Mô phỏng migrate một tài khoản: giữ nguyên id + bcrypt hash; token legacy cũ vẫn dùng được."""
    key = await legacy.make_key("viewer", 1)
    await legacy.client.post("/v1/auth/register", headers=ip(90), json={
        "key": key, "username": "nguoi_cu", "password": "mat-khau-cu-123", "email": "nguoi_cu@example.com"})
    await legacy.client.get("/v1/auth/verify-email", params={"token": legacy.verify[-1]})
    old_login = await legacy.client.post("/v1/auth/login", headers=ip(91),
                                         json={"username": "nguoi_cu", "password": "mat-khau-cu-123"})
    old_access = old_login.json()["access_token"]
    row = await legacy.account("nguoi_cu")

    async with identity.db.connection() as conn:
        await conn.execute(
            "INSERT INTO accounts (id, username, email, password_hash, role, invite_key, email_verified, "
            "created_at, status) VALUES ($1,$2,$3,$4,$5,$6,true,$7,'active')",
            row["id"], row["username"], row["email"], row["password_hash"], row["role"], row["invite_key"],
            datetime.fromisoformat(row["created_at"]).astimezone(timezone.utc))

    r = await identity.client.post("/v1/auth/login", headers=ip(92),
                                   json={"username": "nguoi_cu", "password": "mat-khau-cu-123"})
    assert r.status_code == 200 and r.json()["user"]["id"] == row["id"]
    # access token do legacy cấp (sub = id cũ) dùng được ngay trên identity-service
    me = await identity.client.get("/v1/auth/me", headers={"Authorization": f"Bearer {old_access}"})
    assert me.status_code == 200 and me.json()["username"] == "nguoi_cu"
