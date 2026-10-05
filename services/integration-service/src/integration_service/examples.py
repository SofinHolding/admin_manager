"""Ví dụ phản hồi cho OpenAPI (chỉ dùng để tài liệu hoá; không ảnh hưởng runtime)."""

from __future__ import annotations

_T = "2026-10-05T10:00:00Z"
_ACC = "3f2a9c1e5b7d4e8f9a0b1c2d3e4f5a6b"
_CLI = "cli_9c1f0a7e3b5d4c2a8e6f1d0b9a7c5e3f"
_KEY = "AbCdEfGhIjKl"
_SNP = "snp_9c1f0a7e3b5d4c2a8e6f1d0b9a7c5e3f"

CLIENT = {"client_id": _CLI, "owner_account_id": _ACC, "tool_name": "My Upload Tool", "client_kind": "primary_tool",
          "status": "active", "contract_version": "2026-10-01", "allowed_platforms": ["facebook", "youtube"],
          "allowed_scopes": ["integration:handshake", "metrics:write", "resources:write", "snapshots:write"],
          "created_from": "api", "created_at": _T, "updated_at": _T}
CLIENT_DETAIL = {**CLIENT, "installations": [{"installation_id": "ins_0123456789abcdef0123456789abcdef", "device_id": "device-abc",
                                              "hostname": "PC-01", "os": "windows", "app_version": "2.4.0",
                                              "capabilities": ["snapshots.push"], "status": "active",
                                              "first_seen_at": _T, "last_seen_at": _T}],
                 "credentials": [{"key_id": _KEY, "scopes": ["snapshots:write"], "status": "active", "label": "pc-01",
                                  "expires_at": None, "last_used_at": _T, "created_at": _T, "revoked_at": None}]}
CREDENTIAL_ISSUED = {"client_id": _CLI, "key_id": _KEY, "api_key": "mpk_" + _KEY + ".<43 ký tự secret, chỉ hiện một lần>",
                     "scopes": ["integration:handshake", "snapshots:write"], "expires_at": "2027-01-03T10:00:00Z",
                     "contract_version": "2026-10-01", "allowed_platforms": ["facebook"]}
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
