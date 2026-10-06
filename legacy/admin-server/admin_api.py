"""Endpoints quản trị — chỉ admin (role='admin') mới gọi được.

Quản lý invite key và tài khoản qua giao diện web thay vì chạy SQL tay.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.manage")


class CreateKeyBody(BaseModel):
    # 'discord' thêm cho module reward-service (dùng chung accounts.role, cột text tự do —
    # xem services/reward-service/store/migrations/001_reward_init.sql). register() dùng nguyên role
    # của key (auth.py) nên tài khoản tạo ra có role='discord' ngay, không cần bước cấp quyền
    # riêng qua reward-service nữa. 'dash_admin' = role Admin Dashboard (ADD) — cộng thêm, KHÔNG
    # thay thế 'admin' (giữ nguyên mọi quyền cũ).
    role: str = Field("viewer", pattern=r"^(viewer|admin|discord|dash_admin)$")
    label: str = Field("", max_length=200)
    max_uses: int = Field(1, ge=1, le=1000)
    expires_days: int = Field(7, ge=1, le=365)


class SetStatusBody(BaseModel):
    status: str = Field(..., pattern=r"^(active|suspended)$")


class UpdateUserBody(BaseModel):
    """Admin chỉnh sửa thông tin user — tất cả field đều optional."""
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr | None = None
    role: str | None = Field(None, pattern=r"^(viewer|admin|dash_admin)$")


class CountryAliasBody(BaseModel):
    """1 dòng chuẩn hoá quốc gia — `alias` là biến thể Tool tự gõ tay (vd "BR"/"brazil"),
    `canonical` là tên chuẩn duy nhất muốn gộp về (vd "Brazil")."""
    alias: str = Field(..., min_length=1, max_length=100)
    canonical: str = Field(..., min_length=1, max_length=100)


class CanonicalBody(BaseModel):
    """Tên quốc gia chuẩn — người dùng tự tạo/sửa/xoá qua UI. `country-aliases` ở trên PHẢI chọn
    `canonical` khớp 1 tên trong danh sách này (dropdown, không gõ tay tự do) để tránh lệch
    hoa/thường tạo ra 1 nhóm trùng (vd "brazil" khác "Brazil")."""
    name: str = Field(..., min_length=1, max_length=100)


def make_router(db: Db, decode_token, cache) -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

    async def require_admin(authorization: str = Header("")) -> dict:
        """role='admin' hoặc 'dash_admin' mới qua — viewer gọi vào đây sẽ bị 403.

        'dash_admin' (Admin Dashboard/ADD) cộng thêm quyền quản lý user/invite-key giống 'admin' cũ
        — không tách quyền, xem `context/RULES.md`."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        payload = decode_token(authorization[7:])
        if payload.get("role") not in ("admin", "dash_admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Chỉ quản trị viên mới có quyền này")
        return payload

    Admin = Depends(require_admin)

    # ── Invite keys ────────────────────────────────────────────────────────────

    @router.post("/keys", summary="Tạo invite key mới")
    async def create_key(body: CreateKeyBody, admin: dict = Admin) -> dict:
        # Chỉ 'admin' thật mới được sinh key cấp quyền 'admin' — chặn dash_admin tự leo thang
        # bằng cách tạo tài khoản admin mới qua invite key (dash_admin vẫn tạo được key
        # viewer/discord/dash_admin bình thường).
        if body.role == "admin" and admin.get("role") != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Chỉ quản trị viên (admin) mới được cấp quyền admin")
        row = await db.create_invite_key(
            role=body.role,
            label=body.label,
            max_uses=body.max_uses,
            expires_days=body.expires_days,
        )
        logger.info("Tao invite key %s role=%s max=%d expires=%dd",
                     row["key"], body.role, body.max_uses, body.expires_days)
        return row

    @router.get("/keys", summary="Danh sách invite keys")
    async def list_keys(_: dict = Admin) -> dict:
        rows = await db.list_invite_keys()
        return {"keys": rows}

    @router.delete("/keys/{key}", summary="Thu hồi invite key")
    async def revoke_key(key: str, _: dict = Admin) -> dict:
        ok = await db.revoke_invite_key(key)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Key không tồn tại")
        logger.info("Thu hoi invite key %s", key)
        return {"ok": True}

    # ── Accounts ───────────────────────────────────────────────────────────────

    @router.get("/users", summary="Danh sách tài khoản")
    async def list_users(_: dict = Admin) -> dict:
        rows = await db.list_accounts()
        return {"users": rows}

    @router.patch("/users/{user_id}/status", summary="Khoá / mở khoá tài khoản")
    async def set_user_status(user_id: str, body: SetStatusBody,
                              admin: dict = Admin) -> dict:
        # Không cho admin tự khoá chính mình
        if user_id == admin.get("sub"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Không thể khoá chính mình")
        ok = await db.set_account_status(user_id, body.status)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Doi status user %s -> %s", user_id, body.status)
        return {"ok": True, "status": body.status}

    @router.patch("/users/{user_id}", summary="Admin chỉnh sửa thông tin user")
    async def update_user(user_id: str, body: UpdateUserBody, admin: dict = Admin) -> dict:
        updates: dict[str, str] = {}
        if body.username is not None:
            # Kiểm trùng username
            existing = await db.get_account_by_username(body.username)
            if existing and existing["id"] != user_id:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Tên đăng nhập đã được sử dụng")
            updates["username"] = body.username
        if body.email is not None:
            existing = await db.get_account_by_email(body.email)
            if existing and existing["id"] != user_id:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Email đã được đăng ký")
            updates["email"] = body.email.lower()
        if body.role is not None:
            # Chỉ 'admin' thật mới được thăng người khác (hoặc chính mình) lên 'admin' — chặn
            # dash_admin tự leo thang qua PATCH role.
            if body.role == "admin" and admin.get("role") != "admin":
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    "Chỉ quản trị viên (admin) mới được cấp quyền admin")
            # Không cho hạ role admin cuối cùng xuống viewer
            if user_id == admin.get("sub") and body.role != "admin":
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "Không thể hạ role chính mình")
            updates["role"] = body.role
        if not updates:
            return {"ok": True, "message": "Không có thay đổi."}
        ok = await db.update_account_by_admin(user_id, updates)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Admin cap nhat user %s: %s", user_id, updates)
        return {"ok": True, "updates": updates}

    @router.delete("/users/{user_id}", summary="Xoá tài khoản")
    async def delete_user(user_id: str, admin: dict = Admin) -> dict:
        if user_id == admin.get("sub"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Không thể xoá chính mình")
        ok = await db.delete_account(user_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại")
        logger.info("Xoa user %s", user_id)
        return {"ok": True}

    # ── Cache ──────────────────────────────────────────────────────────────────

    @router.post("/cache/flush", summary="Xoá cache Redis — ép tổng hợp lại từ PostgreSQL")
    async def flush_cache(_: dict = Admin) -> dict:
        """Lối thoát cho cửa sổ làm mới 5 phút: admin bấm là số liệu tính lại ngay."""
        removed = await cache.invalidate_all()
        logger.info("Xoa cache Redis: %d key", removed)
        return {"ok": True, "removed": removed, "enabled": cache.ready}

    # ── Tên quốc gia chuẩn (master list — người dùng tự tạo/sửa/xoá) ──────────
    #
    # Trước đây "Tên chuẩn muốn gộp về" là ô text tự do → gõ sai/lệch hoa-thường (vd "brazil" thay
    # vì "Brazil") tạo ra 1 nhóm MỚI tách riêng thay vì gộp đúng vào nhóm cũ. Bảng này là danh sách
    # tên chuẩn "chính thức" — FE chọn từ dropdown (không gõ tay), BE validate alias phải khớp
    # đúng 1 tên trong bảng này mới cho lưu, loại bỏ hoàn toàn rủi ro gõ sai ở điểm tạo alias.

    @router.get("/country-canonicals", summary="Danh sách tên quốc gia chuẩn")
    async def list_country_canonicals(_: dict = Admin) -> dict:
        rows = await db.list_country_canonicals()
        return {"items": rows}

    @router.post("/country-canonicals", summary="Tạo tên quốc gia chuẩn mới")
    async def create_country_canonical(body: CanonicalBody, _: dict = Admin) -> dict:
        name = body.name.strip()
        existing = await db.get_country_canonical(name)
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f'Tên chuẩn "{existing["name"]}" đã tồn tại')
        row = await db.create_country_canonical(name)
        logger.info("Tao ten chuan quoc gia %s", name)
        return row

    @router.patch("/country-canonicals/{name}", summary="Đổi tên 1 tên quốc gia chuẩn")
    async def rename_country_canonical(name: str, body: CanonicalBody, _: dict = Admin) -> dict:
        current = await db.get_country_canonical(name)
        if not current:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tên chuẩn")
        new_name = body.name.strip()
        if new_name.lower() != current["name"].lower():
            conflict = await db.get_country_canonical(new_name)
            if conflict:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f'Tên chuẩn "{conflict["name"]}" đã tồn tại')
        ok = await db.rename_country_canonical(current["name"], new_name)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tên chuẩn")
        # Đổi tên cascade sang country_aliases.canonical -> số liệu cache theo tên cũ sẽ sai lệch.
        await cache.invalidate_all()
        logger.info("Doi ten chuan quoc gia %s -> %s", current["name"], new_name)
        return {"ok": True, "name": new_name}

    @router.delete("/country-canonicals/{name}", summary="Xoá 1 tên quốc gia chuẩn")
    async def delete_country_canonical(name: str, _: dict = Admin) -> dict:
        row = await db.get_country_canonical(name)
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tên chuẩn")
        in_use = [a for a in await db.list_country_aliases()
                  if a["canonical"].lower() == row["name"].lower()]
        if in_use:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Còn {len(in_use)} biến thể đang dùng tên chuẩn này — xoá hết "
                                "biến thể trong nhóm trước khi xoá tên chuẩn")
        await db.delete_country_canonical(row["name"])
        logger.info("Xoa ten chuan quoc gia %s", name)
        return {"ok": True}

    # ── Chuẩn hoá quốc gia (alias → tên chuẩn) ────────────────────────────────
    #
    # "1 quy tắc duy nhất": Tool ở nhiều máy khác nhau tự gõ tay tên quốc gia (vd "BR"/"Brazil"/
    # "brazil" đều 1 nước) — không validate được từ xa. Admin/dash_admin tự gộp các biến thể về
    # đúng 1 tên chuẩn (lấy từ bảng `country_canonicals` ở trên), áp dụng NGAY tại tầng đọc dữ
    # liệu — KHÔNG cần sửa gì bên Tool.

    @router.get("/country-aliases", summary="Danh sách chuẩn hoá quốc gia (alias → tên chuẩn)")
    async def list_country_aliases(_: dict = Admin) -> dict:
        rows = await db.list_country_aliases()
        return {"items": rows}

    @router.post("/country-aliases", summary="Thêm/sửa 1 dòng chuẩn hoá quốc gia")
    async def upsert_country_alias(body: CountryAliasBody, _: dict = Admin) -> dict:
        canon = await db.get_country_canonical(body.canonical.strip())
        if not canon:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                'Tên chuẩn chưa tồn tại — tạo tên chuẩn trước ở mục "Tên quốc '
                                'gia chuẩn" rồi chọn lại')
        # Dùng đúng chữ hoa/thường đã lưu trong canonical (không dùng body.canonical) — loại bỏ
        # rủi ro gõ lệch case tạo nhóm trùng ngay cả khi validate qua được (vd "brazil" vs "Brazil").
        row = await db.upsert_country_alias(body.alias, canon["name"])
        # Chuẩn hoá ảnh hưởng NGAY tới dữ liệu đã cache — xoá cache để số liệu đúng ngay lập tức,
        # không bắt admin phải nhớ bấm "Làm mới" riêng.
        await cache.invalidate_all()
        logger.info("Upsert country alias %s -> %s", row["alias"], row["canonical"])
        return row

    @router.delete("/country-aliases/{alias}", summary="Xoá 1 dòng chuẩn hoá quốc gia")
    async def delete_country_alias(alias: str, _: dict = Admin) -> dict:
        ok = await db.delete_country_alias(alias)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy alias")
        await cache.invalidate_all()
        logger.info("Xoa country alias %s", alias)
        return {"ok": True}


    return router
