"""Dựng `GET /v1/integrations/me/connection-guide` + `/me/openapi.json` cho CHỦ client personal."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI
from manager_contracts import CONTRACT_VERSION
from manager_sdk import Principal

from integration_service.clients.service import (
    ClientService,
    public_client,
    public_credential,
    public_installation,
)
from integration_service.config import MAX_BATCH_ITEMS, Settings
from integration_service.credentials import repository as credentials_repo
from integration_service.db import Database
from integration_service.guide.catalog import CATALOG
from integration_service.platforms import repository as registry
from integration_service.installations import repository as installations_repo

_MAX_RECENT_SNAPSHOTS = 5
_MAX_REJECTIONS_SAMPLE = 20
_MAX_SCHEMA_DEPTH = 12
# Mã lỗi Tool có thể gặp ở mặt phẳng máy: (HTTP status, ý nghĩa). Mã riêng từng endpoint nằm ở `CATALOG.error_codes`.
_ERRORS: dict[str, tuple[int, str]] = {
    "AUTH_INVALID": (401, "Thiếu/sai API key hoặc key đã hết hạn"),
    "AUTH_REVOKED": (401, "API key đã bị thu hồi hoặc Tool bị tạm dừng"),
    "SCOPE_DENIED": (403, "Khoá không có scope cho endpoint này"),
    "PLATFORM_NOT_ALLOWED": (403, "Platform ngoài phạm vi client được phép"),
    "CREDENTIAL_NOT_ACTIVATED": (403, "Khoá chưa kích hoạt: gọi /handshake từ thiết bị trước"),
    "INSTALLATION_MISMATCH": (403, "X-Installation-Id vắng hoặc khác installation đã kích hoạt"),
    "SHARED_CLIENT_REQUIRES_DELEGATION": (403, "Client dùng chung phải ghi dữ liệu qua X-Delegated-Job"),
    "CREDENTIAL_DEVICE_BOUND": (409, "Khoá đã kích hoạt cho thiết bị khác — đổi máy phải xoay khoá"),
    "IDEMPOTENCY_CONFLICT": (409, "Cùng Idempotency-Key nhưng body khác"),
    "SNAPSHOT_ALREADY_COMMITTED": (409, "Snapshot đã commit"),
    "SNAPSHOT_NOT_FOUND": (404, "Không tìm thấy snapshot của Tool này"),
    "BATCH_TOO_LARGE": (413, "Tối đa 500 bản ghi mỗi batch"),
    "RECORD_VALIDATION_FAILED": (422, "Body sai schema (kể cả gửi trường owner_*)"),
    "SNAPSHOT_INCOMPLETE": (422, "Commit lệch expected_counts hoặc còn bản ghi bị từ chối"),
    "CONTRACT_VERSION_UNSUPPORTED": (400, f"Chỉ hỗ trợ X-Contract-Version {CONTRACT_VERSION}"),
    "RATE_LIMITED": (429, "Vượt giới hạn request, chờ theo Retry-After"),
}


_CODE_HINT = ("Mã nền tảng do Admin tạo sẵn: một mã trong `platforms[].codes` của hướng dẫn này (mã chính hoặc mã phụ). "
              "Mã khác bị từ chối PLATFORM_NOT_ALLOWED và dữ liệu không được lưu ở đâu cả.")
_PLATFORM_RULES = [
    "Mọi dữ liệu gửi lên PHẢI kèm mã nền tảng (`platform`) — một mã trong `platforms[].codes` (Admin tạo sẵn).",
    "Mỗi snapshot chỉ chứa dữ liệu của MỘT nền tảng. Tool có dữ liệu của nhiều nền tảng: tạo MỘT snapshot cho mỗi nền tảng, "
    "mỗi snapshot kèm mã nền tảng của dữ liệu đó (SDK: `push_by_platform`).",
    "Server chuẩn hoá mã phụ về mã chính trước khi lưu, nên Dashboard gom đúng nền tảng.",
    "Mã không đúng (không có trong danh sách, đã gỡ, nền tảng đã tắt) bị từ chối `PLATFORM_NOT_ALLOWED`; dữ liệu đó không được lưu ở đâu cả.",
]


def _resolve(node, components: dict, depth: int = 0, seen: frozenset[str] = frozenset()):
    """Thay `$ref` bằng schema thật (giới hạn độ sâu, chặn vòng lặp) để Tool đọc được từng trường mà không cần OpenAPI."""
    if isinstance(node, list):
        return [_resolve(x, components, depth, seen) for x in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        if name in seen or depth >= _MAX_SCHEMA_DEPTH:
            return {"$ref": ref}
        return _resolve(components.get(name, {}), components, depth + 1, seen | {name})
    return {k: _resolve(v, components, depth, seen) for k, v in node.items()}


def _request_doc(spec: dict, method: str, path: str) -> tuple[dict | None, dict | None]:
    """(schema đã resolve, ví dụ) của request body JSON lấy từ OpenAPI thật của app — một nguồn sự thật."""
    op = spec.get("paths", {}).get(path, {}).get(method.lower(), {})
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
    schema = body.get("schema")
    if schema is None:
        return None, None
    return _resolve(schema, spec.get("components", {}).get("schemas", {})), body.get("example")


def _inject_codes(node, codes: list[str]) -> None:
    """Gắn danh sách mã nền tảng ĐƯỢC DÙNG của client vào mọi trường `platform`/`platforms` của schema đã resolve, để chủ Tool
    thấy chính xác mã nào hợp lệ ngay trong schema request (không phải đoán)."""
    if isinstance(node, list):
        for x in node:
            _inject_codes(x, codes)
    elif isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            if isinstance(props.get("platform"), dict):
                props["platform"] = {**props["platform"], "enum": codes, "description": _CODE_HINT}
            plural = props.get("platforms")
            if isinstance(plural, dict) and isinstance(plural.get("items"), dict):
                props["platforms"] = {**plural, "items": {**plural["items"], "enum": codes}, "description": _CODE_HINT}
        for v in node.values():
            _inject_codes(v, codes)


class GuideService:
    def __init__(self, db: Database, settings: Settings, clients: ClientService, now: Callable[[], datetime]) -> None:
        self._db = db
        self._settings = settings
        self._clients = clients
        self._now = now

    async def connection_guide(self, app: FastAPI, principal: Principal) -> dict:
        async with self._db.connection() as conn:
            client = await self._clients.load_owned_personal(conn, principal)
            creds = await credentials_repo.list_for_client(conn, client["id"])
            active = [c for c in creds if c["status"] == "active"]
            credential = max(active, key=lambda c: c["created_at"]) if active else None
            allowed_scopes = set(credential["scopes"]) if credential else set(client["allowed_scopes"])
            installs = await installations_repo.list_for_client(conn, client["id"])
            snapshots = await self._recent_snapshots(conn, client["id"])
            platforms = [{"key": p["key"], "display_name": p["display_name"], "aliases": list(p["aliases"]),
                          "codes": [p["key"], *p["aliases"]]}
                         for p in await registry.list_platforms(conn)
                         if p["enabled"] and p["key"] in client["allowed_platforms"]]
        codes = sorted(c for p in platforms for c in p["codes"])
        spec = app.openapi()
        endpoints = []
        for e in CATALOG:
            if e.scope != "integration:handshake" and e.scope not in allowed_scopes:
                continue
            schema, example = _request_doc(spec, e.method, e.path)
            _inject_codes(schema, codes)
            endpoints.append({"method": e.method, "path": e.path, "scope": e.scope, "summary": e.summary,
                              "description": e.description, "request_schema": schema,
                              "example_request": e.example_request if e.example_request is not None else example,
                              "example_response": e.example_response, "error_codes": e.error_codes})
        return {
            "base_url": self._settings.public_base_url or None, "contract_version": CONTRACT_VERSION,
            "auth": {"header": "Authorization", "format": "Bearer mpk_<key_id>.<secret>"},
            "required_headers": [
                {"name": "Authorization", "required_when": "luôn", "description": "mpk_<key_id>.<secret>"},
                {"name": "X-Contract-Version", "required_when": "khuyến nghị", "description": f"vd {CONTRACT_VERSION}"},
                {"name": "X-Installation-Id", "required_when": "mọi endpoint máy trừ /handshake",
                 "description": "installation_id trả về lúc handshake"},
                {"name": "Idempotency-Key", "required_when": "mọi POST/PUT ghi",
                 "description": "8-128 ký tự [A-Za-z0-9_.:-]"}],
            "client": public_client(client), "credential": public_credential(credential) if credential else None,
            "installations": [public_installation(i) for i in installs], "endpoints": endpoints,
            "limits": {"batch_max_items": MAX_BATCH_ITEMS, "body_max_bytes": self._settings.max_body_bytes},
            "error_codes": [{"code": code, "http_status": status, "meaning": meaning}
                            for code, (status, meaning) in sorted(_ERRORS.items(), key=lambda kv: (kv[1][0], kv[0]))],
            "recent_snapshots": snapshots, "platforms": platforms, "platform_rules": _PLATFORM_RULES}

    async def _recent_snapshots(self, conn, client_id: str) -> list[dict]:
        rows = await conn.fetch(
            "SELECT id, platform, status, created_at, committed_at FROM snapshots WHERE client_id = $1 "
            "ORDER BY created_at DESC LIMIT $2", client_id, _MAX_RECENT_SNAPSHOTS)
        out = []
        for row in rows:
            rejections = await conn.fetch(
                "SELECT item_key, code, message FROM snapshot_items WHERE snapshot_id = $1 AND status = 'rejected' "
                "LIMIT $2", row["id"], _MAX_REJECTIONS_SAMPLE)
            counts = await conn.fetch(
                "SELECT kind, status, count(*) AS n FROM snapshot_items WHERE snapshot_id = $1 GROUP BY kind, status",
                row["id"])
            out.append({"snapshot_id": row["id"], "platform": row["platform"], "status": row["status"],
                       "counts": {r["kind"]: r["n"] for r in counts if r["status"] == "staged"},
                       "created_at": row["created_at"], "committed_at": row["committed_at"],
                       "rejections": [{"item_key": r["item_key"], "code": r["code"], "message": r["message"]}
                                     for r in rejections]})
        return out

    async def reduced_openapi(self, app: FastAPI, principal: Principal) -> dict:
        """OpenAPI rút gọn: chỉ các endpoint trong connection-guide + components cần thiết."""
        guide = await self.connection_guide(app, principal)
        full = app.openapi()
        wanted = {(e["method"].lower(), e["path"]) for e in guide["endpoints"]}
        paths: dict = {}
        for path, methods in full.get("paths", {}).items():
            kept = {m: spec for m, spec in methods.items() if (m, path) in wanted}
            if kept:
                paths[path] = kept
        return {"openapi": full.get("openapi", "3.1.0"),
               "info": {"title": "Integration Service — Connection Guide", "version": CONTRACT_VERSION},
               "paths": paths, "components": full.get("components", {})}
