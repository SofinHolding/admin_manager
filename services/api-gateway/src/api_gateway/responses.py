"""Phản hồi do CHÍNH gateway sinh (lỗi biên, health nội bộ): envelope ổn định, không traceback, không lộ nội bộ."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable

from manager_contracts import error_envelope

Send = Callable[[dict], Awaitable[None]]
Headers = Iterable[tuple[bytes, bytes]]


def _hdr(extra: Headers | None, body_len: int) -> list[tuple[bytes, bytes]]:
    out = [(b"content-type", b"application/json"), (b"content-length", str(body_len).encode())]
    out.extend(extra or [])
    return out


async def send_json(send: Send, status: int, payload: dict, *, headers: Headers | None = None,
                    head_only: bool = False) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": _hdr(headers, len(body))})
    await send({"type": "http.response.body", "body": b"" if head_only else body})


async def send_error(send: Send, status: int, code: str, message: str, *, request_id: str,
                     details: dict | None = None, headers: Headers | None = None,
                     head_only: bool = False) -> None:
    await send_json(send, status, error_envelope(code, message, request_id=request_id, details=details),
                    headers=headers, head_only=head_only)
