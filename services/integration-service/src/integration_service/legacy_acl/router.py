"""Route legacy: `/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*` — NGUYÊN đường dẫn, query, body, status và `{detail}` của
admin-server (docs/api/legacy/admin-server.openapi.json), do integration-service phục vụ theo route-map của gateway.

Các route này KHÔNG nằm trong OpenAPI công khai của integration-service (`include_in_schema=False`): hợp đồng của chúng là
bản OpenAPI legacy đã đóng băng; test parity dựng lại chúng với `include_in_schema=True` để so khớp với bản đó.
Tên hàm giữ như legacy để `operationId` trùng.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from manager_sdk import Principal

from integration_service.legacy_acl import schemas
from integration_service.legacy_acl.auth import require_admin, require_ops, require_user
from integration_service.legacy_acl.errors import LegacyError
from integration_service.http_utils import client_ip


def _svc(request: Request):
    return request.app.state.container.legacy


def build_routers(include_in_schema: bool = False) -> list[APIRouter]:
    resources = APIRouter(prefix="/v1/resources", tags=["resources"], include_in_schema=include_in_schema)
    ops = APIRouter(prefix="/v1/ops", tags=["ops"], include_in_schema=include_in_schema)
    links = APIRouter(prefix="/v1/viewer/links", tags=["links"], include_in_schema=include_in_schema)
    User, Ops, Admin = Depends(require_user), Depends(require_ops), Depends(require_admin)

    # ── /v1/resources ────────────────────────────────────────────────────────────────

    @resources.get("/platforms", summary="Danh sách platform user được phép nhập trên website")
    async def list_available_platforms(request: Request, payload: Principal = User) -> dict:
        return await _svc(request).available_platforms()

    @resources.get("/sources", summary="Danh sách Source của chính mình")
    async def list_sources(request: Request, payload: Principal = User) -> dict:
        return await _svc(request).list_sources(payload)

    @resources.post("/sources", summary="Tạo Source mới — namespace nghiệp vụ, vd 'Facebook / ITL'")
    async def create_source(request: Request, body: schemas.CreateSourceBody, payload: Principal = User) -> dict:
        return await _svc(request).create_source(payload, body)

    @resources.patch("/sources/{source_id}", summary="Sửa tên/metadata Source")
    async def update_source(request: Request, source_id: str, body: schemas.UpdateSourceBody,
                            payload: Principal = User) -> dict:
        return await _svc(request).update_source(payload, source_id, body)

    @resources.get("", summary="Danh sách resource của chính mình — filter được")
    async def list_resources(
        request: Request, platform: str = Query(""), source_instance_id: str = Query(""),
        country: str = Query(""), origin: str = Query(""),
        mapping_status: str = Query(""), execution_mode: str = Query(""),
        verification_status: str = Query(""), enabled: bool | None = Query(None),
        q: str = Query(""), payload: Principal = User,
    ) -> dict:
        filters = {"platform": platform, "source_instance_id": source_instance_id, "country": country,
                   "origin": origin, "mapping_status": mapping_status, "execution_mode": execution_mode,
                   "verification_status": verification_status, "enabled": enabled, "q": q}
        return await _svc(request).list_resources(payload, filters)

    @resources.post("", summary="Nhập 1 resource (page/channel/profile) vào 1 Source")
    async def create_resource(request: Request, body: schemas.CreateResourceBody, payload: Principal = User) -> dict:
        return await _svc(request).create_resource(payload, body)

    @resources.post("/import", summary="Nhập resource hàng loạt — mỗi dòng 1 URL/username/ID")
    async def import_resources(request: Request, body: schemas.ImportResourceBody, payload: Principal = User) -> dict:
        return await _svc(request).import_resources(payload, body)

    @resources.patch("/{resource_id}", summary="Sửa thông tin hiển thị resource (KHÔNG đổi execution_mode)")
    async def update_resource(request: Request, resource_id: str, body: schemas.UpdateResourceBody,
                              payload: Principal = User) -> dict:
        return await _svc(request).update_resource(payload, resource_id, body)

    @resources.delete("/{resource_id}", summary="Xoá resource")
    async def delete_resource(request: Request, resource_id: str, payload: Principal = User) -> dict:
        return await _svc(request).delete_resource(payload, resource_id)

    # ── /v1/ops ──────────────────────────────────────────────────────────────────────

    @ops.get("/inventory", summary="Toàn bộ resource user đã khai báo — filter được")
    async def inventory(
        request: Request, owner_account_id: str = Query(""), platform: str = Query(""),
        source_instance_id: str = Query(""), country: str = Query(""),
        origin: str = Query(""), mapping_status: str = Query(""),
        execution_mode: str = Query(""), verification_status: str = Query(""),
        enabled: bool | None = Query(None), q: str = Query(""),
        payload: Principal = Ops,
    ) -> dict:
        filters = {"platform": platform, "source_instance_id": source_instance_id, "country": country,
                   "origin": origin, "mapping_status": mapping_status, "execution_mode": execution_mode,
                   "verification_status": verification_status, "enabled": enabled, "q": q}
        return await _svc(request).inventory(filters, owner_account_id)

    @ops.get("/inventory/revision", summary="Revision inventory — Tool B so sánh để biết có cần tải lại không")
    async def inventory_revision(request: Request, owner_account_id: str = Query(""), payload: Principal = Ops) -> dict:
        return await _svc(request).inventory_revision(owner_account_id)

    @ops.get("/users", summary="Danh sách user đang có resource (cho filter dropdown)")
    async def ops_users(request: Request, payload: Principal = Ops) -> dict:
        return await _svc(request).inventory_users()

    @ops.get("/platforms", summary="Danh sách platform + policy hiện tại")
    async def ops_platforms(request: Request, payload: Principal = Ops) -> dict:
        return await _svc(request).policies()

    @ops.get("/sources", summary="Toàn bộ Source của mọi user")
    async def ops_sources(request: Request, owner_account_id: str = Query(""), payload: Principal = Ops) -> dict:
        return await _svc(request).ops_sources(owner_account_id)

    @ops.post("/batches", summary="Tạo batch mới — 1 batch nên cùng 1 platform")
    async def create_batch(request: Request, body: schemas.CreateBatchBody, payload: Principal = Ops) -> dict:
        return await _svc(request).create_batch(payload, body)

    @ops.get("/batches", summary="Danh sách batch")
    async def list_batches(request: Request, mine_only: bool = Query(False), payload: Principal = Ops) -> dict:
        return await _svc(request).list_batches(payload, mine_only)

    @ops.get("/batches/{batch_id}", summary="Chi tiết batch + toàn bộ item")
    async def get_batch(request: Request, batch_id: str, payload: Principal = Ops) -> dict:
        return await _svc(request).get_batch(batch_id)

    @ops.post("/batches/{batch_id}/items",
              summary="Thêm resource vào Run Queue — resource không thoả điều kiện bị SKIP kèm lý do")
    async def add_items(request: Request, batch_id: str, body: schemas.AddItemsBody, payload: Principal = Ops) -> dict:
        return await _svc(request).add_items(payload, batch_id, body)

    @ops.patch("/items/{item_id}/cancel", summary="Huỷ 1 item trong queue")
    async def cancel_item(request: Request, item_id: str, payload: Principal = Ops) -> dict:
        if not await _svc(request).cancel_item(payload, item_id):
            raise LegacyError(400, "Item không tồn tại hoặc đã ở trạng thái cuối (success/failed/cancelled)")
        return {"ok": True}

    @ops.get("/platform-policies", summary="Danh sách policy platform")
    async def list_policies(request: Request, payload: Principal = Ops) -> dict:
        return await _svc(request).policies()

    @ops.patch("/platform-policies/{platform}", summary="Sửa policy platform (chỉ admin)")
    async def update_policy(request: Request, platform: str, body: schemas.UpdatePolicyBody,
                            payload: Principal = Admin) -> dict:
        return await _svc(request).update_policy(payload, platform, body)

    @ops.patch("/resources/{resource_id}/execution-mode",
               summary="Đổi execution_mode của 1 resource (chỉ admin, có audit log)")
    async def set_execution_mode(request: Request, resource_id: str, body: schemas.SetExecutionModeBody,
                                 payload: Principal = Admin) -> dict:
        return await _svc(request).set_execution_mode(payload, resource_id, body)

    # ── /v1/viewer/links ─────────────────────────────────────────────────────────────

    @links.post("/code", summary="Tạo mã liên kết Tool mới (TTL 15 phút)")
    async def create_code(request: Request, payload: Principal = User) -> dict:
        return await _svc(request).create_link_code(payload)

    @links.get("", summary="Danh sách Tool đã liên kết với account hiện tại")
    async def list_links(request: Request, payload: Principal = User) -> dict:
        return await _svc(request).list_links(payload)

    @links.delete("/{tool_user_id}", summary="Gỡ liên kết")
    async def revoke(request: Request, tool_user_id: str, payload: Principal = User) -> dict:
        return await _svc(request).revoke_link(payload, tool_user_id)

    @links.post("/redeem", summary="Tool đổi mã lấy liên kết (public — không cần JWT dashboard)")
    async def redeem(body: schemas.RedeemBody, request: Request) -> dict:
        return await _svc(request).redeem(body, client_ip(request))

    return [resources, ops, links]
