"""HTML tối giản, an toàn cho trang đăng nhập/consent/lỗi của IdP.

* Mọi giá trị động đều qua `html.escape(quote=True)`; không nhúng giá trị vào script/style/thuộc tính URL.
* Header chống clickjacking (`X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`), không cache, no-referrer.
* CSP chặt: không script; style dùng nonce theo từng phản hồi.
"""

from __future__ import annotations

import secrets
from html import escape

from fastapi.responses import HTMLResponse, Response

_CSS = ("body{font:16px system-ui,sans-serif;background:#f4f5f7;margin:0;display:grid;place-items:center;"
        "min-height:100vh}main{background:#fff;padding:2rem;border-radius:8px;max-width:26rem;width:90%;"
        "box-shadow:0 1px 6px rgba(0,0,0,.15)}h1{font-size:1.25rem;margin:0 0 1rem}label{display:block;"
        "margin:.75rem 0 .25rem}input[type=text],input[type=password]{width:100%;box-sizing:border-box;"
        "padding:.5rem}button{margin-top:1rem;padding:.5rem 1rem;cursor:pointer}.err{color:#b00020;margin:.5rem 0}"
        ".muted{color:#555;font-size:.9rem}")


def secure_headers(response: Response, nonce: str | None = None) -> Response:
    """Header bảo mật cho MỌI phản hồi của IdP có thể bị trình duyệt hiển thị."""
    style = f"style-src 'nonce-{nonce}'" if nonce else "style-src 'none'"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; {style}; frame-ancestors 'none'; base-uri 'none'")
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def page(title: str, body_html: str, status: int = 200) -> HTMLResponse:
    """`body_html` chỉ được ghép từ các hàm trong module này (đã escape); `title` được escape ở đây."""
    nonce = secrets.token_urlsafe(16)
    doc = (f'<!doctype html><html lang="vi"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{escape(title, quote=True)}</title><style nonce="{nonce}">{_CSS}</style></head>'
           f"<body><main><h1>{escape(title, quote=True)}</h1>{body_html}</main></body></html>")
    return secure_headers(HTMLResponse(doc, status_code=status), nonce)  # type: ignore[return-value]


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{escape(name, quote=True)}" value="{escape(value, quote=True)}">'


def error_page(message: str, status: int = 400) -> HTMLResponse:
    return page("Không thể xử lý yêu cầu", f"<p>{escape(message, quote=True)}</p>"
                '<p class="muted">Nếu bạn đến từ một ứng dụng khác, hãy quay lại và thử lại.</p>', status)


def message_page(title: str, message: str, status: int = 200) -> HTMLResponse:
    return page(title, f"<p>{escape(message, quote=True)}</p>", status)


def login_page(*, csrf: str, authz: str, error: str = "", status: int = 200, client_name: str = "") -> HTMLResponse:
    who = (f'<p class="muted">Đăng nhập để tiếp tục tới <b>{escape(client_name, quote=True)}</b>.</p>'
           if client_name else "")
    err = f'<p class="err" role="alert">{escape(error, quote=True)}</p>' if error else ""
    body = (f'{who}{err}<form method="post" action="/oauth/login" autocomplete="on">'
            f'{_hidden("csrf", csrf)}{_hidden("authz", authz)}'
            '<label for="u">Tên đăng nhập</label><input id="u" type="text" name="username" required '
            'autocomplete="username" maxlength="64">'
            '<label for="p">Mật khẩu</label><input id="p" type="password" name="password" required '
            'autocomplete="current-password" maxlength="256">'
            '<button type="submit">Đăng nhập</button></form>')
    return page("Đăng nhập", body, status)


def consent_page(*, csrf: str, authz: str, client_name: str, scopes: list[str]) -> HTMLResponse:
    items = "".join(f"<li>{escape(s, quote=True)}</li>" for s in scopes)
    body = (f"<p><b>{escape(client_name, quote=True)}</b> muốn truy cập tài khoản của bạn với các quyền:</p>"
            f"<ul>{items}</ul>"
            f'<form method="post" action="/oauth/authorize">{_hidden("csrf", csrf)}{_hidden("authz", authz)}'
            '<button type="submit" name="decision" value="allow">Cho phép</button> '
            '<button type="submit" name="decision" value="deny">Từ chối</button></form>')
    return page("Cho phép truy cập", body)


def logout_confirm_page(*, csrf: str, client_id: str, post_logout_redirect_uri: str) -> HTMLResponse:
    body = ("<p>Bạn có muốn đăng xuất khỏi tài khoản không?</p>"
            f'<form method="post" action="/oauth/logout">{_hidden("csrf", csrf)}'
            f'{_hidden("client_id", client_id)}{_hidden("post_logout_redirect_uri", post_logout_redirect_uri)}'
            '<button type="submit">Đăng xuất</button></form>')
    return page("Đăng xuất", body)
