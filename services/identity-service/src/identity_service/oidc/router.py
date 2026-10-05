"""HTTP của OIDC/OAuth2: discovery, authorize, trang đăng nhập hosted, token, userinfo, introspect, revoke, logout."""

from __future__ import annotations

import hmac
import re
import secrets
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from identity_service.deps import client_ip, get_container
from identity_service.errors import DomainError
from identity_service.oidc import pages
from identity_service.oidc.errors import AuthzPageError, AuthzRedirectError, OAuthError

router = APIRouter(tags=["oidc"])

_MAX_BODY = 32 * 1024
_MAX_AUTHZ = 4096
_CSRF = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_LOGIN_FAILED = "Tên đăng nhập hoặc mật khẩu không đúng"


# ── tiện ích ────────────────────────────────────────────────────────────────────

def _secure(request: Request) -> bool:
    return get_container(request).settings.issuer.startswith("https://")


def _cookie_names(request: Request) -> tuple[str, str]:
    """(sso, csrf). `__Host-` yêu cầu Secure + Path=/ + không Domain: chỉ dùng khi issuer là https."""
    prefix = "__Host-" if _secure(request) else ""
    return prefix + "idp_sso", prefix + "idp_csrf"


def _collapse(pairs: list[tuple[str, str]]) -> tuple[dict[str, str], set[str]]:
    params: dict[str, str] = {}
    dups: set[str] = set()
    for k, v in pairs:
        if k in params:
            dups.add(k)
        else:
            params[k] = v
    return params, dups


async def _body_pairs(request: Request) -> list[tuple[str, str]]:
    if not request.headers.get("content-type", "").lower().startswith("application/x-www-form-urlencoded"):
        raise OAuthError("invalid_request", "Content-Type phải là application/x-www-form-urlencoded")
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise OAuthError("invalid_request", "body quá lớn", 413)
    try:
        return parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=50)
    except (UnicodeDecodeError, ValueError):
        raise OAuthError("invalid_request", "body không hợp lệ") from None


async def _form(request: Request) -> tuple[dict[str, str], set[str]]:
    return _collapse(await _body_pairs(request))


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _json(data: dict, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return _no_store(JSONResponse(data, status_code=status, headers=headers))  # type: ignore[return-value]


def _oauth_error(exc: OAuthError) -> JSONResponse:
    body = {"error": exc.error}
    if exc.description:
        body["error_description"] = exc.description
    return _json(body, exc.status, exc.headers)


def _redirect(url: str, status: int = 302) -> Response:
    return pages.secure_headers(RedirectResponse(url, status_code=status))


def _with_query(base: str, params: dict[str, str]) -> str:
    return base + ("&" if "?" in base else "?") + urlencode(params)


def _new_csrf() -> str:
    return secrets.token_urlsafe(32)


def _set_csrf(request: Request, response: Response, token: str) -> Response:
    _, name = _cookie_names(request)
    response.set_cookie(name, token, max_age=3600, httponly=True, samesite="strict", secure=_secure(request), path="/")
    return response


def _csrf_ok(request: Request, submitted: str | None) -> bool:
    _, name = _cookie_names(request)
    cookie = request.cookies.get(name, "")
    return bool(submitted and cookie and _CSRF.match(cookie) and hmac.compare_digest(cookie, submitted))


def _rate_limited(exc: DomainError) -> Response:
    resp = pages.message_page("Quá nhiều lần thử", exc.detail, 429)
    resp.headers.update(exc.headers or {})
    return resp


# ── discovery ───────────────────────────────────────────────────────────────────

@router.get("/.well-known/openid-configuration", summary="OpenID Provider Configuration")
async def discovery(request: Request) -> JSONResponse:
    resp = JSONResponse(get_container(request).oidc.discovery())
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


# ── /oauth/authorize ────────────────────────────────────────────────────────────

async def _authorize(request: Request, pairs: list[tuple[str, str]], *, decision: str | None) -> Response:
    c = get_container(request)
    svc = c.oidc
    params, dups = _collapse(pairs)
    try:
        client = await svc.resolve_client_redirect(params, dups)
    except AuthzPageError as exc:
        return pages.error_page(exc.message, exc.status)  # client_id/redirect_uri sai: KHÔNG redirect
    redirect_uri = params["redirect_uri"]
    issuer = c.settings.issuer

    def fail(exc: AuthzRedirectError) -> Response:
        out = {"error": exc.error}
        if exc.description:
            out["error_description"] = exc.description
        state = params.get("state", "")
        if state and len(state) <= 1024:
            out["state"] = state
        out["iss"] = issuer
        return _redirect(_with_query(redirect_uri, out))

    try:
        req = svc.validate_request(client, params, dups)
    except AuthzRedirectError as exc:
        return fail(exc)

    sso = await svc.sso_lookup(request.cookies.get(_cookie_names(request)[0]))
    if sso is None:
        if params.get("prompt") == "none":
            return fail(AuthzRedirectError("login_required"))
        authz = urlencode(pairs)
        if len(authz) > _MAX_AUTHZ:
            return pages.error_page("Yêu cầu quá dài.")
        return _redirect("/oauth/login?" + urlencode({"authz": authz}))

    if not client["first_party"]:  # first-party bỏ qua consent; client khác phải được người dùng cho phép
        if decision is None:
            if params.get("prompt") == "none":
                return fail(AuthzRedirectError("consent_required"))
            csrf = _new_csrf()
            resp = pages.consent_page(csrf=csrf, authz=urlencode(pairs), client_name=client["name"], scopes=req.scope)
            return _set_csrf(request, resp, csrf)
        if decision != "allow":
            return fail(AuthzRedirectError("access_denied", "người dùng từ chối"))

    code = await svc.issue_code(req, sso)
    return _redirect(_with_query(redirect_uri, {"code": code, "state": req.state, "iss": issuer}))


@router.get("/oauth/authorize", summary="Authorization endpoint (code + PKCE)")
async def authorize_get(request: Request) -> Response:
    return await _authorize(request, request.query_params.multi_items(), decision=None)


@router.post("/oauth/authorize", summary="Xác nhận consent")
async def authorize_post(request: Request) -> Response:
    try:
        form, _ = await _form(request)
    except OAuthError as exc:
        return pages.error_page(exc.description or "Yêu cầu không hợp lệ.", exc.status)
    if not _csrf_ok(request, form.get("csrf")):
        return pages.error_page("Phiên biểu mẫu không hợp lệ hoặc đã hết hạn. Hãy thử lại.", 403)
    authz = form.get("authz", "")
    if not authz or len(authz) > _MAX_AUTHZ or form.get("decision") not in ("allow", "deny"):
        return pages.error_page("Yêu cầu không hợp lệ.")
    return await _authorize(request, parse_qsl(authz, keep_blank_values=True, max_num_fields=50),
                            decision=form["decision"])


# ── trang đăng nhập hosted ─────────────────────────────────────────────────────

async def _check_authz_field(request: Request, authz: str) -> tuple[bool, dict | None, Response | None]:
    """Nếu có `authz`: phải là một yêu cầu authorize có client_id/redirect_uri hợp lệ (chống dùng trang login làm
    open-redirect hay phản chiếu dữ liệu tuỳ ý)."""
    if not authz:
        return True, None, None
    if len(authz) > _MAX_AUTHZ:
        return False, None, pages.error_page("Yêu cầu quá dài.")
    params, dups = _collapse(parse_qsl(authz, keep_blank_values=True, max_num_fields=50))
    try:
        client = await get_container(request).oidc.resolve_client_redirect(params, dups)
    except AuthzPageError as exc:
        return False, None, pages.error_page(exc.message, exc.status)
    return True, client, None


@router.get("/oauth/login", summary="Trang đăng nhập hosted")
async def login_get(request: Request) -> Response:
    c = get_container(request)
    authz = request.query_params.get("authz", "")
    ok, client, err = await _check_authz_field(request, authz)
    if not ok:
        return err  # type: ignore[return-value]
    if await c.oidc.sso_lookup(request.cookies.get(_cookie_names(request)[0])) is not None:
        if authz:
            return _redirect("/oauth/authorize?" + authz)
        return pages.message_page("Đã đăng nhập", "Bạn đã đăng nhập.")
    csrf = _new_csrf()
    resp = pages.login_page(csrf=csrf, authz=authz, client_name=client["name"] if client else "")
    return _set_csrf(request, resp, csrf)


@router.post("/oauth/login", summary="Gửi form đăng nhập hosted")
async def login_post(request: Request) -> Response:
    c = get_container(request)
    try:
        form, _ = await _form(request)
    except OAuthError as exc:
        return pages.error_page(exc.description or "Yêu cầu không hợp lệ.", exc.status)
    if not _csrf_ok(request, form.get("csrf")):
        return pages.error_page("Phiên biểu mẫu không hợp lệ hoặc đã hết hạn. Hãy tải lại trang đăng nhập.", 403)
    authz = form.get("authz", "")
    ok, client, err = await _check_authz_field(request, authz)
    if not ok:
        return err  # type: ignore[return-value]
    try:
        c.limiter.check(client_ip(request), "oauth-login", 10, 300)
    except DomainError as exc:
        return _rate_limited(exc)

    def again(message: str, status: int) -> Response:
        csrf = _new_csrf()
        return _set_csrf(request, pages.login_page(csrf=csrf, authz=authz, error=message, status=status,
                                                   client_name=client["name"] if client else ""), csrf)

    username, password = form.get("username", ""), form.get("password", "")
    if not username or not password or len(username) > 64 or len(password) > 256:
        return again(_LOGIN_FAILED, 401)
    try:
        account = await c.auth.authenticate(username, password)
    except DomainError as exc:
        # 401 (sai username HOẶC mật khẩu) dùng đúng một thông điệp; trạng thái pending/suspended chỉ lộ SAU khi
        # mật khẩu đúng (như /v1/auth/login), nên không giúp dò username.
        return again(_LOGIN_FAILED if exc.status_code == 401 else exc.detail, exc.status_code)
    sso_name, _ = _cookie_names(request)
    raw, ttl = await c.oidc.sso_login(account, request.cookies.get(sso_name))
    resp = _redirect(("/oauth/authorize?" + authz) if authz else "/oauth/login", 303)
    resp.set_cookie(sso_name, raw, max_age=ttl, httponly=True, samesite="lax", secure=_secure(request), path="/")
    return resp


# ── /oauth/token ────────────────────────────────────────────────────────────────

@router.post("/oauth/token", summary="Token endpoint (authorization_code + PKCE, refresh_token)")
async def token(request: Request) -> Response:
    c = get_container(request)
    try:
        c.limiter.check(client_ip(request), "oauth-token", 120, 60)
    except DomainError as exc:
        return _json({"error": "temporarily_unavailable", "error_description": "quá nhiều yêu cầu"}, 429,
                     exc.headers)
    try:
        pairs = await _body_pairs(request)
        form, dups = _collapse(pairs)
        if dups:
            raise OAuthError("invalid_request", "tham số bị lặp")
        client = await c.oidc.authenticate_client(request.headers.get("authorization", ""), form)
        return _json(await c.oidc.token(client, form))
    except OAuthError as exc:
        return _oauth_error(exc)


# ── /oauth/userinfo ─────────────────────────────────────────────────────────────

async def _userinfo(request: Request) -> Response:
    c = get_container(request)
    try:
        auth = request.headers.get("authorization", "")
        scheme, _, token_value = auth.partition(" ")
        if scheme.lower() != "bearer" or not token_value.strip():
            raise OAuthError("invalid_token", "thiếu Bearer access token", 401, {"WWW-Authenticate": "Bearer"})
        return _json(await c.oidc.userinfo(token_value.strip()))
    except OAuthError as exc:
        return _oauth_error(exc)


@router.get("/oauth/userinfo", summary="UserInfo (Bearer access token)")
async def userinfo_get(request: Request) -> Response:
    return await _userinfo(request)


@router.post("/oauth/userinfo", summary="UserInfo (Bearer access token, POST)")
async def userinfo_post(request: Request) -> Response:
    return await _userinfo(request)


# ── /oauth/introspect, /oauth/revoke ───────────────────────────────────────────

@router.post("/oauth/introspect", summary="Token introspection (RFC 7662, bắt buộc xác thực client)")
async def introspect(request: Request) -> Response:
    c = get_container(request)
    try:
        c.limiter.check(client_ip(request), "oauth-introspect", 600, 60)
        form, dups = _collapse(await _body_pairs(request))
        if dups:
            raise OAuthError("invalid_request", "tham số bị lặp")
        client = await c.oidc.authenticate_client(request.headers.get("authorization", ""), form,
                                                  require_confidential=True)
        return _json(await c.oidc.introspect(client, form.get("token", ""), form.get("token_type_hint", "")))
    except DomainError as exc:
        return _json({"error": "temporarily_unavailable"}, 429, exc.headers)
    except OAuthError as exc:
        return _oauth_error(exc)


@router.post("/oauth/revoke", summary="Thu hồi token (RFC 7009)")
async def revoke(request: Request) -> Response:
    c = get_container(request)
    try:
        c.limiter.check(client_ip(request), "oauth-revoke", 120, 60)
        form, dups = _collapse(await _body_pairs(request))
        if dups:
            raise OAuthError("invalid_request", "tham số bị lặp")
        client = await c.oidc.authenticate_client(request.headers.get("authorization", ""), form)
        if "token" not in form:
            raise OAuthError("invalid_request", "thiếu token")
        await c.oidc.revoke(client, form["token"], form.get("token_type_hint", ""))
        return _no_store(Response(status_code=200))
    except DomainError as exc:
        return _json({"error": "temporarily_unavailable"}, 429, exc.headers)
    except OAuthError as exc:
        return _oauth_error(exc)


# ── /oauth/logout ───────────────────────────────────────────────────────────────

async def _logout(request: Request, params: dict[str, str], *, confirmed: bool) -> Response:
    c = get_container(request)
    sso_name, _ = _cookie_names(request)
    try:
        result = await c.oidc.logout(
            id_token_hint=params.get("id_token_hint", ""), client_id=params.get("client_id", ""),
            post_logout_redirect_uri=params.get("post_logout_redirect_uri", ""),
            sso_cookie=request.cookies.get(sso_name), confirmed=confirmed)
    except AuthzPageError as exc:
        return pages.error_page(exc.message, exc.status)
    if result["needs_confirm"]:
        csrf = _new_csrf()
        resp = pages.logout_confirm_page(csrf=csrf, client_id=params.get("client_id", ""),
                                         post_logout_redirect_uri=params.get("post_logout_redirect_uri", ""))
        return _set_csrf(request, resp, csrf)
    if result["redirect"]:
        target = result["redirect"]
        state = params.get("state", "")
        resp = _redirect(_with_query(target, {"state": state}) if state and len(state) <= 1024 else target)
    else:
        resp = pages.message_page("Đã đăng xuất", "Bạn đã đăng xuất.")
    resp.delete_cookie(sso_name, path="/", secure=_secure(request), httponly=True, samesite="lax")
    return resp


@router.get("/oauth/logout", summary="RP-Initiated Logout")
async def logout_get(request: Request) -> Response:
    params, dups = _collapse(request.query_params.multi_items())
    if dups:
        return pages.error_page("Yêu cầu không hợp lệ: tham số bị lặp.")
    return await _logout(request, params, confirmed=False)


@router.post("/oauth/logout", summary="Xác nhận đăng xuất (khi không có id_token_hint)")
async def logout_post(request: Request) -> Response:
    try:
        form, dups = await _form(request)
    except OAuthError as exc:
        return pages.error_page(exc.description or "Yêu cầu không hợp lệ.", exc.status)
    if dups or not _csrf_ok(request, form.get("csrf")):
        return pages.error_page("Phiên biểu mẫu không hợp lệ hoặc đã hết hạn.", 403)
    return await _logout(request, {k: form.get(k, "") for k in ("client_id", "post_logout_redirect_uri")},
                         confirmed=True)
