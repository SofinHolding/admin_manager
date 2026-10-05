"""`PUT /v1/integrations/projects/{project_key}` — Tool khai báo chủ đề/nhóm; server tự upsert (mặt phẳng MÁY)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from integration_service.api_models import Envelope, ProjectUpsertOut
from integration_service.credentials.service import MachineContext
from integration_service.deps import get_container, machine
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok_response
from integration_service.projects.service import ProjectUpsert

router = APIRouter(prefix="/v1/integrations", tags=["machine"])


@router.put("/projects/{project_key}", summary="Upsert project/chủ đề",
            description=("Scope `resources:write`. `project_key` dạng `^[a-z][a-z0-9_]{0,63}$`, phạm vi theo owner (suy từ "
                         "API key). Tạo nếu chưa có, cập nhật nếu có — không có bước Admin mapping. Hỗ trợ `Idempotency-Key`."),
            responses={200: ok_response(Envelope[ProjectUpsertOut], "Project sau upsert", ex.PROJECT),
                       **error_responses(400, 401, 403, 409, 422, 429, 503)})
async def put_project(request: Request, body: ProjectUpsert,
                      project_key: str = Path(..., pattern=r"^[a-z][a-z0-9_]{0,63}$"),
                      ctx: MachineContext = Depends(machine("resources:write"))):
    c = get_container(request)

    async def work(conn, extra):
        return await c.projects.upsert(conn, ctx, project_key, body)

    return await c.runner.write(request, ctx, action="project.upsert", work=work)
