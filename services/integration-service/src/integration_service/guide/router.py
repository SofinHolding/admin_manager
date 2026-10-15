"""`GET /v1/integrations/me/connection-guide` + `/me/openapi.json` (mặt phẳng USER, chủ client personal)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from manager_sdk import Principal

from integration_service import examples as ex
from integration_service.api_models import ConnectionGuideOut, Envelope
from integration_service.deps import (
    get_container,
    require_tool_integration,
    user_bearer,
)
from integration_service.http_utils import error_responses, ok, ok_response

router = APIRouter(prefix="/v1/integrations/me", tags=["guide"], dependencies=[Depends(user_bearer)])


@router.get("/connection-guide", summary="Hướng dẫn kết nối Tool cá nhân",
            description=("Chủ client personal (cần `capability:tool_integration`). 404 `CLIENT_NOT_FOUND` nếu chưa "
                         "có Tool cá nhân. Chỉ liệt kê endpoint mà scope của credential hiện có cho phép "
                         "(`handshake` luôn có)."),
            responses={200: ok_response(Envelope[ConnectionGuideOut], "Hướng dẫn kết nối", ex.CONNECTION_GUIDE),
                       **error_responses(401, 403, 404, 429, 503)})
async def connection_guide(request: Request, principal: Principal = Depends(require_tool_integration)):
    return ok(await get_container(request).guide.connection_guide(request.app, principal))


@router.get("/openapi.json", summary="OpenAPI rút gọn cho Tool cá nhân",
            description="Chủ client personal. Chỉ gồm endpoint ở connection-guide + components cần thiết.",
            responses={200: {"description": "OpenAPI rút gọn"}, **error_responses(401, 403, 404, 429, 503)})
async def me_openapi(request: Request, principal: Principal = Depends(require_tool_integration)):
    spec = await get_container(request).guide.reduced_openapi(request.app, principal)
    return spec
