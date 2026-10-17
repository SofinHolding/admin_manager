"""Resource: batch upsert của Tool (mặt phẳng MÁY) và bật/tắt của admin (mặt phẳng USER)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from manager_sdk import Principal

from integration_service.api_models import Envelope, ResourceStateOut, ResourceStatePatch
from integration_service.api_models import ResourceBatchOut
from integration_service.credentials.service import MachineContext
from integration_service.delegation import delegated
from integration_service.deps import get_container, machine, require_admin, require_super_admin, user_bearer
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok, ok_response, parse_json_object, raw_body_doc
from integration_service.ingest import validate

router = APIRouter(prefix="/v1/integrations", tags=["machine"])
admin_router = APIRouter(prefix="/v1/integrations", tags=["admin"], dependencies=[Depends(user_bearer)])

_BATCH_EXAMPLE = {"items": [{"platform": "facebook", "resource_type": "page", "external_id": "123456789",
                             "display_name": "Example Page", "username": "example", "country": {"code": "BR"},
                             "observed_at": "2026-10-05T06:55:00Z"}]}


@router.post("/resources/batch", summary="Upsert resource hàng loạt (ngoài snapshot)",
             description=("Scope `resources:write`. Tối đa 500 bản ghi (`BATCH_TOO_LARGE`), body ≤ 2 MiB (413). Khoá tự nhiên "
                          "`(owner, platform, resource_type, external_id)`; `observed_at` mới hơn thắng. Mỗi bản ghi lỗi nằm "
                          "trong `rejections` (không bỏ sót im lặng). Trường `owner_*` => 422 `RECORD_VALIDATION_FAILED`. "
                          "Platform ngoài phạm vi => 403 `PLATFORM_NOT_ALLOWED`. Hỗ trợ `Idempotency-Key`. Client dùng chung "
                          "(`ownership=shared`) BẮT BUỘC kèm `X-Delegated-Job` nếu không => 403 `SHARED_CLIENT_REQUIRES_DELEGATION`."),
             openapi_extra=raw_body_doc("ResourceUpsertBatch", _BATCH_EXAMPLE),
             responses={200: ok_response(Envelope[ResourceBatchOut], "Kết quả theo bản ghi", ex.RESOURCE_BATCH),
                        **error_responses(400, 401, 403, 409, 413, 422, 429, 503)})
async def resources_batch(request: Request,
                          ctx: MachineContext = Depends(delegated("resources:write", require_for_shared=True))):
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


_USER_CHANNELS_EXAMPLE = {"users": [{
    "owner_account_id": "acc_example", "username": "alice", "visible_in_shared_tool": True,
    "totals": {"active": 2, "removed": 1, "shared_tool": 2, "personal_tool": 0},
    "channels": [{"id": "res_example", "platform": "youtube", "resource_type": "channel", "external_id": "UC_example",
                  "display_name": "Example", "url": "", "origin": "web_manual", "execution_mode": "fallback_tool",
                  "enabled": True, "removed": False, "shared_tool_visible": True, "created_at": "2026-10-17T02:02:29+00:00",
                  "updated_at": "2026-10-17T02:02:29+00:00", "last_job_status": "succeeded", "last_job_at": "2026-10-17T07:16:00+00:00",
                  "content_count": 30, "last_collected_at": "2026-10-17T07:16:00+00:00"}]}],
    "summary": {"users": 1, "visible_in_shared_tool": 1, "active_channels": 2, "shared_tool_channels": 2, "removed_channels": 1}}


@admin_router.get("/admin/user-channels", summary="Kênh theo từng user (admin)",
                  description=("Role `admin` hoặc `dash_admin`, chỉ đọc. Mọi kênh của mọi user (nhập từ website lẫn do Tool đẩy), gom theo user, kèm "
                               "job gần nhất và số video đã có số liệu. `shared_tool_visible` cho biết Tool dùng chung có thấy kênh đó không "
                               "(`execution_mode=fallback_tool`, chưa xoá). `include_removed` (mặc định true) kèm cả kênh đã xoá để giải thích "
                               "vì sao một kênh/user không còn ở Tool. Không gồm nhóm (`resource_type=group`)."),
                  responses={200: ok_response(Envelope[dict], "Kênh theo user", _USER_CHANNELS_EXAMPLE),
                             **error_responses(401, 403, 503)})
async def admin_user_channels(request: Request, owner_account_id: str = Query("", description="Chỉ một user"),
                              include_removed: bool = Query(True), _: Principal = Depends(require_admin)):
    c = get_container(request)
    return ok(await c.resources.list_user_channels(owner_account_id=owner_account_id, include_removed=include_removed))


@router.get("/resources", summary="Danh sách resource (machine)",
            description=(
                "Scope `jobs:claim`. "
                "**`fallback_tool`** (key tool dùng chung): thấy TẤT CẢ owner; "
                "`owner_account_id` để lọc thêm khi pull từng user. "
                "**Các client khác**: chỉ thấy resource của chính key đó."
            ),
            responses={200: ok_response(Envelope[dict], "Danh sách resource",
                           {"items": [], "is_shared": True, "owner_account_id": "acc_example"}),
                       **error_responses(401, 403, 503)})
async def list_own_resources(
    request: Request,
    platform: str = Query(""),
    resource_type: str = Query(""),
    owner_account_id: str = Query("", description="Lọc theo owner (chỉ có hiệu lực với fallback_tool key)"),
    ctx: MachineContext = Depends(machine("jobs:claim")),
) -> dict:
    c = get_container(request)
    return ok(await c.resources.list_for_owner(
        ctx, platform=platform, resource_type=resource_type, owner_account_id=owner_account_id,
    ))


@router.get("/resource-owners", summary="Danh sách user có resource (machine)",
            description=(
                "Scope `jobs:claim`. "
                "**`fallback_tool`**: trả tất cả owner trong hệ thống kèm số resource. "
                "**Các client khác**: trả chính owner của key."
            ),
            responses={200: ok_response(Envelope[dict], "Danh sách owner",
                           {"users": [{"id": "acc_example", "username": "alice", "resource_count": 3}]}),
                       **error_responses(401, 403, 503)})
async def list_resource_owners(request: Request, ctx: MachineContext = Depends(machine("jobs:claim"))) -> dict:
    c = get_container(request)
    return ok(await c.resources.list_resource_owners(ctx))
