"""Nghiệp vụ OIDC/OAuth2: authorize, đổi code (PKCE), refresh xoay vòng, userinfo, introspection, revoke, logout.

Nguyên tắc bảo mật (mỗi mục có test):
  * `redirect_uri` so khớp CHÍNH XÁC (so sánh chuỗi) với giá trị đã đăng ký; sai thì không bao giờ redirect.
  * PKCE S256 bắt buộc với public client; `plain` bị từ chối; verifier so sánh hằng-thời-gian.
  * Auth code: lưu HASH, TTL ngắn, dùng một lần; dùng lại => thu hồi mọi thứ cấp từ code đó (RFC 6749 §4.1.2).
  * Refresh token xoay vòng theo family; xuất trình lại token đã xoay => thu hồi family và phiên (phát hiện đánh cắp).
  * Bí mật (secret client, code, refresh, cookie SSO) chỉ lưu sha256; không bao giờ log.
  * Phiên OIDC dùng chung bảng `sessions` nên đăng xuất/khoá/introspect nhất quán với `/v1/auth/*`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import unquote_plus

from identity_service.accounts import repository as accounts
from identity_service.audit import repository as audit
from identity_service.auth import session_repository as sessions
from identity_service.auth.service import AuthService
from identity_service.config import Settings
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.oidc import repository as repo
from identity_service.oidc.errors import AuthzPageError, AuthzRedirectError, OAuthError
from identity_service.outbox import events
from identity_service.permissions import repository as perms_repo
from identity_service.permissions.service import effective_permissions
from identity_service.security import hash_token, utcnow
from identity_service.tokens.service import TokenService

logger = logging.getLogger("identity.oidc")

SUPPORTED_SCOPES = ("openid", "profile", "email", "offline_access")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")           # base64url(sha256) không padding
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")     # RFC 7636 §4.1
_STATE_MAX = 1024
_NONCE_MAX = 512
AUTHZ_PARAMS = ("response_type", "client_id", "redirect_uri", "scope", "state", "code_challenge",
                "code_challenge_method", "nonce", "prompt", "response_mode", "request", "request_uri")
_DUMMY_SECRET_HASH = hash_token("khong-ton-tai-" + secrets.token_hex(8))


def pkce_s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


def at_hash(access_token: str) -> str:
    """OIDC Core §3.1.3.6: nửa trái của sha256(access_token), base64url."""
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()


class AuthzRequest:
    __slots__ = ("client", "redirect_uri", "state", "scope", "code_challenge", "nonce")

    def __init__(self, client: dict, redirect_uri: str, state: str, scope: list[str], code_challenge: str,
                 nonce: str) -> None:
        self.client, self.redirect_uri, self.state = client, redirect_uri, state
        self.scope, self.code_challenge, self.nonce = scope, code_challenge, nonce


class OidcService:
    def __init__(self, db: Database, settings: Settings, auth: AuthService, tokens: TokenService, *,
                 now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.settings = settings
        self.auth = auth
        self.tokens = tokens
        self.now = now

    # ── discovery ───────────────────────────────────────────────────────────────

    def discovery(self) -> dict:
        iss = self.settings.issuer
        return {
            "issuer": iss,
            "authorization_endpoint": f"{iss}/oauth/authorize",
            "token_endpoint": f"{iss}/oauth/token",
            "userinfo_endpoint": f"{iss}/oauth/userinfo",
            "jwks_uri": f"{iss}/.well-known/jwks.json",
            "introspection_endpoint": f"{iss}/oauth/introspect",
            "revocation_endpoint": f"{iss}/oauth/revoke",
            "end_session_endpoint": f"{iss}/oauth/logout",
            "scopes_supported": list(SUPPORTED_SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
            "introspection_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "revocation_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
            "code_challenge_methods_supported": ["S256"],
            "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "nonce", "sid", "at_hash",
                                 "preferred_username", "name", "email", "email_verified", "role", "perms"],
            "claims_parameter_supported": False,
            "request_parameter_supported": False,
            "request_uri_parameter_supported": False,
            "backchannel_logout_supported": True,
            "backchannel_logout_session_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    # ── SSO (phiên đăng nhập của trang hosted) ─────────────────────────────────

    async def sso_lookup(self, cookie_value: str | None) -> dict | None:
        if not cookie_value or len(cookie_value) > 256:
            return None
        async with self.db.connection() as conn:
            return await repo.sso_get_live(conn, hash_token(cookie_value), self.now())

    async def sso_login(self, account: dict, previous_cookie: str | None) -> tuple[str, int]:
        """Tạo phiên SSO mới (luôn đổi token để chống session fixation); thu hồi phiên SSO cũ của cookie hiện tại."""
        raw = secrets.token_urlsafe(32)
        sid = uuid.uuid4().hex
        now = self.now()
        ttl = self.settings.sso_ttl_s
        async with self.db.transaction() as conn:
            if previous_cookie and len(previous_cookie) <= 256:
                old = await repo.sso_sid_by_hash(conn, hash_token(previous_cookie))
                if old:
                    await self._revoke_sso_tree(conn, old, now)
            await sessions.create(conn, sid=sid, account_id=account["id"], client_type="sso", now=now)
            await repo.sso_create(conn, token_hash=hash_token(raw), sid=sid, account_id=account["id"], now=now,
                                  expires_at=now + timedelta(seconds=ttl))
            await accounts.update_last_login(conn, account["id"], now)
            await audit.record(conn, account_id=account["id"], event_type="sso_login", now=now,
                               target_type="account", target_id=account["id"])
        return raw, ttl

    # ── client ──────────────────────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> dict | None:
        if not client_id or len(client_id) > 64:
            return None
        async with self.db.connection() as conn:
            return await repo.client_get(conn, client_id)

    # ── /oauth/authorize ────────────────────────────────────────────────────────

    async def resolve_client_redirect(self, params: dict[str, str], duplicates: set[str]) -> dict:
        """Bước ĐẦU: chỉ khi client_id và redirect_uri đáng tin mới được phép redirect lỗi về client.

        Sai => AuthzPageError (trang lỗi, KHÔNG redirect). `redirect_uri` so khớp bằng `in` trên danh sách đã đăng ký:
        không chuẩn hoá, không prefix/substring, không wildcard, phân biệt hoa-thường và dấu `/` cuối.
        """
        if "client_id" in duplicates or "redirect_uri" in duplicates:
            raise AuthzPageError("Yêu cầu không hợp lệ: tham số bị lặp.")
        client = await self.get_client(params.get("client_id", ""))
        if client is None or client["status"] != "active":
            raise AuthzPageError("Ứng dụng không tồn tại hoặc đã bị vô hiệu.")
        redirect_uri = params.get("redirect_uri", "")
        if not redirect_uri or redirect_uri not in client["redirect_uris"]:
            raise AuthzPageError("redirect_uri không khớp với địa chỉ đã đăng ký của ứng dụng.")
        return client

    def validate_request(self, client: dict, params: dict[str, str], duplicates: set[str]) -> AuthzRequest:
        """Bước SAU: lỗi được trả về client qua redirect (kèm `state` nếu có)."""
        if duplicates & set(AUTHZ_PARAMS):
            raise AuthzRedirectError("invalid_request", "tham số bị lặp")
        if params.get("request"):
            raise AuthzRedirectError("request_not_supported")
        if params.get("request_uri"):
            raise AuthzRedirectError("request_uri_not_supported")
        response_type = params.get("response_type", "")
        if not response_type:
            raise AuthzRedirectError("invalid_request", "thiếu response_type")
        if response_type != "code":
            raise AuthzRedirectError("unsupported_response_type", "chỉ hỗ trợ response_type=code")
        if params.get("response_mode", "query") != "query":
            raise AuthzRedirectError("invalid_request", "chỉ hỗ trợ response_mode=query")
        state = params.get("state", "")
        if not state:
            raise AuthzRedirectError("invalid_request", "state là bắt buộc")
        if len(state) > _STATE_MAX:
            raise AuthzRedirectError("invalid_request", "state quá dài")
        challenge = params.get("code_challenge", "")
        method = params.get("code_challenge_method", "")
        if challenge:
            if method != "S256":
                raise AuthzRedirectError("invalid_request", "code_challenge_method phải là S256 (không hỗ trợ plain)")
            if not _CHALLENGE.match(challenge):
                raise AuthzRedirectError("invalid_request", "code_challenge không hợp lệ")
        else:
            if method:
                raise AuthzRedirectError("invalid_request", "có code_challenge_method nhưng thiếu code_challenge")
            if client["type"] == "public":
                raise AuthzRedirectError("invalid_request", "public client bắt buộc dùng PKCE (code_challenge, S256)")
        scope = [s for s in params.get("scope", "").split(" ") if s]
        if len(set(scope)) != len(scope):
            raise AuthzRedirectError("invalid_scope", "scope bị lặp")
        if "openid" not in scope:
            raise AuthzRedirectError("invalid_scope", "scope phải có openid")
        if any(s not in client["allowed_scopes"] for s in scope):
            raise AuthzRedirectError("invalid_scope", "scope không được phép cho ứng dụng này")
        nonce = params.get("nonce", "")
        if len(nonce) > _NONCE_MAX:
            raise AuthzRedirectError("invalid_request", "nonce quá dài")
        return AuthzRequest(client, params["redirect_uri"], state, scope, challenge, nonce)

    async def issue_code(self, req: AuthzRequest, sso: dict) -> str:
        """Cấp authorization code cho phiên SSO đã đăng nhập. Chỉ lưu hash; sid của grant được tạo sẵn."""
        code = secrets.token_urlsafe(32)
        now = self.now()
        async with self.db.transaction() as conn:
            await repo.code_insert(
                conn, code_hash=hash_token(code), client_id=req.client["client_id"], account_id=sso["account_id"],
                sid=uuid.uuid4().hex, sso_sid=sso["sid"], family_id=uuid.uuid4().hex, redirect_uri=req.redirect_uri,
                code_challenge=req.code_challenge, nonce=req.nonce, scope=" ".join(req.scope),
                auth_time=sso["auth_time"], now=now, expires_at=now + timedelta(seconds=self.settings.auth_code_ttl_s))
        return code

    # ── xác thực client (token / introspect / revoke) ──────────────────────────

    async def authenticate_client(self, authorization: str, form: dict[str, str], *,
                                  require_confidential: bool = False) -> dict:
        basic_id = basic_secret = None
        used_basic = False
        if authorization:
            scheme, _, cred = authorization.partition(" ")
            if scheme.lower() != "basic":
                raise OAuthError("invalid_client", "phương thức xác thực client không được hỗ trợ", 401,
                                 {"WWW-Authenticate": 'Basic realm="oauth"'})
            try:
                decoded = base64.b64decode(cred.strip(), validate=True).decode("utf-8")
                raw_id, sep, raw_secret = decoded.partition(":")
                if not sep:
                    raise ValueError
                basic_id, basic_secret = unquote_plus(raw_id), unquote_plus(raw_secret)
            except (binascii.Error, ValueError, UnicodeDecodeError):
                raise OAuthError("invalid_client", "Authorization Basic sai định dạng", 401,
                                 {"WWW-Authenticate": 'Basic realm="oauth"'}) from None
            used_basic = True
        post_id, post_secret = form.get("client_id"), form.get("client_secret")
        if used_basic and post_secret is not None:
            raise OAuthError("invalid_request", "chỉ dùng một phương thức xác thực client")
        if used_basic and post_id is not None and post_id != basic_id:
            raise OAuthError("invalid_request", "client_id không khớp")
        client_id = basic_id if used_basic else post_id
        secret = basic_secret if used_basic else post_secret
        headers = {"WWW-Authenticate": 'Basic realm="oauth"'} if used_basic else {}
        if not client_id:
            raise OAuthError("invalid_client", "thiếu client_id", 401, headers)
        client = await self.get_client(client_id)
        if client is None or client["status"] != "active":
            hmac.compare_digest(hash_token(secret or ""), _DUMMY_SECRET_HASH)  # thời gian ổn định
            raise OAuthError("invalid_client", "xác thực client thất bại", 401, headers)
        if client["type"] == "public":
            if secret is not None or require_confidential:
                raise OAuthError("invalid_client", "xác thực client thất bại", 401, headers)
            return client
        if not secret or not hmac.compare_digest(hash_token(secret), client["secret_hash"]):
            raise OAuthError("invalid_client", "xác thực client thất bại", 401, headers)
        return client

    # ── /oauth/token ────────────────────────────────────────────────────────────

    async def token(self, client: dict, form: dict[str, str]) -> dict:
        grant = form.get("grant_type", "")
        if grant == "authorization_code":
            return await self._grant_code(client, form)
        if grant == "refresh_token":
            return await self._grant_refresh(client, form)
        if not grant:
            raise OAuthError("invalid_request", "thiếu grant_type")
        raise OAuthError("unsupported_grant_type", "chỉ hỗ trợ authorization_code và refresh_token")

    async def _issue_tokens(self, conn, client: dict, account: dict, *, sid: str, scope: str, auth_time: datetime,
                            nonce: str, family_id: str, with_id_token: bool, with_refresh: bool) -> dict:
        now = self.now()
        perms = effective_permissions(account["role"], await perms_repo.granted(conn, account["id"]))
        # Client first-party: aud = [nền tảng, audience client] (dùng được cho API nền tảng).
        # Client bên thứ ba: CHỈ audience riêng (mặc định client_id); audience trùng nền tảng bị bỏ qua.
        first_party = bool(client["first_party"])
        if first_party:
            audiences = [client["audience"]]
        else:
            own = client["audience"]
            audiences = [own if own and own != self.settings.audience else client["client_id"]]
        access = await self.tokens.issue(
            account=account, perms=perms, sid=sid, client_type="web", audiences=audiences,
            platform_audience=first_party, extra={"client_id": client["client_id"], "scope": scope})
        out = {"access_token": access, "token_type": "Bearer", "expires_in": self.settings.access_token_ttl_s,
               "scope": scope}
        scopes = scope.split(" ")
        if with_id_token:
            issued = int(time.time())
            claims = {"iss": self.settings.issuer, "sub": account["id"], "aud": client["client_id"],
                      "azp": client["client_id"], "iat": issued, "exp": issued + self.settings.access_token_ttl_s,
                      "auth_time": int(auth_time.timestamp()), "sid": sid, "at_hash": at_hash(access)}
            if nonce:
                claims["nonce"] = nonce
            if "profile" in scopes:
                claims["preferred_username"] = account["username"]
            if "email" in scopes:
                claims["email"] = account["email"]
                claims["email_verified"] = bool(account["email_verified"])
            out["id_token"] = await self.tokens.sign(claims, typ="JWT")
        if with_refresh:
            raw = secrets.token_urlsafe(32)
            await repo.refresh_insert(
                conn, token_hash=hash_token(raw), family_id=family_id, client_id=client["client_id"],
                account_id=account["id"], sid=sid, scope=scope, auth_time=auth_time, now=now,
                expires_at=now + timedelta(days=self.settings.refresh_token_ttl_days))
            out["refresh_token"] = raw
        return out

    async def _revoke_session(self, conn, sid: str, account_id: str, now: datetime) -> None:
        """Thu hồi phiên + refresh token của phiên; phát `session.revoked` (đúng một lần) để back-channel chạy."""
        await repo.refresh_revoke_sid(conn, sid, now)
        if await sessions.revoke_if_active(conn, sid, now):
            await events.session_revoked(conn, account_id, sid)

    async def _revoke_grant(self, conn, family_id: str, sid: str, account_id: str, now: datetime) -> None:
        await repo.refresh_revoke_family(conn, family_id, now)
        await self._revoke_session(conn, sid, account_id, now)

    async def _revoke_sso_tree(self, conn, sso_sid: str, now: datetime) -> None:
        """Thu hồi phiên SSO và MỌI phiên client dựng trên nó (đăng xuất một lần, báo back-channel cho từng RP)."""
        account_id = await conn.fetchval("SELECT account_id FROM sessions WHERE sid = $1", sso_sid)
        for sid in await repo.sids_under_sso(conn, sso_sid):
            owner = await conn.fetchval("SELECT account_id FROM oidc_session_clients WHERE sid = $1 LIMIT 1", sid)
            await self._revoke_session(conn, sid, owner or account_id or "", now)
        if account_id:
            await self._revoke_session(conn, sso_sid, account_id, now)

    async def _grant_code(self, client: dict, form: dict[str, str]) -> dict:
        code, redirect_uri = form.get("code", ""), form.get("redirect_uri", "")
        if not code:
            raise OAuthError("invalid_request", "thiếu code")
        if not redirect_uri:
            raise OAuthError("invalid_request", "thiếu redirect_uri")
        now = self.now()
        failure: tuple[str, str] | None = None
        result: dict | None = None
        async with self.db.transaction() as conn:
            row = await repo.code_lock(conn, hash_token(code))
            if row is None:
                raise OAuthError("invalid_grant", "authorization code không hợp lệ")
            if row["used_at"] is not None:
                # RFC 6749 §4.1.2: dùng lại code => thu hồi mọi token/phiên đã cấp từ code này.
                await self._revoke_grant(conn, row["family_id"], row["sid"], row["account_id"], now)
                failure = ("invalid_grant", "authorization code đã được sử dụng")
                logger.warning("Auth code bị dùng lại: client=%s -> thu hồi family", row["client_id"])
            else:
                await repo.code_mark_used(conn, hash_token(code), now)  # đốt code ở MỌI nhánh sau đây
                failure = self._check_code(row, client, redirect_uri, form.get("code_verifier"), now)
                if failure is None:
                    account = await accounts.get_by_id(conn, row["account_id"])
                    if not account or account["status"] != "active":
                        failure = ("invalid_grant", "tài khoản không khả dụng")
                    else:
                        scope = row["scope"]
                        await sessions.create(conn, sid=row["sid"], account_id=account["id"], client_type="oidc",
                                              now=now)
                        await repo.session_client_add(conn, sid=row["sid"], client_id=client["client_id"],
                                                      account_id=account["id"], sso_sid=row["sso_sid"], now=now)
                        result = await self._issue_tokens(
                            conn, client, account, sid=row["sid"], scope=scope, auth_time=row["auth_time"],
                            nonce=row["nonce"], family_id=row["family_id"], with_id_token="openid" in scope.split(" "),
                            with_refresh="offline_access" in scope.split(" "))
        if failure:
            raise OAuthError(*failure)
        return result  # type: ignore[return-value]

    @staticmethod
    def _check_code(row: dict, client: dict, redirect_uri: str, verifier: str | None,
                    now: datetime) -> tuple[str, str] | None:
        if row["client_id"] != client["client_id"]:
            return ("invalid_grant", "code không được cấp cho client này")
        if row["expires_at"] <= now:
            return ("invalid_grant", "authorization code đã hết hạn")
        if redirect_uri != row["redirect_uri"]:
            return ("invalid_grant", "redirect_uri không khớp với yêu cầu authorize")
        challenge = row["code_challenge"]
        if challenge:
            if not verifier or not _VERIFIER.match(verifier):
                return ("invalid_grant", "code_verifier thiếu hoặc không hợp lệ")
            if not hmac.compare_digest(pkce_s256(verifier), challenge):
                return ("invalid_grant", "code_verifier không khớp code_challenge")
        else:
            if client["type"] == "public":
                return ("invalid_grant", "public client bắt buộc PKCE")
            if verifier:
                return ("invalid_grant", "yêu cầu authorize không dùng PKCE nhưng có code_verifier")
        return None

    async def _grant_refresh(self, client: dict, form: dict[str, str]) -> dict:
        raw = form.get("refresh_token", "")
        if not raw:
            raise OAuthError("invalid_request", "thiếu refresh_token")
        now = self.now()
        failure: tuple[str, str] | None = None
        result: dict | None = None
        async with self.db.transaction() as conn:
            row = await repo.refresh_lock(conn, hash_token(raw))
            if row is None:
                raise OAuthError("invalid_grant", "refresh token không hợp lệ")
            if row["client_id"] != client["client_id"]:
                await self._revoke_grant(conn, row["family_id"], row["sid"], row["account_id"], now)
                failure = ("invalid_grant", "refresh token không được cấp cho client này")
            elif row["used_at"] is not None or row["revoked_at"] is not None:
                # Token đã xoay (hoặc đã bị thu hồi) mà vẫn được xuất trình => nghi bị đánh cắp: giết cả family.
                await self._revoke_grant(conn, row["family_id"], row["sid"], row["account_id"], now)
                failure = ("invalid_grant", "refresh token đã được sử dụng hoặc bị thu hồi")
                if row["used_at"] is not None:
                    logger.warning("Refresh token bị dùng lại: client=%s -> thu hồi family", row["client_id"])
            elif row["expires_at"] <= now:
                failure = ("invalid_grant", "refresh token đã hết hạn")
            else:
                account = await accounts.get_by_id(conn, row["account_id"])
                if (not account or account["status"] != "active"
                        or not await sessions.is_active(conn, row["sid"])):
                    await self._revoke_grant(conn, row["family_id"], row["sid"], row["account_id"], now)
                    failure = ("invalid_grant", "phiên hoặc tài khoản không còn hiệu lực")
                else:
                    granted = row["scope"].split(" ")
                    requested = [s for s in form.get("scope", "").split(" ") if s] or granted
                    if any(s not in granted for s in requested):
                        failure = ("invalid_scope", "không được mở rộng scope khi refresh")
                    else:
                        await repo.refresh_mark_used(conn, hash_token(raw), now)
                        scope = " ".join(requested)
                        result = await self._issue_tokens(
                            conn, client, account, sid=row["sid"], scope=scope, auth_time=row["auth_time"],
                            nonce="", family_id=row["family_id"], with_id_token=False,
                            with_refresh="offline_access" in granted)
        if failure:
            raise OAuthError(*failure)
        return result  # type: ignore[return-value]

    # ── /oauth/userinfo ─────────────────────────────────────────────────────────

    async def _live_access_claims(self, token: str) -> tuple[dict, dict]:
        """(claims, account) của access token CÒN SỐNG; ném DomainError(401) nếu không."""
        payload = await self.auth.verify_access_token(token, any_audience=True)  # token client bên thứ ba
        async with self.db.connection() as conn:
            account = await accounts.get_by_id(conn, payload["sub"])
        if not account or account["status"] != "active":
            raise DomainError(401, "Tài khoản không khả dụng")
        return payload, account

    async def userinfo(self, token: str) -> dict:
        try:
            payload, account = await self._live_access_claims(token)
        except DomainError:
            raise OAuthError("invalid_token", "access token không hợp lệ hoặc đã bị thu hồi", 401,
                             {"WWW-Authenticate": 'Bearer error="invalid_token"'}) from None
        scopes = str(payload.get("scope", "")).split(" ")
        if "openid" not in scopes:
            raise OAuthError("insufficient_scope", "cần scope openid", 403,
                             {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="openid"'})
        async with self.db.connection() as conn:
            perms = effective_permissions(account["role"], await perms_repo.granted(conn, account["id"]))
        info: dict = {"sub": account["id"], "role": account["role"], "perms": perms}
        if "profile" in scopes:
            info["preferred_username"] = account["username"]
            info["name"] = account["username"]
        if "email" in scopes:
            info["email"] = account["email"]
            info["email_verified"] = bool(account["email_verified"])
        return info

    # ── /oauth/introspect (RFC 7662) ───────────────────────────────────────────

    async def introspect(self, client: dict, token: str, hint: str) -> dict:
        inactive = {"active": False}
        if not token or len(token) > 8192:
            return inactive
        if hint == "refresh_token" or token.count(".") != 2:
            return await self._introspect_refresh(client, token)
        try:
            payload, account = await self._live_access_claims(token)
        except DomainError:
            return inactive
        if payload.get("role") != account["role"]:
            return inactive  # đổi role sau khi cấp => token mang quyền cũ không còn hợp lệ
        audiences = payload.get("aud") or []
        audiences = [audiences] if isinstance(audiences, str) else audiences
        # Client chỉ được hỏi về token của chính nó (cấp cho nó, hoặc `aud` là audience của nó).
        if payload.get("client_id") != client["client_id"] and not (client["audience"] and client["audience"] in audiences):
            return inactive
        out = {"active": True, "token_type": "Bearer", "iss": payload["iss"], "sub": payload["sub"], "aud": audiences,
               "exp": payload["exp"], "iat": payload["iat"], "nbf": payload.get("nbf"), "jti": payload.get("jti"),
               "sid": payload.get("sid"), "username": account["username"], "role": account["role"],
               "perms": payload.get("perms", []), "scope": payload.get("scope", ""),
               "client_id": payload.get("client_id", "")}
        return {k: v for k, v in out.items() if v is not None}

    async def _introspect_refresh(self, client: dict, token: str) -> dict:
        now = self.now()
        async with self.db.connection() as conn:
            row = await repo.refresh_get(conn, hash_token(token))
            if (row is None or row["client_id"] != client["client_id"] or row["used_at"] is not None
                    or row["revoked_at"] is not None or row["expires_at"] <= now):
                return {"active": False}
            account = await accounts.get_by_id(conn, row["account_id"])
            if not account or account["status"] != "active" or not await sessions.is_active(conn, row["sid"]):
                return {"active": False}
        return {"active": True, "token_type": "refresh_token", "client_id": row["client_id"], "sub": account["id"],
                "scope": row["scope"], "sid": row["sid"], "exp": int(row["expires_at"].timestamp()),
                "iat": int(row["created_at"].timestamp())}

    # ── /oauth/revoke (RFC 7009) ───────────────────────────────────────────────

    async def revoke(self, client: dict, token: str, hint: str) -> None:
        """Luôn thành công (kể cả token lạ). Token của client khác bị bỏ qua. Thu hồi một token = thu hồi cả grant
        (refresh family + phiên), vì access token gắn với phiên `sid`."""
        if not token or len(token) > 8192:
            return
        now = self.now()
        async with self.db.transaction() as conn:
            row = await repo.refresh_get(conn, hash_token(token)) if hint != "access_token" else None
            if row is not None:
                if row["client_id"] == client["client_id"]:
                    await self._revoke_grant(conn, row["family_id"], row["sid"], row["account_id"], now)
                return
        if token.count(".") != 2:
            return
        try:
            payload = await self.tokens.verify(token, any_audience=True)
        except DomainError:
            return
        if payload.get("client_id") != client["client_id"] or not payload.get("sid"):
            return
        async with self.db.transaction() as conn:
            row = await conn.fetchrow("SELECT family_id FROM oidc_refresh_tokens WHERE sid = $1 LIMIT 1", payload["sid"])
            if row:
                await repo.refresh_revoke_family(conn, row["family_id"], now)
            await self._revoke_session(conn, payload["sid"], payload["sub"], now)

    # ── /oauth/logout (RP-initiated, OIDC RP-Initiated Logout 1.0) ─────────────

    async def logout(self, *, id_token_hint: str, client_id: str, post_logout_redirect_uri: str,
                     sso_cookie: str | None, confirmed: bool) -> dict:
        """Trả `{"needs_confirm": bool, "redirect": str|None, "client": dict|None}`.

        Có `id_token_hint` hợp lệ (chữ ký, iss, chưa cần còn hạn) => tự xác thực yêu cầu, đăng xuất ngay.
        Không có hint => cần người dùng xác nhận (form + CSRF) vì GET đăng xuất là vector CSRF.
        `post_logout_redirect_uri` phải khớp CHÍNH XÁC giá trị đã đăng ký; không khớp thì không redirect.
        """
        now = self.now()
        hint_claims: dict | None = None
        if id_token_hint:
            try:
                hint_claims = await self.tokens.verify_own(id_token_hint, verify_exp=False)
            except DomainError:
                raise AuthzPageError("id_token_hint không hợp lệ.") from None
            aud = hint_claims.get("aud")
            hint_client = aud if isinstance(aud, str) else (aud[0] if aud else "")
            if client_id and client_id != hint_client:
                raise AuthzPageError("client_id không khớp id_token_hint.")
            client_id = hint_client
        client = await self.get_client(client_id) if client_id else None
        if id_token_hint and client is None:
            raise AuthzPageError("id_token_hint không hợp lệ.")
        redirect = None
        if post_logout_redirect_uri and client and post_logout_redirect_uri in client["post_logout_redirect_uris"]:
            redirect = post_logout_redirect_uri

        if hint_claims is None and not confirmed:
            return {"needs_confirm": True, "redirect": redirect, "client": client}

        sso = await self.sso_lookup(sso_cookie)
        async with self.db.transaction() as conn:
            if hint_claims is not None:
                sid = hint_claims.get("sid")
                row = await conn.fetchrow("SELECT sso_sid, account_id FROM oidc_session_clients WHERE sid = $1 "
                                          "AND client_id = $2", sid or "", client["client_id"]) if sid else None
                # Hint phải là của CHÍNH phiên (sub khớp) thì mới có quyền kết thúc phiên đó.
                if row is not None and row["account_id"] == hint_claims.get("sub"):
                    if row["sso_sid"]:
                        await self._revoke_sso_tree(conn, row["sso_sid"], now)
                    else:
                        await self._revoke_session(conn, sid, row["account_id"], now)
            if sso is not None and (hint_claims is None or sso["account_id"] == hint_claims.get("sub")):
                await self._revoke_sso_tree(conn, sso["sid"], now)
        return {"needs_confirm": False, "redirect": redirect, "client": client}
