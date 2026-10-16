"""Dịch HAI CHIỀU giữa mô hình canonical và hình dạng legacy của admin-server (Anti-Corruption Layer).

Phần legacy KHÔNG có chỗ trong schema canonical (`input_ref`, `mapping_status`, `verification_status`, `resource_type` thô,
`canonical_external_id`) nằm trong `data.extensions.legacy` của resource — vẫn là dữ liệu canonical hợp lệ (extensions do
hợp đồng ingest cho phép) nên Analytics/Dashboard đọc cùng một read model; chỉ ACL hiểu các khoá này.

Không hardcode tên Tool/platform: platform là dữ liệu (policy), `client_id` ảo `legacy_web` chỉ đánh dấu "nhập từ web".
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

LEGACY_CLIENT = "legacy_web"       # client_id ảo của resource nhập từ website (không phải một Tool)
ORIGIN_WEB = "web_manual"           # giá trị `origin` của legacy; Tool ghi `tool` (mặc định của canonical)
LEGACY_NS = "legacy"                # khoá trong data.extensions

# execution_mode: legacy dùng `fallback_local`, canonical (integration-api-v1.md §4) dùng `fallback_tool`.
MODE_TO_CANONICAL = {"fallback_local": "fallback_tool", "primary_tool": "primary_tool", "disabled": "disabled"}
MODE_TO_LEGACY = {v: k for k, v in MODE_TO_CANONICAL.items()}

# Trạng thái item của Run Queue legacy suy ra từ job canonical.
JOB_TO_ITEM_STATUS = {"pending": "queued", "leased": "running", "done": "success", "failed": "failed",
                      "dead": "failed", "cancelled": "cancelled"}
ITEM_TERMINAL = ("success", "failed", "cancelled")

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_EXTERNAL_ID = 200


def legacy_iso(value: datetime | None) -> str:
    """Định dạng thời gian của legacy: `isoformat(timespec="seconds")` UTC (vd 2026-10-05T10:00:00+00:00)."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_key(raw: str, fallback: str = "unspecified") -> str:
    """Tên tự do của legacy -> khoá canonical `^[a-z][a-z0-9_]{0,63}$` (rỗng/không hợp lệ -> `fallback`)."""
    text = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")[:64]
    if text and text[0].isdigit():
        text = "n_" + text
    return text if _KEY.match(text) else fallback


def external_id_of(input_ref: str) -> str:
    """Resource nhập tay chưa có id thật của nền tảng: dùng `input_ref` làm khoá tự nhiên (rút gọn bằng hash khi quá dài)."""
    ref = input_ref.strip()
    if len(ref) <= MAX_EXTERNAL_ID:
        return ref
    return "sha256:" + hashlib.sha256(ref.encode()).hexdigest()


def legacy_ext(row: dict) -> dict:
    return ((row.get("data") or {}).get("extensions") or {}).get(LEGACY_NS) or {}


def country_text(data: dict) -> str:
    country = data.get("country") or {}
    return country.get("name") or country.get("code") or ""


def resource_to_legacy(row: dict) -> dict:
    """Hàng `resources` canonical -> dict managed_resource của legacy (cùng khoá, cùng kiểu)."""
    data = row.get("data") or {}
    ext = legacy_ext(row)
    return {
        "id": row["id"], "owner_account_id": row["owner_account_id"], "platform": row["platform"],
        "resource_type": ext.get("resource_type", row["resource_type"] if row["resource_type"] != "unspecified" else ""),
        "canonical_external_id": ext.get("canonical_external_id", ""),
        "input_ref": ext.get("input_ref", row["external_id"]),
        "username": data.get("username") or "", "url": data.get("url") or "",
        "display_name": data.get("display_name") or "", "country": country_text(data),
        "origin": row["origin"], "mapping_status": ext.get("mapping_status", "unmapped"),
        "execution_mode": MODE_TO_LEGACY.get(row["execution_mode"], row["execution_mode"]),
        "verification_status": ext.get("verification_status", "unverified"),
        "enabled": row["state"] == "active",
        "created_at": legacy_iso(row["created_at"]), "updated_at": legacy_iso(row["updated_at"]),
    }


def source_to_legacy(row: dict) -> dict:
    return {"id": row["id"], "owner_account_id": row["owner_account_id"], "name": row["name"],
            "platform": row["platform"], "project_key": row["project_key"], "default_country": row["default_country"],
            "origin": row["origin"], "created_at": legacy_iso(row["created_at"]),
            "updated_at": legacy_iso(row["updated_at"])}


def policy_to_legacy(row: dict) -> dict:
    # `display_name` là trường CỘNG THÊM (client legacy bỏ qua): tên hiển thị lấy từ Platform Registry.
    return {"platform": row["platform"], "display_name": row.get("display_name") or row["platform"],
            "web_entry_enabled": row["web_entry_enabled"],
            "fallback_enabled": row["fallback_enabled"], "adapter_status": row["adapter_status"],
            "updated_by": row["updated_by"], "updated_at": legacy_iso(row["updated_at"])}


def batch_to_legacy(row: dict) -> dict:
    return {"id": row["id"], "created_by": row["created_by"], "platform": row["platform"], "name": row["name"],
            "status": row["status"], "created_at": legacy_iso(row["created_at"]),
            "updated_at": legacy_iso(row["updated_at"])}


def item_to_legacy(row: dict) -> dict:
    """Item của Run Queue. Có job canonical gắn kèm thì trạng thái/attempts/lỗi lấy từ job (nguồn sự thật duy nhất)."""
    status, attempts, last_error, updated = row["status"], row["attempts"], row["last_error"], row["updated_at"]
    if row.get("job_id"):
        status = JOB_TO_ITEM_STATUS.get(row["job_status"], status)
        attempts = row["job_attempts"]
        last_error = row["job_last_error"] or ""
        updated = max(updated, row["job_updated_at"])
    return {"id": row["id"], "batch_id": row["batch_id"], "resource_id": row["resource_id"],
            "source_assignment_id": row["source_assignment_id"], "owner_account_id": row["owner_account_id"],
            "platform": row["platform"], "status": status, "priority": row["priority"], "attempts": attempts,
            "last_error": last_error, "created_at": legacy_iso(row["created_at"]), "updated_at": legacy_iso(updated)}
