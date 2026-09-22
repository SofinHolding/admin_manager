"""Endpoint `/v1/reward/credentials` — quản lý thông tin Discord của chính account (plan C.2).

Token Discord CHỈ được giải mã trong tiến trình xử lý request này (verify) và trong
`engine/worker_manager.py` lúc worker khởi động job. Response KHÔNG BAO GIỜ chứa
`token_ciphertext`/token thô, kể cả dạng che.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from crypto import TokenCrypto
from discord.http import DiscordHttpClient
from discord.slash_commands import fetch_guild_command
from security import AuthContext, Security
from settings import RewardSettings
from store.pool import Pool
from store.repositories import credentials as credentials_repo

logger = logging.getLogger("reward.credentials")

_SNOWFLAKE = re.compile(r"^\d{15,25}$")


def _validate_snowflake(value: str, field: str) -> str:
    if not _SNOWFLAKE.match(value):
        raise ValueError(f"{field} phải là snowflake Discord hợp lệ (15-25 chữ số)")
    return value


class CredentialsBody(BaseModel):
    token: str | None = Field(None, min_length=1)
    guild_id: str
    channel_id: str
    command_name: str = "give-xp"
    confirm_mode: str = "reply"
    # Mặc định khớp câu trả lời thật của Axolink Management ("N XP has been given to @user") —
    # đã hiệu chuẩn từ dữ liệu Discord thật. User để trống ô Success pattern trên UI vẫn hoạt động
    # đúng ngay, không cần tự gõ regex. Vẫn sửa được nếu đổi sang bot khác có định dạng trả lời khác.
    success_pattern: str | None = r"\d+\s*XP has been given to"
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


def _public_view(row: dict | None) -> dict:
    if row is None:
        return {
            "exists": False, "status": None, "discord_user_id": None, "discord_username": None,
            "guild_id": None, "channel_id": None, "command_name": None, "confirm_mode": None,
            "success_pattern": None, "failure_pattern": None, "leveling_bot_id": None,
            "delay_ms": None, "jitter_ms": None, "verified_at": None, "last_error": None,
        }
    return {
        "exists": True, "status": row["status"], "discord_user_id": row["discord_user_id"],
        "discord_username": row["discord_username"], "guild_id": row["guild_id"],
        "channel_id": row["channel_id"], "command_name": row["command_name"],
        "confirm_mode": row["confirm_mode"], "success_pattern": row["success_pattern"],
        "failure_pattern": row["failure_pattern"], "leveling_bot_id": row["leveling_bot_id"],
        "delay_ms": row["delay_ms"], "jitter_ms": row["jitter_ms"],
        "verified_at": row["verified_at"], "last_error": row["last_error"],
    }


def make_router(
    security: Security, pool: Pool, settings: RewardSettings,
    discord_http: DiscordHttpClient, crypto: TokenCrypto,
) -> APIRouter:
    router = APIRouter(prefix="/v1/reward/credentials", tags=["reward-credentials"])
    Reward = Depends(security.require_reward_user)
    Active = Depends(security.require_active_account)
    async def _verify_and_upsert(
        ctx: AuthContext, body: CredentialsBody, *, plaintext_token: str | None,
    ) -> dict:
        """Chạy luồng xác thực token + command-index rồi UPSERT (dùng chung cho PUT + verify)."""
        token_ciphertext: str | None = None
        discord_user_id: str | None = None
        discord_username: str | None = None
        verify_token = plaintext_token

        if plaintext_token is not None:
            res = await discord_http.request(
                "GET", f"{settings.discord_api_base}/users/@me",
                headers={"Authorization": plaintext_token}, retry_on_network_error=False)
            if res.status_code == 401:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token Discord không hợp lệ")
            if res.status_code >= 400:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không thể xác thực token Discord")
            me = res.json()
            discord_user_id = str(me.get("id") or "")
            discord_username = me.get("username")
            token_ciphertext = crypto.encrypt(plaintext_token)
        else:
            # Không có token mới — nếu đã có token cũ, dùng lại để xác thực command-index.
            async with pool.acquire() as conn:
                existing = await credentials_repo.get_raw(conn, ctx.account_id)
            if existing is not None and existing.get("token_ciphertext"):
                verify_token = crypto.decrypt(existing["token_ciphertext"])

        new_status = "unverified"
        last_error: str | None = None
        set_verified = False
        command_meta: dict | None = None

        if verify_token is not None:
            command = await fetch_guild_command(
                discord_http, api_base=settings.discord_api_base, token=verify_token,
                guild_id=body.guild_id, command_name=body.command_name)
            if command is None:
                new_status = "invalid"
                last_error = f"Không tìm thấy lệnh /{body.command_name} trong guild"
            else:
                new_status = "valid"
                set_verified = True
                command_meta = {
                    "application_id": command.application_id, "command_id": command.id,
                    "version": command.version,
                    "member_option_name": command.member_option_name,
                    "member_option_type": command.member_option_type,
                    "amount_option_name": command.amount_option_name,
                    "amount_option_type": command.amount_option_type,
                }

        async with pool.acquire() as conn:
            row = await credentials_repo.upsert(
                conn, account_id=ctx.account_id, token_ciphertext=token_ciphertext,
                discord_user_id=discord_user_id, discord_username=discord_username,
                guild_id=body.guild_id, channel_id=body.channel_id, command_name=body.command_name,
                confirm_mode=body.confirm_mode, success_pattern=body.success_pattern,
                failure_pattern=body.failure_pattern, leveling_bot_id=body.leveling_bot_id,
                delay_ms=body.delay_ms, jitter_ms=body.jitter_ms, status=new_status,
                last_error=last_error, set_verified=set_verified,
            )

        return {
            "ok": True, "status": row["status"], "discord_username": row["discord_username"],
            "command": command_meta,
        }

    @router.get("", summary="Xem cấu hình Discord của chính mình (không bao giờ trả token)")
    async def get_credentials(ctx: AuthContext = Depends(security.require_reward_user)) -> dict:
        async with pool.acquire() as conn:
            row = await credentials_repo.get_by_account(conn, ctx.account_id)
        return _public_view(row)

    @router.put("", summary="Lưu/cập nhật cấu hình + (tuỳ chọn) token Discord")
    async def put_credentials(
        body: CredentialsBody, ctx: AuthContext = Active, _reward: AuthContext = Reward,
    ) -> dict:
        return await _verify_and_upsert(ctx, body, plaintext_token=body.token)

    @router.post("/verify", summary="Xác thực lại token + command hiện có")
    async def verify_credentials(ctx: AuthContext = Active, _reward: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            existing = await credentials_repo.get_raw(conn, ctx.account_id)
        if existing is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chưa lưu cấu hình Discord")
        body = CredentialsBody(
            token=None, guild_id=existing["guild_id"], channel_id=existing["channel_id"],
            command_name=existing["command_name"], confirm_mode=existing["confirm_mode"],
            success_pattern=existing["success_pattern"], failure_pattern=existing["failure_pattern"],
            leveling_bot_id=existing["leveling_bot_id"], delay_ms=existing["delay_ms"],
            jitter_ms=existing["jitter_ms"],
        )
        return await _verify_and_upsert(ctx, body, plaintext_token=None)

    @router.delete("", summary="Xoá cấu hình Discord của chính mình")
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
        return {"ok": True}

    return router
