"""Resource: batch upsert của Tool (mặt phẳng MÁY) và bật/tắt của admin (mặt phẳng USER)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from manager_sdk import Principal

from integration_service.api_models import Envelope, ResourceStateOut, ResourceStatePatch
from integration_service.api_models import ResourceBatchOut
from integration_service.credentials.service import MachineContext
from integration_service.deps import get_container, machine, require_super_admin, user_bearer
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok, ok_response, parse_json_object, raw_body_doc
from integration_service.ingest import validate

router = APIRouter(prefix="/v1/integrations", tags=["machine"])
admin_router = APIRouter(prefix="/v1/integrations", tags=["admin"], dependencies=[Depends(user_bearer)])

_BATCH_EXAMPLE = {"items": [{"platform": "facebook", "resource_type": "page", "external_id": "123456789",
                             "display_name": "Example Page", "username": "example", "country": {"code": "BR"},
                             "projects": [{"project_key": "itl", "enabled": True}],
                             "observed_at": "2026-10-05T06:55:00Z"}]}


@router.post("/resources/batch", summary="Upsert resource hàng loạt (ngoài snapshot)",
             description=("Scope `resources:write`. Tối đa 500 bản ghi (`BATCH_TOO_LARGE`), body ≤ 2 MiB (413). Khoá tự nhiên "
                          "`(owner, platform, resource_type, external_id)`; `observed_at` mới hơn thắng. Mỗi bản ghi lỗi nằm "
                          "trong `rejections` (không bỏ sót im lặng). Trường `owner_*` => 422 `RECORD_VALIDATION_FAILED`. "
                          "Platform ngoài phạm vi => 403 `PLATFORM_NOT_ALLOWED`. Hỗ trợ `Idempotency-Key`."),
             openapi_extra=raw_body_doc("ResourceUpsertBatch", _BATCH_EXAMPLE),
             responses={200: ok_response(Envelope[ResourceBatchOut], "Kết quả theo bản ghi", ex.RESOURCE_BATCH),
                        **error_responses(400, 401, 403, 409, 413, 422, 429, 503)})
async def resources_batch(request: Request, ctx: MachineContext = Depends(machine("resources:write"))):
    c = get_container(request)
    raw = await request.body()

    async def work(conn, extra):
        items = validate.extract_items(parse_json_object(raw))
        return await c.resources.upsert_batch(conn, ctx, items)

    return await c.runner.write(request, ctx, action="resources.batch", work=work)


@admin_router.patch("/admin/resources/{resource_id}", summary="Bật/tắt resource (admin)",
              description="Chỉ role `admin`. Đổi ngoài snapshot nên phát `resource.changed` và gán `revision` mới.",
              responses={200: ok_response(Envelope[ResourceStateOut], "Trạng thái mới", ex.RESOURCE_STATE),
                         **error_responses(401, 403, 404, 422, 503)})
async def admin_patch_resource(request: Request, resource_id: str, body: ResourceStatePatch,
                               principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "resource.set_state", actor_account_id=principal.sub, resource_id=resource_id):
        result = await c.resources.admin_set_state(resource_id, body.state)
    return ok(result)
