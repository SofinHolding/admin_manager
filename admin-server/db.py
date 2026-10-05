"""Tầng CSDL cho admin web — ĐỌC bảng sync, ĐỌC/GHI bảng riêng.

Kết nối CÙNG Postgres (database ufsync) với sync server, nhưng chạy ở process riêng (port 8421).
Bảng `documents`, `user_meta` chỉ ĐỌC — sync server là người ghi duy nhất. Ba bảng mới
(`invite_keys`, `accounts`, `refresh_tokens`) do admin-server quản lý, sync server không biết tới.

Tách biệt ghi chú: nếu cần sửa DDL bảng `documents` thì sửa ở `server/db.py`, KHÔNG sửa ở đây.
File này không import gì từ `server/` — hai process không chia sẻ code runtime.

SQLite cho test: cùng lý do với `server/db.py` — toàn bộ auth flow chạy được mà không cần Postgres.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import string
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import stats_core

logger = logging.getLogger("admin.db")

# ── DDL bảng MỚI (admin-server sở hữu) ────────────────────────────────────────
#
# Payload của `documents` lưu dạng chuỗi JSON — admin-server parse bằng `json.loads` khi cần aggregate.
# Không dùng `jsonb` vì sync server cũng lưu TEXT, và admin-server chỉ ĐỌC nên không có quyền ALTER.

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS invite_keys (
    key         TEXT PRIMARY KEY,
    role        TEXT NOT NULL DEFAULT 'viewer',
    label       TEXT NOT NULL DEFAULT '',
    max_uses    INTEGER NOT NULL DEFAULT 1,
    used_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    invite_key      TEXT NOT NULL,
    email_verified  INTEGER NOT NULL DEFAULT 0,
    verify_token    TEXT NOT NULL DEFAULT '',
    verify_token_at TEXT NOT NULL DEFAULT '',
    reset_token     TEXT NOT NULL DEFAULT '',
    reset_token_at  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    last_login      TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS account_links (
    account_id   TEXT NOT NULL,
    tool_user_id TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    linked_at    TEXT NOT NULL,
    PRIMARY KEY (account_id, tool_user_id)
);

CREATE TABLE IF NOT EXISTS link_codes (
    code        TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS channel_groups (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_group_members (
    group_id          TEXT NOT NULL,
    channel_ref       TEXT NOT NULL,
    platform_tag      TEXT NOT NULL DEFAULT '',
    country_override  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    PRIMARY KEY (group_id, channel_ref)
);

CREATE TABLE IF NOT EXISTS country_aliases (
    alias       TEXT PRIMARY KEY,
    canonical   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS country_canonicals (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

-- ── Local Fallback Tool v1 — inventory/queue (xem tasks/backlog/local-operations-hub-v1.md) ──

CREATE TABLE IF NOT EXISTS platform_policies (
    platform            TEXT PRIMARY KEY,
    web_entry_enabled   INTEGER NOT NULL DEFAULT 1,
    fallback_enabled    INTEGER NOT NULL DEFAULT 0,
    adapter_status      TEXT NOT NULL DEFAULT 'none',
    updated_by          TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resource_sources (
    id                TEXT PRIMARY KEY,
    owner_account_id  TEXT NOT NULL,
    name              TEXT NOT NULL,
    platform          TEXT NOT NULL,
    project_key       TEXT NOT NULL DEFAULT '',
    default_country   TEXT NOT NULL DEFAULT '',
    origin            TEXT NOT NULL DEFAULT 'web_manual',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_resources (
    id                     TEXT PRIMARY KEY,
    owner_account_id       TEXT NOT NULL,
    platform               TEXT NOT NULL,
    resource_type          TEXT NOT NULL DEFAULT '',
    canonical_external_id  TEXT NOT NULL DEFAULT '',
    input_ref              TEXT NOT NULL DEFAULT '',
    username               TEXT NOT NULL DEFAULT '',
    url                    TEXT NOT NULL DEFAULT '',
    display_name           TEXT NOT NULL DEFAULT '',
    country                TEXT NOT NULL DEFAULT '',
    origin                 TEXT NOT NULL DEFAULT 'web_manual',
    mapping_status         TEXT NOT NULL DEFAULT 'unmapped',
    execution_mode         TEXT NOT NULL DEFAULT 'disabled',
    verification_status    TEXT NOT NULL DEFAULT 'unverified',
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_assignments (
    id                  TEXT PRIMARY KEY,
    resource_id         TEXT NOT NULL,
    source_instance_id  TEXT NOT NULL,
    owner_account_id    TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (resource_id, source_instance_id)
);

CREATE TABLE IF NOT EXISTS operation_batches (
    id          TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL,
    platform    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_items (
    id                     TEXT PRIMARY KEY,
    batch_id               TEXT NOT NULL,
    resource_id            TEXT NOT NULL,
    source_assignment_id   TEXT NOT NULL DEFAULT '',
    owner_account_id       TEXT NOT NULL,
    platform               TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'queued',
    priority               INTEGER NOT NULL DEFAULT 0,
    attempts               INTEGER NOT NULL DEFAULT 0,
    last_error             TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    target_type  TEXT NOT NULL DEFAULT '',
    target_id    TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',
    at           TEXT NOT NULL
);
"""

_PG_DDL = """
CREATE TABLE IF NOT EXISTS invite_keys (
    key         text PRIMARY KEY,
    role        text NOT NULL DEFAULT 'viewer',
    label       text NOT NULL DEFAULT '',
    max_uses    integer NOT NULL DEFAULT 1,
    used_count  integer NOT NULL DEFAULT 0,
    created_at  text NOT NULL,
    expires_at  text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS accounts (
    id              text PRIMARY KEY,
    username        text NOT NULL UNIQUE,
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    role            text NOT NULL DEFAULT 'viewer',
    invite_key      text NOT NULL,
    email_verified  boolean NOT NULL DEFAULT false,
    verify_token    text NOT NULL DEFAULT '',
    verify_token_at text NOT NULL DEFAULT '',
    reset_token     text NOT NULL DEFAULT '',
    reset_token_at  text NOT NULL DEFAULT '',
    created_at      text NOT NULL,
    last_login      text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       text PRIMARY KEY,
    account_id  text NOT NULL,
    created_at  text NOT NULL,
    expires_at  text NOT NULL,
    revoked     boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS account_links (
    account_id   text NOT NULL,
    tool_user_id text NOT NULL,
    label        text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'active',
    linked_at    text NOT NULL,
    PRIMARY KEY (account_id, tool_user_id)
);

CREATE TABLE IF NOT EXISTS link_codes (
    code        text PRIMARY KEY,
    account_id  text NOT NULL,
    created_at  text NOT NULL,
    expires_at  text NOT NULL,
    used_at     text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS channel_groups (
    id          text PRIMARY KEY,
    account_id  text NOT NULL,
    name        text NOT NULL,
    created_at  text NOT NULL,
    updated_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_group_members (
    group_id          text NOT NULL,
    channel_ref       text NOT NULL,
    platform_tag      text NOT NULL DEFAULT '',
    country_override  text NOT NULL DEFAULT '',
    created_at        text NOT NULL,
    PRIMARY KEY (group_id, channel_ref)
);

CREATE TABLE IF NOT EXISTS country_aliases (
    alias       text PRIMARY KEY,
    canonical   text NOT NULL,
    created_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS country_canonicals (
    name        text PRIMARY KEY,
    created_at  text NOT NULL
);

-- ── Local Fallback Tool v1 — inventory/queue (xem tasks/backlog/local-operations-hub-v1.md) ──

CREATE TABLE IF NOT EXISTS platform_policies (
    platform            text PRIMARY KEY,
    web_entry_enabled   boolean NOT NULL DEFAULT true,
    fallback_enabled    boolean NOT NULL DEFAULT false,
    adapter_status      text NOT NULL DEFAULT 'none',
    updated_by          text NOT NULL DEFAULT '',
    updated_at          text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resource_sources (
    id                text PRIMARY KEY,
    owner_account_id  text NOT NULL,
    name              text NOT NULL,
    platform          text NOT NULL,
    project_key       text NOT NULL DEFAULT '',
    default_country   text NOT NULL DEFAULT '',
    origin            text NOT NULL DEFAULT 'web_manual',
    created_at        text NOT NULL,
    updated_at        text NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_resources (
    id                     text PRIMARY KEY,
    owner_account_id       text NOT NULL,
    platform               text NOT NULL,
    resource_type          text NOT NULL DEFAULT '',
    canonical_external_id  text NOT NULL DEFAULT '',
    input_ref              text NOT NULL DEFAULT '',
    username               text NOT NULL DEFAULT '',
    url                    text NOT NULL DEFAULT '',
    display_name           text NOT NULL DEFAULT '',
    country                text NOT NULL DEFAULT '',
    origin                 text NOT NULL DEFAULT 'web_manual',
    mapping_status         text NOT NULL DEFAULT 'unmapped',
    execution_mode         text NOT NULL DEFAULT 'disabled',
    verification_status    text NOT NULL DEFAULT 'unverified',
    enabled                boolean NOT NULL DEFAULT true,
    created_at             text NOT NULL,
    updated_at             text NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_assignments (
    id                  text PRIMARY KEY,
    resource_id         text NOT NULL,
    source_instance_id  text NOT NULL,
    owner_account_id    text NOT NULL,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          text NOT NULL,
    updated_at          text NOT NULL,
    UNIQUE (resource_id, source_instance_id)
);

CREATE TABLE IF NOT EXISTS operation_batches (
    id          text PRIMARY KEY,
    created_by  text NOT NULL,
    platform    text NOT NULL,
    name        text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'open',
    created_at  text NOT NULL,
    updated_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_items (
    id                     text PRIMARY KEY,
    batch_id               text NOT NULL,
    resource_id            text NOT NULL,
    source_assignment_id   text NOT NULL DEFAULT '',
    owner_account_id       text NOT NULL,
    platform               text NOT NULL,
    status                 text NOT NULL DEFAULT 'queued',
    priority               integer NOT NULL DEFAULT 0,
    attempts               integer NOT NULL DEFAULT 0,
    last_error             text NOT NULL DEFAULT '',
    created_at             text NOT NULL,
    updated_at             text NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_audit_log (
    id           bigserial PRIMARY KEY,
    account_id   text NOT NULL,
    event_type   text NOT NULL,
    target_type  text NOT NULL DEFAULT '',
    target_id    text NOT NULL DEFAULT '',
    payload      text NOT NULL DEFAULT '{}',
    at           text NOT NULL
);
"""


# ── Chuẩn hoá quốc gia — "1 quy tắc duy nhất" cho toàn dự án ────────────────────
#
# Vấn đề: mỗi máy Tool do 1 người tự gõ tên quốc gia khi cấu hình page/kênh (vd "BR", "Brazil",
# "brazil" đều chỉ 1 nước) — không có validate ở phía Tool. Đẩy lên admin-server thì các biến thể
# này KHÔNG tự gộp được với nhau, làm vỡ mọi biểu đồ/so sánh theo quốc gia. Giải pháp: 1 bảng alias
# do admin/dash_admin quản lý qua UI (`AdminPage.tsx` tab "Chuẩn hoá quốc gia"), tra theo
# `alias.strip().lower()` → `canonical` — áp dụng NGAY tại tầng đọc dữ liệu (`_fb_page_meta`,
# `_yt_channels_meta`, `country_overrides`), KHÔNG đụng gì tới Tool (`upload-facebook`).
#
# Danh sách dưới đây chỉ là khởi tạo hợp lý cho các thị trường thường gặp — admin thêm/sửa/xoá qua
# UI, KHÔNG cần sửa code hay deploy lại khi phát hiện biến thể mới.
_DEFAULT_COUNTRY_ALIASES: list[tuple[str, str]] = [
    ("br", "Brazil"), ("bra", "Brazil"), ("brazil", "Brazil"), ("brasil", "Brazil"),
    ("vn", "Việt Nam"), ("vietnam", "Việt Nam"), ("viet nam", "Việt Nam"), ("việt nam", "Việt Nam"),
    ("ng", "Nigeria"), ("nga", "Nigeria"), ("nigeria", "Nigeria"),
    ("us", "United States"), ("usa", "United States"), ("united states", "United States"),
    ("united states of america", "United States"),
    ("uk", "United Kingdom"), ("gb", "United Kingdom"), ("united kingdom", "United Kingdom"),
    ("in", "India"), ("india", "India"),
    ("id", "Indonesia"), ("indo", "Indonesia"), ("indonesia", "Indonesia"),
    ("ph", "Philippines"), ("phil", "Philippines"), ("philippines", "Philippines"),
    ("th", "Thailand"), ("thailand", "Thailand"),
    ("my", "Malaysia"), ("malaysia", "Malaysia"),
    ("sg", "Singapore"), ("singapore", "Singapore"),
    ("mx", "Mexico"), ("mexico", "Mexico"), ("méxico", "Mexico"),
    ("ar", "Argentina"), ("argentina", "Argentina"),
    ("co", "Colombia"), ("colombia", "Colombia"),
    ("pe", "Peru"), ("peru", "Peru"), ("perú", "Peru"),
    ("cl", "Chile"), ("chile", "Chile"),
    ("eg", "Egypt"), ("egypt", "Egypt"),
    ("ke", "Kenya"), ("kenya", "Kenya"),
    ("gh", "Ghana"), ("ghana", "Ghana"),
    ("za", "South Africa"), ("south africa", "South Africa"),
    ("pk", "Pakistan"), ("pakistan", "Pakistan"),
    ("bd", "Bangladesh"), ("bangladesh", "Bangladesh"),
    ("tr", "Turkey"), ("turkey", "Turkey"), ("türkiye", "Turkey"),
    ("sa", "Saudi Arabia"), ("saudi arabia", "Saudi Arabia"),
    ("ae", "United Arab Emirates"), ("uae", "United Arab Emirates"),
    ("fr", "France"), ("france", "France"),
    ("de", "Germany"), ("germany", "Germany"),
    ("es", "Spain"), ("spain", "Spain"), ("españa", "Spain"),
    ("it", "Italy"), ("italy", "Italy"),
    ("ca", "Canada"), ("canada", "Canada"),
    ("au", "Australia"), ("australia", "Australia"),
    ("jp", "Japan"), ("japan", "Japan"),
    ("kr", "South Korea"), ("korea", "South Korea"), ("south korea", "South Korea"),
]


# ── Local Fallback Tool v1 — policy mặc định (seed 1 lần khi bảng rỗng, admin sửa qua UI sau) ──
# Theo bảng ví dụ ở đặc tả (`tasks/backlog/local-operations-hub-v1.md` mục 8) — KHÔNG hardcode
# platform nào khác ngoài 4 cái này vào LOGIC; admin có thể thêm platform mới qua
# `upsert_platform_policy` mà không cần sửa code.
_DEFAULT_PLATFORM_POLICIES: list[tuple[str, bool, bool]] = [
    # (platform, web_entry_enabled, fallback_enabled) — adapter_status luôn 'none' lúc seed (V1
    # chưa có adapter thật cho platform nào).
    ("facebook", True, True),
    ("tiktok", True, True),
    ("youtube", True, True),
    ("instagram", True, False),
]

_EXECUTION_MODES = ("primary_tool", "fallback_local", "disabled")
_ITEM_STATUSES = ("queued", "blocked_no_adapter", "ready", "running", "success", "failed", "cancelled")


def _gen_key() -> str:
    """Tạo invite key ngắn gọn dạng XXXX-XXXX — chữ in hoa + số, dễ copy-paste."""
    chars = string.ascii_uppercase + string.digits
    p1 = "".join(secrets.choice(chars) for _ in range(4))
    p2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"{p1}-{p2}"


def _gen_link_code() -> str:
    """Mã liên kết Tool ⇄ account — 8 ký tự hex in hoa, đủ ngắn để gõ tay, đủ dài để không đoán
    trúng trong TTL 15 phút (đã có rate-limit ở endpoint /redeem chặn dò thêm)."""
    return secrets.token_hex(4).upper()


def _apply_channel_refs(fb_posts: list[dict], yt_daily: list[dict], chans: list[dict],
                        tc: dict[str, str],
                        channel_refs: set[str] | None) -> tuple[list[dict], list[dict], list[dict], dict[str, str]]:
    """Lọc thêm theo tập channel_ref cụ thể (group kênh do viewer tự tạo) — áp SAU khi đã lọc
    user_ids, TRƯỚC khi đưa vào `stats_core`. `None` = không lọc thêm (giữ nguyên).

    Không đụng `stats_core.py` (phải giữ byte-identical với Tool): lọc bằng cách thu hẹp chính
    danh sách đầu vào — `top_channels`/`views_by_country`/`country_tags` không có tham số
    yt_filter/fb_filter nên đây là cách DUY NHẤT áp group filter cho cả 5 phương thức thống nhất."""
    if channel_refs is None:
        return fb_posts, yt_daily, chans, tc
    fb2 = [p for p in fb_posts if p.get("target") in channel_refs]
    yt2 = [r for r in yt_daily if r.get("channel_id") in channel_refs]
    chans2 = [c for c in chans if c.get("id") in channel_refs]
    tc2 = {k: v for k, v in tc.items() if k in channel_refs}
    return fb2, yt2, chans2, tc2


def _apply_country_overrides(chans: list[dict], tc: dict[str, str],
                             overrides: dict[str, str] | None) -> tuple[list[dict], dict[str, str]]:
    """Ghi đè quốc gia theo `channel_group_members.country_override` (viewer tự sửa khi map kênh
    vào group) — áp TRƯỚC `_apply_channel_refs`. Ghi đè LUÔN thắng quốc gia gốc (kể cả target/kênh
    trước đó chưa có quốc gia nào), để "đồng bộ" đúng như viewer đã gắn ở Nhóm kênh — không có chỗ
    nào khác trong hệ thống đọc `country_override`, đây là điểm áp dụng DUY NHẤT."""
    if not overrides:
        return chans, tc
    tc2 = {**tc, **overrides}
    chans2 = [{**c, "country": overrides.get(c["id"], c.get("country"))} for c in chans]
    return chans2, tc2


def _channel_breakdown(pairings: list[dict], groups: list[dict], channel_meta: list[dict],
                       target_country: dict[str, str]) -> dict:
    """Phân tích "Kênh/Page" theo nền tảng + chủ đề (`repost_groups`) + quốc gia — mục 25.4 đặc tả
    `tasks/backlog/local-operations-hub-v1.md`. Tính THẲNG ở đây bằng dữ liệu đã tải (không đụng
    `stats_core.py` — file đó phải byte-identical với repo Tool, không được sửa tuỳ tiện).

    `managed` đếm Facebook theo CẶP (target, group_id) — 1 trang chạy nhiều chủ đề (vd cùng lúc
    "ITL" và "Sible") được tính là NHIỀU kênh riêng biệt, khớp đúng yêu cầu người dùng (không gộp
    theo target). YouTube không có khái niệm "chủ đề"/bật-tắt riêng trong dữ liệu Tool nên
    managed=active=số kênh (giữ nguyên hành vi hiện tại, không suy diễn thêm)."""
    groups_by_id = {g["id"]: g for g in groups}
    group_name = {g["id"]: (g.get("name") or g["id"]) for g in groups}

    fb_pairs: dict[tuple[str, str], bool] = {}
    for p in pairings:
        target = (p.get("target") or "").strip()
        if not target:
            continue
        gid = p.get("group_id") or ""
        key = (target, gid)
        active = bool(p.get("enabled")) and stats_core.cho_chay(groups_by_id.get(gid))
        fb_pairs[key] = fb_pairs.get(key, False) or active

    fb_managed = len(fb_pairs)
    fb_active = sum(1 for v in fb_pairs.values() if v)

    fb_by_group: dict[str, dict] = {}
    fb_by_country: dict[str, dict] = {}
    for (target, gid), active in fb_pairs.items():
        glabel = group_name.get(gid, "Không chủ đề") if gid else "Không chủ đề"
        grow = fb_by_group.setdefault(glabel, {"managed": 0, "active": 0})
        grow["managed"] += 1
        if active:
            grow["active"] += 1
        ctry = target_country.get(target) or stats_core.KHAC
        crow = fb_by_country.setdefault(ctry, {"managed": 0, "active": 0})
        crow["managed"] += 1
        if active:
            crow["active"] += 1

    yt_by_country: dict[str, dict] = {}
    for c in channel_meta:
        ctry = c.get("country") or stats_core.KHAC
        row = yt_by_country.setdefault(ctry, {"managed": 0, "active": 0})
        row["managed"] += 1
        row["active"] += 1
    yt_managed = len(channel_meta)

    by_platform = [
        {"platform": "facebook", "managed": fb_managed, "active": fb_active,
         "by_group": [{"group": k, **v} for k, v in sorted(fb_by_group.items())],
         "by_country": [{"country": k, **v} for k, v in sorted(fb_by_country.items())]},
        {"platform": "youtube", "managed": yt_managed, "active": yt_managed,
         "by_country": [{"country": k, **v} for k, v in sorted(yt_by_country.items())]},
    ]
    return {
        "total": {"managed": fb_managed + yt_managed, "active": fb_active + yt_managed},
        "by_platform": by_platform,
    }


def _normalize_country(raw: str, aliases: dict[str, str]) -> str:
    """Chuẩn hoá 1 giá trị quốc gia THÔ (Tool tự gõ tay, không validate — vd "BR"/"Brazil"/"brazil")
    về "tên chuẩn" duy nhất theo bảng `country_aliases` do admin quản lý. Tra theo
    `raw.strip().lower()`; không khớp alias nào → giữ nguyên bản đã trim (không tự bịa quốc gia lạ)."""
    if not raw:
        return raw
    key = raw.strip().lower()
    return aliases.get(key, raw.strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_between(a: str, b: str) -> list[str]:
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


# ── Giao diện ───────────────────────────────────────────────────────────────────

class Db:
    """Giao diện chung cho SQLite (test) và Postgres (production)."""

    async def setup(self) -> None: ...
    async def close(self) -> None: ...

    # ── Invite key ──────────────────────────────────────────────────────────────
    async def get_invite_key(self, key: str) -> dict | None: ...
    async def use_invite_key(self, key: str) -> bool: ...

    # ── Account ─────────────────────────────────────────────────────────────────
    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict: ...
    async def get_account_by_username(self, username: str) -> dict | None: ...
    async def get_account_by_email(self, email: str) -> dict | None: ...
    async def get_account_by_id(self, account_id: str) -> dict | None: ...
    async def username_exists(self, username: str) -> bool: ...
    async def email_exists(self, email: str) -> bool: ...
    async def set_email_verified(self, account_id: str) -> None: ...
    async def set_verify_token(self, account_id: str, token: str) -> None: ...
    async def get_account_by_verify_token(self, token: str) -> dict | None: ...
    async def set_reset_token(self, account_id: str, token: str) -> None: ...
    async def get_account_by_reset_token(self, token: str) -> dict | None: ...
    async def clear_reset_token(self, account_id: str) -> None: ...
    async def update_password(self, account_id: str, password_hash: str) -> None: ...
    async def update_last_login(self, account_id: str) -> None: ...

    # ── Refresh token ───────────────────────────────────────────────────────────
    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str: ...
    async def get_refresh_token(self, token: str) -> dict | None: ...
    async def revoke_refresh_token(self, token: str) -> None: ...
    async def revoke_all_refresh_tokens(self, account_id: str) -> None: ...

    # ── Admin management ─────────────────────────────────────────────────────────
    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict: ...
    async def list_invite_keys(self) -> list[dict]: ...
    async def revoke_invite_key(self, key: str) -> bool: ...
    async def list_accounts(self) -> list[dict]: ...
    async def set_account_status(self, account_id: str, new_status: str) -> bool: ...
    async def delete_account(self, account_id: str) -> bool: ...
    async def update_account_profile(self, account_id: str, updates: dict) -> None: ...
    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool: ...

    # ── Liên kết Tool ⇄ account (mã pairing 1 lần) ─────────────────────────────
    async def tool_user_id_exists(self, tool_user_id: str) -> bool: ...
    async def create_link_code(self, account_id: str, ttl_minutes: int = 15) -> dict: ...
    async def redeem_link_code(self, code: str, tool_user_id: str,
                               device_id: str = "") -> dict | None: ...
    async def list_links(self, account_id: str) -> list[dict]: ...
    async def revoke_link(self, account_id: str, tool_user_id: str) -> bool: ...
    async def linked_user_ids(self, account_id: str) -> list[str]: ...

    # ── Group/tag kênh (metadata riêng của admin-server, KHÔNG phải repost_groups) ──
    async def create_channel_group(self, account_id: str, name: str) -> dict: ...
    async def list_channel_groups(self, account_id: str) -> list[dict]: ...
    async def get_channel_group(self, group_id: str) -> dict | None: ...
    async def rename_channel_group(self, group_id: str, name: str) -> bool: ...
    async def delete_channel_group(self, group_id: str) -> bool: ...
    async def list_group_members(self, group_id: str) -> list[dict]: ...
    async def add_group_member(self, group_id: str, channel_ref: str, platform_tag: str = "",
                               country_override: str = "") -> dict: ...
    async def remove_group_member(self, group_id: str, channel_ref: str) -> bool: ...
    async def group_channel_refs(self, group_id: str) -> set[str]: ...
    async def channel_refs_by_platform(self, platform_tag: str,
                                       account_ids: list[str] | None = None) -> set[str]: ...
    async def list_platform_tags(self, account_ids: list[str] | None = None) -> list[str]: ...
    async def channel_refs_by_native_platform(self, platform: str,
                                              user_ids: list[str] | None = None) -> set[str]: ...
    async def country_overrides(self, account_ids: list[str] | None = None) -> dict[str, str]: ...

    # ── Chuẩn hoá quốc gia (alias → tên chuẩn, admin/dash_admin quản lý qua UI) ──
    async def list_country_aliases(self) -> list[dict]: ...
    async def upsert_country_alias(self, alias: str, canonical: str) -> dict: ...
    async def delete_country_alias(self, alias: str) -> bool: ...

    # ── Tên quốc gia chuẩn (master list — người dùng tự tạo/sửa/xoá qua UI; alias ở trên PHẢI
    # trỏ về 1 tên trong danh sách này, không cho gõ tay tự do để tránh lệch hoa/thường tạo nhóm
    # trùng) ──
    async def list_country_canonicals(self) -> list[dict]: ...
    async def get_country_canonical(self, name: str) -> dict | None: ...
    async def create_country_canonical(self, name: str) -> dict: ...
    async def rename_country_canonical(self, old_name: str, new_name: str) -> bool: ...
    async def delete_country_canonical(self, name: str) -> bool: ...

    # ── Viewer data (ĐỌC XUYÊN USER từ bảng sync, TRỪ KHI `user_ids` thu hẹp) ────
    #
    # Admin-server đọc `documents` để dựng biểu đồ — cùng dữ liệu admin cũ ở `server/admin.py`
    # nhưng chạy ở process riêng. KHÔNG GHI vào `documents` hay `user_meta`.
    # `user_ids=None` → không lọc (admin/dash_admin mặc định); `[]` → không có Tool nào liên kết
    # (trả rỗng); danh sách cụ thể → lọc theo `documents.user_id`.
    # `channel_refs=None` → không lọc theo group; tập cụ thể → chỉ tính các target/channel_id đó.
    # `country_overrides` → áp `channel_group_members.country_override` (nếu có) TRƯỚC khi tính.
    async def traffic_metric(self, date_from: str, date_to: str, scope: str = "", country: str = "",
                             user_ids: list[str] | None = None,
                             channel_refs: set[str] | None = None,
                             country_overrides: dict[str, str] | None = None) -> dict: ...
    async def summary(self, date_from: str, date_to: str, country: str = "",
                      user_ids: list[str] | None = None,
                      channel_refs: set[str] | None = None,
                      country_overrides: dict[str, str] | None = None) -> dict: ...
    async def top_channels(self, date_from: str, date_to: str, country: str = "", limit: int = 15,
                           user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[dict]: ...
    async def views_by_country(self, date_from: str, date_to: str,
                               user_ids: list[str] | None = None,
                               channel_refs: set[str] | None = None,
                               country_overrides: dict[str, str] | None = None) -> dict: ...
    async def country_tags(self, user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[str]: ...
    async def doc_version(self) -> str: ...
    async def channel_breakdown(self, user_ids: list[str] | None = None,
                                channel_refs: set[str] | None = None,
                                country_overrides: dict[str, str] | None = None,
                                owner_scope: list[str] | None = None) -> dict: ...
    async def last_synced_at(self, user_ids: list[str] | None = None) -> str | None: ...

    # ── Local Fallback Tool v1 — inventory/queue ─────────────────────────────────
    async def list_platform_policies(self) -> list[dict]: ...
    async def get_platform_policy(self, platform: str) -> dict | None: ...
    async def upsert_platform_policy(self, platform: str, web_entry_enabled: bool | None,
                                     fallback_enabled: bool | None, adapter_status: str | None,
                                     updated_by: str) -> dict: ...

    async def create_resource_source(self, owner_account_id: str, name: str, platform: str,
                                     project_key: str = "", default_country: str = "",
                                     origin: str = "web_manual") -> dict: ...
    async def list_resource_sources(self, owner_account_id: str | None = None) -> list[dict]: ...
    async def get_resource_source(self, source_id: str) -> dict | None: ...
    async def update_resource_source(self, source_id: str, updates: dict) -> bool: ...

    async def create_managed_resource(self, *, owner_account_id: str, platform: str,
                                      resource_type: str, input_ref: str, username: str, url: str,
                                      display_name: str, country: str, origin: str,
                                      execution_mode: str, source_instance_id: str,
                                      actor_account_id: str) -> dict: ...
    async def list_managed_resources(self, owner_account_id: str | None,
                                     filters: dict) -> list[dict]: ...
    async def get_managed_resource(self, resource_id: str) -> dict | None: ...
    async def update_managed_resource(self, resource_id: str, updates: dict,
                                      actor_account_id: str) -> bool: ...
    async def delete_managed_resource(self, resource_id: str, actor_account_id: str) -> bool: ...
    async def list_resource_assignments(self, resource_id: str) -> list[dict]: ...
    async def inventory_revision(self, owner_account_id: str | None = None) -> str: ...
    async def list_inventory_owners(self) -> list[dict]: ...

    async def create_operation_batch(self, created_by: str, platform: str, name: str) -> dict: ...
    async def list_operation_batches(self, created_by: str | None = None) -> list[dict]: ...
    async def get_operation_batch(self, batch_id: str) -> dict | None: ...
    async def add_operation_items(self, batch_id: str, resource_ids: list[str]) -> dict: ...
    async def list_operation_items(self, batch_id: str) -> list[dict]: ...
    async def cancel_operation_item(self, item_id: str, actor_account_id: str) -> bool: ...

    async def log_audit(self, account_id: str, event_type: str, target_type: str = "",
                        target_id: str = "", payload: dict | None = None) -> None: ...


# ── SQLite ──────────────────────────────────────────────────────────────────────

class SqliteDb(Db):
    """Cho test — cùng pattern với `server/db.py`."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    async def setup(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SQLITE_DDL)
            now = _now()
            # Chỉ seed mặc định khi bảng THỰC SỰ trống (DB mới hoàn toàn) — trước đây dùng
            # "INSERT OR IGNORE" vô điều kiện mỗi lần khởi động, nghĩa là XOÁ xong restart lại
            # server thì alias/tên chuẩn đã xoá bị HỒI SINH (OR IGNORE chỉ chặn trùng khi dòng
            # còn tồn tại, không chặn khi dòng đã bị xoá hẳn). Giờ chỉ seed đúng 1 lần ở DB rỗng.
            existing = self._conn.execute("SELECT COUNT(*) FROM country_aliases").fetchone()[0]
            if existing == 0:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO country_aliases (alias, canonical, created_at) "
                    "VALUES (?, ?, ?)",
                    [(a, c, now) for a, c in _DEFAULT_COUNTRY_ALIASES])
            # Backfill tên chuẩn "chính thức" từ các canonical đã có (mặc định + dữ liệu cũ) —
            # tự hồi phục nếu DB đã tồn tại trước khi bảng này ra đời, không cần migration tay.
            self._conn.execute(
                "INSERT OR IGNORE INTO country_canonicals (name, created_at) "
                "SELECT DISTINCT canonical, ? FROM country_aliases", (now,))
            # Platform policy mặc định — seed 1 lần, cùng lý do country_aliases ở trên (không
            # hồi sinh policy admin đã xoá tay).
            existing_pp = self._conn.execute("SELECT COUNT(*) FROM platform_policies").fetchone()[0]
            if existing_pp == 0:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO platform_policies "
                    "(platform, web_entry_enabled, fallback_enabled, adapter_status, updated_at) "
                    "VALUES (?, ?, ?, 'none', ?)",
                    [(p, int(w), int(f), now) for p, w, f in _DEFAULT_PLATFORM_POLICIES])
            self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Local Fallback Tool v1 — platform policy ─────────────────────────────────

    async def list_platform_policies(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM platform_policies ORDER BY platform").fetchall()
        return [dict(r) for r in rows]

    async def get_platform_policy(self, platform: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM platform_policies WHERE platform = ?", (platform,)).fetchone()
        return dict(row) if row else None

    async def upsert_platform_policy(self, platform: str, web_entry_enabled: bool | None,
                                     fallback_enabled: bool | None, adapter_status: str | None,
                                     updated_by: str) -> dict:
        """Cập nhật TỪNG PHẦN — field `None` giữ nguyên giá trị cũ (hoặc mặc định nếu platform
        chưa có policy nào, admin thêm platform mới qua đây mà không cần sửa code/deploy)."""
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM platform_policies WHERE platform = ?", (platform,)).fetchone()
            base = dict(cur) if cur else {
                "web_entry_enabled": 1, "fallback_enabled": 0, "adapter_status": "none",
            }
            w = base["web_entry_enabled"] if web_entry_enabled is None else int(web_entry_enabled)
            f = base["fallback_enabled"] if fallback_enabled is None else int(fallback_enabled)
            a = base["adapter_status"] if adapter_status is None else adapter_status
            self._conn.execute(
                "INSERT INTO platform_policies "
                "(platform, web_entry_enabled, fallback_enabled, adapter_status, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(platform) DO UPDATE SET web_entry_enabled=excluded.web_entry_enabled, "
                "fallback_enabled=excluded.fallback_enabled, adapter_status=excluded.adapter_status, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (platform, w, f, a, updated_by, now))
            self._conn.commit()
        await self.log_audit(updated_by, "platform_policy_changed", "policy", platform,
                             {"web_entry_enabled": bool(w), "fallback_enabled": bool(f),
                              "adapter_status": a})
        return {"platform": platform, "web_entry_enabled": bool(w), "fallback_enabled": bool(f),
                "adapter_status": a, "updated_by": updated_by, "updated_at": now}

    # ── Resource Sources (namespace nghiệp vụ, mở rộng vô hạn — KHÔNG hardcode tên) ──

    async def create_resource_source(self, owner_account_id: str, name: str, platform: str,
                                     project_key: str = "", default_country: str = "",
                                     origin: str = "web_manual") -> dict:
        sid = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO resource_sources "
                "(id, owner_account_id, name, platform, project_key, default_country, origin, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, owner_account_id, name, platform, project_key, default_country, origin,
                 now, now))
            self._conn.commit()
        return {"id": sid, "owner_account_id": owner_account_id, "name": name, "platform": platform,
                "project_key": project_key, "default_country": default_country, "origin": origin,
                "created_at": now, "updated_at": now}

    async def list_resource_sources(self, owner_account_id: str | None = None) -> list[dict]:
        with self._lock:
            if owner_account_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM resource_sources ORDER BY platform, name").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM resource_sources WHERE owner_account_id = ? "
                    "ORDER BY platform, name", (owner_account_id,)).fetchall()
        return [dict(r) for r in rows]

    async def get_resource_source(self, source_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM resource_sources WHERE id = ?", (source_id,)).fetchone()
        return dict(row) if row else None

    async def update_resource_source(self, source_id: str, updates: dict) -> bool:
        allowed = {"name", "project_key", "default_country"}
        fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not fields:
            return False
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE resource_sources SET {set_clause} WHERE id = ?",
                (*fields.values(), source_id))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Managed Resources (danh tính canonical) + Assignments ───────────────────────

    async def create_managed_resource(self, *, owner_account_id: str, platform: str,
                                      resource_type: str, input_ref: str, username: str, url: str,
                                      display_name: str, country: str, origin: str,
                                      execution_mode: str, source_instance_id: str,
                                      actor_account_id: str) -> dict:
        rid = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO managed_resources "
                "(id, owner_account_id, platform, resource_type, input_ref, username, url, "
                "display_name, country, origin, mapping_status, execution_mode, "
                "verification_status, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmapped', ?, 'unverified', 1, ?, ?)",
                (rid, owner_account_id, platform, resource_type, input_ref, username, url,
                 display_name, country, origin, execution_mode, now, now))
            if source_instance_id:
                aid = uuid.uuid4().hex
                self._conn.execute(
                    "INSERT INTO resource_assignments "
                    "(id, resource_id, source_instance_id, owner_account_id, enabled, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (aid, rid, source_instance_id, owner_account_id, now, now))
            self._conn.commit()
        await self.log_audit(actor_account_id, "resource_created", "resource", rid,
                             {"platform": platform, "input_ref": input_ref, "origin": origin,
                              "execution_mode": execution_mode})
        if source_instance_id:
            await self.log_audit(actor_account_id, "assignment_created", "resource", rid,
                                 {"source_instance_id": source_instance_id})
        return await self.get_managed_resource(rid)

    async def list_managed_resources(self, owner_account_id: str | None,
                                     filters: dict) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_account_id is not None:
            clauses.append("owner_account_id = ?")
            params.append(owner_account_id)
        for col in ("platform", "country", "origin", "mapping_status", "execution_mode",
                    "verification_status"):
            v = filters.get(col)
            if v:
                clauses.append(f"{col} = ?")
                params.append(v)
        if filters.get("enabled") is not None:
            clauses.append("enabled = ?")
            params.append(1 if filters["enabled"] else 0)
        if filters.get("source_instance_id"):
            clauses.append(
                "id IN (SELECT resource_id FROM resource_assignments WHERE source_instance_id = ?)")
            params.append(filters["source_instance_id"])
        q = (filters.get("q") or "").strip()
        if q:
            clauses.append(
                "(display_name LIKE ? OR username LIKE ? OR url LIKE ? OR input_ref LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM managed_resources {where} ORDER BY created_at DESC",
                params).fetchall()
            out = [dict(r) for r in rows]
            if out:
                # Gắn `source_ids` cho mỗi resource — Tool B cần field này để dựng Source Tree
                # (User -> Platform -> Source -> Resource, mục 11 đặc tả). 1 truy vấn gộp, KHÔNG
                # N+1 theo từng resource.
                placeholders = ",".join("?" * len(out))
                arows = self._conn.execute(
                    "SELECT resource_id, source_instance_id FROM resource_assignments "
                    f"WHERE resource_id IN ({placeholders})",
                    [r["id"] for r in out]).fetchall()
                by_resource: dict[str, list[str]] = {}
                for ar in arows:
                    by_resource.setdefault(ar["resource_id"], []).append(ar["source_instance_id"])
                for r in out:
                    r["source_ids"] = by_resource.get(r["id"], [])
        return out

    async def get_managed_resource(self, resource_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM managed_resources WHERE id = ?", (resource_id,)).fetchone()
        return dict(row) if row else None

    async def update_managed_resource(self, resource_id: str, updates: dict,
                                      actor_account_id: str) -> bool:
        allowed = {"display_name", "username", "url", "country", "resource_type",
                  "execution_mode", "verification_status", "mapping_status", "enabled",
                  "canonical_external_id"}
        fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not fields:
            return False
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE managed_resources SET {set_clause} WHERE id = ?",
                (*fields.values(), resource_id))
            self._conn.commit()
            ok = cur.rowcount > 0
        if ok:
            event = "execution_mode_changed" if "execution_mode" in updates else "resource_updated"
            await self.log_audit(actor_account_id, event, "resource", resource_id, dict(updates))
        return ok

    async def delete_managed_resource(self, resource_id: str, actor_account_id: str) -> bool:
        with self._lock:
            self._conn.execute(
                "DELETE FROM resource_assignments WHERE resource_id = ?", (resource_id,))
            cur = self._conn.execute("DELETE FROM managed_resources WHERE id = ?", (resource_id,))
            self._conn.commit()
            ok = cur.rowcount > 0
        if ok:
            await self.log_audit(actor_account_id, "resource_deleted", "resource", resource_id, {})
        return ok

    async def list_resource_assignments(self, resource_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM resource_assignments WHERE resource_id = ? ORDER BY created_at",
                (resource_id,)).fetchall()
        return [dict(r) for r in rows]

    async def inventory_revision(self, owner_account_id: str | None = None) -> str:
        """`max(updated_at).count` — đổi mỗi khi có resource được tạo/sửa/xoá. Tool B so sánh
        chuỗi này với lần poll trước để biết có cần tải lại inventory không (mục 18 đặc tả)."""
        with self._lock:
            if owner_account_id is None:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(updated_at), '') AS m, COUNT(*) AS n "
                    "FROM managed_resources").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(updated_at), '') AS m, COUNT(*) AS n "
                    "FROM managed_resources WHERE owner_account_id = ?",
                    (owner_account_id,)).fetchone()
        return f"{row['m']}.{row['n']}"

    async def list_inventory_owners(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT a.id, a.username FROM accounts a "
                "JOIN managed_resources m ON m.owner_account_id = a.id "
                "ORDER BY a.username").fetchall()
        return [dict(r) for r in rows]

    # ── Run Queue (batch/item) ───────────────────────────────────────────────────

    async def create_operation_batch(self, created_by: str, platform: str, name: str) -> dict:
        bid = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO operation_batches (id, created_by, platform, name, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (bid, created_by, platform, name, now, now))
            self._conn.commit()
        await self.log_audit(created_by, "batch_created", "batch", bid,
                             {"platform": platform, "name": name})
        return {"id": bid, "created_by": created_by, "platform": platform, "name": name,
                "status": "open", "created_at": now, "updated_at": now}

    async def list_operation_batches(self, created_by: str | None = None) -> list[dict]:
        with self._lock:
            if created_by is None:
                rows = self._conn.execute(
                    "SELECT * FROM operation_batches ORDER BY created_at DESC").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM operation_batches WHERE created_by = ? "
                    "ORDER BY created_at DESC", (created_by,)).fetchall()
        return [dict(r) for r in rows]

    async def get_operation_batch(self, batch_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operation_batches WHERE id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None

    async def add_operation_items(self, batch_id: str, resource_ids: list[str]) -> dict:
        """Chỉ thêm resource thoả `execution_mode=='fallback_local'` +
        `platform_policies.fallback_enabled` + `enabled` + cùng platform với batch (mục 12/14 đặc
        tả `local-operations-hub-v1.md`) — resource không thoả bị SKIP kèm lý do, KHÔNG âm thầm bỏ
        qua. V1 chưa có adapter cho platform nào (`adapter_status` seed mặc định 'none') nên mọi
        item vừa thêm đều dừng ở `blocked_no_adapter`, KHÔNG được giả lập `success` (mục 13/15)."""
        batch = await self.get_operation_batch(batch_id)
        if not batch:
            return {"added": [],
                    "skipped": [{"resource_id": r, "reason": "batch_not_found"} for r in resource_ids]}
        added: list[str] = []
        skipped: list[dict] = []
        now = _now()
        for rid in resource_ids:
            res = await self.get_managed_resource(rid)
            if not res:
                skipped.append({"resource_id": rid, "reason": "resource_not_found"})
                continue
            if res["platform"] != batch["platform"]:
                skipped.append({"resource_id": rid, "reason": "platform_mismatch"})
                continue
            if not res["enabled"]:
                skipped.append({"resource_id": rid, "reason": "resource_disabled"})
                continue
            if res["execution_mode"] != "fallback_local":
                skipped.append({"resource_id": rid, "reason": "execution_mode_not_fallback_local"})
                continue
            policy = await self.get_platform_policy(res["platform"])
            if not policy or not policy["fallback_enabled"]:
                skipped.append({"resource_id": rid, "reason": "platform_fallback_disabled"})
                continue
            status = "queued" if policy["adapter_status"] == "ready" else "blocked_no_adapter"
            assigns = await self.list_resource_assignments(rid)
            source_assignment_id = assigns[0]["id"] if assigns else ""
            iid = uuid.uuid4().hex
            with self._lock:
                self._conn.execute(
                    "INSERT INTO operation_items (id, batch_id, resource_id, source_assignment_id, "
                    "owner_account_id, platform, status, priority, attempts, last_error, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, '', ?, ?)",
                    (iid, batch_id, rid, source_assignment_id, res["owner_account_id"],
                     res["platform"], status, now, now))
                self._conn.commit()
            await self.log_audit(batch["created_by"], "item_queued", "item", iid,
                                 {"batch_id": batch_id, "resource_id": rid, "status": status})
            added.append(iid)
        return {"added": added, "skipped": skipped}

    async def list_operation_items(self, batch_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM operation_items WHERE batch_id = ? ORDER BY created_at",
                (batch_id,)).fetchall()
        return [dict(r) for r in rows]

    async def cancel_operation_item(self, item_id: str, actor_account_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM operation_items WHERE id = ?", (item_id,)).fetchone()
            if not row or row["status"] in ("success", "failed", "cancelled"):
                return False
            cur = self._conn.execute(
                "UPDATE operation_items SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (_now(), item_id))
            self._conn.commit()
            ok = cur.rowcount > 0
        if ok:
            await self.log_audit(actor_account_id, "item_cancelled", "item", item_id, {})
        return ok

    # ── Audit log ─────────────────────────────────────────────────────────────────

    async def log_audit(self, account_id: str, event_type: str, target_type: str = "",
                        target_id: str = "", payload: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO operation_audit_log (account_id, event_type, target_type, target_id, "
                "payload, at) VALUES (?, ?, ?, ?, ?, ?)",
                (account_id, event_type, target_type, target_id,
                 json.dumps(payload or {}, ensure_ascii=False), _now()))
            self._conn.commit()

    # ── Invite key ──────────────────────────────────────────────────────────────

    async def get_invite_key(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM invite_keys WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    async def use_invite_key(self, key: str) -> bool:
        """Tăng `used_count` nếu còn lượt. Trả False nếu hết lượt hoặc bị thu hồi.

        Kiểm TẠI THỜI ĐIỂM GHI chứ không tách kiểm rồi ghi: hai người paste cùng key cùng lúc mà
        chỉ kiểm trước thì cả hai đều thấy "còn lượt" rồi cùng tăng — vượt trần."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE invite_keys SET used_count = used_count + 1 "
                "WHERE key = ? AND status = 'active' AND used_count < max_uses", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Account ─────────────────────────────────────────────────────────────────

    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict:
        account_id = uuid.uuid4().hex
        now = _now()
        verify_token = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO accounts "
                "(id, username, email, password_hash, role, invite_key, "
                " verify_token, verify_token_at, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (account_id, username, email.lower(), password_hash, role, invite_key,
                 verify_token, now, now))
            self._conn.commit()
        return {"id": account_id, "username": username, "email": email.lower(),
                "role": role, "verify_token": verify_token, "status": "pending"}

    async def get_account_by_username(self, username: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    async def get_account_by_email(self, email: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    async def get_account_by_id(self, account_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None

    async def username_exists(self, username: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM accounts WHERE username = ?", (username,)).fetchone()
        return row is not None

    async def email_exists(self, email: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM accounts WHERE email = ?", (email.lower(),)).fetchone()
        return row is not None

    async def set_email_verified(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET email_verified = 1, status = 'active', "
                "verify_token = '' WHERE id = ?", (account_id,))
            self._conn.commit()

    async def set_verify_token(self, account_id: str, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET verify_token = ?, verify_token_at = ? WHERE id = ?",
                (token, _now(), account_id))
            self._conn.commit()

    async def get_account_by_verify_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE verify_token = ? AND verify_token != ''",
                (token,)).fetchone()
        return dict(row) if row else None

    async def set_reset_token(self, account_id: str, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET reset_token = ?, reset_token_at = ? WHERE id = ?",
                (token, _now(), account_id))
            self._conn.commit()

    async def get_account_by_reset_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE reset_token = ? AND reset_token != ''",
                (token,)).fetchone()
        return dict(row) if row else None

    async def clear_reset_token(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET reset_token = '', reset_token_at = '' WHERE id = ?",
                (account_id,))
            self._conn.commit()

    async def update_password(self, account_id: str, password_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = ?",
                (password_hash, account_id))
            self._conn.commit()

    async def update_last_login(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET last_login = ? WHERE id = ?", (_now(), account_id))
            self._conn.commit()

    # ── Refresh token ───────────────────────────────────────────────────────────

    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str:
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=ttl_days)
        with self._lock:
            self._conn.execute(
                "INSERT INTO refresh_tokens (token, account_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token, account_id, now.isoformat(timespec="seconds"),
                 expires.isoformat(timespec="seconds")))
            self._conn.commit()
        return token

    async def get_refresh_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM refresh_tokens WHERE token = ? AND revoked = 0",
                (token,)).fetchone()
        if not row:
            return None
        r = dict(row)
        # Hết hạn thì coi như không có — caller không phải kiểm lại.
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc):
            return None
        return r

    async def revoke_refresh_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE token = ?", (token,))
            self._conn.commit()

    async def revoke_all_refresh_tokens(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE account_id = ?", (account_id,))
            self._conn.commit()

    # ── Admin management ─────────────────────────────────────────────────────────

    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict:
        """Tạo invite key mới — key tự sinh dạng XXXX-XXXX, hết hạn sau `expires_days` ngày."""
        key = _gen_key()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=expires_days)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO invite_keys (key, role, label, max_uses, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, role, label, max_uses, now.isoformat(timespec="seconds"), expires))
            self._conn.commit()
        return {"key": key, "role": role, "label": label, "max_uses": max_uses,
                "expires_at": expires, "status": "active", "used_count": 0,
                "created_at": now.isoformat(timespec="seconds")}

    async def list_invite_keys(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM invite_keys ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    async def revoke_invite_key(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE invite_keys SET status = 'revoked' WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    async def list_accounts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, username, email, role, invite_key, email_verified, "
                "created_at, last_login, status "
                "FROM accounts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    async def set_account_status(self, account_id: str, new_status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE accounts SET status = ? WHERE id = ?", (new_status, account_id))
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_account(self, account_id: str) -> bool:
        """Xoá tài khoản và revoke mọi refresh token — chỉ admin mới gọi được."""
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE account_id = ?", (account_id,))
            cur = self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def update_account_profile(self, account_id: str, updates: dict) -> None:
        """User tự cập nhật hồ sơ — chỉ cho phép username, email."""
        allowed = {"username", "email"}
        parts = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ?")
                vals.append(v)
        if not parts:
            return
        vals.append(account_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ?", tuple(vals))
            self._conn.commit()

    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool:
        """Admin chỉnh sửa user — cho phép username, email, role."""
        allowed = {"username", "email", "role"}
        parts = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ?")
                vals.append(v)
        if not parts:
            return True
        vals.append(account_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ?", tuple(vals))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Liên kết Tool ⇄ account (mã pairing 1 lần) ─────────────────────────────

    async def tool_user_id_exists(self, tool_user_id: str) -> bool:
        """`True` nếu `tool_user_id` đã từng xuất hiện trong `documents` — chặn redeem bằng ID
        bịa/đoán mò (không chặn được kẻ ĐÃ biết đúng ID nạn nhân — xem RULES.md phần rủi ro còn
        mở). Bảng `documents` chưa tồn tại (dev/LocalFileDb không dùng bảng này, hoặc test chưa
        seed) → trả False, fail-closed."""
        rows = self._safe_docs_query(
            "SELECT 1 FROM documents WHERE user_id = ? LIMIT 1", (tool_user_id,))
        return len(rows) > 0

    async def create_link_code(self, account_id: str, ttl_minutes: int = 15) -> dict:
        code = _gen_link_code()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO link_codes (code, account_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (code, account_id, now.isoformat(timespec="seconds"), expires))
            self._conn.commit()
        return {"code": code, "expires_at": expires}

    async def redeem_link_code(self, code: str, tool_user_id: str,
                               device_id: str = "") -> dict | None:
        """Đổi mã lấy liên kết — dùng 1 lần. Trả None nếu mã sai/hết hạn/đã dùng/`tool_user_id`
        không tồn tại — CỐ Ý dùng chung 1 kết quả None cho mọi lý do thất bại (router trả cùng 1
        thông báo lỗi) để không biến đây thành oracle dò `tool_user_id` nào có thật.

        Kiểm `tool_user_id_exists` NGOÀI `with self._lock` — `_safe_docs_query` cũng tự khoá
        `self._lock`, `threading.Lock` không tái nhập được, gọi lồng bên trong sẽ tự deadlock.
        Sai `tool_user_id` → KHÔNG đốt mã (giữ `status='pending')`, vì nhiều khả năng là Tool gõ
        nhầm — cho thử lại trong TTL thay vì bắt tạo mã mới.

        `device_id` chưa dùng tới (dự trù cho sau này khi Tool có UI thật) — nhận để giữ contract
        ổn định, không phá API khi thêm logic đa thiết bị."""
        if not await self.tool_user_id_exists(tool_user_id):
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM link_codes WHERE code = ? AND status = 'pending'", (code,)).fetchone()
            if not row:
                return None
            row = dict(row)
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                self._conn.execute(
                    "UPDATE link_codes SET status = 'expired' WHERE code = ?", (code,))
                self._conn.commit()
                return None
            now = _now()
            self._conn.execute(
                "UPDATE link_codes SET status = 'used', used_at = ? WHERE code = ?", (now, code))
            self._conn.execute(
                "INSERT INTO account_links (account_id, tool_user_id, linked_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(account_id, tool_user_id) DO UPDATE SET status = 'active'",
                (row["account_id"], tool_user_id, now))
            self._conn.commit()
        return {"account_id": row["account_id"], "tool_user_id": tool_user_id}

    async def list_links(self, account_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_user_id, label, status, linked_at FROM account_links "
                "WHERE account_id = ? AND status = 'active' ORDER BY linked_at DESC",
                (account_id,)).fetchall()
        return [dict(r) for r in rows]

    async def revoke_link(self, account_id: str, tool_user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE account_links SET status = 'revoked' "
                "WHERE account_id = ? AND tool_user_id = ? AND status = 'active'",
                (account_id, tool_user_id))
            self._conn.commit()
            return cur.rowcount > 0

    async def linked_user_ids(self, account_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_user_id FROM account_links WHERE account_id = ? AND status = 'active'",
                (account_id,)).fetchall()
        return [r["tool_user_id"] for r in rows]

    # ── Group/tag kênh (metadata riêng, KHÔNG phải repost_groups của Tool) ──────

    async def create_channel_group(self, account_id: str, name: str) -> dict:
        gid = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO channel_groups (id, account_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (gid, account_id, name, now, now))
            self._conn.commit()
        return {"id": gid, "account_id": account_id, "name": name,
                "created_at": now, "updated_at": now}

    async def list_channel_groups(self, account_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM channel_groups WHERE account_id = ? ORDER BY created_at DESC",
                (account_id,)).fetchall()
        return [dict(r) for r in rows]

    async def get_channel_group(self, group_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM channel_groups WHERE id = ?", (group_id,)).fetchone()
        return dict(row) if row else None

    async def rename_channel_group(self, group_id: str, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE channel_groups SET name = ?, updated_at = ? WHERE id = ?",
                (name, _now(), group_id))
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_channel_group(self, group_id: str) -> bool:
        with self._lock:
            self._conn.execute(
                "DELETE FROM channel_group_members WHERE group_id = ?", (group_id,))
            cur = self._conn.execute("DELETE FROM channel_groups WHERE id = ?", (group_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def list_group_members(self, group_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT channel_ref, platform_tag, country_override FROM channel_group_members "
                "WHERE group_id = ? ORDER BY created_at", (group_id,)).fetchall()
        return [dict(r) for r in rows]

    async def add_group_member(self, group_id: str, channel_ref: str, platform_tag: str = "",
                               country_override: str = "") -> dict:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO channel_group_members "
                "(group_id, channel_ref, platform_tag, country_override, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id, channel_ref) DO UPDATE SET "
                "platform_tag = excluded.platform_tag, country_override = excluded.country_override",
                (group_id, channel_ref, platform_tag, country_override, now))
            self._conn.execute(
                "UPDATE channel_groups SET updated_at = ? WHERE id = ?", (now, group_id))
            self._conn.commit()
        return {"group_id": group_id, "channel_ref": channel_ref, "platform_tag": platform_tag,
                "country_override": country_override}

    async def remove_group_member(self, group_id: str, channel_ref: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM channel_group_members WHERE group_id = ? AND channel_ref = ?",
                (group_id, channel_ref))
            self._conn.commit()
            return cur.rowcount > 0

    async def group_channel_refs(self, group_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT channel_ref FROM channel_group_members WHERE group_id = ?",
                (group_id,)).fetchall()
        return {r["channel_ref"] for r in rows}

    async def channel_refs_by_platform(self, platform_tag: str,
                                       account_ids: list[str] | None = None) -> set[str]:
        """channel_ref nào được gắn `platform_tag` này — dùng cho bộ lọc "Nền tảng" (dựa trên nhãn
        tự gắn trong Nhóm kênh, KHÔNG phải phân loại tự động). `account_ids=None` → xuyên mọi
        account (chỉ admin/dash_admin không chỉ định owner mới đi nhánh này)."""
        with self._lock:
            if account_ids is None:
                rows = self._conn.execute(
                    "SELECT DISTINCT channel_ref FROM channel_group_members "
                    "WHERE platform_tag = ?", (platform_tag,)).fetchall()
            else:
                placeholders = ",".join("?" * len(account_ids))
                rows = self._conn.execute(
                    "SELECT DISTINCT m.channel_ref FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    f"WHERE m.platform_tag = ? AND g.account_id IN ({placeholders})",
                    (platform_tag, *account_ids)).fetchall()
        return {r["channel_ref"] for r in rows}

    async def list_platform_tags(self, account_ids: list[str] | None = None) -> list[str]:
        with self._lock:
            if account_ids is None:
                rows = self._conn.execute(
                    "SELECT DISTINCT platform_tag FROM channel_group_members "
                    "WHERE platform_tag != '' ORDER BY platform_tag").fetchall()
            else:
                placeholders = ",".join("?" * len(account_ids))
                rows = self._conn.execute(
                    "SELECT DISTINCT m.platform_tag FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    f"WHERE m.platform_tag != '' AND g.account_id IN ({placeholders}) "
                    "ORDER BY m.platform_tag", account_ids).fetchall()
        return [r["platform_tag"] for r in rows]

    async def channel_refs_by_native_platform(self, platform: str,
                                              user_ids: list[str] | None = None) -> set[str]:
        """`target`/`channel_id` THẬT thuộc nền tảng gốc (facebook/youtube) — suy trực tiếp từ cấu
        trúc dữ liệu (`fb_page_meta`/`yt_channels`), KHÔNG qua nhãn tự gắn `platform_tag`. Dùng khi
        bộ lọc "Nền tảng" hợp nhất (1 nút duy nhất) chọn đúng 1 trong 2 giá trị dự trữ
        'youtube'/'facebook' thay vì 1 nhãn tự do."""
        if platform == "youtube":
            return {c["id"] for c in self._yt_channels_meta(user_ids=user_ids)}
        if platform == "facebook":
            return {m["target"] for m in self._fb_page_meta(user_ids=user_ids)}
        return set()

    async def country_overrides(self, account_ids: list[str] | None = None) -> dict[str, str]:
        """`{channel_ref: quốc_gia}` từ `channel_group_members.country_override` — viewer tự sửa
        quốc gia của 1 kênh khi map nó vào group (đồng bộ với nhãn nền tảng cùng lúc gắn). Đây là
        nơi DUY NHẤT đọc cột này — `_apply_country_overrides` ghi đè quốc gia gốc bằng giá trị này.
        Giá trị trả về ĐÃ chuẩn hoá qua `country_aliases` — viewer gõ "brazil" cũng về "Brazil"."""
        with self._lock:
            if account_ids is None:
                rows = self._conn.execute(
                    "SELECT channel_ref, country_override FROM channel_group_members "
                    "WHERE country_override != ''").fetchall()
            else:
                placeholders = ",".join("?" * len(account_ids))
                rows = self._conn.execute(
                    "SELECT m.channel_ref, m.country_override FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    f"WHERE m.country_override != '' AND g.account_id IN ({placeholders})",
                    account_ids).fetchall()
        aliases = self._country_alias_map()
        return {r["channel_ref"]: _normalize_country(r["country_override"], aliases) for r in rows}

    # ── Chuẩn hoá quốc gia (alias → tên chuẩn) ────────────────────────────────

    def _country_alias_map(self) -> dict[str, str]:
        """`{alias viết thường: tên chuẩn}` — tra 1 lần/lượt gọi loader, dùng cho mọi giá trị quốc
        gia đọc được (native lẫn `country_override`). Bảng chưa có (test chưa seed) → rỗng, an toàn."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT alias, canonical FROM country_aliases").fetchall()
            except sqlite3.OperationalError:
                return {}
        return {r["alias"]: r["canonical"] for r in rows}

    async def list_country_aliases(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM country_aliases ORDER BY canonical, alias").fetchall()
        return [dict(r) for r in rows]

    async def upsert_country_alias(self, alias: str, canonical: str) -> dict:
        key = alias.strip().lower()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO country_aliases (alias, canonical, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical",
                (key, canonical.strip(), now))
            self._conn.commit()
        return {"alias": key, "canonical": canonical.strip()}

    async def delete_country_alias(self, alias: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM country_aliases WHERE alias = ?", (alias.strip().lower(),))
            self._conn.commit()
            return cur.rowcount > 0

    async def list_country_canonicals(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM country_canonicals ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    async def get_country_canonical(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM country_canonicals WHERE lower(name) = lower(?)",
                (name.strip(),)).fetchone()
        return dict(row) if row else None

    async def create_country_canonical(self, name: str) -> dict:
        key = name.strip()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO country_canonicals (name, created_at) VALUES (?, ?)", (key, now))
            self._conn.commit()
        return {"name": key, "created_at": now}

    async def rename_country_canonical(self, old_name: str, new_name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE country_canonicals SET name = ? WHERE lower(name) = lower(?)",
                (new_name.strip(), old_name.strip()))
            self._conn.execute(
                "UPDATE country_aliases SET canonical = ? WHERE lower(canonical) = lower(?)",
                (new_name.strip(), old_name.strip()))
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_country_canonical(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM country_canonicals WHERE lower(name) = lower(?)", (name.strip(),))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Viewer data ─────────────────────────────────────────────────────────────
    #
    # Đọc từ bảng `documents` — bảng này do sync server tạo (DDL ở `server/db.py`). Admin-server
    # KHÔNG tạo bảng này; nếu bảng chưa tồn tại (test environment) thì trả rỗng thay vì nổ lỗi.

    def _safe_docs_query(self, sql: str, args: tuple) -> list[dict]:
        """Truy vấn bảng `documents` — trả [] nếu bảng chưa tồn tại (môi trường test)."""
        with self._lock:
            try:
                rows = self._conn.execute(sql, args).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    return []
                raise

    def _load_fb(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        """Tải fb_posts — lọc quốc gia nếu cần (cùng dùng cho summary/traffic/top).

        Import script lưu target gốc trong trường `_target` (để tránh trùng tên trường khác).
        Chuẩn hoá thành `target` để code sau dùng chung — giống Electron fb_store.
        `user_ids=None` không lọc theo Tool; danh sách cụ thể chỉ giữ bản ghi của các Tool đó."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents WHERE store = 'fb_posts' AND deleted = 0", ())
        result = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("_empty"):
                continue
            if country and p.get("country", "") != country:
                continue
            # Chuẩn hoá: đảm bảo mọi post đều có trường `target` dùng được
            if not p.get("target") and p.get("_target"):
                p["target"] = p["_target"]
            result.append(p)
        return result

    def _load_yt(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        """Tải yt_insights_daily — lọc quốc gia nếu cần."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'yt_insights_daily' AND deleted = 0", ())
        result = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if country and p.get("country", "") != country:
                continue
            result.append(p)
        return result

    def _load_events(self, user_ids: list[str] | None = None) -> list[dict]:
        """Tải repost_events — nhật ký đăng bài (dùng đếm videos_posted trong summary).

        Electron đọc từ repost_events.json (list_by_id shape), admin đọc từ documents table.
        Chỉ lấy event có kind — bỏ record rác."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'repost_events' AND deleted = 0", ())
        result = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("kind"):
                result.append(p)
        return result

    def _load_pairings(self, user_ids: list[str] | None = None) -> list[dict]:
        """Tải repost_pairings — cặp reup (dùng đếm managed/active channels trong summary).

        Electron đọc từ repost_pairings.json (dict_of_records shape)."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'repost_pairings' AND deleted = 0", ())
        result = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("target"):
                result.append(p)
        return result

    def _load_groups(self, user_ids: list[str] | None = None) -> list[dict]:
        """Tải repost_groups — nhóm reup (dùng kiểm cho_chay trong summary)."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'repost_groups' AND deleted = 0", ())
        result = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            if p.get("id"):
                result.append(p)
        return result

    def _fb_page_meta(self, user_ids: list[str] | None = None) -> list[dict]:
        """Projection fb_page_meta (target → tên+quốc gia page) do Tool đẩy lên — thay HOÀN TOÀN
        lối đọc `fb_pages` cũ (tên page bị seal ở production). Shape dict_of_records.

        `country` được CHUẨN HOÁ qua `country_aliases` ngay tại đây — Tool tự gõ tay quốc gia
        (vd "BR"/"Brazil"/"brazil") nên phải gộp về 1 tên duy nhất trước khi vào `stats_core`."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'fb_page_meta' AND deleted = 0", ())
        aliases = self._country_alias_map()
        out: list[dict] = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            t = p.get("target")
            if t:
                out.append({"target": t, "name": p.get("name") or t,
                            "country": _normalize_country(p.get("country") or "", aliases)})
        return out

    def _yt_channels_meta(self, user_ids: list[str] | None = None) -> list[dict]:
        """Meta kênh YouTube (id/title/country) từ store `yt_channels` — KHÔNG đọc từ
        `yt_insights_daily` (không có các trường này). Bỏ bản ghi `_meta` (không có `id`).
        `country` chuẩn hoá qua `country_aliases` — xem ghi chú `_fb_page_meta`."""
        rows = self._safe_docs_query(
            "SELECT user_id, payload FROM documents "
            "WHERE store = 'yt_channels' AND deleted = 0", ())
        aliases = self._country_alias_map()
        out: list[dict] = []
        for row in rows:
            if user_ids is not None and row.get("user_id") not in user_ids:
                continue
            p = json.loads(row["payload"]) if row.get("payload") else {}
            cid = p.get("id")
            if cid:
                out.append({"id": cid, "title": p.get("title") or "",
                            "country": _normalize_country(p.get("country") or "", aliases)})
        return out

    async def traffic_metric(self, date_from: str, date_to: str, scope: str = "", country: str = "",
                             user_ids: list[str] | None = None,
                             channel_refs: set[str] | None = None,
                             country_overrides: dict[str, str] | None = None) -> dict:
        """Traffic theo chỉ số — nay tính qua `stats_core` (nguồn CHUNG với Tool)."""
        meta = self._fb_page_meta(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, self._load_pairings(user_ids=user_ids),
                                           self._load_groups(user_ids=user_ids))
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = self._load_fb(user_ids=user_ids)
        yt_daily = self._load_yt(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)
        return stats_core.traffic_metric(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            events=self._load_events(user_ids=user_ids), scope=scope, country=country,
            yt_filter=yt_f, fb_filter=fb_f)

    async def summary(self, date_from: str, date_to: str, country: str = "",
                      user_ids: list[str] | None = None,
                      channel_refs: set[str] | None = None,
                      country_overrides: dict[str, str] | None = None) -> dict:
        """KPI tổng — nay tính qua `stats_core`. Kỳ trước: cùng độ dài, liền kề (khớp router Tool)."""
        d0 = date.fromisoformat(date_from)
        so_ngay = len(stats_core.days_between(date_from, date_to))
        prev_from = (d0 - timedelta(days=so_ngay)).isoformat()
        prev_to = (d0 - timedelta(days=1)).isoformat()

        meta = self._fb_page_meta(user_ids=user_ids)
        pairings = self._load_pairings(user_ids=user_ids)
        groups = self._load_groups(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = self._load_fb(user_ids=user_ids)
        yt_daily = self._load_yt(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        if channel_refs is not None:
            pairings = [p for p in pairings if p.get("target") in channel_refs]
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)

        events = self._load_events(user_ids=user_ids)
        if channel_refs is not None:
            events = [e for e in events if e.get("target") in channel_refs]
        ev_now = [e for e in events if date_from <= (e.get("date") or "") <= date_to]
        ev_prev = [e for e in events if prev_from <= (e.get("date") or "") <= prev_to]

        return stats_core.summary(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            events=ev_now, prev_events=ev_prev,
            prev_from=prev_from, prev_to=prev_to,
            pairings=pairings, groups=groups,
            yt_filter=yt_f, fb_filter=fb_f)

    async def top_channels(self, date_from: str, date_to: str, country: str = "", limit: int = 15,
                           user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[dict]:
        """Top kênh/page theo views — nay tính qua `stats_core`. Trả list (viewer bọc {"items"})."""
        meta = self._fb_page_meta(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, self._load_pairings(user_ids=user_ids),
                                           self._load_groups(user_ids=user_ids))
        tn = stats_core.target_names(meta)
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = self._load_fb(user_ids=user_ids)
        yt_daily = self._load_yt(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        result = stats_core.top_channels(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            channel_meta=chans, target_names=tn, target_country=tc,
            country=country, limit=limit)
        return result["items"]

    async def views_by_country(self, date_from: str, date_to: str,
                               user_ids: list[str] | None = None,
                               channel_refs: set[str] | None = None,
                               country_overrides: dict[str, str] | None = None) -> dict:
        """Views theo quốc gia/ngày — nay tính qua `stats_core`."""
        meta = self._fb_page_meta(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, self._load_pairings(user_ids=user_ids),
                                           self._load_groups(user_ids=user_ids))
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = self._load_fb(user_ids=user_ids)
        yt_daily = self._load_yt(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        return stats_core.views_by_country(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            channel_meta=chans, target_country=tc)

    async def country_tags(self, user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[str]:
        """Danh mục quốc gia — nay tính qua `stats_core`."""
        meta = self._fb_page_meta(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, self._load_pairings(user_ids=user_ids),
                                           self._load_groups(user_ids=user_ids))
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        _, _, chans, tc = _apply_channel_refs([], [], chans, tc, channel_refs)
        return stats_core.country_tags(channel_meta=chans, target_country=tc)

    async def channel_breakdown(self, user_ids: list[str] | None = None,
                                channel_refs: set[str] | None = None,
                                country_overrides: dict[str, str] | None = None,
                                owner_scope: list[str] | None = None) -> dict:
        """Phân tích kênh theo platform/chủ đề/quốc gia — xem `_channel_breakdown` (module-level,
        thuần, KHÔNG đụng `stats_core.py`)."""
        meta = self._fb_page_meta(user_ids=user_ids)
        pairings = self._load_pairings(user_ids=user_ids)
        groups = self._load_groups(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = self._yt_channels_meta(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        if channel_refs is not None:
            pairings = [p for p in pairings if p.get("target") in channel_refs]
            chans = [c for c in chans if c["id"] in channel_refs]
        result = _channel_breakdown(pairings, groups, chans, tc)

        # Nhãn nền tảng tự do (vd "tiktok") — THÔNG TIN BỔ SUNG hiển thị riêng, KHÔNG cộng vào
        # `total` (channel_ref đã đếm trong facebook/youtube native ở trên — nhãn tự do chỉ gắn
        # THÊM vào kênh có sẵn, không phải nền tảng vật lý riêng, cộng vào sẽ đếm đúp).
        for tag in await self.list_platform_tags(owner_scope):
            refs = await self.channel_refs_by_platform(tag, owner_scope)
            if channel_refs is not None:
                refs = refs & channel_refs
            result["by_platform"].append({
                "platform": tag, "managed": len(refs), "active": len(refs), "is_custom_tag": True,
            })
        return result

    async def last_synced_at(self, user_ids: list[str] | None = None) -> str | None:
        """Dev/test (SQLite) KHÔNG có bảng `user_meta` — bảng đó do sync server VPS tạo, chỉ tồn
        tại ở Postgres production. Trả `None` = "không rõ độ mới" thay vì bịa mốc giờ giả."""
        return None

    async def doc_version(self) -> str:
        """Con dấu phiên bản kho `documents` — đổi khi có ghi mới.

        Bản SQLite dùng cho dev/test: `import_local_data.py` xoá sạch rồi nạp lại nên cả số bản
        ghi lẫn `MAX(rev)` đều đổi. Bảng chưa tồn tại → `_safe_docs_query` trả [] → mốc cố định,
        lúc đó TTL của cache là thứ duy nhất giới hạn tuổi dữ liệu."""
        rows = self._safe_docs_query(
            "SELECT COUNT(*) AS n, COALESCE(MAX(rev), 0) AS r FROM documents", ())
        if not rows:
            return "na"
        return f"{rows[0]['n']}.{rows[0]['r']}"


# ── Postgres ────────────────────────────────────────────────────────────────────

class PostgresDb(Db):
    """Cho triển khai thật — cùng CSDL ufsync với sync server."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def setup(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
        async with self._pool.acquire() as c:
            await c.execute(_PG_DDL)
            now = _now()
            # Chỉ seed mặc định khi bảng THỰC SỰ trống (DB mới hoàn toàn) — xem ghi chú bản SQLite:
            # "INSERT ... ON CONFLICT DO NOTHING" vô điều kiện mỗi lần khởi động sẽ HỒI SINH alias
            # đã bị xoá hẳn (dòng không còn -> không còn gì để "DO NOTHING" chặn). Giờ chỉ seed
            # đúng 1 lần ở DB rỗng.
            existing = await c.fetchval("SELECT COUNT(*) FROM country_aliases")
            if existing == 0:
                await c.executemany(
                    "INSERT INTO country_aliases (alias, canonical, created_at) "
                    "VALUES ($1, $2, $3) ON CONFLICT (alias) DO NOTHING",
                    [(a, c2, now) for a, c2 in _DEFAULT_COUNTRY_ALIASES])
            # Backfill tên chuẩn "chính thức" từ các canonical đã có (mặc định + dữ liệu cũ) —
            # tự hồi phục nếu DB đã tồn tại trước khi bảng này ra đời, không cần migration tay.
            await c.execute(
                "INSERT INTO country_canonicals (name, created_at) "
                "SELECT DISTINCT canonical, $1 FROM country_aliases "
                "ON CONFLICT (name) DO NOTHING", now)
            existing_pp = await c.fetchval("SELECT COUNT(*) FROM platform_policies")
            if existing_pp == 0:
                await c.executemany(
                    "INSERT INTO platform_policies "
                    "(platform, web_entry_enabled, fallback_enabled, adapter_status, updated_at) "
                    "VALUES ($1, $2, $3, 'none', $4) ON CONFLICT (platform) DO NOTHING",
                    [(p, w, f, now) for p, w, f in _DEFAULT_PLATFORM_POLICIES])

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Local Fallback Tool v1 — platform policy ─────────────────────────────────

    async def list_platform_policies(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM platform_policies ORDER BY platform")
        return [dict(r) for r in rows]

    async def get_platform_policy(self, platform: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM platform_policies WHERE platform = $1", platform)
        return dict(row) if row else None

    async def upsert_platform_policy(self, platform: str, web_entry_enabled: bool | None,
                                     fallback_enabled: bool | None, adapter_status: str | None,
                                     updated_by: str) -> dict:
        now = _now()
        async with self._pool.acquire() as c:
            cur = await c.fetchrow("SELECT * FROM platform_policies WHERE platform = $1", platform)
            base = dict(cur) if cur else {
                "web_entry_enabled": True, "fallback_enabled": False, "adapter_status": "none",
            }
            w = base["web_entry_enabled"] if web_entry_enabled is None else web_entry_enabled
            f = base["fallback_enabled"] if fallback_enabled is None else fallback_enabled
            a = base["adapter_status"] if adapter_status is None else adapter_status
            await c.execute(
                "INSERT INTO platform_policies "
                "(platform, web_entry_enabled, fallback_enabled, adapter_status, updated_by, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (platform) DO UPDATE SET web_entry_enabled=excluded.web_entry_enabled, "
                "fallback_enabled=excluded.fallback_enabled, adapter_status=excluded.adapter_status, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                platform, w, f, a, updated_by, now)
        await self.log_audit(updated_by, "platform_policy_changed", "policy", platform,
                             {"web_entry_enabled": w, "fallback_enabled": f, "adapter_status": a})
        return {"platform": platform, "web_entry_enabled": w, "fallback_enabled": f,
                "adapter_status": a, "updated_by": updated_by, "updated_at": now}

    # ── Resource Sources (namespace nghiệp vụ, mở rộng vô hạn — KHÔNG hardcode tên) ──

    async def create_resource_source(self, owner_account_id: str, name: str, platform: str,
                                     project_key: str = "", default_country: str = "",
                                     origin: str = "web_manual") -> dict:
        sid = uuid.uuid4().hex
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO resource_sources "
                "(id, owner_account_id, name, platform, project_key, default_country, origin, "
                "created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                sid, owner_account_id, name, platform, project_key, default_country, origin,
                now, now)
        return {"id": sid, "owner_account_id": owner_account_id, "name": name, "platform": platform,
                "project_key": project_key, "default_country": default_country, "origin": origin,
                "created_at": now, "updated_at": now}

    async def list_resource_sources(self, owner_account_id: str | None = None) -> list[dict]:
        async with self._pool.acquire() as c:
            if owner_account_id is None:
                rows = await c.fetch("SELECT * FROM resource_sources ORDER BY platform, name")
            else:
                rows = await c.fetch(
                    "SELECT * FROM resource_sources WHERE owner_account_id = $1 "
                    "ORDER BY platform, name", owner_account_id)
        return [dict(r) for r in rows]

    async def get_resource_source(self, source_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM resource_sources WHERE id = $1", source_id)
        return dict(row) if row else None

    async def update_resource_source(self, source_id: str, updates: dict) -> bool:
        allowed = {"name", "project_key", "default_country"}
        fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not fields:
            return False
        fields["updated_at"] = _now()
        cols = list(fields.keys())
        set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(cols))
        async with self._pool.acquire() as c:
            tag = await c.execute(
                f"UPDATE resource_sources SET {set_clause} WHERE id = ${len(cols) + 1}",
                *fields.values(), source_id)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Managed Resources (danh tính canonical) + Assignments ───────────────────────

    async def create_managed_resource(self, *, owner_account_id: str, platform: str,
                                      resource_type: str, input_ref: str, username: str, url: str,
                                      display_name: str, country: str, origin: str,
                                      execution_mode: str, source_instance_id: str,
                                      actor_account_id: str) -> dict:
        rid = uuid.uuid4().hex
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO managed_resources "
                "(id, owner_account_id, platform, resource_type, input_ref, username, url, "
                "display_name, country, origin, mapping_status, execution_mode, "
                "verification_status, enabled, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'unmapped', $11, "
                "'unverified', true, $12, $13)",
                rid, owner_account_id, platform, resource_type, input_ref, username, url,
                display_name, country, origin, execution_mode, now, now)
            if source_instance_id:
                aid = uuid.uuid4().hex
                await c.execute(
                    "INSERT INTO resource_assignments "
                    "(id, resource_id, source_instance_id, owner_account_id, enabled, "
                    "created_at, updated_at) VALUES ($1, $2, $3, $4, true, $5, $6)",
                    aid, rid, source_instance_id, owner_account_id, now, now)
        await self.log_audit(actor_account_id, "resource_created", "resource", rid,
                             {"platform": platform, "input_ref": input_ref, "origin": origin,
                              "execution_mode": execution_mode})
        if source_instance_id:
            await self.log_audit(actor_account_id, "assignment_created", "resource", rid,
                                 {"source_instance_id": source_instance_id})
        return await self.get_managed_resource(rid)

    async def list_managed_resources(self, owner_account_id: str | None,
                                     filters: dict) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []

        def add(clause_tmpl: str, value: Any) -> None:
            params.append(value)
            clauses.append(clause_tmpl.format(len(params)))

        if owner_account_id is not None:
            add("owner_account_id = ${}", owner_account_id)
        for col in ("platform", "country", "origin", "mapping_status", "execution_mode",
                    "verification_status"):
            v = filters.get(col)
            if v:
                add(f"{col} = ${{}}", v)
        if filters.get("enabled") is not None:
            add("enabled = ${}", bool(filters["enabled"]))
        if filters.get("source_instance_id"):
            add("id IN (SELECT resource_id FROM resource_assignments WHERE source_instance_id = ${})",
                filters["source_instance_id"])
        q = (filters.get("q") or "").strip()
        if q:
            params.append(f"%{q}%")
            i = len(params)
            clauses.append(
                f"(display_name ILIKE ${i} OR username ILIKE ${i} OR url ILIKE ${i} "
                f"OR input_ref ILIKE ${i})")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                f"SELECT * FROM managed_resources {where} ORDER BY created_at DESC", *params)
            out = [dict(r) for r in rows]
            if out:
                arows = await c.fetch(
                    "SELECT resource_id, source_instance_id FROM resource_assignments "
                    "WHERE resource_id = ANY($1::text[])", [r["id"] for r in out])
                by_resource: dict[str, list[str]] = {}
                for ar in arows:
                    by_resource.setdefault(ar["resource_id"], []).append(ar["source_instance_id"])
                for r in out:
                    r["source_ids"] = by_resource.get(r["id"], [])
        return out

    async def get_managed_resource(self, resource_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM managed_resources WHERE id = $1", resource_id)
        return dict(row) if row else None

    async def update_managed_resource(self, resource_id: str, updates: dict,
                                      actor_account_id: str) -> bool:
        allowed = {"display_name", "username", "url", "country", "resource_type",
                  "execution_mode", "verification_status", "mapping_status", "enabled",
                  "canonical_external_id"}
        fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not fields:
            return False
        if "enabled" in fields:
            fields["enabled"] = bool(fields["enabled"])
        fields["updated_at"] = _now()
        cols = list(fields.keys())
        set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(cols))
        async with self._pool.acquire() as c:
            tag = await c.execute(
                f"UPDATE managed_resources SET {set_clause} WHERE id = ${len(cols) + 1}",
                *fields.values(), resource_id)
        try:
            ok = int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            ok = False
        if ok:
            event = "execution_mode_changed" if "execution_mode" in updates else "resource_updated"
            await self.log_audit(actor_account_id, event, "resource", resource_id, dict(updates))
        return ok

    async def delete_managed_resource(self, resource_id: str, actor_account_id: str) -> bool:
        async with self._pool.acquire() as c:
            await c.execute("DELETE FROM resource_assignments WHERE resource_id = $1", resource_id)
            tag = await c.execute("DELETE FROM managed_resources WHERE id = $1", resource_id)
        try:
            ok = int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            ok = False
        if ok:
            await self.log_audit(actor_account_id, "resource_deleted", "resource", resource_id, {})
        return ok

    async def list_resource_assignments(self, resource_id: str) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM resource_assignments WHERE resource_id = $1 ORDER BY created_at",
                resource_id)
        return [dict(r) for r in rows]

    async def inventory_revision(self, owner_account_id: str | None = None) -> str:
        async with self._pool.acquire() as c:
            if owner_account_id is None:
                row = await c.fetchrow(
                    "SELECT COALESCE(MAX(updated_at), '') AS m, COUNT(*) AS n "
                    "FROM managed_resources")
            else:
                row = await c.fetchrow(
                    "SELECT COALESCE(MAX(updated_at), '') AS m, COUNT(*) AS n "
                    "FROM managed_resources WHERE owner_account_id = $1", owner_account_id)
        return f"{row['m']}.{row['n']}"

    async def list_inventory_owners(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT DISTINCT a.id, a.username FROM accounts a "
                "JOIN managed_resources m ON m.owner_account_id = a.id "
                "ORDER BY a.username")
        return [dict(r) for r in rows]

    # ── Run Queue (batch/item) ───────────────────────────────────────────────────

    async def create_operation_batch(self, created_by: str, platform: str, name: str) -> dict:
        bid = uuid.uuid4().hex
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO operation_batches (id, created_by, platform, name, status, "
                "created_at, updated_at) VALUES ($1, $2, $3, $4, 'open', $5, $6)",
                bid, created_by, platform, name, now, now)
        await self.log_audit(created_by, "batch_created", "batch", bid,
                             {"platform": platform, "name": name})
        return {"id": bid, "created_by": created_by, "platform": platform, "name": name,
                "status": "open", "created_at": now, "updated_at": now}

    async def list_operation_batches(self, created_by: str | None = None) -> list[dict]:
        async with self._pool.acquire() as c:
            if created_by is None:
                rows = await c.fetch("SELECT * FROM operation_batches ORDER BY created_at DESC")
            else:
                rows = await c.fetch(
                    "SELECT * FROM operation_batches WHERE created_by = $1 "
                    "ORDER BY created_at DESC", created_by)
        return [dict(r) for r in rows]

    async def get_operation_batch(self, batch_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM operation_batches WHERE id = $1", batch_id)
        return dict(row) if row else None

    async def add_operation_items(self, batch_id: str, resource_ids: list[str]) -> dict:
        """Xem ghi chú bản SQLite — cùng quy tắc (mục 12/13/14/15 đặc tả), chỉ khác cú pháp
        asyncpg."""
        batch = await self.get_operation_batch(batch_id)
        if not batch:
            return {"added": [],
                    "skipped": [{"resource_id": r, "reason": "batch_not_found"} for r in resource_ids]}
        added: list[str] = []
        skipped: list[dict] = []
        now = _now()
        for rid in resource_ids:
            res = await self.get_managed_resource(rid)
            if not res:
                skipped.append({"resource_id": rid, "reason": "resource_not_found"})
                continue
            if res["platform"] != batch["platform"]:
                skipped.append({"resource_id": rid, "reason": "platform_mismatch"})
                continue
            if not res["enabled"]:
                skipped.append({"resource_id": rid, "reason": "resource_disabled"})
                continue
            if res["execution_mode"] != "fallback_local":
                skipped.append({"resource_id": rid, "reason": "execution_mode_not_fallback_local"})
                continue
            policy = await self.get_platform_policy(res["platform"])
            if not policy or not policy["fallback_enabled"]:
                skipped.append({"resource_id": rid, "reason": "platform_fallback_disabled"})
                continue
            status = "queued" if policy["adapter_status"] == "ready" else "blocked_no_adapter"
            assigns = await self.list_resource_assignments(rid)
            source_assignment_id = assigns[0]["id"] if assigns else ""
            iid = uuid.uuid4().hex
            async with self._pool.acquire() as c:
                await c.execute(
                    "INSERT INTO operation_items (id, batch_id, resource_id, source_assignment_id, "
                    "owner_account_id, platform, status, priority, attempts, last_error, "
                    "created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7, 0, 0, '', $8, $9)",
                    iid, batch_id, rid, source_assignment_id, res["owner_account_id"],
                    res["platform"], status, now, now)
            await self.log_audit(batch["created_by"], "item_queued", "item", iid,
                                 {"batch_id": batch_id, "resource_id": rid, "status": status})
            added.append(iid)
        return {"added": added, "skipped": skipped}

    async def list_operation_items(self, batch_id: str) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM operation_items WHERE batch_id = $1 ORDER BY created_at", batch_id)
        return [dict(r) for r in rows]

    async def cancel_operation_item(self, item_id: str, actor_account_id: str) -> bool:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT status FROM operation_items WHERE id = $1", item_id)
            if not row or row["status"] in ("success", "failed", "cancelled"):
                return False
            tag = await c.execute(
                "UPDATE operation_items SET status = 'cancelled', updated_at = $2 WHERE id = $1",
                item_id, _now())
        try:
            ok = int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            ok = False
        if ok:
            await self.log_audit(actor_account_id, "item_cancelled", "item", item_id, {})
        return ok

    # ── Audit log ─────────────────────────────────────────────────────────────────

    async def log_audit(self, account_id: str, event_type: str, target_type: str = "",
                        target_id: str = "", payload: dict | None = None) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO operation_audit_log (account_id, event_type, target_type, target_id, "
                "payload, at) VALUES ($1, $2, $3, $4, $5, $6)",
                account_id, event_type, target_type, target_id,
                json.dumps(payload or {}, ensure_ascii=False), _now())

    # ── Invite key ──────────────────────────────────────────────────────────────

    async def get_invite_key(self, key: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM invite_keys WHERE key = $1", key)
        return dict(row) if row else None

    async def use_invite_key(self, key: str) -> bool:
        """Xem ghi chú bản SQLite — cùng ý: kiểm và ghi CÙNG LÚC trong một câu UPDATE."""
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE invite_keys SET used_count = used_count + 1 "
                "WHERE key = $1 AND status = 'active' AND used_count < max_uses", key)
        # asyncpg trả "UPDATE 0" hoặc "UPDATE 1"
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Account ─────────────────────────────────────────────────────────────────

    async def create_account(self, username: str, email: str, password_hash: str,
                             role: str, invite_key: str) -> dict:
        account_id = uuid.uuid4().hex
        now = _now()
        verify_token = uuid.uuid4().hex
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO accounts "
                "(id, username, email, password_hash, role, invite_key, "
                " verify_token, verify_token_at, created_at, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending')",
                account_id, username, email.lower(), password_hash, role, invite_key,
                verify_token, now, now)
        return {"id": account_id, "username": username, "email": email.lower(),
                "role": role, "verify_token": verify_token, "status": "pending"}

    async def get_account_by_username(self, username: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE username = $1", username)
        return dict(row) if row else None

    async def get_account_by_email(self, email: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE email = $1", email.lower())
        return dict(row) if row else None

    async def get_account_by_id(self, account_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        return dict(row) if row else None

    async def username_exists(self, username: str) -> bool:
        async with self._pool.acquire() as c:
            return await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM accounts WHERE username = $1)", username)

    async def email_exists(self, email: str) -> bool:
        async with self._pool.acquire() as c:
            return await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM accounts WHERE email = $1)", email.lower())

    async def set_email_verified(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET email_verified = true, status = 'active', "
                "verify_token = '' WHERE id = $1", account_id)

    async def set_verify_token(self, account_id: str, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET verify_token = $2, verify_token_at = $3 WHERE id = $1",
                account_id, token, _now())

    async def get_account_by_verify_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM accounts WHERE verify_token = $1 AND verify_token != ''", token)
        return dict(row) if row else None

    async def set_reset_token(self, account_id: str, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET reset_token = $2, reset_token_at = $3 WHERE id = $1",
                account_id, token, _now())

    async def get_account_by_reset_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM accounts WHERE reset_token = $1 AND reset_token != ''", token)
        return dict(row) if row else None

    async def clear_reset_token(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET reset_token = '', reset_token_at = '' WHERE id = $1",
                account_id)

    async def update_password(self, account_id: str, password_hash: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET password_hash = $2 WHERE id = $1",
                account_id, password_hash)

    async def update_last_login(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE accounts SET last_login = $2 WHERE id = $1", account_id, _now())

    # ── Refresh token ───────────────────────────────────────────────────────────

    async def create_refresh_token(self, account_id: str, ttl_days: int = 30) -> str:
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=ttl_days)
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO refresh_tokens (token, account_id, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4)",
                token, account_id, now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"))
        return token

    async def get_refresh_token(self, token: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM refresh_tokens WHERE token = $1 AND NOT revoked", token)
        if not row:
            return None
        r = dict(row)
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc):
            return None
        return r

    async def revoke_refresh_token(self, token: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE token = $1", token)

    async def revoke_all_refresh_tokens(self, account_id: str) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE account_id = $1", account_id)

    # ── Admin management ─────────────────────────────────────────────────────────

    async def create_invite_key(self, role: str, label: str, max_uses: int,
                                expires_days: int) -> dict:
        key = _gen_key()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=expires_days)).isoformat(timespec="seconds")
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO invite_keys (key, role, label, max_uses, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                key, role, label, max_uses, now.isoformat(timespec="seconds"), expires)
        return {"key": key, "role": role, "label": label, "max_uses": max_uses,
                "expires_at": expires, "status": "active", "used_count": 0,
                "created_at": now.isoformat(timespec="seconds")}

    async def list_invite_keys(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM invite_keys ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def revoke_invite_key(self, key: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE invite_keys SET status = 'revoked' WHERE key = $1", key)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def list_accounts(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, username, email, role, invite_key, email_verified, "
                "created_at, last_login, status "
                "FROM accounts ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def set_account_status(self, account_id: str, new_status: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE accounts SET status = $2 WHERE id = $1", account_id, new_status)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def delete_account(self, account_id: str) -> bool:
        async with self._pool.acquire() as c:
            await c.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE account_id = $1", account_id)
            tag = await c.execute("DELETE FROM accounts WHERE id = $1", account_id)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def update_account_profile(self, account_id: str, updates: dict) -> None:
        allowed = {"username", "email"}
        parts = []
        vals = []
        idx = 1
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ${idx}")
                vals.append(v)
                idx += 1
        if not parts:
            return
        vals.append(account_id)
        async with self._pool.acquire() as c:
            await c.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ${idx}", *vals)

    async def update_account_by_admin(self, account_id: str, updates: dict) -> bool:
        allowed = {"username", "email", "role"}
        parts = []
        vals = []
        idx = 1
        for k, v in updates.items():
            if k in allowed:
                parts.append(f"{k} = ${idx}")
                vals.append(v)
                idx += 1
        if not parts:
            return True
        vals.append(account_id)
        async with self._pool.acquire() as c:
            tag = await c.execute(
                f"UPDATE accounts SET {', '.join(parts)} WHERE id = ${idx}", *vals)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Liên kết Tool ⇄ account (mã pairing 1 lần) ─────────────────────────────

    async def tool_user_id_exists(self, tool_user_id: str) -> bool:
        """Xem ghi chú bản SQLite. Bảng `documents` do sync server tạo — CSDL sạch/chưa chạy sync
        lần nào thì bảng có thể chưa tồn tại → trả False, fail-closed thay vì nổ lỗi."""
        try:
            async with self._pool.acquire() as c:
                row = await c.fetchval(
                    "SELECT 1 FROM documents WHERE user_id = $1 LIMIT 1", tool_user_id)
            return row is not None
        except Exception as e:  # noqa: BLE001 — thiếu bảng/lỗi tạm thời -> fail-closed, không nổ 500
            logger.debug("tool_user_id_exists that bai, fail-closed: %s", e)
            return False

    async def create_link_code(self, account_id: str, ttl_minutes: int = 15) -> dict:
        code = _gen_link_code()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO link_codes (code, account_id, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4)",
                code, account_id, now.isoformat(timespec="seconds"), expires)
        return {"code": code, "expires_at": expires}

    async def redeem_link_code(self, code: str, tool_user_id: str,
                               device_id: str = "") -> dict | None:
        """Xem ghi chú bản SQLite — cùng logic (kiểm tồn tại trước, không đốt mã nếu sai ID),
        giao dịch atomic qua transaction Postgres cho phần link_codes/account_links."""
        if not await self.tool_user_id_exists(tool_user_id):
            return None
        async with self._pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow(
                    "SELECT * FROM link_codes WHERE code = $1 AND status = 'pending' FOR UPDATE",
                    code)
                if not row:
                    return None
                row = dict(row)
                if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                    await c.execute(
                        "UPDATE link_codes SET status = 'expired' WHERE code = $1", code)
                    return None
                now = _now()
                await c.execute(
                    "UPDATE link_codes SET status = 'used', used_at = $2 WHERE code = $1",
                    code, now)
                await c.execute(
                    "INSERT INTO account_links (account_id, tool_user_id, linked_at) "
                    "VALUES ($1, $2, $3) "
                    "ON CONFLICT (account_id, tool_user_id) DO UPDATE SET status = 'active'",
                    row["account_id"], tool_user_id, now)
        return {"account_id": row["account_id"], "tool_user_id": tool_user_id}

    async def list_links(self, account_id: str) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT tool_user_id, label, status, linked_at FROM account_links "
                "WHERE account_id = $1 AND status = 'active' ORDER BY linked_at DESC", account_id)
        return [dict(r) for r in rows]

    async def revoke_link(self, account_id: str, tool_user_id: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE account_links SET status = 'revoked' "
                "WHERE account_id = $1 AND tool_user_id = $2 AND status = 'active'",
                account_id, tool_user_id)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def linked_user_ids(self, account_id: str) -> list[str]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT tool_user_id FROM account_links WHERE account_id = $1 AND status = 'active'",
                account_id)
        return [r["tool_user_id"] for r in rows]

    # ── Group/tag kênh (metadata riêng, KHÔNG phải repost_groups của Tool) ──────

    async def create_channel_group(self, account_id: str, name: str) -> dict:
        gid = uuid.uuid4().hex
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO channel_groups (id, account_id, name, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5)", gid, account_id, name, now, now)
        return {"id": gid, "account_id": account_id, "name": name,
                "created_at": now, "updated_at": now}

    async def list_channel_groups(self, account_id: str) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM channel_groups WHERE account_id = $1 ORDER BY created_at DESC",
                account_id)
        return [dict(r) for r in rows]

    async def get_channel_group(self, group_id: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM channel_groups WHERE id = $1", group_id)
        return dict(row) if row else None

    async def rename_channel_group(self, group_id: str, name: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "UPDATE channel_groups SET name = $2, updated_at = $3 WHERE id = $1",
                group_id, name, _now())
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def delete_channel_group(self, group_id: str) -> bool:
        async with self._pool.acquire() as c:
            await c.execute("DELETE FROM channel_group_members WHERE group_id = $1", group_id)
            tag = await c.execute("DELETE FROM channel_groups WHERE id = $1", group_id)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def list_group_members(self, group_id: str) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT channel_ref, platform_tag, country_override FROM channel_group_members "
                "WHERE group_id = $1 ORDER BY created_at", group_id)
        return [dict(r) for r in rows]

    async def add_group_member(self, group_id: str, channel_ref: str, platform_tag: str = "",
                               country_override: str = "") -> dict:
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO channel_group_members "
                "(group_id, channel_ref, platform_tag, country_override, created_at) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (group_id, channel_ref) DO UPDATE SET "
                "platform_tag = excluded.platform_tag, country_override = excluded.country_override",
                group_id, channel_ref, platform_tag, country_override, now)
            await c.execute(
                "UPDATE channel_groups SET updated_at = $2 WHERE id = $1", group_id, now)
        return {"group_id": group_id, "channel_ref": channel_ref, "platform_tag": platform_tag,
                "country_override": country_override}

    async def remove_group_member(self, group_id: str, channel_ref: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "DELETE FROM channel_group_members WHERE group_id = $1 AND channel_ref = $2",
                group_id, channel_ref)
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def group_channel_refs(self, group_id: str) -> set[str]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT channel_ref FROM channel_group_members WHERE group_id = $1", group_id)
        return {r["channel_ref"] for r in rows}

    async def channel_refs_by_platform(self, platform_tag: str,
                                       account_ids: list[str] | None = None) -> set[str]:
        """Xem ghi chú bản SQLite — cùng logic."""
        async with self._pool.acquire() as c:
            if account_ids is None:
                rows = await c.fetch(
                    "SELECT DISTINCT channel_ref FROM channel_group_members "
                    "WHERE platform_tag = $1", platform_tag)
            else:
                rows = await c.fetch(
                    "SELECT DISTINCT m.channel_ref FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    "WHERE m.platform_tag = $1 AND g.account_id = ANY($2::text[])",
                    platform_tag, account_ids)
        return {r["channel_ref"] for r in rows}

    async def list_platform_tags(self, account_ids: list[str] | None = None) -> list[str]:
        async with self._pool.acquire() as c:
            if account_ids is None:
                rows = await c.fetch(
                    "SELECT DISTINCT platform_tag FROM channel_group_members "
                    "WHERE platform_tag != '' ORDER BY platform_tag")
            else:
                rows = await c.fetch(
                    "SELECT DISTINCT m.platform_tag FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    "WHERE m.platform_tag != '' AND g.account_id = ANY($1::text[]) "
                    "ORDER BY m.platform_tag", account_ids)
        return [r["platform_tag"] for r in rows]

    async def channel_refs_by_native_platform(self, platform: str,
                                              user_ids: list[str] | None = None) -> set[str]:
        """Xem ghi chú bản SQLite — cùng logic, suy từ `fb_page_meta`/`yt_channels` thật."""
        if platform == "youtube":
            return {c["id"] for c in await self._yt_channels_meta_pg(user_ids=user_ids)}
        if platform == "facebook":
            return {m["target"] for m in await self._fb_page_meta_pg(user_ids=user_ids)}
        return set()

    async def country_overrides(self, account_ids: list[str] | None = None) -> dict[str, str]:
        """Xem ghi chú bản SQLite — cùng logic, giá trị trả về đã chuẩn hoá qua `country_aliases`."""
        async with self._pool.acquire() as c:
            if account_ids is None:
                rows = await c.fetch(
                    "SELECT channel_ref, country_override FROM channel_group_members "
                    "WHERE country_override != ''")
            else:
                rows = await c.fetch(
                    "SELECT m.channel_ref, m.country_override FROM channel_group_members m "
                    "JOIN channel_groups g ON g.id = m.group_id "
                    "WHERE m.country_override != '' AND g.account_id = ANY($1::text[])",
                    account_ids)
        aliases = await self._country_alias_map()
        return {r["channel_ref"]: _normalize_country(r["country_override"], aliases) for r in rows}

    # ── Chuẩn hoá quốc gia (alias → tên chuẩn) ────────────────────────────────

    async def _country_alias_map(self) -> dict[str, str]:
        """Xem ghi chú bản SQLite — cùng logic."""
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch("SELECT alias, canonical FROM country_aliases")
        except Exception as e:  # noqa: BLE001 — thiếu bảng/lỗi tạm thời -> rỗng, không nổ 500
            logger.debug("country_alias_map that bai, fail-open rong: %s", e)
            return {}
        return {r["alias"]: r["canonical"] for r in rows}

    async def list_country_aliases(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM country_aliases ORDER BY canonical, alias")
        return [dict(r) for r in rows]

    async def upsert_country_alias(self, alias: str, canonical: str) -> dict:
        key = alias.strip().lower()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO country_aliases (alias, canonical, created_at) VALUES ($1, $2, $3) "
                "ON CONFLICT (alias) DO UPDATE SET canonical = excluded.canonical",
                key, canonical.strip(), _now())
        return {"alias": key, "canonical": canonical.strip()}

    async def delete_country_alias(self, alias: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "DELETE FROM country_aliases WHERE alias = $1", alias.strip().lower())
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def list_country_canonicals(self) -> list[dict]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM country_canonicals ORDER BY name")
        return [dict(r) for r in rows]

    async def get_country_canonical(self, name: str) -> dict | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT * FROM country_canonicals WHERE lower(name) = lower($1)", name.strip())
        return dict(row) if row else None

    async def create_country_canonical(self, name: str) -> dict:
        key = name.strip()
        now = _now()
        async with self._pool.acquire() as c:
            await c.execute(
                "INSERT INTO country_canonicals (name, created_at) VALUES ($1, $2)", key, now)
        return {"name": key, "created_at": now}

    async def rename_country_canonical(self, old_name: str, new_name: str) -> bool:
        async with self._pool.acquire() as c:
            async with c.transaction():
                tag = await c.execute(
                    "UPDATE country_canonicals SET name = $1 WHERE lower(name) = lower($2)",
                    new_name.strip(), old_name.strip())
                await c.execute(
                    "UPDATE country_aliases SET canonical = $1 WHERE lower(canonical) = lower($2)",
                    new_name.strip(), old_name.strip())
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    async def delete_country_canonical(self, name: str) -> bool:
        async with self._pool.acquire() as c:
            tag = await c.execute(
                "DELETE FROM country_canonicals WHERE lower(name) = lower($1)", name.strip())
        try:
            return int(str(tag).rsplit(" ", 1)[-1]) > 0
        except ValueError:
            return False

    # ── Viewer data ─────────────────────────────────────────────────────────────

    async def _load_fb_pg(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        """Xem ghi chú `_load_fb` bản SQLite — cùng logic chuẩn hoá `_target` → `target`."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'fb_posts' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'fb_posts' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            if p.get("_empty"):
                continue
            if country and p.get("country", "") != country:
                continue
            if not p.get("target") and p.get("_target"):
                p["target"] = p["_target"]
            result.append(p)
        return result

    async def _load_yt_pg(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents "
                    "WHERE store = 'yt_insights_daily' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents "
                    "WHERE store = 'yt_insights_daily' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        result = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            if country and p.get("country", "") != country:
                continue
            result.append(p)
        return result

    async def _load_events_pg(self, user_ids: list[str] | None = None) -> list[dict]:
        """Xem ghi chú `_load_events` bản SQLite."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_events' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_events' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("kind")]

    async def _load_pairings_pg(self, user_ids: list[str] | None = None) -> list[dict]:
        """Xem ghi chú `_load_pairings` bản SQLite."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_pairings' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_pairings' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("target")]

    async def _load_groups_pg(self, user_ids: list[str] | None = None) -> list[dict]:
        """Xem ghi chú `_load_groups` bản SQLite."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_groups' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'repost_groups' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        return [json.loads(r["payload"]) for r in rows
                if r["payload"] and json.loads(r["payload"]).get("id")]

    async def _fb_page_meta_pg(self, user_ids: list[str] | None = None) -> list[dict]:
        """Xem `_fb_page_meta` bản SQLite — đọc projection fb_page_meta từ documents. `country`
        chuẩn hoá qua `country_aliases` — xem ghi chú bản SQLite."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'fb_page_meta' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'fb_page_meta' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        aliases = await self._country_alias_map()
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            t = p.get("target")
            if t:
                out.append({"target": t, "name": p.get("name") or t,
                            "country": _normalize_country(p.get("country") or "", aliases)})
        return out

    async def _yt_channels_meta_pg(self, user_ids: list[str] | None = None) -> list[dict]:
        """Xem `_yt_channels_meta` bản SQLite — đọc store `yt_channels` (title/country plaintext).
        `country` chuẩn hoá qua `country_aliases`."""
        async with self._pool.acquire() as c:
            if user_ids is None:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'yt_channels' AND NOT deleted")
            else:
                rows = await c.fetch(
                    "SELECT payload FROM documents WHERE store = 'yt_channels' AND NOT deleted "
                    "AND user_id = ANY($1::text[])", user_ids)
        aliases = await self._country_alias_map()
        out: list[dict] = []
        for row in rows:
            p = json.loads(row["payload"]) if row["payload"] else {}
            cid = p.get("id")
            if cid:
                out.append({"id": cid, "title": p.get("title") or "",
                            "country": _normalize_country(p.get("country") or "", aliases)})
        return out

    async def traffic_metric(self, date_from: str, date_to: str, scope: str = "", country: str = "",
                             user_ids: list[str] | None = None,
                             channel_refs: set[str] | None = None,
                             country_overrides: dict[str, str] | None = None) -> dict:
        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(user_ids=user_ids),
                                           await self._load_groups_pg(user_ids=user_ids))
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = await self._load_fb_pg(user_ids=user_ids)
        yt_daily = await self._load_yt_pg(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)
        return stats_core.traffic_metric(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            events=await self._load_events_pg(user_ids=user_ids), scope=scope, country=country,
            yt_filter=yt_f, fb_filter=fb_f)

    async def summary(self, date_from: str, date_to: str, country: str = "",
                      user_ids: list[str] | None = None,
                      channel_refs: set[str] | None = None,
                      country_overrides: dict[str, str] | None = None) -> dict:
        d0 = date.fromisoformat(date_from)
        so_ngay = len(stats_core.days_between(date_from, date_to))
        prev_from = (d0 - timedelta(days=so_ngay)).isoformat()
        prev_to = (d0 - timedelta(days=1)).isoformat()

        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        pairings = await self._load_pairings_pg(user_ids=user_ids)
        groups = await self._load_groups_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = await self._load_fb_pg(user_ids=user_ids)
        yt_daily = await self._load_yt_pg(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        if channel_refs is not None:
            pairings = [p for p in pairings if p.get("target") in channel_refs]
        yt_f, fb_f = stats_core.country_filters(country, chans, tc)

        events = await self._load_events_pg(user_ids=user_ids)
        if channel_refs is not None:
            events = [e for e in events if e.get("target") in channel_refs]
        ev_now = [e for e in events if date_from <= (e.get("date") or "") <= date_to]
        ev_prev = [e for e in events if prev_from <= (e.get("date") or "") <= prev_to]

        return stats_core.summary(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            events=ev_now, prev_events=ev_prev,
            prev_from=prev_from, prev_to=prev_to,
            pairings=pairings, groups=groups,
            yt_filter=yt_f, fb_filter=fb_f)

    async def top_channels(self, date_from: str, date_to: str, country: str = "", limit: int = 15,
                           user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[dict]:
        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(user_ids=user_ids),
                                           await self._load_groups_pg(user_ids=user_ids))
        tn = stats_core.target_names(meta)
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = await self._load_fb_pg(user_ids=user_ids)
        yt_daily = await self._load_yt_pg(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        result = stats_core.top_channels(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            channel_meta=chans, target_names=tn, target_country=tc,
            country=country, limit=limit)
        return result["items"]

    async def views_by_country(self, date_from: str, date_to: str,
                               user_ids: list[str] | None = None,
                               channel_refs: set[str] | None = None,
                               country_overrides: dict[str, str] | None = None) -> dict:
        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(user_ids=user_ids),
                                           await self._load_groups_pg(user_ids=user_ids))
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        fb_posts = await self._load_fb_pg(user_ids=user_ids)
        yt_daily = await self._load_yt_pg(user_ids=user_ids)
        fb_posts, yt_daily, chans, tc = _apply_channel_refs(fb_posts, yt_daily, chans, tc, channel_refs)
        return stats_core.views_by_country(
            date_from, date_to,
            fb_posts=fb_posts, yt_daily=yt_daily,
            channel_meta=chans, target_country=tc)

    async def country_tags(self, user_ids: list[str] | None = None,
                           channel_refs: set[str] | None = None,
                           country_overrides: dict[str, str] | None = None) -> list[str]:
        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, await self._load_pairings_pg(user_ids=user_ids),
                                           await self._load_groups_pg(user_ids=user_ids))
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        _, _, chans, tc = _apply_channel_refs([], [], chans, tc, channel_refs)
        return stats_core.country_tags(channel_meta=chans, target_country=tc)

    async def channel_breakdown(self, user_ids: list[str] | None = None,
                                channel_refs: set[str] | None = None,
                                country_overrides: dict[str, str] | None = None,
                                owner_scope: list[str] | None = None) -> dict:
        """Xem ghi chú bản SQLite — cùng logic, chỉ khác cú pháp asyncpg (`_pg` suffix loaders)."""
        meta = await self._fb_page_meta_pg(user_ids=user_ids)
        pairings = await self._load_pairings_pg(user_ids=user_ids)
        groups = await self._load_groups_pg(user_ids=user_ids)
        tc = stats_core.target_country_map(meta, pairings, groups)
        chans = await self._yt_channels_meta_pg(user_ids=user_ids)
        chans, tc = _apply_country_overrides(chans, tc, country_overrides)
        if channel_refs is not None:
            pairings = [p for p in pairings if p.get("target") in channel_refs]
            chans = [c for c in chans if c["id"] in channel_refs]
        result = _channel_breakdown(pairings, groups, chans, tc)

        for tag in await self.list_platform_tags(owner_scope):
            refs = await self.channel_refs_by_platform(tag, owner_scope)
            if channel_refs is not None:
                refs = refs & channel_refs
            result["by_platform"].append({
                "platform": tag, "managed": len(refs), "active": len(refs), "is_custom_tag": True,
            })
        return result

    async def last_synced_at(self, user_ids: list[str] | None = None) -> str | None:
        """`MAX(user_meta.last_seen)` — mốc Tool push gần nhất lên VPS (mục 26 đặc tả
        `tasks/backlog/local-operations-hub-v1.md`: UI phải hiển thị data freshness). `user_meta`
        do sync server tạo/ghi — admin-server chỉ ĐỌC. `user_ids=[]` (account chưa link Tool nào)
        → None ngay, không query. Bảng thiếu/lỗi tạm thời → None, KHÔNG làm hỏng request (cùng
        nguyên tắc với `doc_version`)."""
        if user_ids is not None and not user_ids:
            return None
        try:
            async with self._pool.acquire() as c:
                if user_ids is None:
                    v = await c.fetchval("SELECT MAX(last_seen) FROM user_meta")
                else:
                    v = await c.fetchval(
                        "SELECT MAX(last_seen) FROM user_meta WHERE user_id = ANY($1::text[])",
                        user_ids)
            return v or None
        except Exception as e:  # noqa: BLE001 — thiếu bảng/lỗi tạm thời không được làm hỏng request
            logger.debug("last_synced_at that bai: %s", e)
            return None

    async def doc_version(self) -> str:
        """Con dấu phiên bản kho `documents` — đổi khi và chỉ khi sync server ghi bản ghi mới.

        `revs.next_rev` tăng đúng một lần cho MỖI lần ghi document (`server/db.py` `next_rev`),
        nên tổng của cột đó là con dấu cho toàn bộ kho. `revs` chỉ một dòng mỗi license nên quét
        hết bảng vẫn dưới 1ms — rẻ hơn nhiều so với việc phục vụ số liệu cũ.

        Bảng `revs` do sync server tạo. CSDL sạch (sync server chưa chạy lần nào) → trả mốc cố
        định thay vì nổ lỗi; khi đó TTL là thứ duy nhất giới hạn tuổi cache."""
        try:
            async with self._pool.acquire() as c:
                v = await c.fetchval("SELECT COALESCE(SUM(next_rev), 0) FROM revs")
            return str(int(v or 0))
        except Exception as e:  # noqa: BLE001 — thiếu bảng/lỗi tạm thời không được làm hỏng request
            logger.debug("doc_version that bai, dung moc co dinh: %s", e)
            return "na"


# ── LocalFileDb — đọc thẳng data/*.json, cùng nguồn với Electron ────────────────
#
# Dùng khi MODE=dev: admin-server đọc trực tiếp từ thư mục data/ của Electron, không cần bước
# import hay Postgres. Mọi thay đổi trong Electron (thêm pairing, đăng bài xong tạo event, cào
# view mới) lập tức phản ánh lên admin web khi load lại trang — cùng file, cùng logic đọc.
#
# Auth (accounts, invite_keys, refresh_tokens) vẫn dùng SQLite tách riêng (SQLITE_AUTH_PATH).
# Viewer data (fb_posts, yt_insights_daily, repost_events, repost_pairings, repost_groups, fb_pages)
# đọc thẳng từ JSON theo đúng hình dạng mà Electron ghi — không qua bảng documents.

class LocalFileDb(SqliteDb):
    """Dev mode: viewer data từ data/*.json, auth từ SQLite riêng.

    Đảm bảo 100% đồng nhất với Electron: cùng file nguồn, cùng hình dạng JSON, cùng logic tính —
    không cần bước import thủ công và không có độ trễ cập nhật."""

    def __init__(self, sqlite_auth_path: str, data_dir: str) -> None:
        super().__init__(sqlite_auth_path)
        self._data_dir = os.path.abspath(data_dir)

    # ── Đọc file JSON thô ────────────────────────────────────────────────────────

    def _read_json(self, filename: str, default=None):
        """Đọc file JSON từ data_dir — trả `default` nếu không tồn tại hoặc lỗi parse."""
        path = os.path.join(self._data_dir, filename)
        if not os.path.exists(path):
            return default if default is not None else {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default if default is not None else {}

    # ── Override _load_* để đọc từ JSON thay vì documents table ────────────────

    def _load_fb(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        """fb_posts.json — shape posts_by_target: {target: [post, ...]}

        Chuẩn hoá: gắn `target` vào mỗi bài để code phía sau dùng chung với SqliteDb._load_fb.
        Electron đọc cùng file này trong insights_store.views_by_day.

        `user_ids` NHẬN nhưng bỏ qua: JSON local là 1 thư mục của 1 Tool, không có khái niệm nhiều
        user — cô lập theo Tool chỉ có ý nghĩa ở PostgresDb (xem `context/RULES.md` phần dev/prod)."""
        data = self._read_json("fb_posts.json") or {}
        result = []
        for target, posts in data.items():
            if not isinstance(posts, list):
                continue
            for post in posts:
                if not isinstance(post, dict):
                    continue
                p = {**post, "target": target}
                # country không lưu ở level bài — bỏ qua lọc nếu có yêu cầu
                if country and p.get("country", "") != country:
                    continue
                result.append(p)
        return result

    def _load_yt(self, country: str = "", user_ids: list[str] | None = None) -> list[dict]:
        """yt_insights_daily.json — shape dict_of_records: {"YYYY-MM-DD|channel_id": record}.

        Electron đọc cùng file này trong insights_store (yt)."""
        data = self._read_json("yt_insights_daily.json") or {}
        result = []
        for _key, record in data.items():
            if not isinstance(record, dict):
                continue
            if country and record.get("country", "") != country:
                continue
            result.append(record)
        return result

    def _load_events(self, user_ids: list[str] | None = None) -> list[dict]:
        """repost_events.json — shape list_by_id: [{id, kind, date, target, ...}].

        Electron đọc qua event_log.get_event_log().between(). Chỉ lấy event có `kind`."""
        data = self._read_json("repost_events.json", default=[]) or []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict) and e.get("kind")]

    def _load_pairings(self, user_ids: list[str] | None = None) -> list[dict]:
        """repost_pairings.json — shape dict_of_records: {id: pairing_record}.

        Electron đọc qua pairing_store.list(). Lấy các record có `target`."""
        data = self._read_json("repost_pairings.json") or {}
        if not isinstance(data, dict):
            return []
        return [v for v in data.values() if isinstance(v, dict) and v.get("target")]

    def _load_groups(self, user_ids: list[str] | None = None) -> list[dict]:
        """repost_groups.json — shape list_by_id: [{id, name, country, enabled, ...}].

        Electron đọc qua group_store.list_groups(). Lấy record có `id`."""
        data = self._read_json("repost_groups.json", default=[]) or []
        if not isinstance(data, list):
            return []
        return [g for g in data if isinstance(g, dict) and g.get("id")]

    def _fb_page_meta(self, user_ids: list[str] | None = None) -> list[dict]:
        """fb_page_meta.json (projection Tool đẩy). Nếu chưa rebuild lần nào → dựng thẳng từ
        fb_pages.json để dev luôn có tên+quốc gia (khớp production đọc bản đã sync). `country`
        chuẩn hoá qua `country_aliases` (dùng chung bảng SQLite auth với `SqliteDb`)."""
        aliases = self._country_alias_map()
        data = self._read_json("fb_page_meta.json") or {}
        if isinstance(data, dict) and data:
            return [{**v, "country": _normalize_country(v.get("country") or "", aliases)}
                    for v in data.values() if isinstance(v, dict) and v.get("target")]
        fb_pages = self._read_json("fb_pages.json") or {}
        page_countries = fb_pages.get("page_countries") or {}
        out: list[dict] = []
        for conn in fb_pages.get("connections") or []:
            if not isinstance(conn, dict):
                continue
            for pg in conn.get("pages") or []:
                if not isinstance(pg, dict):
                    continue
                pid = str(pg.get("id") or "")
                if not pid:
                    continue
                kind = "profile" if pg.get("kind") == "profile" else "page"
                out.append({"target": f"{kind}:{pid}", "name": pg.get("name") or pid,
                            "country": _normalize_country((page_countries.get(pid) or "").strip(), aliases)})
        return out

    def _yt_channels_meta(self, user_ids: list[str] | None = None) -> list[dict]:
        """yt_channels.json (container `channels`) → {id,title,country}. Electron đọc list_channels().
        `country` chuẩn hoá qua `country_aliases`."""
        aliases = self._country_alias_map()
        data = self._read_json("yt_channels.json") or {}
        out: list[dict] = []
        for ch in (data.get("channels") or []):
            if not isinstance(ch, dict):
                continue
            cid = ch.get("id")
            if cid:
                out.append({"id": cid, "title": ch.get("title") or "",
                            "country": _normalize_country(ch.get("country") or "", aliases)})
        return out

    # Tên file khớp đúng các `_read_json` ở trên — đổi danh sách này khi thêm nguồn đọc mới,
    # nếu không cache sẽ không thấy file mới thay đổi.
    _VIEWER_FILES = ("fb_posts.json", "yt_insights_daily.json", "repost_events.json",
                     "repost_pairings.json", "repost_groups.json", "fb_page_meta.json",
                     "fb_pages.json", "yt_channels.json")

    async def doc_version(self) -> str:
        """Dev mode: con dấu từ (mtime, size) của các file JSON mà viewer thực sự đọc.

        Electron ghi đè file → mtime đổi → con dấu đổi → cache tự vô hiệu."""
        parts: list[str] = []
        for name in self._VIEWER_FILES:
            try:
                st = os.stat(os.path.join(self._data_dir, name))
                parts.append(f"{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append("-")
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ── Factory ─────────────────────────────────────────────────────────────────────

def make_db(url: str, data_dir: str = "") -> Db:
    """Tạo Db phù hợp từ URL.

    Scheme hỗ trợ:
    - ``sqlite:///path``  → SqliteDb (test/dev với import script)
    - ``postgresql://…``  → PostgresDb (production VPS)
    - ``file:///path``    → LocalFileDb (dev: đọc thẳng data/*.json, auth SQLite riêng)
      Kèm ``data_dir`` là thư mục data/ của Electron.
    """
    if url.startswith("file:///"):
        # Dev mode: đọc thẳng file JSON. SQLite auth ở thư mục cạnh data_dir.
        sqlite_auth = url[len("file:///"):]  # path tới file SQLite auth
        return LocalFileDb(sqlite_auth, data_dir or "../data")
    if url.startswith("sqlite:///"):
        return SqliteDb(url[len("sqlite:///"):])
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDb(url)
    raise ValueError(f"DATABASE_URL không hiểu được: {url!r}")


def _tong(m: dict[str, int | None]) -> int | None:
    """`None` nếu không có ngày nào có số — khác hẳn tổng bằng 0.

    `0` là "có dữ liệu, không ai xem"; `None` là "chưa cào ngày nào". Vẽ giống nhau lên biểu đồ là
    đọc sai số liệu."""
    co = [v for v in m.values() if v is not None]
    return sum(co) if co else None
