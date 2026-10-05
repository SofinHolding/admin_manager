"""Endpoints `/v1/ops/*` — Admin/ADD (role `admin`/`dash_admin`) và Tool B (Local Fallback Tool)
dùng chung, theo đặc tả `tasks/backlog/local-operations-hub-v1.md`.

Phân quyền (mục 20 đặc tả):
- `dash_admin`/`admin`: đọc inventory TOÀN HỆ THỐNG (mọi user), filter/chọn, tạo/huỷ batch.
- CHỈ `admin`: sửa platform policy, đổi `execution_mode` của resource, xử lý duplicate/conflict.

Route đổi `execution_mode` (`PATCH /resources/{id}/execution-mode`) KHÔNG nằm trong danh sách API
gốc (mục 19) nhưng bắt buộc phải có để thực thi quy tắc 6 (mục 6) — route user
(`resources.py`) cố tình không cho sửa field này.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.ops")

_OPS_ROLES = ("admin", "dash_admin")
_ADMIN_ROLE = "admin"


class CreateBatchBody(BaseModel):
    platform: str = Field(..., min_length=1, max_length=50)
    name: str = Field("", max_length=200)


class AddItemsBody(BaseModel):
    resource_ids: list[str] = Field(..., min_length=1, max_length=1000)


class UpdatePolicyBody(BaseModel):
    web_entry_enabled: bool | None = None
    fallback_enabled: bool | None = None
    adapter_status: str | None = Field(None, pattern=r"^(none|planned|ready)$")


class SetExecutionModeBody(BaseModel):
    execution_mode: str = Field(..., pattern=r"^(primary_tool|fallback_local|disabled)$")


def make_router(db: Db) -> APIRouter:
    router = APIRouter(prefix="/v1/ops", tags=["ops"])

    async def require_ops(authorization: str = Header("")) -> dict:
        """T1 — chỉ verify JWT + role, KHÔNG tra DB account suspended (khớp mẫu `viewer.py`).
        Đủ cho route đọc; route ghi nhạy cảm (`execution-mode`, `platform-policies` PATCH) thêm
        `require_admin` chặt hơn."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        payload = _decode_access_token(authorization[7:])
        if payload.get("role") not in _OPS_ROLES:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ admin/dash_admin được truy cập")
        return payload

    Ops = Depends(require_ops)

    async def require_admin(authorization: str = Header("")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        from auth import _decode_access_token
        payload = _decode_access_token(authorization[7:])
        if payload.get("role") != _ADMIN_ROLE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ admin được thao tác này")
        return payload

    Admin = Depends(require_admin)

    async def _enrich_owner(rows: list[dict]) -> list[dict]:
        """Gắn `owner_username` vào mỗi dòng — Tool B/Ops UI PHẢI hiển thị rõ owner (tiêu chí
        nghiệm thu #4)."""
        if not rows:
            return rows
        owners = await db.list_inventory_owners()
        name_by_id = {o["id"]: o["username"] for o in owners}
        return [{**r, "owner_username": name_by_id.get(r["owner_account_id"], "")} for r in rows]

    # ── Inventory (đọc toàn hệ thống) ────────────────────────────────────────────

    @router.get("/inventory", summary="Toàn bộ resource user đã khai báo — filter được")
    async def inventory(
        owner_account_id: str = Query(""), platform: str = Query(""),
        source_instance_id: str = Query(""), country: str = Query(""),
        origin: str = Query(""), mapping_status: str = Query(""),
        execution_mode: str = Query(""), verification_status: str = Query(""),
        enabled: bool | None = Query(None), q: str = Query(""),
        payload: dict = Ops,
    ) -> dict:
        filters = {
            "platform": platform, "source_instance_id": source_instance_id,
            "country": country, "origin": origin, "mapping_status": mapping_status,
            "execution_mode": execution_mode, "verification_status": verification_status,
            "enabled": enabled, "q": q,
        }
        rows = await db.list_managed_resources(owner_account_id or None, filters)
        return {"items": await _enrich_owner(rows)}

    @router.get("/inventory/revision",
               summary="Revision inventory — Tool B so sánh để biết có cần tải lại không")
    async def inventory_revision(owner_account_id: str = Query(""), payload: dict = Ops) -> dict:
        rev = await db.inventory_revision(owner_account_id or None)
        return {"revision": rev}

    @router.get("/users", summary="Danh sách user đang có resource (cho filter dropdown)")
    async def ops_users(payload: dict = Ops) -> dict:
        return {"items": await db.list_inventory_owners()}

    @router.get("/platforms", summary="Danh sách platform + policy hiện tại")
    async def ops_platforms(payload: dict = Ops) -> dict:
        return {"items": await db.list_platform_policies()}

    @router.get("/sources", summary="Toàn bộ Source của mọi user")
    async def ops_sources(owner_account_id: str = Query(""), payload: dict = Ops) -> dict:
        rows = await db.list_resource_sources(owner_account_id or None)
        return {"items": await _enrich_owner(rows)}

    # ── Run Queue ─────────────────────────────────────────────────────────────────

    @router.post("/batches", summary="Tạo batch mới — 1 batch nên cùng 1 platform")
    async def create_batch(body: CreateBatchBody, payload: dict = Ops) -> dict:
        return await db.create_operation_batch(payload["sub"], body.platform, body.name.strip())

    @router.get("/batches", summary="Danh sách batch")
    async def list_batches(mine_only: bool = Query(False), payload: dict = Ops) -> dict:
        created_by = payload["sub"] if mine_only else None
        return {"items": await db.list_operation_batches(created_by)}

    @router.get("/batches/{batch_id}", summary="Chi tiết batch + toàn bộ item")
    async def get_batch(batch_id: str, payload: dict = Ops) -> dict:
        batch = await db.get_operation_batch(batch_id)
        if not batch:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy batch")
        items = await db.list_operation_items(batch_id)
        return {**batch, "items": items}

    @router.post("/batches/{batch_id}/items",
                summary="Thêm resource vào Run Queue — resource không thoả điều kiện bị SKIP kèm lý do")
    async def add_items(batch_id: str, body: AddItemsBody, payload: dict = Ops) -> dict:
        batch = await db.get_operation_batch(batch_id)
        if not batch:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy batch")
        return await db.add_operation_items(batch_id, body.resource_ids)

    @router.patch("/items/{item_id}/cancel", summary="Huỷ 1 item trong queue")
    async def cancel_item(item_id: str, payload: dict = Ops) -> dict:
        ok = await db.cancel_operation_item(item_id, payload["sub"])
        if not ok:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Item không tồn tại hoặc đã ở trạng thái cuối (success/failed/cancelled)")
        return {"ok": True}

    # ── Platform Policy (CHỈ admin sửa — mục 20 đặc tả) ──────────────────────────

    @router.get("/platform-policies", summary="Danh sách policy platform")
    async def list_policies(payload: dict = Ops) -> dict:
        return {"items": await db.list_platform_policies()}

    @router.patch("/platform-policies/{platform}", summary="Sửa policy platform (chỉ admin)")
    async def update_policy(platform: str, body: UpdatePolicyBody, payload: dict = Admin) -> dict:
        return await db.upsert_platform_policy(
            platform, body.web_entry_enabled, body.fallback_enabled, body.adapter_status,
            payload["sub"])

    # ── Execution mode (CHỈ admin — không có trong mục 19, bắt buộc theo mục 6) ──

    @router.patch("/resources/{resource_id}/execution-mode",
                summary="Đổi execution_mode của 1 resource (chỉ admin, có audit log)")
    async def set_execution_mode(resource_id: str, body: SetExecutionModeBody,
                                 payload: dict = Admin) -> dict:
        res = await db.get_managed_resource(resource_id)
        if not res:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy resource")
        ok = await db.update_managed_resource(
            resource_id, {"execution_mode": body.execution_mode}, payload["sub"])
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không cập nhật được")
        return {"ok": True}

    return router
