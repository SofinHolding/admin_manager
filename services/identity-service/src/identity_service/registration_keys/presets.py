"""Gói quyền (preset) trên invite key — nguồn sự thật DUY NHẤT cho role + capability khi admin tạo/sửa mã mời.

Mỗi preset ánh xạ 1-1 sang `{role, capabilities}`: khi key mang preset, đăng ký dùng key đó cấp đúng
`role` và capability_grant `{capabilities, override_defaults: true}` tương ứng (thay thế default theo role).
Xem docs/architecture/capability-model-v1.md (mục preset).
"""

from __future__ import annotations

from dataclasses import dataclass

from identity_service.permissions.capabilities import (
    ALL_CAPABILITIES,
    CAP_DASHBOARD_ACCESS,
    CAP_MANUAL_CHANNEL_MGMT,
    CAP_TOOL_INTEGRATION,
    CAP_WEB_ACCESS,
)


@dataclass(frozen=True)
class Preset:
    role: str
    label: str
    capabilities: tuple[str, ...]
    admin_only: bool = False   # chỉ role `admin` đúng nghĩa mới được gán preset này vào key


PRESETS: dict[str, Preset] = {
    "viewer": Preset(role="viewer", label="Người xem",
                     capabilities=(CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT)),
    "viewer_tool": Preset(role="viewer", label="Người xem + Tool riêng",
                          capabilities=(CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT,
                                        CAP_TOOL_INTEGRATION)),
    "dash_admin": Preset(role="dash_admin", label="Quản lý Dashboard",
                         capabilities=(CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS)),
    "admin": Preset(role="admin", label="Quản trị viên",
                    capabilities=tuple(c for c in ALL_CAPABILITIES if c != CAP_TOOL_INTEGRATION),
                    admin_only=True),
    "discord": Preset(role="discord", label="Vận hành Discord", capabilities=()),
}


def get(name: str) -> Preset | None:
    return PRESETS.get(name)


def list_presets() -> list[dict]:
    """Hình dạng trả về cho `GET /v1/admin/keys/presets` (UI hiển thị)."""
    return [{"key": key, "label": p.label, "role": p.role, "capabilities": list(p.capabilities),
             "admin_only": p.admin_only} for key, p in PRESETS.items()]
