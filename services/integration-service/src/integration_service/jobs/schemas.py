"""Schema API của jobs (nguồn cho OpenAPI). Mọi body `extra="forbid"`: trường `owner_*` bị từ chối (job do server gán owner)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from integration_service.ingest.schemas import Key

JobStatus = Literal["pending", "leased", "done", "failed", "dead", "cancelled"]
JobKind = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
Capability = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ErrorCodeStr = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:\-]{1,64}$")]


class JobCreate(BaseModel):
    """Mặt phẳng USER. Owner = chủ resource (user thường chỉ tạo cho resource của mình); platform lấy từ resource."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "resource_id": "res_0123456789abcdef0123456789abcdef", "kind": "resource.sync", "priority": 0,
        "payload": {"mode": "refresh"}, "dedupe_key": "refresh-2026-10-05"}})
    resource_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    kind: JobKind = "resource.sync"
    priority: Annotated[StrictInt, Field(ge=-100, le=100)] = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    required_capability: Annotated[str, StringConstraints(max_length=64)] = ""
    dedupe_key: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    max_attempts: Annotated[StrictInt, Field(ge=1, le=20)] | None = None


class ResourceRef(BaseModel):
    resource_id: str
    platform: str
    resource_type: str
    external_id: str
    display_name: str | None = None
    username: str | None = None
    url: str | None = None


class JobOut(BaseModel):
    job_id: str
    owner_account_id: str
    resource_id: str | None
    platform: str
    kind: str
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    required_capability: str
    resource_owner_id: str | None = None
    run_after: datetime
    lease_until: datetime | None
    claimed_by_client: str | None
    result: dict[str, Any] | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class JobCreated(JobOut):
    created: bool = True


class JobPage(BaseModel):
    jobs: list[JobOut]
    next_offset: int | None = None


class JobClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "installation_id": "ins_0123456789abcdef0123456789abcdef", "platforms": ["facebook"],
        "capabilities": ["jobs.pull"], "limit": 10, "lease_seconds": 300}})
    installation_id: Annotated[str, StringConstraints(pattern=r"^ins_[0-9a-f]{32}$")]
    platforms: list[Key] = Field(default_factory=list, max_length=20)
    capabilities: list[Capability] = Field(default_factory=list, max_length=50)
    limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20
    lease_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 300


class ClaimedJob(BaseModel):
    job_id: str
    owner_account_id: str
    kind: str
    platform: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    lease_until: datetime
    lease_token: str
    resource: ResourceRef | None = None


class JobClaimOut(BaseModel):
    jobs: list[ClaimedJob]
    server_time: datetime


class JobCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "result": {"fetched": 120}, "lease_token": "0123456789abcdef0123456789abcdef"}})
    result: dict[str, Any] = Field(default_factory=dict)
    lease_token: Annotated[str, StringConstraints(max_length=64)] | None = None


class JobFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "error_code": "TARGET_UNREACHABLE", "message": "Timeout khi mở trang", "retryable": True}})
    error_code: ErrorCodeStr
    message: Annotated[str, StringConstraints(max_length=500)] = ""
    retryable: StrictBool = True
    lease_token: Annotated[str, StringConstraints(max_length=64)] | None = None


class JobHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"lease_seconds": 300}})
    lease_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 300
    lease_token: Annotated[str, StringConstraints(max_length=64)] | None = None


class JobTransitionOut(BaseModel):
    job_id: str
    status: JobStatus
    attempts: int
    max_attempts: int
    run_after: datetime | None = None
    lease_until: datetime | None = None
    completed_at: datetime | None = None
    replayed: bool = False
