"""Chính sách đăng ký kèm Integration nằm trên registration key (architecture-assessment-v2.md mục 2.3).

Hai việc, đều thuần và CỤC BỘ (không gọi service khác):
  * `normalize_policy`: admin tạo key => kiểm định dạng chính sách;
  * `plan_registration`: user đăng ký => kiểm `requested ⊆ policy`, trả bản kế hoạch provisioning hoặc ném lỗi ĐỒNG BỘ.
    Gọi TRƯỚC khi tiêu hao key / tạo account nên sai thì không có tác dụng phụ nào.
"""

from __future__ import annotations

import re
import secrets

from identity_service.errors import DomainError

CLIENT_KINDS = ("primary_tool", "fallback_tool", "service")
DEFAULT_SCOPES = ("integration:handshake", "resources:write", "snapshots:write", "metrics:write")
FALLBACK_SCOPES = ("jobs:claim", "jobs:complete")
# Không bao giờ cấp qua đăng ký: chỉ admin cấp cho fallback_tool (integration-api-v1.md §1).
NEVER_AT_REGISTRATION = frozenset({"jobs:claim:system"})
_PLATFORM = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_SCOPE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)+$")
_TOOL_NAME = re.compile(r"^[\w .\-]{1,64}$")
_APP = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CAPABILITY = re.compile(r"^capability:[a-z][a-z0-9_]*$")
MAX_LIST = 32


def _err(status: int, message: str, code: str) -> DomainError:
    return DomainError(status, message, code=code)


def _str_list(value: object, pattern: re.Pattern, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise _err(422, f"{label} phải là danh sách (tối đa {MAX_LIST})", "REQUEST_VALIDATION_FAILED")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not pattern.match(item):
            raise _err(422, f"{label} chứa giá trị không hợp lệ", "REQUEST_VALIDATION_FAILED")
        if item not in out:
            out.append(item)
    return out


def normalize_policy(raw: dict | None) -> dict | None:
    """Chuẩn hoá chính sách khi admin tạo key. None => key không cho đăng ký kèm integration."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _err(422, "integration_policy phải là object", "REQUEST_VALIDATION_FAILED")
    unknown = set(raw) - {"allow_integration_registration", "default_client_kind", "allowed_platforms",
                          "allowed_scopes", "max_integration_clients", "contract_version"}
    if unknown:
        raise _err(422, "integration_policy có trường không được hỗ trợ", "REQUEST_VALIDATION_FAILED")
    kind = raw.get("default_client_kind", "primary_tool")
    if kind not in CLIENT_KINDS:
        raise _err(422, "default_client_kind không hợp lệ", "REQUEST_VALIDATION_FAILED")
    scopes = _str_list(raw.get("allowed_scopes", list(DEFAULT_SCOPES)), _SCOPE, "allowed_scopes")
    if any(s in NEVER_AT_REGISTRATION for s in scopes):
        raise _err(403, "Scope này không thể cấp qua đăng ký", "SCOPE_DENIED")
    maxc = raw.get("max_integration_clients", 1)
    if isinstance(maxc, bool) or not isinstance(maxc, int) or not 0 <= maxc <= 100:
        raise _err(422, "max_integration_clients phải là số nguyên 0..100", "REQUEST_VALIDATION_FAILED")
    version = raw.get("contract_version", "2026-10-01")
    if not isinstance(version, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", version):
        raise _err(422, "contract_version phải có dạng YYYY-MM-DD", "REQUEST_VALIDATION_FAILED")
    return {"allow_integration_registration": bool(raw.get("allow_integration_registration", False)),
            "default_client_kind": kind,
            "allowed_platforms": _str_list(raw.get("allowed_platforms", []), _PLATFORM, "allowed_platforms"),
            "allowed_scopes": scopes, "max_integration_clients": maxc, "contract_version": version}


def normalize_apps(raw: object) -> list[str]:
    return _str_list(raw if raw is not None else [], _APP, "allowed_apps")


def normalize_capability_grant(raw: dict | None) -> dict | None:
    """Chuẩn hoá `capability_grant` khi admin tạo key (docs/architecture/capability-model-v1.md §4).

    None => key không cấp thêm gì ngoài default theo role. `override_defaults: true` => đăng ký chỉ nhận
    đúng danh sách `capabilities` (bỏ qua default của role) — dùng cho role đặc biệt như `discord`.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _err(422, "capability_grant phải là object", "REQUEST_VALIDATION_FAILED")
    unknown = set(raw) - {"capabilities", "override_defaults"}
    if unknown:
        raise _err(422, "capability_grant có trường không được hỗ trợ", "REQUEST_VALIDATION_FAILED")
    caps = _str_list(raw.get("capabilities", []), _CAPABILITY, "capabilities")
    return {"capabilities": caps, "override_defaults": bool(raw.get("override_defaults", False))}


def plan_registration(policy: dict | None, req: dict) -> dict:
    """Kế hoạch provisioning từ yêu cầu của user. Ném DomainError (403/422 kèm `code`) nếu vượt chính sách."""
    if not policy or not policy.get("allow_integration_registration"):
        raise _err(403, "Mã mời này không cho phép đăng ký kèm integration", "INTEGRATION_NOT_ALLOWED")
    tool_name = (req.get("tool_name") or "").strip()
    if not _TOOL_NAME.match(tool_name):
        raise _err(422, "tool_name không hợp lệ", "REQUEST_VALIDATION_FAILED")
    kind = req.get("client_kind") or policy.get("default_client_kind") or "primary_tool"
    if kind not in CLIENT_KINDS:
        raise _err(422, "client_kind không hợp lệ", "REQUEST_VALIDATION_FAILED")
    platforms = _str_list([str(p).strip().lower() if isinstance(p, str) else p for p in req.get("platforms") or []],
                          _PLATFORM, "platforms")
    allowed_platforms = policy.get("allowed_platforms") or []
    if any(p not in allowed_platforms for p in platforms):
        raise _err(403, "Platform không nằm trong chính sách của mã mời", "PLATFORM_NOT_ALLOWED")
    if int(policy.get("max_integration_clients", 1)) < 1:  # đăng ký tạo đúng 1 client
        raise _err(403, "Mã mời không cho phép tạo thêm integration client", "INTEGRATION_LIMIT_EXCEEDED")

    allowed_scopes = policy.get("allowed_scopes") or []
    explicit = req.get("scopes")
    if explicit is not None:
        scopes = _str_list(explicit, _SCOPE, "scopes")
        if any(s in NEVER_AT_REGISTRATION or s not in allowed_scopes for s in scopes):
            raise _err(403, "Scope vượt chính sách của mã mời", "SCOPE_DENIED")
    else:
        wanted = [*DEFAULT_SCOPES, *(FALLBACK_SCOPES if kind == "fallback_tool" else ())]
        scopes = [s for s in wanted if s in allowed_scopes]
        if "integration:handshake" not in scopes:
            raise _err(403, "Chính sách mã mời không cấp đủ scope tối thiểu", "SCOPE_DENIED")
    plan = {"client_id": "cli_" + secrets.token_hex(12), "tool_name": tool_name, "client_kind": kind,
            "platforms": platforms, "scopes": scopes}
    if policy.get("contract_version"):
        plan["contract_version"] = policy["contract_version"]
    return plan
