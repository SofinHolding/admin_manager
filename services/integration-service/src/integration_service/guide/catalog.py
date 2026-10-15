"""Danh mục TƯỜNG MINH endpoint mặt phẳng MÁY ↔ scope (nguồn cho `GET /v1/integrations/me/connection-guide` và
`/me/openapi.json`). `tests/test_guide_catalog.py` đối chiếu danh mục này với route table thật của FastAPI
(mọi route `tags=["machine"]`) để đảm bảo KHÔNG route nào bị thiếu.

`scope=None` nghĩa là route này KHÔNG yêu cầu scope cụ thể ngoài xác thực cơ bản (hiện không có route nào như vậy —
mọi route máy đều gắn 1 scope qua `machine(scope)`/`delegated(scope, ...)`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from integration_service import examples as ex


@dataclass(frozen=True)
class EndpointDoc:
    method: str
    path: str
    scope: str
    summary: str
    description: str
    example_request: dict | None = None
    example_response: dict | None = None
    error_codes: list[str] = field(default_factory=list)


CATALOG: tuple[EndpointDoc, ...] = (
    EndpointDoc("POST", "/v1/integrations/handshake", "integration:handshake", "Handshake của Tool",
               "Khai báo thiết bị + capabilities; lần đầu BIND credential vào thiết bị này (đổi máy = xoay khoá).",
               example_response=ex.HANDSHAKE,
               error_codes=["CREDENTIAL_DEVICE_BOUND", "PLATFORM_NOT_ALLOWED"]),
    EndpointDoc("POST", "/v1/integrations/resources/batch", "resources:write", "Upsert resource hàng loạt",
               "Upsert resource ngoài snapshot. Client dùng chung cần `X-Delegated-Job`.",
               example_response=ex.RESOURCE_BATCH,
               error_codes=["CREDENTIAL_NOT_ACTIVATED", "INSTALLATION_MISMATCH", "SHARED_CLIENT_REQUIRES_DELEGATION",
                            "PLATFORM_NOT_ALLOWED"]),
    EndpointDoc("GET", "/v1/integrations/resources", "jobs:claim", "Danh sách resource",
               "Chỉ `fallback_tool` dùng thực tế (các client khác chỉ thấy resource của chính mình)."),
    EndpointDoc("GET", "/v1/integrations/resource-owners", "jobs:claim", "Danh sách user có resource", ""),
    EndpointDoc("PUT", "/v1/integrations/projects/{project_key}", "resources:write", "Upsert project/chủ đề",
               "Client dùng chung cần `X-Delegated-Job`.", example_response=ex.PROJECT,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots", "snapshots:write", "Tạo snapshot (status=building)",
               "Client dùng chung cần `X-Delegated-Job`.", example_response=ex.SNAPSHOT,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots/{snapshot_id}/resources", "snapshots:write",
               "Gửi batch resource vào snapshot", "", example_response=ex.BATCH,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots/{snapshot_id}/content", "snapshots:write",
               "Gửi batch nội dung vào snapshot", "", example_response=ex.BATCH,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots/{snapshot_id}/events", "snapshots:write",
               "Gửi batch sự kiện vào snapshot", "", example_response=ex.BATCH,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots/{snapshot_id}/metrics", "metrics:write",
               "Gửi batch metric vào snapshot", "", example_response=ex.BATCH,
               error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("POST", "/v1/integrations/snapshots/{snapshot_id}/commit", "snapshots:write", "Commit snapshot",
               "", example_response=ex.COMMIT, error_codes=["SHARED_CLIENT_REQUIRES_DELEGATION"]),
    EndpointDoc("GET", "/v1/integrations/snapshots/{snapshot_id}", "snapshots:write", "Trạng thái snapshot",
               "", example_response=ex.SNAPSHOT_DETAIL),
    EndpointDoc("POST", "/v1/integrations/jobs/claim", "jobs:claim", "Claim job (Fallback Tool)",
               "`installation_id` trong body PHẢI trùng installation đã bind cho credential này.",
               example_response=ex.JOB_CLAIM, error_codes=["INSTALLATION_MISMATCH"]),
    EndpointDoc("POST", "/v1/integrations/jobs/{job_id}/heartbeat", "jobs:claim", "Gia hạn lease job",
               "", example_response=ex.JOB_TRANSITION),
    EndpointDoc("POST", "/v1/integrations/jobs/{job_id}/complete", "jobs:complete", "Hoàn thành job",
               "", example_response=ex.JOB_TRANSITION),
    EndpointDoc("POST", "/v1/integrations/jobs/{job_id}/fail", "jobs:complete", "Báo job thất bại",
               "", example_response=ex.JOB_TRANSITION),
    EndpointDoc("POST", "/v1/integrations/workers/register", "jobs:claim", "Đăng ký worker (Fallback Tool)",
               "", example_response=ex.WORKER_REGISTER),
    EndpointDoc("POST", "/v1/integrations/workers/{worker_id}/heartbeat", "jobs:claim", "Heartbeat worker",
               "", example_response=ex.WORKER_HEARTBEAT),
)
