"""Quản trị Platform Registry / Metric Registry (admin). Thêm platform/metric = thêm dòng dữ liệu, KHÔNG đổi code."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from manager_sdk import Principal

from integration_service.api_models import Envelope, MetricPut, PlatformPut, RegistryEntry
from integration_service.deps import user_bearer, get_container, require_admin, require_super_admin
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok, ok_response
from integration_service.platforms import repository

router = APIRouter(prefix="/v1/integrations/admin", tags=["admin"], dependencies=[Depends(user_bearer)])

_KEY = r"^[a-z][a-z0-9_]{0,63}$"


def _entry(row: dict) -> dict:
    return {"key": row["key"], "display_name": row["display_name"], "enabled": row["enabled"],
            "description": row.get("description")}


@router.get("/platforms", summary="Danh sách Platform Registry",
            description=("Chỉ `admin`/`dash_admin`. Liệt kê Platform Registry (dữ liệu, seed bằng migration): "
                         "những platform mà Tool có thể gửi dữ liệu lên."),
            responses={200: ok_response(Envelope[list[RegistryEntry]], "Platform", [ex.REGISTRY_ENTRY]),
                       **error_responses(401, 403, 503)})
async def list_platforms(request: Request, _: Principal = Depends(require_admin)):
    async with get_container(request).db.connection() as conn:
        return ok([_entry(r) for r in await repository.list_platforms(conn)])


@router.put("/platforms/{key}", summary="Thêm/sửa platform (admin)",
            description=("Chỉ role `admin`. Thêm hoặc sửa một dòng Platform Registry (key `^[a-z][a-z0-9_]{0,63}$`); "
                         "không cần đổi code. `enabled=false` chặn mọi dữ liệu mới của platform đó."),
            responses={200: ok_response(Envelope[RegistryEntry], "Platform sau upsert", ex.REGISTRY_ENTRY),
                       **error_responses(401, 403, 422, 503)})
async def put_platform(request: Request, body: PlatformPut, key: str = Path(..., pattern=_KEY),
                       principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "registry.platform.put", actor_account_id=principal.sub, detail={"key": key}):
        async with c.db.connection() as conn:
            row = await repository.upsert_platform(conn, key, body.display_name, body.enabled, c.now())
    return ok(_entry(row))


@router.get("/metrics", summary="Danh sách Metric Registry", description=("Chỉ `admin`/`dash_admin`. Liệt kê Metric Registry (dữ liệu): khoá metric canonical hợp lệ trong "
                         "`metrics`; khoá lạ phải đặt vào `extensions`."),
            responses={200: ok_response(Envelope[list[RegistryEntry]], "Metric", [{**ex.REGISTRY_ENTRY, "key": "views", "display_name": "Views"}]),
                       **error_responses(401, 403, 503)})
async def list_metrics(request: Request, _: Principal = Depends(require_admin)):
    async with get_container(request).db.connection() as conn:
        return ok([_entry(r) for r in await repository.list_metrics(conn)])


@router.put("/metrics/{key}", summary="Thêm/sửa metric canonical (admin)",
            description=("Chỉ role `admin`. Thêm hoặc sửa một khoá trong Metric Registry (không thêm cột DB, không đổi code); "
                         "metric chỉ trở thành KPI chính thức khi được nâng lên registry."),
            responses={200: ok_response(Envelope[RegistryEntry], "Metric sau upsert", {**ex.REGISTRY_ENTRY, "key": "saves"}),
                       **error_responses(401, 403, 422, 503)})
async def put_metric(request: Request, body: MetricPut, key: str = Path(..., pattern=_KEY),
                     principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "registry.metric.put", actor_account_id=principal.sub, detail={"key": key}):
        async with c.db.connection() as conn:
            row = await repository.upsert_metric(conn, key, body.display_name, body.description, body.enabled, c.now())
    return ok(_entry(row))
