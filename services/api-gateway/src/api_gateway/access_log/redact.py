"""Che secret trước khi ghi log. KHÔNG BAO GIỜ log: Authorization, cookie, body, giá trị query nhạy cảm.

Gateway chỉ log: method, path (đã che segment trông như secret), query (khoá giữ nguyên, giá trị nhạy cảm bị che),
status, latency, upstream, request_id. Header và body không bao giờ được đưa vào log.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"token|code|pass|pwd|key|secret|auth|sig|session|jwt|credential|cookie|otp|bearer|sid|signature|"
    r"cred|private|nonce|state", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(?:^eyJ[\w-]{5,})|(?:\bmpk_[\w.-]+)|(?:\bBearer\s+\S+)|(?:^[A-Za-z0-9_\-]{40,}$)|(?:^[0-9a-fA-F]{32,}$)")
_SECRET_SEGMENT = re.compile(r"^(?:eyJ[\w-]+(?:\.[\w-]+)*|mpk_[\w.-]+|[A-Za-z0-9_\-]{40,})$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_VALUE = 64
_MAX_PAIRS = 20


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub("?", text)[:limit]


def redact_path(path: str) -> str:
    segments = []
    for seg in path.split("/"):
        segments.append(REDACTED if _SECRET_SEGMENT.match(seg) else _clean(seg, 128))
    return _clean("/".join(segments), 512)


def redact_query(query: bytes | str) -> str:
    if isinstance(query, bytes):
        query = query.decode("latin-1")
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=True, errors="replace", max_num_fields=_MAX_PAIRS * 2)
    except ValueError:
        return REDACTED
    parts = []
    for key, value in pairs[:_MAX_PAIRS]:
        k = _clean(key, 64)
        if _SENSITIVE_KEY.search(key) or _SECRET_VALUE.search(value):
            parts.append(f"{k}={REDACTED}")
        else:
            parts.append(f"{k}={_clean(value, _MAX_VALUE)}")
    if len(pairs) > _MAX_PAIRS:
        parts.append("...")
    return "&".join(parts)
