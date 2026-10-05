"""`POST /v1/integrations/handshake` — Tool khai báo installation + capabilities (mặt phẳng MÁY)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from integration_service.api_models import Envelope, HandshakeOut
from integration_service.credentials.service import MachineContext
from integration_service.deps import get_container, machine
from integration_service import examples as ex
from integration_service.http_utils import client_ip, error_responses, ok_response
from integration_service.installations.service import HandshakeRequest

router = APIRouter(prefix="/v1/integrations", tags=["machine"])

_EXAMPLE = {"installation": {"device_id": "device-abc", "hostname": "PC-01", "os": "windows", "app_version": "2.4.0"},
            "tool": {"tool_name": "Facebook Operations Tool", "contract_version": "2026-10-01"},
            "platforms": ["facebook"], "capabilities": ["resources.push", "snapshots.push", "metrics.push"]}


@router.post("/handshake", summary="Handshake của Tool",
             description=("Scope `integration:handshake`. Tạo/cập nhật installation theo `(client, device_id)`. "
                          "`tool_name` chỉ là metadata — ownership suy ra từ API key. Trả giao của capabilities khai báo và "
                          "được phép; platform ngoài phạm vi client => `PLATFORM_NOT_ALLOWED`."),
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                 "schema": {"$ref": "#/components/schemas/HandshakeRequest"}, "example": _EXAMPLE}}}},
             responses={200: ok_response(Envelope[HandshakeOut], "Phạm vi được phép", ex.HANDSHAKE),
                        **error_responses(400, 401, 403, 422, 429, 503)})
async def handshake(request: Request, body: HandshakeRequest, ctx: MachineContext = Depends(machine("integration:handshake"))):
    c = get_container(request)

    async def work(conn, extra):
        return await c.installations.handshake(conn, ctx, body, client_ip(request))

    return await c.runner.write(request, ctx, action="handshake", work=work)
