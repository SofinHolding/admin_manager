"""Capability = permission với namespace `capability:*` (docs/architecture/capability-model-v1.md).

Tái dùng catalog `permissions` + bảng `account_permissions` — KHÔNG có bảng riêng. Module này chỉ giữ
danh sách tên capability và mapping default theo role, dùng khi đăng ký (`auth/service.py`) và seed một
lần cho tài khoản cũ (migration `0005_capabilities.sql`).
"""

from __future__ import annotations

CAPABILITY_PREFIX = "capability:"

CAP_WEB_ACCESS = "capability:web_access"
CAP_DASHBOARD_ACCESS = "capability:dashboard_access"
CAP_MANUAL_CHANNEL_MGMT = "capability:manual_channel_mgmt"
CAP_TOOL_INTEGRATION = "capability:tool_integration"
CAP_API_WRITE = "capability:api_write"
CAP_API_READ = "capability:api_read"
CAP_MULTI_TOOL = "capability:multi_tool"

ALL_CAPABILITIES = (CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT, CAP_TOOL_INTEGRATION,
                    CAP_API_WRITE, CAP_API_READ, CAP_MULTI_TOOL)

# Mục 3 của design spec: capability mặc định cấp lúc đăng ký theo role.
DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "viewer": (CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS),
    "dash_admin": (CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS),
    "admin": ALL_CAPABILITIES,
    "discord": (),
}


def default_for_role(role: str) -> tuple[str, ...]:
    return DEFAULT_CAPABILITIES.get(role, ())
