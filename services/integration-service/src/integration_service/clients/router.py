"""Quản lý Integration Client (mặt phẳng USER — access token RS256 do Identity ký).

`POST /clients` tạo client DÙNG CHUNG (CHỈ admin, wizard `draft -> configured -> active`). Client CÁ NHÂN KHÔNG
có đường tạo tay — tự provision khi account có `capability:tool_integration` (xem provisioning/handlers.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from manager_sdk import Principal

from integration_service.api_models import (ClientActivate, ClientConfigure, ClientDetailOut, ClientOut, ClientPatch,
                                            ClientTransferRequest, ClientWizardCreate, Envelope)
from integration_service.clients.service import public_client
from integration_service.credentials.service import CredentialService
from integration_service.deps import user_bearer, get_container, require_super_admin, require_user
from integration_service import examples as ex
from integration_service.http_utils import EnvelopeResponse, error_responses, ok, ok_response

router = APIRouter(prefix="/v1/integrations", tags=["clients"], dependencies=[Depends(user_bearer)])

_USER_ERRORS = error_responses(401, 403, 404, 422, 429, 503)
_ADMIN_ERRORS = error_responses(401, 403, 404, 409, 422, 429, 503)


@router.post("/clients", status_code=201, summary="Bước 1 — Tạo client dùng chung (admin)",
             description=("CHỈ role `admin`. `client_kind` chỉ `fallback_tool`/`service` (`primary_tool` => 422, tự "
                          "provision từ capability). Owner = admin gọi (người chịu trách nhiệm). Tạo ở trạng thái "
                          "`draft` — chưa cấp được credential cho tới khi `PUT .../config` rồi `POST .../activate`."),
             responses={201: ok_response(Envelope[ClientOut], "Client draft", ex.CLIENT_DRAFT), **_ADMIN_ERRORS})
async def create_client(request: Request, body: ClientWizardCreate, principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "client.create", actor_account_id=principal.sub,
                             account_id=principal.sub) as extra:
        row = await c.clients.create_wizard(principal, tool_name=body.tool_name, client_kind=body.client_kind,
                                            purpose=body.purpose, platforms=body.platforms)
        extra["client_id"] = row["id"]
    return ok(public_client(row), status=201)


@router.put("/clients/{client_id}/config", summary="Bước 2 — Cấu hình platform/scope (admin)",
            description=("CHỈ role `admin`, CHỈ khi client đang `draft` (ngược lại 409 `CLIENT_NOT_READY`). "
                         "`allowed_scopes` theo policy kind (`jobs:claim:system`/`jobs:*` chỉ `fallback_tool`)."),
            responses={200: ok_response(Envelope[ClientOut], "Client đã cấu hình",
                       {**ex.CLIENT_DRAFT, "allowed_platforms": ["facebook"],
                        "allowed_scopes": ["integration:handshake", "resources:write", "snapshots:write",
                                          "metrics:write", "jobs:claim", "jobs:complete"]}), **_ADMIN_ERRORS})
async def configure_client(request: Request, client_id: str, body: ClientConfigure,
                           principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "client.configure", actor_account_id=principal.sub, client_id=client_id,
                             detail={"allowed_platforms": body.allowed_platforms, "allowed_scopes": body.allowed_scopes}):
        row = await c.clients.configure(client_id, allowed_platforms=body.allowed_platforms,
                                        allowed_scopes=body.allowed_scopes)
    return ok(public_client(row))


@router.post("/clients/{client_id}/activate", summary="Bước 3 — Kích hoạt (admin)",
             description=("CHỈ role `admin`. Cần `configured_at` đã có (bước 2); `confirm_tool_name` PHẢI khớp "
                          "`tool_name` (chống kích hoạt nhầm client). Sau bước này mới cấp được credential."),
             responses={200: ok_response(Envelope[ClientOut], "Client active", ex.CLIENT), **_ADMIN_ERRORS})
async def activate_client(request: Request, client_id: str, body: ClientActivate,
                          principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    async with c.audit.scope(request, "client.activate", actor_account_id=principal.sub, client_id=client_id):
        row = await c.clients.activate(principal, client_id, confirm_tool_name=body.confirm_tool_name)
    return ok(public_client(row))


@router.post("/clients/{client_id}/transfer", summary="Chuyển chủ client cá nhân (admin)",
             description=("CHỈ role `admin`, CHỈ client `ownership=personal`. Thu hồi mọi credential cũ, đổi "
                          "`owner_account_id`, cấp credential mới `reveal_state=pending` cho chủ mới (chủ mới tự "
                          "`reveal`). Dữ liệu đã ingest trước đó GIỮ owner cũ. Đích đã có client personal pending/"
                          "active => 409 `PERSONAL_CLIENT_EXISTS`."),
             responses={200: ok_response(Envelope[ClientOut], "Client sau khi chuyển chủ", ex.CLIENT_TRANSFERRED),
                        **_ADMIN_ERRORS})
async def transfer_client(request: Request, client_id: str, body: ClientTransferRequest,
                          principal: Principal = Depends(require_super_admin)):
    c = get_container(request)
    credentials: CredentialService = c.credentials
    async with c.audit.scope(request, "client.transfer", actor_account_id=principal.sub, client_id=client_id,
                             detail={"new_owner_account_id": body.new_owner_account_id}):
        row = await credentials.transfer(client_id, new_owner_account_id=body.new_owner_account_id)
    return ok(public_client(row))


@router.get("/clients", summary="Danh sách client",
            description="User thường chỉ thấy client của mình. `admin`/`dash_admin` thấy tất cả, lọc bằng `owner`. "
                        "Mỗi item kèm `ownership`, `purpose`, `credentials` (tóm tắt, chưa revoked). Không bao giờ "
                        "trả secret/hash.",
            responses={200: ok_response(Envelope[list[ClientOut]], "Danh sách client", [ex.CLIENT]), **_USER_ERRORS})
async def list_clients(request: Request, owner: str | None = Query(None, max_length=64),
                       status: str | None = Query(None, max_length=40), limit: int = Query(50, ge=1, le=200),
                       offset: int = Query(0, ge=0, le=100_000), principal: Principal = Depends(require_user)):
    rows = await get_container(request).clients.list(principal, owner=owner, status=status, limit=limit, offset=offset)
    return ok({"items": [public_client(r) for r in rows]})


@router.get("/clients/{client_id}", summary="Chi tiết client",
            description="Chủ client hoặc admin. Kèm installations và credentials (key_id, scopes, last_used_at, "
                        "status, reveal_state, bound_device_id, activated_at).",
            responses={200: ok_response(Envelope[ClientDetailOut], "Chi tiết", ex.CLIENT_DETAIL), **_USER_ERRORS})
async def get_client(request: Request, client_id: str, principal: Principal = Depends(require_user)):
    return ok(await get_container(request).clients.detail(principal, client_id))


@router.patch("/clients/{client_id}", summary="Sửa client (admin)",
              description=("Chỉ role `admin`. `status` (suspended/deleted thu hồi MỌI credential; sang `active` "
                           "khi đang `draft` => 409 `CLIENT_NOT_READY`, dùng `.../activate`), `allowed_scopes` "
                           "(`jobs:claim:system` chỉ cho `fallback_tool`), `allowed_platforms`, `tool_name`."),
              responses={200: ok_response(Envelope[ClientOut], "Client sau khi sửa", {**ex.CLIENT, "status": "suspended"}), **_USER_ERRORS})
async def patch_client(request: Request, client_id: str, body: ClientPatch,
                       principal: Principal = Depends(require_super_admin)) -> EnvelopeResponse:
    c = get_container(request)
    fields = body.model_dump(exclude_unset=True)
    async with c.audit.scope(request, "client.patch", actor_account_id=principal.sub, client_id=client_id,
                             detail={"fields": sorted(fields)}):
        row = await c.clients.admin_patch(client_id, fields=fields)
    return ok(public_client(row))
