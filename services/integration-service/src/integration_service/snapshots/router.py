"""Snapshot + nhận dữ liệu theo batch + commit (mặt phẳng MÁY, namespace chung `/v1/integrations/*`)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from manager_contracts import ErrorCode

from integration_service.api_models import (BatchResult, CommitOut, Envelope, SnapshotDetailOut, SnapshotOut)
from integration_service.credentials.service import MachineContext
from integration_service.delegation import delegated
from integration_service.deps import get_container
from integration_service import examples as ex
from integration_service.errors import not_found
from integration_service.http_utils import (EnvelopeResponse, envelope, error_responses, ok_response, parse_json_object,
                                            parse_model, raw_body_doc)
from integration_service.ingest import schemas, validate

router = APIRouter(prefix="/v1/integrations", tags=["machine"])

_SNAPSHOT_ID = re.compile(r"^snp_[0-9a-f]{32}$")
_BATCH_DOC = ("Tối đa 500 bản ghi (`BATCH_TOO_LARGE`), body ≤ 2 MiB (413, hỗ trợ gzip). Mỗi bản ghi sai nằm trong `rejections` "
              "(không bỏ sót im lặng); trường `owner_*` => 422 `RECORD_VALIDATION_FAILED`. Giá trị metric là SỐ TUYỆT ĐỐI "
              "(ghi đè, không cộng); `null` = chưa thu thập, `0` = đã thu thập và bằng 0. Hỗ trợ `Idempotency-Key`. "
              "Client dùng chung (`ownership=shared`) BẮT BUỘC kèm `X-Delegated-Job` nếu không => 403 "
              "`SHARED_CLIENT_REQUIRES_DELEGATION`.")

_SNAPSHOT_EXAMPLE = {"platform": "facebook", "snapshot_type": "full", "observed_at": "2026-10-05T06:55:00Z",
                     "report_timezone": "Asia/Ho_Chi_Minh",
                     "scope": {"resources": True,
                               "content_window": {"from": "2026-09-05", "to": "2026-10-05"}},
                     "expected_counts": {"resources": 1, "content": 2}}
_CONTENT_EXAMPLE = {"items": [{"external_id": "p1", "resource_external_id": "123456789", "published_date": "2026-10-04",
                               "metrics": {"views": 1200, "reactions": 35, "comments": None},
                               "observed_at": "2026-10-05T06:55:00Z"}]}


def _check_id(snapshot_id: str) -> str:
    if not _SNAPSHOT_ID.match(snapshot_id):
        raise not_found(ErrorCode.SNAPSHOT_NOT_FOUND, "Không tìm thấy snapshot")
    return snapshot_id



@router.post("/snapshots", status_code=201, summary="Tạo snapshot (status=building)",
             description=("Scope `snapshots:write`. `snapshot_type=full` + `scope` khai báo \"tập này ĐẦY ĐỦ\" cho phạm vi; "
                          "bản ghi thuộc phạm vi nhưng vắng mặt sẽ bị đánh dấu `removed` sau commit. `expected_counts` được kiểm "
                          "lúc commit. Đổi `report_timezone` giữa các snapshot của cùng Tool => `TIMEZONE_CHANGED`."),
             responses={201: ok_response(Envelope[SnapshotOut], "Snapshot đang building", ex.SNAPSHOT),
                        **error_responses(400, 401, 403, 404, 409, 422, 429, 503)})
async def create_snapshot(request: Request, body: schemas.SnapshotCreate,
                          ctx: MachineContext = Depends(delegated("snapshots:write", require_for_shared=True))):
    c = get_container(request)

    async def work(conn, extra):
        data = await c.snapshots.create(conn, ctx, body, ctx.installation_id)
        extra["snapshot_id"] = data["snapshot_id"]
        return data

    return await c.runner.write(request, ctx, action="snapshot.create", work=work, status=201)


def _batch_route(kind: str, schema_name: str, scope: str, example: dict | None, summary: str):
    @router.post(f"/snapshots/{{snapshot_id}}/{kind}", summary=summary, description=f"Scope `{scope}`. " + _BATCH_DOC,
                 openapi_extra=raw_body_doc(schema_name, example),
                 responses={200: ok_response(Envelope[BatchResult], "Kết quả theo bản ghi", ex.BATCH),
                            **error_responses(400, 401, 403, 404, 409, 413, 422, 429, 503)},
                 name=f"snapshot_{kind}")
    async def handler(request: Request, snapshot_id: str,
                      ctx: MachineContext = Depends(delegated(scope, require_for_shared=True))):
        c = get_container(request)
        _check_id(snapshot_id)
        raw = await request.body()

        async def work(conn, extra):
            items = validate.extract_items(parse_json_object(raw))
            return await c.snapshots.stage(conn, ctx, snapshot_id, kind, items, extra)

        return await c.runner.write(request, ctx, action=f"snapshot.{kind}", work=work)

    return handler


_batch_route("resources", "ResourceBatch", "snapshots:write", None, "Gửi batch resource (kênh/page)")
_batch_route("content", "ContentBatch", "snapshots:write", _CONTENT_EXAMPLE, "Gửi batch bài đăng/video + metric cộng dồn")
_batch_route("events", "EventBatch", "snapshots:write", None, "Gửi batch sự kiện (vd đăng bài thành công)")
_batch_route("metrics", "MetricsBatch", "metrics:write", None, "Gửi batch metric theo ngày của kênh")


@router.post("/snapshots/{snapshot_id}/commit", summary="Commit snapshot (atomic)",
             description=("Scope `snapshots:write`. MỘT transaction: kiểm `expected_counts`/rejections (lệch => 422 "
                          "`SNAPSHOT_INCOMPLETE`, không đổi gì), merge staging -> canonical (observed_at mới hơn thắng), suy "
                          "luận `removed`, gán `revision`, ghi outbox `snapshot.committed`, lật con trỏ `current`. Commit lần 2 "
                          "=> 409 `SNAPSHOT_ALREADY_COMMITTED`. Body tuỳ chọn `{accept_partial}`; `Idempotency-Key` tuỳ chọn."),
             openapi_extra=raw_body_doc("CommitRequest", {"accept_partial": False}),
             responses={200: ok_response(Envelope[CommitOut], "Đã commit", ex.COMMIT),
                        **error_responses(400, 401, 403, 404, 409, 413, 422, 429, 503)})
async def commit_snapshot(request: Request, snapshot_id: str,
                          ctx: MachineContext = Depends(delegated("snapshots:write", require_for_shared=True))):
    c = get_container(request)
    _check_id(snapshot_id)
    raw = await request.body()
    guard = c.runner.guard(request, ctx, raw)
    async with c.audit.scope(request, "snapshot.commit", **c.runner.audit_fields(ctx)) as extra:
        req = parse_model(schemas.CommitRequest, parse_json_object(raw) if raw.strip() else {})
        status, body, replayed = await c.snapshots.commit(ctx, snapshot_id, req, guard, extra)
    return EnvelopeResponse(status_code=status, content=body,
                            headers={"Idempotent-Replayed": "true"} if replayed else None)


@router.get("/snapshots/{snapshot_id}", summary="Trạng thái snapshot",
            description="Scope `snapshots:write`. Trạng thái, đếm staging, rejections (tối đa 100), kết quả commit.",
            responses={200: ok_response(Envelope[SnapshotDetailOut], "Chi tiết snapshot", ex.SNAPSHOT_DETAIL),
                       **error_responses(401, 403, 404, 429, 503)})
async def get_snapshot(request: Request, snapshot_id: str, ctx: MachineContext = Depends(delegated("snapshots:write"))):
    _check_id(snapshot_id)
    return EnvelopeResponse(content=envelope(await get_container(request).snapshots.get(ctx, snapshot_id)))

