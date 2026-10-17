"""Gom kênh theo user cho trang quản trị "Kênh theo người dùng" — để đối chiếu với những gì Tool dùng chung nhìn thấy.

Tool dùng chung (`fallback_tool`) đọc `GET /v1/integrations/resources` rồi chỉ giữ kênh `execution_mode == fallback_tool`, chưa xoá, không phải
nhóm (`system_api.shared_view`); một user chỉ xuất hiện ở Tool khi còn ≥1 kênh như vậy. `shared_tool_visible` mô phỏng đúng quy tắc đó trên từng
kênh để admin so hai bên mà không phải suy luận.
"""
from __future__ import annotations

from datetime import datetime

SHARED_MODE = "fallback_tool"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def shared_tool_visible(row: dict) -> bool:
    return row["execution_mode"] == SHARED_MODE and not row["removed"] and row["resource_type"] != "group"


def channel_view(row: dict) -> dict:
    return {
        "id": row["id"], "platform": row["platform"], "resource_type": row["resource_type"], "external_id": row["external_id"],
        "display_name": row["display_name"], "url": row["url"], "origin": row["origin"], "execution_mode": row["execution_mode"],
        "enabled": row["state"] == "active", "removed": bool(row["removed"]), "shared_tool_visible": shared_tool_visible(row),
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "last_job_status": row["last_job_status"], "last_job_at": _iso(row["last_job_at"]),
        "content_count": int(row["content_count"] or 0), "last_collected_at": _iso(row["last_observed_at"]),
    }


def build_user_channels(rows: list[dict]) -> dict:
    """`{users: [{owner_account_id, username, channels: [...], totals}], summary}`; user sắp theo tên, kênh còn hoạt động trước kênh đã xoá."""
    users: dict[str, dict] = {}
    for row in rows:
        oid = row["owner_account_id"]
        u = users.setdefault(oid, {"owner_account_id": oid, "username": row["owner_username"] or oid, "channels": []})
        u["channels"].append(channel_view(row))
    for u in users.values():
        live = [c for c in u["channels"] if not c["removed"]]
        u["totals"] = {
            "active": len(live),
            "removed": len(u["channels"]) - len(live),
            "shared_tool": sum(1 for c in live if c["shared_tool_visible"]),
            "personal_tool": sum(1 for c in live if c["execution_mode"] == "primary_tool"),
        }
        u["visible_in_shared_tool"] = u["totals"]["shared_tool"] > 0
    ordered = sorted(users.values(), key=lambda u: (u["username"].lower(), u["owner_account_id"]))
    return {"users": ordered, "summary": {
        "users": len(ordered), "visible_in_shared_tool": sum(1 for u in ordered if u["visible_in_shared_tool"]),
        "active_channels": sum(u["totals"]["active"] for u in ordered), "shared_tool_channels": sum(u["totals"]["shared_tool"] for u in ordered),
        "removed_channels": sum(u["totals"]["removed"] for u in ordered)}}
