"""Schema đăng ký / heartbeat Shared Collector worker (PHASE 4, nguồn cho OpenAPI).

Chưa có bảng lưu trữ bền vững (xem `workers/router.py`): các model này chỉ mô tả hợp đồng HTTP, không ánh xạ
tới bảng DB nào."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

WorkerId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
Capability = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Platform = Annotated[str, StringConstraints(min_length=1, max_length=32)]


class WorkerRegister(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "worker_id": "collector-01", "capabilities": ["data.collect"], "platforms": ["tiktok", "facebook"]}})
    worker_id: WorkerId
    capabilities: list[Capability] = Field(default_factory=list, max_length=20)
    platforms: list[Platform] = Field(default_factory=list, max_length=20)


class WorkerRegisterOut(BaseModel):
    registration_id: str
    heartbeat_interval_s: int


class WorkerHeartbeat(BaseModel):
    """Chưa mang dữ liệu nghiệp vụ nào (reserved cho thống kê trong tương lai) — body `{}` là đủ."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {}})


class WorkerHeartbeatOut(BaseModel):
    ok: bool
    next_heartbeat_in_s: int
