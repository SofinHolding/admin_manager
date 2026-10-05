"""Endpoint nội bộ (không qua gateway, KHÔNG nằm trong OpenAPI công khai): feed + canonical export.

Xác thực bằng `X-Service-Key` (mỗi bên đọc một khoá: INTEGRATION_FEED_KEY_<READER>). Sai/thiếu => 401 AUTH_INVALID.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from manager_contracts import ErrorCode
from manager_sdk import ServiceKeyAuth

from integration_service.errors import not_found
from integration_service.feed import canonical
from integration_service.feed import repository as outbox
from integration_service.http_utils import EnvelopeResponse
from integration_service.security import iso


def build_router(service_auth: ServiceKeyAuth) -> APIRouter:
    router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False,
                       dependencies=[Depends(service_auth)])

    def db(request: Request):
        return request.app.state.container.db

    @router.get("/feed")
    async def feed(request: Request, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                   types: str = Query("")) -> JSONResponse:
        wanted = [t for t in (x.strip() for x in types.split(",")) if t] or None
        async with db(request).connection() as conn:
            rows = await outbox.read(conn, after=after, limit=limit + 1, types=wanted)
            head = await outbox.head(conn)
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [{"seq": r["seq"], "id": r["id"], "type": r["type"], "subject": r["subject"],
                   "time": iso(r["created_at"]), "data_version": r["data_version"], "data": r["data"]}
                  for r in rows]
        return EnvelopeResponse(content={"events": events, "next_cursor": rows[-1]["seq"] if rows else after,
                                         "has_more": has_more, "head": head})

    @router.get("/canonical/summary")
    async def canonical_summary(request: Request) -> JSONResponse:
        async with db(request).connection() as conn:
            return EnvelopeResponse(content=await canonical.summary(conn))

    @router.get("/canonical/{kind}")
    async def canonical_export(request: Request, kind: str, after_revision: int = Query(0, ge=0),
                               limit: int = Query(500, ge=1, le=1000)) -> JSONResponse:
        if kind not in canonical.KINDS:
            raise not_found(ErrorCode.NOT_FOUND, "kind phải là một trong: " + ", ".join(canonical.KINDS))
        async with db(request).connection() as conn:
            return EnvelopeResponse(content=await canonical.export(conn, kind, after_revision, limit))

    return router
