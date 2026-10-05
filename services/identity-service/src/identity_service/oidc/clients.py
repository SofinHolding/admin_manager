"""Quản trị OIDC client (admin/dash_admin) và seed client first-party cho DEV.

* `redirect_uris`/`post_logout_redirect_uris`/`backchannel_logout_uri`: https, hoặc http loopback ngoài production;
  cấm wildcard, fragment, userinfo, scheme lạ (xem `oidc/uri.py`).
* Secret của confidential client sinh ngẫu nhiên, CHỈ trả đúng một lần (lúc tạo hoặc xoay); DB chỉ lưu sha256.
* `first_party` (bỏ màn consent) chỉ role `admin` được đặt — dash_admin không tự tạo client bỏ qua consent.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable
from datetime import datetime

import asyncpg

from identity_service.audit import repository as audit
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.oidc import repository as repo
from identity_service.oidc.service import SUPPORTED_SCOPES
from identity_service.oidc.uri import validate_client_uri, validate_uri_list
from identity_service.security import hash_token, iso, utcnow

logger = logging.getLogger("identity.oidc.clients")

_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_SCOPE_TOKEN = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,64}$")   # RFC 6749 §3.3 (không có dấu cách, `"`, `\`)
_AUDIENCE = re.compile(r"^[A-Za-z0-9_.:/-]{0,128}$")


def client_to_dict(row: dict) -> dict:
    """Biểu diễn công khai cho admin: KHÔNG bao giờ có secret hay hash."""
    return {"client_id": row["client_id"], "name": row["name"], "type": row["type"],
            "redirect_uris": list(row["redirect_uris"]),
            "post_logout_redirect_uris": list(row["post_logout_redirect_uris"]),
            "backchannel_logout_uri": row["backchannel_logout_uri"], "allowed_scopes": list(row["allowed_scopes"]),
            "audience": row["audience"], "first_party": row["first_party"], "status": row["status"],
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}


class ClientService:
    def __init__(self, db: Database, env: str, *, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.env = env
        self.now = now

    # ── validate ────────────────────────────────────────────────────────────────

    def _scopes(self, values: object) -> list[str]:
        if not isinstance(values, list) or not values:
            raise DomainError(422, "allowed_scopes phải là danh sách không rỗng")
        out: list[str] = []
        for s in values:
            if not isinstance(s, str) or not _SCOPE_TOKEN.match(s):
                raise DomainError(422, "scope không hợp lệ")
            if s not in out:
                out.append(s)
        if "openid" not in out:
            raise DomainError(422, "allowed_scopes phải có openid")
        return out

    def _uris(self, values: object, label: str, *, min_items: int = 0) -> list[str]:
        try:
            return validate_uri_list(values, env=self.env, label=label, min_items=min_items)
        except ValueError as exc:
            raise DomainError(422, str(exc)) from None

    def _backchannel(self, value: object) -> str:
        if value in (None, ""):
            return ""
        try:
            return validate_client_uri(value, env=self.env, label="backchannel_logout_uri")
        except ValueError as exc:
            raise DomainError(422, str(exc)) from None

    @staticmethod
    def _audience(value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str) or not _AUDIENCE.match(value):
            raise DomainError(422, "audience không hợp lệ")
        return value

    @staticmethod
    def _name(value: object) -> str:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
            raise DomainError(422, "name phải dài 1..200 ký tự")
        return value.strip()

    # ── CRUD ────────────────────────────────────────────────────────────────────

    async def create(self, actor: dict, body: dict) -> dict:
        ctype = body.get("type")
        if ctype not in ("public", "confidential"):
            raise DomainError(422, "type phải là public hoặc confidential")
        client_id = body.get("client_id") or ("cid_" + secrets.token_hex(10))
        if not isinstance(client_id, str) or not _CLIENT_ID.match(client_id):
            raise DomainError(422, "client_id không hợp lệ")
        first_party = bool(body.get("first_party", False))
        if first_party and actor.get("role") != "admin":
            raise DomainError(403, "Chỉ admin mới được tạo client first-party")
        secret = secrets.token_urlsafe(32) if ctype == "confidential" else ""
        values = dict(
            client_id=client_id, name=self._name(body.get("name")), type=ctype,
            secret_hash=hash_token(secret) if secret else "",
            redirect_uris=self._uris(body.get("redirect_uris"), "redirect_uris", min_items=1),
            post_logout_redirect_uris=self._uris(body.get("post_logout_redirect_uris", []), "post_logout_redirect_uris"),
            backchannel_logout_uri=self._backchannel(body.get("backchannel_logout_uri")),
            allowed_scopes=self._scopes(body.get("allowed_scopes", ["openid", "profile", "email"])),
            audience=self._audience(body.get("audience")), first_party=first_party)
        try:
            async with self.db.transaction() as conn:
                row = await repo.client_insert(conn, now=self.now(), **values)
                await audit.record(conn, account_id=actor.get("sub", ""), event_type="oidc_client_created",
                                   now=self.now(), target_type="oidc_client", target_id=client_id,
                                   payload={"type": ctype, "first_party": first_party})
        except asyncpg.UniqueViolationError:
            raise DomainError(409, "client_id đã tồn tại") from None
        out = client_to_dict(row)
        if secret:
            out["client_secret"] = secret  # hiển thị DUY NHẤT một lần
        logger.info("Tạo OIDC client %s type=%s", client_id, ctype)
        return out

    async def list(self) -> dict:
        async with self.db.connection() as conn:
            return {"clients": [client_to_dict(r) for r in await repo.client_list(conn)]}

    async def get(self, client_id: str) -> dict:
        async with self.db.connection() as conn:
            row = await repo.client_get(conn, client_id)
        if not row:
            raise DomainError(404, "Client không tồn tại")
        return client_to_dict(row)

    async def update(self, actor: dict, client_id: str, body: dict) -> dict:
        allowed = {"name", "redirect_uris", "post_logout_redirect_uris", "backchannel_logout_uri", "allowed_scopes",
                   "audience", "first_party", "status", "rotate_secret"}
        unknown = set(body) - allowed
        if unknown:
            raise DomainError(422, "Trường không được hỗ trợ: " + ", ".join(sorted(unknown)))
        updates: dict = {}
        if "name" in body:
            updates["name"] = self._name(body["name"])
        if "redirect_uris" in body:
            updates["redirect_uris"] = self._uris(body["redirect_uris"], "redirect_uris", min_items=1)
        if "post_logout_redirect_uris" in body:
            updates["post_logout_redirect_uris"] = self._uris(body["post_logout_redirect_uris"],
                                                              "post_logout_redirect_uris")
        if "backchannel_logout_uri" in body:
            updates["backchannel_logout_uri"] = self._backchannel(body["backchannel_logout_uri"])
        if "allowed_scopes" in body:
            updates["allowed_scopes"] = self._scopes(body["allowed_scopes"])
        if "audience" in body:
            updates["audience"] = self._audience(body["audience"])
        if "first_party" in body:
            if actor.get("role") != "admin":
                raise DomainError(403, "Chỉ admin mới được đổi first_party")
            updates["first_party"] = bool(body["first_party"])
        if "status" in body:
            if body["status"] not in ("active", "disabled"):
                raise DomainError(422, "status phải là active hoặc disabled")
            updates["status"] = body["status"]
        secret = ""
        async with self.db.transaction() as conn:
            current = await repo.client_get(conn, client_id)
            if not current:
                raise DomainError(404, "Client không tồn tại")
            if body.get("rotate_secret"):
                if current["type"] != "confidential":
                    raise DomainError(422, "Chỉ confidential client có secret")
                secret = secrets.token_urlsafe(32)
                updates["secret_hash"] = hash_token(secret)
            row = await repo.client_update(conn, client_id, updates, self.now())
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="oidc_client_updated",
                               now=self.now(), target_type="oidc_client", target_id=client_id,
                               payload={"fields": sorted(k for k in updates if k != "secret_hash"),
                                        "secret_rotated": bool(secret)})
        out = client_to_dict(row)
        if secret:
            out["client_secret"] = secret
        return out

    async def delete(self, actor: dict, client_id: str) -> dict:
        from identity_service.auth import session_repository as sessions
        from identity_service.outbox import events

        now = self.now()
        async with self.db.transaction() as conn:
            if not await repo.client_get(conn, client_id):
                raise DomainError(404, "Client không tồn tại")
            # Xoá client = thu hồi mọi phiên đã cấp cho nó (token còn hạn bị từ chối ở introspect/userinfo).
            rows = await conn.fetch("SELECT sid, account_id FROM oidc_session_clients WHERE client_id = $1", client_id)
            for r in rows:
                await repo.refresh_revoke_sid(conn, r["sid"], now)
                if await sessions.revoke_if_active(conn, r["sid"], now):
                    await events.session_revoked(conn, r["account_id"], r["sid"])
            await repo.client_delete(conn, client_id)
            await audit.record(conn, account_id=actor.get("sub", ""), event_type="oidc_client_deleted", now=now,
                               target_type="oidc_client", target_id=client_id)
        return {"ok": True}

    # ── seed DEV ────────────────────────────────────────────────────────────────

    async def seed_web(self, redirect_uris: list[str], post_logout: list[str]) -> tuple[dict, bool]:
        """Tạo/cập nhật client first-party `manager-web` (public). Trả (client, created)."""
        uris = self._uris(redirect_uris, "redirect_uris", min_items=1)
        post = self._uris(post_logout, "post_logout_redirect_uris")
        async with self.db.transaction() as conn:
            existing = await repo.client_get(conn, "manager-web")
            if existing:
                row = await repo.client_update(conn, "manager-web", {"redirect_uris": uris,
                                                                     "post_logout_redirect_uris": post}, self.now())
                return client_to_dict(row), False
            row = await repo.client_insert(
                conn, client_id="manager-web", name="Manager Web", type="public", secret_hash="",
                redirect_uris=uris, post_logout_redirect_uris=post, backchannel_logout_uri="",
                allowed_scopes=list(SUPPORTED_SCOPES), audience="manager-web", first_party=True, now=self.now())
            return client_to_dict(row), True
