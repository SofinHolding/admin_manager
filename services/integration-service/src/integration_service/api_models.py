"""Schema request/response của API công khai (nguồn cho OpenAPI). Phản hồi thật được dựng thủ công rồi bọc envelope
`{ok, data, meta}`; các model này tồn tại để TÀI LIỆU hoá và để test contract so khớp với bản OpenAPI đóng băng."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from integration_service.http_utils import OkMeta
from integration_service.ingest.schemas import Key

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    ok: bool = True
    data: T
    meta: OkMeta


# ── Client / credential (mặt phẳng USER) ────────────────────────────────────────────

ClientKind = Literal["primary_tool", "fallback_tool", "service"]
ClientStatus = Literal["draft", "pending_email_verification", "active", "suspended", "deleted"]
Ownership = Literal["personal", "shared"]


class ClientWizardCreate(BaseModel):
    """Bước 1 wizard (CHỈ admin). Client tạo ở `draft`; platforms/scopes gán ở bước `PUT .../config`."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "tool_name": "Fallback Collector", "client_kind": "fallback_tool",
        "purpose": "Thu thập dữ liệu hộ user khi primary tool lỗi", "platforms": ["facebook"]}})
    tool_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    client_kind: Literal["fallback_tool", "service"]
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    platforms: list[Key] = Field(default_factory=list, max_length=20)


class ClientConfigure(BaseModel):
    """Bước 2 wizard (CHỈ admin, chỉ khi client `draft`)."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "allowed_platforms": ["facebook"], "allowed_scopes": ["integration:handshake", "resources:write",
        "snapshots:write", "metrics:write", "jobs:claim", "jobs:complete"]}})
    allowed_platforms: list[Key] = Field(min_length=1, max_length=20)
    allowed_scopes: list[Annotated[str, StringConstraints(max_length=64)]] = Field(min_length=1, max_length=20)


class ClientActivate(BaseModel):
    """Bước 3 wizard (CHỈ admin): `confirm_tool_name` phải khớp `tool_name` — chống kích hoạt nhầm."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"confirm_tool_name": "Fallback Collector"}})
    confirm_tool_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ClientTransferRequest(BaseModel):
    """Chuyển chủ client PERSONAL (CHỈ admin)."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"new_owner_account_id": "acc_example"}})
    new_owner_account_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ClientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"status": "suspended"}})
    status: Literal["active", "suspended", "deleted"] | None = None
    allowed_scopes: list[Annotated[str, StringConstraints(max_length=64)]] | None = Field(default=None, max_length=20)
    allowed_platforms: list[Key] | None = Field(default=None, max_length=20)
    tool_name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None


class CredentialSummaryOut(BaseModel):
    """Tóm tắt credential trong `GET /clients` (list) — không có scopes/label/created_at đầy đủ."""
    key_id: str
    status: Literal["active", "revoked"]
    reveal_state: Literal["pending", "revealed", "expired"] | None = None
    bound_device_id: str | None = None
    activated_at: datetime | None = None
    last_used_at: datetime | None
    expires_at: datetime | None


class ClientOut(BaseModel):
    client_id: str
    owner_account_id: str
    tool_name: str
    client_kind: ClientKind
    status: ClientStatus
    ownership: Ownership
    purpose: str
    contract_version: str
    allowed_platforms: list[str]
    allowed_scopes: list[str]
    created_from: Literal["api", "provisioning"]
    configured_at: datetime | None = None
    activated_at: datetime | None = None
    activated_by: str | None = None
    created_at: datetime
    updated_at: datetime
    owner_username: str | None = None
    activated_by_username: str | None = None     # username của admin kích hoạt (thay vì lộ account_id)
    credentials: list[CredentialSummaryOut] | None = None


class CredentialInfo(BaseModel):
    key_id: str
    scopes: list[str]
    status: Literal["active", "revoked"]
    label: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None = None
    reveal_state: Literal["pending", "revealed", "expired"] | None = None
    reveal_expires_at: datetime | None = None
    revealed_at: datetime | None = None
    bound_device_id: str | None = None
    bound_installation_id: str | None = None
    activated_at: datetime | None = None


class InstallationInfoOut(BaseModel):
    installation_id: str
    device_id: str
    hostname: str
    os: str
    app_version: str
    capabilities: list[str]
    status: str
    first_seen_at: datetime
    last_seen_at: datetime


class ClientDetailOut(ClientOut):
    installations: list[InstallationInfoOut]
    credentials: list[CredentialInfo]


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "scopes": ["integration:handshake", "snapshots:write"], "expires_in_days": 90, "label": "pc-01"}})
    scopes: list[Annotated[str, StringConstraints(max_length=64)]] | None = Field(default=None, max_length=20)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    label: Annotated[str, StringConstraints(max_length=100)] = ""


class RotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"grace_seconds": 300}})
    grace_seconds: int = Field(default=0, ge=0, le=86400)


class CredentialIssued(BaseModel):
    """`api_key` CHỈ xuất hiện khi CHỦ (hoặc máy, lúc reveal) tự nhận plaintext. Admin cấp/xoay HỘ trên client
    personal => `api_key=null`, `reveal_state="pending"` (chủ phải gọi `/me/credential/reveal`)."""
    client_id: str
    key_id: str
    api_key: str | None = None
    scopes: list[str]
    expires_at: datetime | None
    contract_version: str
    allowed_platforms: list[str]
    reveal_state: Literal["pending", "revealed", "expired"] | None = None
    previous_key_id: str | None = None
    previous_key_expires_at: datetime | None = None


class CredentialRevoked(BaseModel):
    client_id: str
    key_id: str
    status: Literal["revoked"]
    already_revoked: bool


# ── Handshake / resource (mặt phẳng MÁY) ────────────────────────────────────────────

class HandshakeOut(BaseModel):
    client_id: str
    installation_id: str
    installation_created: bool
    owner_account_id: str
    accepted_contract_version: str
    allowed_platforms: list[str]
    allowed_scopes: list[str]
    allowed_capabilities: list[str]
    accepted_capabilities: list[str]
    server_time: datetime


class Rejection(BaseModel):
    index: int
    external_id: str | None = None
    code: str
    message: str


class BatchResult(BaseModel):
    received: int
    created: int
    updated: int
    unchanged: int
    stale_ignored: int
    rejected: int
    rejections: list[Rejection]


class ResourceUpsertItemOut(BaseModel):
    external_id: str
    resource_id: str
    status: Literal["created", "updated", "unchanged", "stale_ignored"]


class ResourceBatchOut(BatchResult):
    accepted: int
    items: list[ResourceUpsertItemOut]


# ── Snapshot ────────────────────────────────────────────────────────────────────────

SnapshotStatus = Literal["building", "committing", "committed", "failed", "abandoned"]


class SnapshotOut(BaseModel):
    snapshot_id: str
    status: SnapshotStatus
    platform: str
    snapshot_type: Literal["full", "delta"]
    observed_at: datetime
    report_timezone: str
    scope: dict[str, Any] | None
    expected_counts: dict[str, int]
    is_current: bool
    created_at: datetime
    updated_at: datetime
    committed_at: datetime | None
    expires_at: datetime | None = None


class SnapshotDetailOut(SnapshotOut):
    staged_counts: dict[str, int]
    rejected_counts: dict[str, int]
    rejected_total: int
    rejections: list[dict[str, Any]]
    accept_partial: bool
    result: dict[str, Any] | None
    failure_reason: str | None
    revision_from: int | None
    revision_to: int | None


class CommitOut(BaseModel):
    snapshot_id: str
    status: Literal["committed"]
    totals: dict[str, int]
    removed: dict[str, int]
    rejected_total: int
    as_of: datetime
    committed_at: datetime
    revision_from: int
    revision_to: int
    is_current: bool
    stats: dict[str, dict[str, int]]
    warnings: list[dict[str, Any]]


# ── Admin / audit ───────────────────────────────────────────────────────────────────

class PlatformPut(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"display_name": "Threads", "enabled": True}})
    display_name: Annotated[str, StringConstraints(max_length=100)] = ""
    enabled: bool = True


class MetricPut(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "display_name": "Saves", "description": "Lượt lưu", "enabled": True}})
    display_name: Annotated[str, StringConstraints(max_length=100)] = ""
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    enabled: bool = True


class PlatformCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "key": "tiktok", "display_name": "TikTok", "aliases": ["tiktok_vn", "douyin"]}})
    key: Key
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    aliases: list[Key] = Field(default_factory=list, max_length=20)


class AliasCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"alias": "tiktok_vn"}})
    alias: Key


class RegistryEntry(BaseModel):
    key: str
    display_name: str
    enabled: bool
    description: str | None = None
    aliases: list[str] | None = None    # mã phụ của nền tảng (chỉ có ở Platform Registry)
    codes: list[str] | None = None      # = [key, *aliases]: mọi mã Tool được gửi lên (chỉ ở GET /platforms của máy)


class ResourceStatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"state": "disabled"}})
    state: Literal["active", "disabled"]


class ResourceStateOut(BaseModel):
    resource_id: str
    state: Literal["active", "disabled"]
    revision: int


class AuditEntry(BaseModel):
    id: int
    at: datetime
    action: str
    result: str
    account_id: str | None
    actor_account_id: str | None
    client_id: str | None
    installation_id: str | None
    key_id: str | None
    snapshot_id: str | None
    request_id: str
    ip: str
    status_code: int | None


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next_before_id: int | None


# ── Connection Guide (mặt phẳng USER `/v1/integrations/me/*`) ───────────────────────

class ConnectionGuideAuth(BaseModel):
    header: Literal["Authorization"]
    format: str


class ConnectionGuideHeader(BaseModel):
    name: str
    required_when: str
    description: str


class ConnectionGuideEndpoint(BaseModel):
    method: str
    path: str
    scope: str
    summary: str
    description: str
    request_schema: dict | None = None
    example_request: dict | None = None
    example_response: dict | None = None
    error_codes: list[str] = Field(default_factory=list)


class ConnectionGuideErrorCode(BaseModel):
    code: str
    http_status: int
    meaning: str


class ConnectionGuideLimits(BaseModel):
    batch_max_items: int
    body_max_bytes: int


class ConnectionGuideOut(BaseModel):
    """`GET /v1/integrations/me/connection-guide` — tự mô tả cách gọi API cho CHỦ client personal (chỉ endpoint mà
    scope của credential hiện có cho phép; `handshake` luôn có)."""
    base_url: str | None
    contract_version: str
    auth: ConnectionGuideAuth
    required_headers: list[ConnectionGuideHeader]
    client: ClientOut
    credential: CredentialInfo | None
    installations: list[InstallationInfoOut]
    endpoints: list[ConnectionGuideEndpoint]
    limits: ConnectionGuideLimits
    error_codes: list[ConnectionGuideErrorCode]
    recent_snapshots: list[dict]

