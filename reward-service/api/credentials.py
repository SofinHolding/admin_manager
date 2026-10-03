"""Endpoint `/v1/reward/credentials` — cấu hình Discord + NHIỀU token Discord của chính account (plan C.2).

Cấu hình (guild/channel/lệnh/pattern/delay) nằm ở `reward_discord_credentials`; token nằm ở
`reward_discord_tokens` (1 account = N token, job xoay vòng giữa chúng — xem `engine/token_pool.py`).
Token Discord CHỈ được giải mã trong tiến trình xử lý request này (verify) và trong
`engine/worker_manager.py` lúc worker khởi động job. Response KHÔNG BAO GIỜ chứa
`token_ciphertext`/token thô, kể cả dạng che.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from crypto import TokenCrypto
from discord.http import DiscordHttpClient
from discord.slash_commands import fetch_guild_command
from domain.defaults import DEFAULT_SUCCESS_PATTERN
from security import AuthContext, Security
from settings import RewardSettings
from store.pool import Pool
from store.repositories import credentials as credentials_repo
from store.repositories import tokens as tokens_repo

logger = logging.getLogger("reward.credentials")

_SNOWFLAKE = re.compile(r"^\d{15,25}$")

MAX_TOKENS_PER_ACCOUNT = 20  # mỗi token = 1 kết nối Gateway khi chạy job


def _validate_snowflake(value: str, field: str) -> str:
    if not _SNOWFLAKE.match(value):
        raise ValueError(f"{field} phải là snowflake Discord hợp lệ (15-25 chữ số)")
    return value


class CredentialsBody(BaseModel):
    guild_id: str
    channel_id: str
    command_name: str = "give-xp"
    confirm_mode: str = "reply"
    # Mặc định khớp câu trả lời thật của Axolink Management ("N XP has been given to @user") —
    # đã hiệu chuẩn từ dữ liệu Discord thật. User để trống ô Success pattern trên UI vẫn hoạt động
    # đúng ngay, không cần tự gõ regex. Vẫn sửa được nếu đổi sang bot khác có định dạng trả lời khác.
    success_pattern: str | None = DEFAULT_SUCCESS_PATTERN
    failure_pattern: str | None = None
    leveling_bot_id: str | None = None
    delay_ms: int = Field(3000, ge=0)
    jitter_ms: int = Field(500, ge=0)

    @field_validator("guild_id", "channel_id")
    @classmethod
    def _check_snowflake(cls, v: str, info) -> str:
        return _validate_snowflake(v, info.field_name)

    @field_validator("confirm_mode")
    @classmethod
    def _check_confirm_mode(cls, v: str) -> str:
        if v not in ("off", "reply"):
            raise ValueError("confirm_mode phải là 'off' hoặc 'reply'")
        return v

    @field_validator("success_pattern", "failure_pattern")
    @classmethod
    def _check_pattern(cls, v: str | None) -> str | None:
        if v:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"Regex không hợp lệ: {exc}") from exc
        return v


class TokenBody(BaseModel):
    token: str = Field(min_length=1)
    label: str = Field("", max_length=60)


class TokenPatchBody(BaseModel):
    label: str | None = Field(None, max_length=60)
    enabled: bool | None = None


def _public_config(row: dict | None) -> dict:
    if row is None:
        return {
            "exists": False, "status": None,
            "guild_id": None, "channel_id": None, "command_name": None, "confirm_mode": None,
            "success_pattern": None, "failure_pattern": None, "leveling_bot_id": None,
            "delay_ms": None, "jitter_ms": None, "verified_at": None, "last_error": None,
        }
    return {
        "exists": True, "status": row["status"], "guild_id": row["guild_id"],
        "channel_id": row["channel_id"], "command_name": row["command_name"],
        "confirm_mode": row["confirm_mode"], "success_pattern": row["success_pattern"],
        "failure_pattern": row["failure_pattern"], "leveling_bot_id": row["leveling_bot_id"],
        "delay_ms": row["delay_ms"], "jitter_ms": row["jitter_ms"],
        "verified_at": row["verified_at"], "last_error": row["last_error"],
    }


def _public_token(row: dict) -> dict:
    return {
        "id": row["id"], "label": row["label"], "discord_user_id": row["discord_user_id"],
        "discord_username": row["discord_username"], "status": row["status"], "enabled": row["enabled"],
        "last_error": row["last_error"], "verified_at": row["verified_at"], "last_used_at": row["last_used_at"],
    }


@dataclass
class TokenCheck:
    """Kết quả xác thực 1 token. `status=None` ⇒ lỗi mạng tạm thời: KHÔNG đổi trạng thái đang lưu."""
    status: str | None
    last_error: str | None = None
    discord_user_id: str | None = None
    discord_username: str | None = None
    command: dict[str, Any] | None = None


def make_router(
    security: Security, pool: Pool, settings: RewardSettings,
    discord_http: DiscordHttpClient, crypto: TokenCrypto,
) -> APIRouter:
    router = APIRouter(prefix="/v1/reward/credentials", tags=["reward-credentials"])
    Reward = Depends(security.require_reward_user)
    Active = Depends(security.require_active_account)

    async def _check_token(plaintext_token: str, cfg: dict | None) -> TokenCheck:
        """`GET /users/@me` rồi (nếu đã có cấu hình) kiểm lệnh slash trong guild bằng CHÍNH token này."""
        try:
            res = await discord_http.request(
                "GET", f"{settings.discord_api_base}/users/@me",
                headers={"Authorization": plaintext_token}, retry_on_network_error=False)
        except httpx.HTTPError:
            return TokenCheck(status=None, last_error="Không kết nối được Discord để xác thực")
        if res.status_code == 401:
            return TokenCheck(status="invalid", last_error="Token Discord không hợp lệ")
        if res.status_code >= 400:
            return TokenCheck(status=None, last_error=f"Discord trả HTTP {res.status_code} khi xác thực token")
        me = res.json()
        base = TokenCheck(
            status="unverified", discord_user_id=str(me.get("id") or "") or None,
            discord_username=me.get("username"))
        if cfg is None:
            return base
        try:
            command = await fetch_guild_command(
                discord_http, api_base=settings.discord_api_base, token=plaintext_token,
                guild_id=cfg["guild_id"], command_name=cfg["command_name"])
        except httpx.HTTPError:
            base.status = None
            base.last_error = "Không kết nối được Discord để kiểm tra lệnh"
            return base
        if command is None:
            base.status = "invalid"
            base.last_error = (
                f"Không tìm thấy lệnh /{cfg['command_name']} trong guild "
                "(sai Guild ID, hoặc tài khoản này chưa vào guild)")
            return base
        base.status = "valid"
        base.command = {
            "application_id": command.application_id, "command_id": command.id, "version": command.version,
            "member_option_name": command.member_option_name, "member_option_type": command.member_option_type,
            "amount_option_name": command.amount_option_name, "amount_option_type": command.amount_option_type,
        }
        return base

    async def _check_stored(token_row: dict, cfg: dict | None) -> TokenCheck:
        try:
            plaintext = crypto.decrypt(token_row["token_ciphertext"])
        except ValueError:
            return TokenCheck(status="invalid", last_error="Không giải mã được token (sai khoá mã hoá)")
        return await _check_token(plaintext, cfg)

    async def _apply_check(account_id: str, token_id: int, check: TokenCheck) -> dict | None:
        async with pool.acquire() as conn:
            if check.status is None:
                return await tokens_repo.get_public(conn, account_id, token_id)
            return await tokens_repo.set_verification(
                conn, account_id=account_id, token_id=token_id, status=check.status,
                last_error=check.last_error, discord_user_id=check.discord_user_id,
                discord_username=check.discord_username)

    async def _sync_config_status(account_id: str) -> dict | None:
        """Cấu hình 'valid' ⇔ có ≥1 token bật + hợp lệ. Gọi sau mọi thay đổi token/cấu hình."""
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, account_id)
            if cfg is None:
                return None
            tokens = await tokens_repo.list_public(conn, account_id)
            if any(t["enabled"] and t["status"] == "valid" for t in tokens):
                await credentials_repo.set_status(
                    conn, account_id=account_id, status="valid", last_error=None, set_verified=True)
            elif not tokens:
                await credentials_repo.set_status(
                    conn, account_id=account_id, status="unverified", last_error="Chưa có token Discord nào")
            else:
                errors = [t["last_error"] for t in tokens if t["enabled"] and t["last_error"]]
                await credentials_repo.set_status(
                    conn, account_id=account_id, status="invalid",
                    last_error=errors[0] if errors else "Không có token Discord nào hợp lệ và đang bật")
            return await credentials_repo.get_by_account(conn, account_id)

    async def _recheck_all(account_id: str) -> tuple[list[dict], dict | None]:
        """Xác thực lại MỌI token đang bật với cấu hình hiện tại → (danh sách token công khai, command meta)."""
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, account_id)
            rows = await tokens_repo.list_raw(conn, account_id)
        command_meta: dict | None = None
        for row in rows:
            if not row["enabled"]:
                continue
            check = await _check_stored(row, cfg)
            await _apply_check(account_id, row["id"], check)
            if check.command is not None and command_meta is None:
                command_meta = check.command
        await _sync_config_status(account_id)
        async with pool.acquire() as conn:
            return [_public_token(t) for t in await tokens_repo.list_public(conn, account_id)], command_meta

    @router.get("", summary="Xem cấu hình Discord + danh sách token của chính mình (không bao giờ trả token)")
    async def get_credentials(ctx: AuthContext = Depends(security.require_reward_user)) -> dict:
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, ctx.account_id)
            tokens = await tokens_repo.list_public(conn, ctx.account_id)
        out = _public_config(cfg)
        out["tokens"] = [_public_token(t) for t in tokens]
        return out

    @router.put("", summary="Lưu/cập nhật cấu hình Discord (xác thực lại mọi token đang bật)")
    async def put_credentials(
        body: CredentialsBody, ctx: AuthContext = Active, _reward: AuthContext = Reward,
    ) -> dict:
        async with pool.acquire() as conn:
            await credentials_repo.upsert(
                conn, account_id=ctx.account_id, guild_id=body.guild_id, channel_id=body.channel_id,
                command_name=body.command_name, confirm_mode=body.confirm_mode,
                success_pattern=body.success_pattern, failure_pattern=body.failure_pattern,
                leveling_bot_id=body.leveling_bot_id, delay_ms=body.delay_ms, jitter_ms=body.jitter_ms,
                status="unverified", last_error=None, set_verified=False)
        tokens, command_meta = await _recheck_all(ctx.account_id)
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, ctx.account_id)
        return {"ok": True, "status": cfg["status"], "last_error": cfg["last_error"],
                "command": command_meta, "tokens": tokens}

    @router.post("/verify", summary="Xác thực lại cấu hình + mọi token đang bật")
    async def verify_credentials(ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            existing = await credentials_repo.get_by_account(conn, ctx.account_id)
        if existing is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chưa lưu cấu hình Discord")
        tokens, command_meta = await _recheck_all(ctx.account_id)
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, ctx.account_id)
        return {"ok": True, "status": cfg["status"], "last_error": cfg["last_error"],
                "command": command_meta, "tokens": tokens}

    @router.delete("", summary="Xoá cấu hình Discord + toàn bộ token của chính mình")
    async def delete_credentials(ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            blocking = await conn.fetchval(
                "SELECT 1 FROM reward_jobs WHERE account_id=$1 AND status IN ('running','paused') LIMIT 1",
                ctx.account_id)
            if blocking:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Không thể xoá: còn job đang chạy hoặc tạm dừng dùng cấu hình này")
            await credentials_repo.delete(conn, ctx.account_id)
            await tokens_repo.delete_all(conn, ctx.account_id)
        return {"ok": True}

    # ── Token (N token / account) ─────────────────────────────────────────

    @router.get("/tokens", summary="Danh sách token Discord của chính mình (không bao giờ trả token)")
    async def list_tokens(ctx: AuthContext = Depends(security.require_reward_user)) -> dict:
        async with pool.acquire() as conn:
            tokens = await tokens_repo.list_public(conn, ctx.account_id)
        return {"tokens": [_public_token(t) for t in tokens]}

    @router.post("/tokens", summary="Thêm 1 token Discord vào bể xoay vòng")
    async def add_token(body: TokenBody, ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, ctx.account_id)
            count = len(await tokens_repo.list_public(conn, ctx.account_id))
        if count >= MAX_TOKENS_PER_ACCOUNT:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Tối đa {MAX_TOKENS_PER_ACCOUNT} token Discord cho mỗi tài khoản")
        token = body.token.strip()
        check = await _check_token(token, cfg)
        if check.status is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, check.last_error or "Không thể xác thực token Discord")
        if check.discord_user_id is None:  # 401 ở /users/@me — token sai, KHÔNG lưu
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token Discord không hợp lệ")
        async with pool.acquire() as conn:
            dup = await tokens_repo.find_by_discord_user(conn, ctx.account_id, check.discord_user_id)
            if dup is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Tài khoản Discord {check.discord_username or check.discord_user_id} đã có trong danh sách token")
            row = await tokens_repo.insert(
                conn, account_id=ctx.account_id, label=body.label.strip() or (check.discord_username or ""),
                token_ciphertext=crypto.encrypt(token), discord_user_id=check.discord_user_id,
                discord_username=check.discord_username, status=check.status, last_error=check.last_error,
                set_verified=check.status == "valid")
        await _sync_config_status(ctx.account_id)
        return {"ok": True, "token": _public_token(row), "command": check.command}

    @router.patch("/tokens/{token_id}", summary="Đổi nhãn / bật-tắt 1 token")
    async def patch_token(
        token_id: int, body: TokenPatchBody, ctx: AuthContext = Active, _reward: AuthContext = Reward,
    ) -> dict:
        async with pool.acquire() as conn:
            row = await tokens_repo.update_meta(
                conn, account_id=ctx.account_id, token_id=token_id,
                label=body.label.strip() if body.label is not None else None, enabled=body.enabled)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy token")
        await _sync_config_status(ctx.account_id)
        return {"ok": True, "token": _public_token(row)}

    @router.post("/tokens/{token_id}/verify", summary="Xác thực lại 1 token")
    async def verify_token(token_id: int, ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            cfg = await credentials_repo.get_by_account(conn, ctx.account_id)
            rows = [r for r in await tokens_repo.list_raw(conn, ctx.account_id) if r["id"] == token_id]
        if not rows:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy token")
        check = await _check_stored(rows[0], cfg)
        row = await _apply_check(ctx.account_id, token_id, check)
        await _sync_config_status(ctx.account_id)
        return {"ok": True, "token": _public_token(row), "command": check.command}

    @router.delete("/tokens/{token_id}", summary="Xoá 1 token khỏi bể xoay vòng")
    async def delete_token(token_id: int, ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            running = await conn.fetchval(
                "SELECT 1 FROM reward_jobs WHERE account_id=$1 AND status='running' LIMIT 1", ctx.account_id)
            if running:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Không thể xoá token khi còn job đang chạy — hãy tạm dừng job trước")
            deleted = await tokens_repo.delete(conn, account_id=ctx.account_id, token_id=token_id)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy token")
        await _sync_config_status(ctx.account_id)
        return {"ok": True}

    return router
