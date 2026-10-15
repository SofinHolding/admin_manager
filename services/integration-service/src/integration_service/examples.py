"""Ví dụ phản hồi cho OpenAPI (chỉ dùng để tài liệu hoá; không ảnh hưởng runtime)."""

from __future__ import annotations

_T = "2026-10-05T10:00:00Z"
_ACC = "3f2a9c1e5b7d4e8f9a0b1c2d3e4f5a6b"
_CLI = "cli_9c1f0a7e3b5d4c2a8e6f1d0b9a7c5e3f"
_KEY = "AbCdEfGhIjKl"
_SNP = "snp_9c1f0a7e3b5d4c2a8e6f1d0b9a7c5e3f"

CLIENT = {"client_id": _CLI, "owner_account_id": _ACC, "tool_name": "Tool cá nhân", "client_kind": "primary_tool",
          "status": "active", "ownership": "personal", "purpose": "", "contract_version": "2026-10-01",
          "allowed_platforms": ["facebook", "youtube"],
          "allowed_scopes": ["integration:handshake", "metrics:write", "resources:write", "snapshots:write"],
          "created_from": "provisioning", "configured_at": None, "activated_at": _T, "activated_by": None,
          "created_at": _T, "updated_at": _T}
CLIENT_DRAFT = {"client_id": _CLI, "owner_account_id": _ACC, "tool_name": "Fallback Collector",
                "client_kind": "fallback_tool", "status": "draft", "ownership": "shared",
                "purpose": "Thu thập dữ liệu hộ user khi primary tool lỗi", "contract_version": "2026-10-01",
                "allowed_platforms": [], "allowed_scopes": [], "created_from": "api", "configured_at": None,
                "activated_at": None, "activated_by": None, "created_at": _T, "updated_at": _T}
CLIENT_DETAIL = {**CLIENT, "installations": [{"installation_id": "ins_0123456789abcdef0123456789abcdef", "device_id": "device-abc",
                                              "hostname": "PC-01", "os": "windows", "app_version": "2.4.0",
                                              "capabilities": ["snapshots.push"], "status": "active",
                                              "first_seen_at": _T, "last_seen_at": _T}],
                 "credentials": [{"key_id": _KEY, "scopes": ["snapshots:write"], "status": "active", "label": "pc-01",
                                  "expires_at": None, "last_used_at": _T, "created_at": _T, "revoked_at": None,
                                  "reveal_state": None, "reveal_expires_at": None, "revealed_at": None,
                                  "bound_device_id": "device-abc", "bound_installation_id": "ins_0123456789abcdef0123456789abcdef",
                                  "activated_at": _T}]}
CREDENTIAL_ISSUED = {"client_id": _CLI, "key_id": _KEY, "api_key": "mpk_" + _KEY + ".<43 ký tự secret, chỉ hiện một lần>",
                     "scopes": ["integration:handshake", "snapshots:write"], "expires_at": "2027-01-03T10:00:00Z",
                     "contract_version": "2026-10-01", "allowed_platforms": ["facebook"], "reveal_state": None}
CREDENTIAL_ISSUED_PENDING = {"client_id": _CLI, "key_id": _KEY, "api_key": None,
                             "scopes": ["integration:handshake", "snapshots:write"], "expires_at": None,
                             "contract_version": "2026-10-01", "allowed_platforms": ["facebook"],
                             "reveal_state": "pending"}
CREDENTIAL_ROTATED = {**CREDENTIAL_ISSUED, "previous_key_id": "ZyXwVuTsRqPo", "previous_key_expires_at": _T}
CREDENTIAL_REVOKED = {"client_id": _CLI, "key_id": _KEY, "status": "revoked", "already_revoked": False}
HANDSHAKE = {"client_id": _CLI, "installation_id": "ins_0123456789abcdef0123456789abcdef", "installation_created": True,
             "owner_account_id": _ACC, "accepted_contract_version": "2026-10-01", "allowed_platforms": ["facebook"],
             "allowed_scopes": ["integration:handshake", "metrics:write", "resources:write", "snapshots:write"],
             "allowed_capabilities": ["metrics.push", "resources.push", "snapshots.push"],
             "accepted_capabilities": ["resources.push", "snapshots.push"], "server_time": _T}
PROJECT = {"created": True, "changed": True, "project": {
    "project_key": "itl", "name": "ITL", "platform": "facebook", "country_code": "BR", "enabled": True,
    "metadata": {"campaign": "q4"}, "revision": 12, "updated_at": _T}}
BATCH = {"received": 3, "created": 1, "updated": 1, "unchanged": 0, "stale_ignored": 0, "rejected": 1,
         "rejections": [{"index": 2, "external_id": "p9", "code": "RECORD_VALIDATION_FAILED",
                         "message": "metrics.views: int_type"}]}
RESOURCE_BATCH = {**BATCH, "accepted": 2, "items": [
    {"external_id": "123456789", "resource_id": "res_0123456789abcdef0123456789abcdef", "status": "created"},
    {"external_id": "987654321", "resource_id": "res_fedcba9876543210fedcba9876543210", "status": "updated"}]}
SNAPSHOT = {"snapshot_id": _SNP, "status": "building", "platform": "facebook", "snapshot_type": "full",
            "observed_at": _T, "report_timezone": "Asia/Ho_Chi_Minh",
            "scope": {"resources": True, "content_window": {"from": "2026-09-05", "to": "2026-10-05"}},
            "expected_counts": {"resources": 1, "content": 2}, "is_current": False, "created_at": _T,
            "updated_at": _T, "committed_at": None, "expires_at": "2026-10-05T16:00:00Z"}
SNAPSHOT_DETAIL = {**SNAPSHOT, "staged_counts": {"resources": 1, "content": 1, "metrics": 0, "events": 0},
                   "rejected_counts": {"resources": 0, "content": 1, "metrics": 0, "events": 0}, "rejected_total": 1,
                   "rejections": [{"kind": "content", "idx": 1, "external_id": "p9", "code": "METRIC_UNKNOWN",
                                   "message": "Khoá metric chưa có trong registry"}],
                   "accept_partial": False, "result": None, "failure_reason": None, "revision_from": None,
                   "revision_to": None}
COMMIT = {"snapshot_id": _SNP, "status": "committed", "totals": {"resources": 1, "content": 2, "metrics": 0, "events": 0},
          "removed": {"resources": 0, "content": 1, "metrics": 0, "events": 0}, "rejected_total": 0, "as_of": _T,
          "committed_at": _T, "revision_from": 41, "revision_to": 44, "is_current": True,
          "stats": {"resources": {"created": 0, "updated": 1, "unchanged": 0, "stale_ignored": 0},
                    "content": {"created": 1, "updated": 1, "unchanged": 0, "stale_ignored": 0}},
          "warnings": []}
AUDIT = {"entries": [{"id": 101, "at": _T, "action": "snapshot.commit", "result": "ok", "account_id": _ACC,
                      "actor_account_id": None, "client_id": _CLI, "installation_id": None, "key_id": _KEY,
                      "snapshot_id": _SNP, "request_id": "req_0123456789abcdef0123456789abcdef", "ip": "10.0.0.1",
                      "status_code": None}], "next_before_id": 101}
REGISTRY_ENTRY = {"key": "threads", "display_name": "Threads", "enabled": True, "description": None}
RESOURCE_STATE = {"resource_id": "res_0123456789abcdef0123456789abcdef", "state": "disabled", "revision": 57}

_JOB = "job_0123456789abcdef0123456789abcdef"
_RES = "res_0123456789abcdef0123456789abcdef"
JOB = {"job_id": _JOB, "owner_account_id": _ACC, "resource_id": _RES, "platform": "facebook", "kind": "resource.sync",
       "status": "pending", "priority": 0, "attempts": 0, "max_attempts": 3, "payload": {"mode": "refresh"},
       "required_capability": "", "resource_owner_id": None, "run_after": _T, "lease_until": None,
       "claimed_by_client": None, "result": None,
       "last_error_code": None, "last_error_message": None, "created_at": _T, "updated_at": _T, "completed_at": None,
       "created": True}
JOB_CLAIM = {"jobs": [{"job_id": _JOB, "owner_account_id": _ACC, "kind": "resource.sync", "platform": "facebook",
                       "payload": {"mode": "refresh"}, "attempt": 1, "max_attempts": 3, "lease_until": "2026-10-05T10:05:00Z",
                       "lease_token": "0123456789abcdef0123456789abcdef",
                       "resource": {"resource_id": _RES, "platform": "facebook", "resource_type": "page",
                                    "external_id": "123456789", "display_name": "Example Page", "username": "example",
                                    "url": None}}], "server_time": _T}
JOB_TRANSITION = {"job_id": _JOB, "status": "done", "attempts": 1, "max_attempts": 3, "run_after": None,
                  "lease_until": None, "completed_at": _T, "replayed": False}
WORKER_REGISTER = {"registration_id": "wkr_9c1f0a7e3b5d4c2a8e6f1d0b9a7c5e3f", "heartbeat_interval_s": 30}
WORKER_HEARTBEAT = {"ok": True, "next_heartbeat_in_s": 30}

CONNECTION_GUIDE = {
    "base_url": "https://api.manager.example.com", "contract_version": "2026-10-01",
    "auth": {"header": "Authorization", "format": "Bearer mpk_<key_id>.<secret>"},
    "required_headers": [
        {"name": "Authorization", "required_when": "luôn", "description": "mpk_<key_id>.<secret>"},
        {"name": "X-Contract-Version", "required_when": "khuyến nghị", "description": "vd 2026-10-01"},
        {"name": "X-Installation-Id", "required_when": "mọi endpoint máy trừ /handshake", "description": "installation_id trả về lúc handshake"},
        {"name": "Idempotency-Key", "required_when": "mọi POST/PUT ghi", "description": "8-128 ký tự [A-Za-z0-9_.:-]"}],
    "client": CLIENT,
    "credential": {"key_id": _KEY, "status": "active", "reveal_state": "revealed", "reveal_expires_at": None,
                   "bound_device_id": "device-abc", "bound_installation_id": "ins_0123456789abcdef0123456789abcdef",
                   "activated_at": _T, "last_used_at": _T, "expires_at": None,
                   "scopes": ["integration:handshake", "snapshots:write"]},
    "installations": [], "endpoints": [{
        "method": "POST", "path": "/v1/integrations/handshake", "scope": "integration:handshake",
        "summary": "Handshake", "description": "Khai báo thiết bị, bind credential.",
        "request_schema": None, "example_request": None, "example_response": HANDSHAKE,
        "error_codes": ["CREDENTIAL_DEVICE_BOUND"]}],
    "limits": {"batch_max_items": 500, "body_max_bytes": 2 * 1024 * 1024},
    "error_codes": [{"code": "CREDENTIAL_NOT_ACTIVATED", "http_status": 403, "meaning": "Chưa handshake/bind thiết bị"}],
    "recent_snapshots": []}
CLIENT_TRANSFERRED = {**CLIENT, "owner_account_id": "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d"}
