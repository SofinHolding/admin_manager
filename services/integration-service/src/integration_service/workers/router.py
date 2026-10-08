"""API đăng ký / heartbeat Shared Collector worker (PHASE 4, mặt phẳng MÁY — xem
docs/architecture/shared-collector-design-v1.md §7).

CHỈ IN-MEMORY: chưa có bảng lưu trữ bền vững nào cho worker. `register` xác thực credential (`client_kind=
fallback_tool`, scope `jobs:claim`) rồi sinh `registration_id` ngẫu nhiên + trả cấu hình heartbeat; `heartbeat` chỉ
xác thực lại rồi trả `ok`. Theo dõi worker bền vững (miss heartbeat > 3 lần => release job về hàng đợi) là việc
TƯƠNG LAI — hiện tại Shared Collector dựa vào lease TTL sẵn có của job (xem `jobs/service.py`) để tự phục hồi khi
worker chết giữa chừng, không phụ thuộc heartbeat này."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from manager_contracts import ErrorCode

from integration_service import examples as ex
from integration_service.api_models import Envelope
from integration_service.credentials.service import MachineContext
from integration_service.deps import machine
from integration_service.errors import forbidden
from integration_service.http_utils import error_responses, ok, ok_response
from integration_service.security import new_id
from integration_service.workers import schemas

router = APIRouter(prefix="/v1/integrations/workers", tags=["machine"])

HEARTBEAT_INTERVAL_S = 30


def _require_fallback_tool(ctx: MachineContext) -> MachineContext:
    """Scope `jobs:claim` đã mặc định chỉ cấp cho `fallback_tool` (xem `clients/policy.py`), nhưng kiểm thêm
    `client_kind` tại đây để không phụ thuộc hoàn toàn vào việc admin không lỡ cấp scope này cho loại client khác."""
    if ctx.client_kind != "fallback_tool":
        raise forbidden(ErrorCode.SCOPE_DENIED, "Chỉ fallback_tool mới đăng ký worker")
    return ctx


@router.post("/register", summary="Đăng ký Shared Collector worker",
             description=("Mặt phẳng MÁY, scope `jobs:claim` + `client_kind=fallback_tool`. IN-MEMORY: chưa lưu bền "
                          "vững, chỉ xác thực credential rồi sinh `registration_id` mới + cấu hình heartbeat. Gọi lại "
                          "nhiều lần (vd worker khởi động lại) không lỗi: mỗi lần trả `registration_id` mới."),
             responses={200: ok_response(Envelope[schemas.WorkerRegisterOut], "Đã đăng ký", ex.WORKER_REGISTER),
                        **error_responses(400, 401, 403, 422, 429, 503)})
async def register(body: schemas.WorkerRegister, ctx: MachineContext = Depends(machine("jobs:claim"))):
    _require_fallback_tool(ctx)
    return ok({"registration_id": new_id("wkr"), "heartbeat_interval_s": HEARTBEAT_INTERVAL_S})


@router.post("/{worker_id}/heartbeat", summary="Heartbeat Shared Collector worker",
             description=("Mặt phẳng MÁY, scope `jobs:claim` + `client_kind=fallback_tool`. IN-MEMORY: chỉ xác thực "
                          "lại credential rồi trả `ok` + khoảng cách heartbeat kế tiếp; chưa theo dõi miss heartbeat "
                          "(không cần đã `register` trước — mỗi lần gọi độc lập, không có trạng thái liên hệ)."),
             responses={200: ok_response(Envelope[schemas.WorkerHeartbeatOut], "OK", ex.WORKER_HEARTBEAT),
                        **error_responses(400, 401, 403, 422, 429, 503)})
async def heartbeat(worker_id: str, body: schemas.WorkerHeartbeat,
                    ctx: MachineContext = Depends(machine("jobs:claim"))):
    _require_fallback_tool(ctx)
    return ok({"ok": True, "next_heartbeat_in_s": HEARTBEAT_INTERVAL_S})
