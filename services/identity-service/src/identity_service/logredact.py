"""Che giá trị nhạy cảm trong log truy cập của uvicorn (query string), vd `id_token_hint`, `code`, `token`.

Access log mặc định in nguyên request-line; với OIDC có tham số mang bí mật/định danh (id_token_hint, code,
access_token, refresh_token, client_secret, token, password). Giá trị bị thay bằng `REDACTED`.
"""

from __future__ import annotations

import logging
import re

SENSITIVE_PARAMS = ("id_token_hint", "code", "access_token", "refresh_token", "client_secret", "token", "password",
                    "logout_token", "code_verifier", "authz", "csrf")
_PATTERN = re.compile(r"(?i)([?&](?:" + "|".join(SENSITIVE_PARAMS) + r")=)[^&\s#]*")


def redact(text: str) -> str:
    return _PATTERN.sub(r"\1REDACTED", text)


class RedactAccessLog(logging.Filter):
    """Gắn vào logger `uvicorn.access`: sửa tham số đường dẫn trong `record.args` (uvicorn truyền
    `(client, method, full_path, http_version, status)`) và cả `record.msg` nếu là chuỗi đã định dạng."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactAccessLog) for f in access.filters):
        access.addFilter(RedactAccessLog())
