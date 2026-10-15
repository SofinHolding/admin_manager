"""Policy scope/client_kind (hợp đồng: integration-api-v1.md §2)."""

from __future__ import annotations

CLIENT_KINDS = ("primary_tool", "fallback_tool", "service")
SYSTEM_SCOPE = "jobs:claim:system"
BASE_SCOPES = ("integration:handshake", "resources:write", "snapshots:write", "metrics:write")
FALLBACK_SCOPES = ("jobs:claim", "jobs:complete")
ALL_SCOPES = frozenset(BASE_SCOPES + FALLBACK_SCOPES + (SYSTEM_SCOPE,))


def ownership_for_kind(client_kind: str) -> str:
    """personal <=> primary_tool (tự provision); shared <=> fallback_tool/service (wizard admin)."""
    return "personal" if client_kind == "primary_tool" else "shared"


CLIENT_STATUSES = ("draft", "pending_email_verification", "active", "suspended", "deleted")


def default_scopes(client_kind: str) -> list[str]:
    scopes = list(BASE_SCOPES)
    if client_kind == "fallback_tool":
        scopes += list(FALLBACK_SCOPES)
    return scopes


def clamp_scopes(requested: list[str] | None, client_kind: str) -> list[str]:
    """Cắt scope theo policy: chỉ giữ scope thường hợp lệ của loại client; KHÔNG BAO GIỜ cho scope hệ thống qua đường này."""
    allowed = set(default_scopes(client_kind))
    if requested is None:
        return default_scopes(client_kind)
    return [s for s in default_scopes(client_kind) if s in set(requested) and s in allowed]
