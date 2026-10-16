"""Platform Registry / Metric Registry. Thêm platform/metric = thêm dòng dữ liệu, KHÔNG đổi code.

Platform Registry là NGUỒN SỰ THẬT về "nền tảng nào tồn tại": Admin/dash_admin tạo; user nhập tay trên web, Tool cá nhân và
Tool dùng chung chỉ được dùng nền tảng có trong registry (và nằm trong `allowed_platforms` của client với Tool).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from manager_sdk import Principal

from integration_service.api_models import AliasCreate, Envelope, MetricPut, PlatformCreate, PlatformPut, RegistryEntry
from integration_service.credentials.service import MachineContext
from integration_service.deps import user_bearer, get_container, machine, require_admin, require_super_admin
from integration_service import examples as ex
from integration_service.errors import Extra, conflict, not_found
from integration_service.http_utils import error_responses, ok, ok_response
from integration_service.platforms import repository
from manager_contracts import ErrorCode

router = APIRouter(prefix="/v1/integrations/admin", tags=["admin"], dependencies=[Depends(user_bearer)])
machine_router = APIRouter(prefix="/v1/integrations", tags=["machine"])

_KEY = r"^[a-z][a-z0-9_]{0,63}$"


def _entry(row: dict, *, codes: bool = False) -> dict:
    out = {"key": row["key"], "display_name": row["display_name"], "enabled": row["enabled"],
           "description": row.get("description")}
    if "aliases" in row:   # chỉ Platform Registry có mã phụ (Metric Registry thì không)
        out["aliases"] = list(row["aliases"])
        if codes:
            out["codes"] = [row["key"], *row["aliases"]]
    return out


def _taken(owner: str, code: str) -> dict:
    return {"code": code, "platform": owner}


@machine_router.get("/platforms", summary="Nền tảng và mã client này được phép gửi dữ liệu lên",
                    description=("Scope `integration:handshake`. Giao của `allowed_platforms` của client với Platform Registry "
                                 "đang bật. Mỗi nền tảng có `key` (mã chính) và `aliases` (mã phụ); `codes` = mọi mã dùng được. "
                                 "Tool PHẢI gửi kèm một mã trong `codes` ở trường `platform` của snapshot/resource — server "
                                 "chuẩn hoá về `key` trước khi lưu để Dashboard gom đúng nền tảng. Mã ngoài danh sách => 403 "
                                 "`PLATFORM_NOT_ALLOWED`."),
                    responses={200: ok_response(Envelope[list[RegistryEntry]], "Platform được phép",
                                                [{**ex.REGISTRY_ENTRY, "codes": ["threads", "threads_app"]}]),
                               **error_responses(401, 403, 429, 503)})
async def list_allowed_platforms(request: Request, ctx: MachineContext = Depends(machine("integration:handshake"))):
    async with get_container(request).db.connection() as conn:
        enabled = {r["key"]: r for r in await repository.list_platforms(conn) if r["enabled"]}
    return ok([_entry(enabled[k], codes=True) for k in sorted(ctx.allowed_platforms) if k in enabled])


@router.get("/platforms", summary="Danh sách Platform Registry (kèm mã phụ)",
            description=("Chỉ `admin`/`dash_admin`. Liệt kê Platform Registry (dữ liệu, seed bằng migration) kèm `aliases` "
                         "(mã phụ) của từng nền tảng: những platform mà Tool có thể gửi dữ liệu lên."),
            responses={200: ok_response(Envelope[list[RegistryEntry]], "Platform", [ex.REGISTRY_ENTRY]),
                       **error_responses(401, 403, 503)})
async def list_platforms(request: Request, _: Principal = Depends(require_admin)):
    async with get_container(request).db.connection() as conn:
        return ok([_entry(r) for r in await repository.list_platforms(conn)])


@router.post("/platforms", status_code=201, summary="Tạo nền tảng mới (admin, dash_admin)",
             description=("Role `admin` hoặc `dash_admin`. Tạo nền tảng với mã chính `key` và (tuỳ chọn) các mã phụ `aliases`. "
                          "Mọi mã (chính + phụ) là DUY NHẤT toàn hệ thống: mã đã được nền tảng nào giữ (kể cả chính nó) => 409 "
                          "`PLATFORM_CODE_EXISTS`. Platform mới được thêm TỰ ĐỘNG vào Tool cá nhân (primary_tool); Tool dùng chung "
                          "chỉ nhận khi `admin` thêm vào `allowed_platforms`."),
             responses={201: ok_response(Envelope[RegistryEntry], "Nền tảng đã tạo", {**ex.REGISTRY_ENTRY, "aliases": []}),
                        **error_responses(401, 403, 409, 422, 503)})
async def create_platform(request: Request, body: PlatformCreate, principal: Principal = Depends(require_admin)):
    c = get_container(request)
    codes = [body.key, *body.aliases]
    repeated = sorted({x for x in codes if codes.count(x) > 1})
    if repeated:
        raise conflict(Extra.PLATFORM_CODE_EXISTS, "Mã bị lặp trong yêu cầu", {"codes": repeated})
    async with c.audit.scope(request, "registry.platform.create", actor_account_id=principal.sub,
                             detail={"key": body.key, "aliases": body.aliases}):
        async with c.db.transaction() as conn:
            await repository.lock_codes(conn)
            for code in codes:
                owner = await repository.code_owner(conn, code)
                if owner is not None:
                    raise conflict(Extra.PLATFORM_CODE_EXISTS, f"Mã nền tảng '{code}' đã tồn tại", _taken(owner, code))
            now = c.now()
            await repository.insert_platform(conn, body.key, body.display_name.strip(), now)
            for alias in body.aliases:
                await repository.insert_alias(conn, body.key, alias, now)
            await repository.grant_to_personal_clients(conn, body.key, now)
            row = await repository.get_platform(conn, body.key)
    return ok(_entry(row), status=201)


@router.put("/platforms/{key}", summary="Sửa tên hiển thị / bật-tắt nền tảng (admin, dash_admin)",
            description=("Role `admin` hoặc `dash_admin`. Sửa `display_name` / `enabled` của một nền tảng (key `^[a-z][a-z0-9_]{0,63}$`); "
                         "`enabled=false` chặn mọi dữ liệu mới của platform đó. Key đang là MÃ PHỤ của nền tảng khác => 409 "
                         "`PLATFORM_CODE_EXISTS`. (Tạo nền tảng mới nên dùng `POST /platforms` — chặn trùng mã.)"),
            responses={200: ok_response(Envelope[RegistryEntry], "Platform sau upsert", ex.REGISTRY_ENTRY),
                       **error_responses(401, 403, 409, 422, 503)})
async def put_platform(request: Request, body: PlatformPut, key: str = Path(..., pattern=_KEY),
                       principal: Principal = Depends(require_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "registry.platform.put", actor_account_id=principal.sub, detail={"key": key}):
        async with c.db.transaction() as conn:
            await repository.lock_codes(conn)
            owner = await repository.code_owner(conn, key)
            if owner is not None and owner != key:
                raise conflict(Extra.PLATFORM_CODE_EXISTS, f"'{key}' là mã phụ của nền tảng '{owner}'", _taken(owner, key))
            await repository.upsert_platform(conn, key, body.display_name, body.enabled, c.now())
            if body.enabled:
                await repository.grant_to_personal_clients(conn, key, c.now())
            row = await repository.get_platform(conn, key)
    return ok(_entry(row))


@router.post("/platforms/{key}/aliases", status_code=201, summary="Thêm mã phụ cho nền tảng (admin, dash_admin)",
             description=("Thêm một mã phụ vào nền tảng `key`. Mã đã được giữ (mã chính hoặc phụ của bất kỳ nền tảng nào) => 409 "
                          "`PLATFORM_CODE_EXISTS`; nền tảng không tồn tại => 404."),
             responses={201: ok_response(Envelope[RegistryEntry], "Nền tảng sau khi thêm mã", {**ex.REGISTRY_ENTRY, "aliases": ["threads_app"]}),
                        **error_responses(401, 403, 404, 409, 422, 503)})
async def add_alias(request: Request, body: AliasCreate, key: str = Path(..., pattern=_KEY),
                    principal: Principal = Depends(require_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "registry.platform.alias.add", actor_account_id=principal.sub,
                             detail={"key": key, "alias": body.alias}):
        async with c.db.transaction() as conn:
            await repository.lock_codes(conn)
            if await repository.get_platform(conn, key) is None:
                raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy nền tảng")
            owner = await repository.code_owner(conn, body.alias)
            if owner is not None:
                raise conflict(Extra.PLATFORM_CODE_EXISTS, f"Mã nền tảng '{body.alias}' đã tồn tại", _taken(owner, body.alias))
            await repository.insert_alias(conn, key, body.alias, c.now())
            row = await repository.get_platform(conn, key)
    return ok(_entry(row), status=201)


@router.delete("/platforms/{key}/aliases/{alias}", summary="Gỡ mã phụ khỏi nền tảng (admin, dash_admin)",
               description=("Gỡ mã phụ. Dữ liệu đã lưu không đổi (đã chuẩn hoá về mã chính); Tool còn gửi mã đã gỡ sẽ bị từ chối "
                            "`PLATFORM_NOT_ALLOWED`. Không tìm thấy => 404. Không gỡ được mã chính."),
               responses={200: ok_response(Envelope[RegistryEntry], "Nền tảng sau khi gỡ mã", ex.REGISTRY_ENTRY),
                          **error_responses(401, 403, 404, 422, 503)})
async def remove_alias(request: Request, key: str = Path(..., pattern=_KEY), alias: str = Path(..., pattern=_KEY),
                       principal: Principal = Depends(require_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "registry.platform.alias.remove", actor_account_id=principal.sub,
                             detail={"key": key, "alias": alias}):
        async with c.db.transaction() as conn:
            await repository.lock_codes(conn)
            if not await repository.delete_alias(conn, key, alias):
                raise not_found(ErrorCode.NOT_FOUND, "Không tìm thấy mã phụ của nền tảng này")
            row = await repository.get_platform(conn, key)
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
