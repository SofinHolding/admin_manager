"""Envelope phản hồi chuẩn: `{ok, data|error, meta}`."""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "2026-10-01"


def ok_envelope(data: Any, *, request_id: str, contract_version: str = CONTRACT_VERSION) -> dict[str, Any]:
    return {"ok": True, "data": data,
            "meta": {"request_id": request_id, "contract_version": contract_version}}


def error_envelope(code: str, message: str, *, request_id: str,
                   details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False,
            "error": {"code": code, "message": message, "details": details or {}},
            "meta": {"request_id": request_id}}
