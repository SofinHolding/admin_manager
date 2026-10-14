"""Quản lý Integration Client (mặt phẳng USER — access token RS256 do Identity ký)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from manager_sdk import Principal

from integration_service.api_models import (ClientCreate, ClientDetailOut, ClientOut, ClientPatch, Envelope)
from integration_service.clients.service import public_client
from integration_service.deps import user_bearer, get_container, require_super_admin, require_user, require_tool_integration
from integration_service import examples as ex
from integration_service.http_utils import EnvelopeResponse, error_responses, ok, ok_response

router = APIRouter(prefix="/v1/integrations", tags=["clients"], dependencies=[Depends(user_bearer)])

_USER_ERRORS = error_responses(401, 403, 404, 422, 429, 503)


@router.post("/clients", status_code=201, summary="Tạo Integration Client",
             description=("Owner = `sub` của access token (không nhận từ body). `platforms` bị cắt theo Platform Registry; "
                          "scope mặc định theo `client_kind`. Client ở trạng thái `pending_email_verification` cho tới khi "
                          "email của account được xác thực (xem `account.verified`)."),
             responses={201: ok_response(Envelope[ClientOut], "Client đã tạo", ex.CLIENT), **error_responses(
                 401, 403, 409, 422, 429, 503)})
async def create_client(request: Request, body: ClientCreate, principal: Principal = Depends(require_tool_integration)):
    c = get_container(request)
    async with c.audit.scope(request, "client.create", actor_account_id=principal.sub,
                             account_id=principal.sub) as extra:
        row = await c.clients.create(principal, tool_name=body.tool_name, client_kind=body.client_kind,
                                     platforms=body.platforms)
        extra["client_id"] = row["id"]
    return ok(public_client(row), status=201)


@router.get("/clients", summary="Danh sách client",
            description="User thường chỉ thấy client của mình. `admin`/`dash_admin` thấy tất cả, lọc bằng `owner`. "
                        "Không bao giờ trả secret/hash.",
            responses={200: ok_response(Envelope[list[ClientOut]], "Danh sách client", [ex.CLIENT]), **_USER_ERRORS})
async def list_clients(request: Request, owner: str | None = Query(None, max_length=64),
                       status: str | None = Query(None, max_length=40), limit: int = Query(50, ge=1, le=200),
                       offset: int = Query(0, ge=0, le=100_000), principal: Principal = Depends(require_user)):
    rows = await get_container(request).clients.list(principal, owner=owner, status=status, limit=limit, offset=offset)
    return ok({"items": [public_client(r) for r in rows]})


@router.get("/clients/{client_id}", summary="Chi tiết client",
            description="Chủ client hoặc admin. Kèm installations và credentials (key_id, scopes, last_used_at, status).",
            responses={200: ok_response(Envelope[ClientDetailOut], "Chi tiết", ex.CLIENT_DETAIL), **_USER_ERRORS})
async def get_client(request: Request, client_id: str, principal: Principal = Depends(require_user)):
    return ok(await get_container(request).clients.detail(principal, client_id))


@router.patch("/clients/{client_id}", summary="Sửa client (admin)",
              description=("Chỉ role `admin`. `status` (suspended/deleted thu hồi MỌI credential), `allowed_scopes` "
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
