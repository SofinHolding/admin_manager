"""`GET /internal/v1/feed` — change feed của Identity (không đi qua gateway)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from identity_service.deps import get_container
from identity_service.internal_auth import require_service_key
from identity_service.outbox import repository as outbox
from identity_service.security import iso

router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False)


@router.get("/feed")
async def feed(request: Request, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
               types: str = Query(""), _reader: str = Depends(require_service_key)) -> dict:
    wanted = [t for t in (x.strip() for x in types.split(",")) if t] or None
    async with get_container(request).db.connection() as conn:
        rows = await outbox.read(conn, after=after, limit=limit + 1, types=wanted)
        head = await outbox.head(conn)
    has_more = len(rows) > limit
    rows = rows[:limit]
    events = [{"seq": r["seq"], "id": r["id"], "type": r["type"], "subject": r["subject"],
               "time": iso(r["created_at"]).replace("+00:00", "Z"), "data_version": r["data_version"],
               "data": r["data"] if isinstance(r["data"], dict) else __import__("json").loads(r["data"])}
              for r in rows]
    return {"events": events, "next_cursor": rows[-1]["seq"] if rows else after, "has_more": has_more, "head": head}
