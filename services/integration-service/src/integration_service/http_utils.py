"""Tiện ích HTTP dùng chung: envelope phản hồi, đọc body JSON thô, IP, Idempotency-Key, giới hạn body, OpenAPI."""

from __future__ import annotations

import json
import re
import zlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from manager_contracts import CONTRACT_VERSION, ErrorCode, error_envelope, ok_envelope
from manager_observability import current_request_id
from manager_observability.request_context import new_request_id
from pydantic import BaseModel

from integration_service.errors import ApiProblem, Extra, invalid
from integration_service.security import iso

_IDEM_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{8,128}$")


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"không serialize được {type(value).__name__}")


class EnvelopeResponse(JSONResponse):
    """JSONResponse chịu được datetime/date (Postgres trả về) và không escape tiếng Việt."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"), default=_default).encode("utf-8")


def ok(data: Any, *, status: int = 200, headers: dict[str, str] | None = None) -> EnvelopeResponse:
    return EnvelopeResponse(status_code=status, headers=headers,
                            content=ok_envelope(data, request_id=current_request_id()))


def envelope(data: Any) -> dict:
    """Envelope thành công dạng JSON thuần (datetime đã thành chuỗi) — dùng để lưu idempotency và trả về."""
    return json.loads(json.dumps(ok_envelope(data, request_id=current_request_id()), default=_default))


def client_ip(request: Request) -> str:
    """IP gốc do gateway chuyển qua `X-Real-IP` (không dùng X-Forwarded-For vì giả mạo được nhiều tầng).

    CHỈ tin header này khi service nằm sau gateway tin cậy (không mở cổng service ra internet)."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def idempotency_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    if key is None:
        return None
    if not _IDEM_KEY.match(key):
        raise ApiProblem(ErrorCode.REQUEST_VALIDATION_FAILED, "Idempotency-Key không hợp lệ (8-128 ký tự [A-Za-z0-9_.:-])",
                         status_code=422)
    return key


def _reject_constant(name: str) -> Any:
    raise ValueError("hằng số JSON không hợp lệ")


def parse_json_object(raw: bytes) -> dict:
    """Parse body thành object JSON; NaN/Infinity và body không phải object bị từ chối (RECORD_VALIDATION_FAILED)."""
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        raise invalid("Body không phải JSON hợp lệ") from None
    if not isinstance(value, dict):
        raise invalid("Body phải là một object JSON")
    return value


def error_responses(*statuses: int, extra: dict[int, str] | None = None) -> dict[int | str, dict]:
    """Khối `responses` OpenAPI cho các mã lỗi thường gặp (schema = ErrorEnvelope)."""
    text = {400: "Yêu cầu sai (vd CONTRACT_VERSION_UNSUPPORTED)", 401: "AUTH_INVALID / AUTH_REVOKED",
            403: "SCOPE_DENIED / PLATFORM_NOT_ALLOWED / CLIENT_NOT_ACTIVE / EMAIL_NOT_VERIFIED",
            404: "Không tìm thấy", 409: "Xung đột trạng thái / IDEMPOTENCY_CONFLICT",
            413: "PAYLOAD_TOO_LARGE / BATCH_TOO_LARGE", 422: "RECORD_VALIDATION_FAILED / REQUEST_VALIDATION_FAILED / SNAPSHOT_INCOMPLETE",
            429: "RATE_LIMITED (kèm Retry-After)", 503: "UNAVAILABLE (dependency tạm chết)"}
    text.update(extra or {})
    # 422 luôn có: path/query/body sai đều ra envelope lỗi của service (không phải HTTPValidationError mặc định của FastAPI).
    return {s: {"description": text.get(s, "Lỗi"), "model": ErrorEnvelope,
                "content": {"application/json": {"example": _error_example(s)}}} for s in sorted({*statuses, 422})}


_ERROR_SAMPLES = {
    400: ("CONTRACT_VERSION_UNSUPPORTED", "Chỉ hỗ trợ contract 2026-10-01"),
    401: ("AUTH_INVALID", "Thiếu hoặc sai API key"),
    403: ("SCOPE_DENIED", "Thiếu scope snapshots:write"),
    404: ("SNAPSHOT_NOT_FOUND", "Không tìm thấy snapshot"),
    409: ("SNAPSHOT_ALREADY_COMMITTED", "Snapshot đã được commit"),
    413: ("BATCH_TOO_LARGE", "Tối đa 500 bản ghi mỗi batch"),
    422: ("RECORD_VALIDATION_FAILED", "Body không hợp lệ"),
    429: ("RATE_LIMITED", "Vượt giới hạn request của client"),
    503: ("UNAVAILABLE", "Dịch vụ tạm thời không khả dụng"),
}


def _error_example(status: int) -> dict:
    code, message = _ERROR_SAMPLES.get(status, ("SERVER_ERROR", "Lỗi"))
    return {"ok": False, "error": {"code": code, "message": message, "details": {}},
            "meta": {"request_id": "req_0123456789abcdef0123456789abcdef"}}


def ok_response(model: Any, description: str, example: Any) -> dict:
    """Khối `responses[2xx]`: schema envelope + ví dụ."""
    body = {"ok": True, "data": example,
            "meta": {"request_id": "req_0123456789abcdef0123456789abcdef", "contract_version": CONTRACT_VERSION}}
    return {"description": description, "model": model, "content": {"application/json": {"example": body}}}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorMeta(BaseModel):
    request_id: str


class ErrorEnvelope(BaseModel):
    ok: bool = False
    error: ErrorBody
    meta: ErrorMeta


class OkMeta(BaseModel):
    request_id: str
    contract_version: str = CONTRACT_VERSION


def raw_body_doc(schema_name: str, example: dict | None = None, description: str = "") -> dict:
    """`openapi_extra` cho endpoint tự parse body (để trả rejections theo từng bản ghi thay vì 422 gộp)."""
    media: dict[str, Any] = {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
    if example is not None:
        media["example"] = example
    return {"requestBody": {"required": True, "description": description, "content": {"application/json": media}}}


def install_openapi_models(app: FastAPI, models: list[type[BaseModel]]) -> None:
    """Đưa thêm schema (request body tự parse) vào `components.schemas` của OpenAPI."""
    original = app.openapi

    def custom() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in models:
            sub = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, definition in sub.pop("$defs", {}).items():
                components[name] = definition
            components[model.__name__] = sub
        schema["components"]["schemas"] = dict(sorted(components.items()))
        from manager_contracts import ErrorCode

        from integration_service.errors import Extra
        schema["info"]["x-error-codes"] = sorted({c.value for c in ErrorCode} | {c.value for c in Extra})
        app.openapi_schema = schema
        return schema

    app.openapi = custom  # type: ignore[method-assign]


# ── Giới hạn body (ASGI thuần: chặn TRƯỚC khi parse, hỗ trợ gzip có trần giải nén) ──────────────

class BodyLimitMiddleware:
    """Body quá `max_bytes` (kể cả sau giải nén gzip) => 413 PAYLOAD_TOO_LARGE; Content-Encoding lạ => 415."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope["headers"]}
        length = headers.get(b"content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            await self._reply(scope, send, 413, Extra.PAYLOAD_TOO_LARGE.value,
                              f"Body vượt giới hạn {self.max_bytes} byte")
            return
        encoding = headers.get(b"content-encoding", b"identity").decode("latin-1").strip().lower()
        if encoding not in ("identity", "gzip", ""):
            await self._reply(scope, send, 415, Extra.UNSUPPORTED_ENCODING.value, "Chỉ hỗ trợ Content-Encoding: gzip")
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await self._reply(scope, send, 413, Extra.PAYLOAD_TOO_LARGE.value,
                                  f"Body vượt giới hạn {self.max_bytes} byte")
                return
            chunks.append(body)
            if not message.get("more_body"):
                break
        data = b"".join(chunks)
        if encoding == "gzip" and data:
            try:
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = inflater.decompress(data, self.max_bytes + 1)
                if inflater.unconsumed_tail or len(data) > self.max_bytes:
                    await self._reply(scope, send, 413, Extra.PAYLOAD_TOO_LARGE.value,
                                      f"Body sau giải nén vượt giới hạn {self.max_bytes} byte")
                    return
                if not inflater.eof:
                    raise zlib.error("gzip bị cắt cụt")
            except zlib.error:
                await self._reply(scope, send, 400, ErrorCode.REQUEST_VALIDATION_FAILED.value, "Body gzip không hợp lệ")
                return
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in scope["headers"]
                                if k.lower() not in (b"content-encoding", b"content-length")]
            scope["headers"].append((b"content-length", str(len(data)).encode()))

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reply(scope, send, status: int, code: str, message: str) -> None:
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if re.match(r"^[A-Za-z0-9_\-.]{8,128}$", incoming) else new_request_id()
        body = json.dumps(error_envelope(code, message, request_id=rid), ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"x-request-id", rid.encode())]})
        await send({"type": "http.response.body", "body": body})


def parse_model(model: type[BaseModel], data: dict):
    """Validate dict bằng model pydantic; lỗi => 422 RECORD_VALIDATION_FAILED (không phản chiếu giá trị đầu vào)."""
    from pydantic import ValidationError
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = [{"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type", "")} for e in exc.errors()[:10]]
        if any(f["loc"] and f["loc"][-1].lower().startswith("owner") for f in fields):
            raise invalid("Trường owner_* không được gửi: quyền sở hữu suy ra từ credential",
                          {"fields": fields}) from None
        raise invalid("Body không hợp lệ", {"fields": fields}) from None
