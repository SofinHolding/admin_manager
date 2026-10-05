"""`GET /v1/integrations/audit` — admin xem Tool nào đã làm gì (không có secret/payload thô)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from manager_sdk import Principal

from integration_service.api_models import AuditPage, Envelope
from integration_service.deps import user_bearer, get_container, require_admin
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok, ok_response

router = APIRouter(prefix="/v1/integrations", tags=["admin"], dependencies=[Depends(user_bearer)])

_FIELDS = ("id", "at", "action", "result", "account_id", "actor_account_id", "client_id", "installation_id", "key_id",
           "snapshot_id", "request_id", "ip", "status_code")


@router.get("/audit", summary="Xem audit (admin)",
            description="`admin`/`dash_admin`. Lọc theo client/account/action/result; phân trang lùi bằng `before_id`.",
            responses={200: ok_response(Envelope[AuditPage], "Các dòng audit mới nhất trước", ex.AUDIT),
                       **error_responses(401, 403, 422, 503)})
async def list_audit(request: Request, client_id: str | None = Query(None, max_length=80),
                     account_id: str | None = Query(None, max_length=80), action: str | None = Query(None, max_length=80),
                     result: str | None = Query(None, max_length=80), before_id: int | None = Query(None, ge=1),
                     limit: int = Query(50, ge=1, le=200), _: Principal = Depends(require_admin)):
    rows = await get_container(request).audit.list(client_id=client_id, account_id=account_id, action=action,
                                                  result=result, before_id=before_id, limit=limit)
    entries = [{k: r[k] for k in _FIELDS} for r in rows]
    return ok({"entries": entries, "next_before_id": rows[-1]["id"] if len(rows) == limit else None})
