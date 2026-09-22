"""Executor slash command — dịch 1-1 từ `src/reward/engine/reward-executor-slash.js`.

Resolve theo thứ tự explicit_id → `reward_user_map` → gateway `op8`. Executor KHÔNG chạm DB ghi
trạng thái item/attempt (chỉ đọc/ghi `reward_user_map` — cache tra cứu, không phải nguồn sự thật)
và KHÔNG quyết định status cuối cùng — chỉ trả `ExecuteOutcome` cho `job_runner` diễn giải.
`point` phải là `int > 0`. Neo xác nhận (`anchor_message_id`) là id message MỚI NHẤT TRƯỚC khi gọi
`POST /interactions` (vì interaction slash trả `204` không kèm message).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import asyncpg

from discord.channel_messages import fetch_channel_messages
from discord.gateway_session import GatewaySession
from discord.http import DiscordHttpClient
from discord.slash_commands import SlashCommand, invoke_slash_command
from store.repositories import user_map as user_map_repo

_SNOWFLAKE = re.compile(r"^\d{15,25}$")
_MENTION_CHARS = re.compile(r"[<@!>]")


@dataclass(frozen=True)
class ExecutorConfig:
    api_base: str
    guild_id: str
    channel_id: str
    user_token: str
    read_authorization: str
    account_id: str


@dataclass
class ExecuteOutcome:
    outcome: str  # 'fail' | 'invoked' | 'http_error' | 'network_error'
    resolve_level: str | None = None
    resolved_user_id: str | None = None
    failure_code: str | None = None
    command_text: str | None = None
    anchor_message_id: str | None = None
    http_status: int | None = None
    application_id: str | None = None
    error_exc: BaseException | None = None


async def _resolve(
    *, session: GatewaySession, config: ExecutorConfig, conn: asyncpg.Connection, item: dict[str, Any],
) -> tuple[str | None, str | None]:
    raw_id = _MENTION_CHARS.sub("", str(item["raw_username"]))
    if _SNOWFLAKE.match(raw_id):
        return raw_id, "explicit_id"

    normalized = item["normalized_username"]
    local = await user_map_repo.lookup(conn, account_id=config.account_id, normalized_username=normalized)
    if local:
        return local, "local"

    try:
        members = await session.request_guild_members(guild_id=config.guild_id, query=normalized, limit=5)
    except Exception:  # noqa: BLE001 — resolve op8 thất bại → coi như không tìm thấy, KHÔNG throw
        members = []
    for m in members or []:
        user = m.get("user") or {}
        username = (user.get("username") or "").lower()
        nick = (m.get("nick") or "").lower()
        global_name = (user.get("global_name") or "").lower()
        if normalized in (username, nick, global_name):
            user_id = user.get("id")
            if user_id:
                try:
                    await user_map_repo.save(
                        conn, account_id=config.account_id, normalized_username=normalized, discord_user_id=user_id)
                except Exception:  # noqa: BLE001 — lưu cache thất bại không chặn luồng resolve
                    pass
                return user_id, "member_search"
    return None, None


async def execute(
    item: dict[str, Any], attempt: dict[str, Any], *,
    http_client: DiscordHttpClient, session: GatewaySession, config: ExecutorConfig,
    command: SlashCommand, pool_conn: asyncpg.Connection,
) -> ExecuteOutcome:
    user_id, resolve_level = await _resolve(session=session, config=config, conn=pool_conn, item=item)
    if not user_id:
        return ExecuteOutcome(outcome="fail", failure_code="USER_NOT_FOUND", resolve_level=resolve_level)

    point = item["point"]
    if not isinstance(point, int) or point <= 0:
        return ExecuteOutcome(
            outcome="fail", failure_code="INVALID_POINT", resolve_level=resolve_level, resolved_user_id=user_id)

    command_text = f"/{command.name} {command.member_option_name}:<@{user_id}> {command.amount_option_name}:{point}"

    anchor = "0"
    try:
        latest = await fetch_channel_messages(
            http_client, api_base=config.api_base, channel_id=config.channel_id,
            authorization=config.read_authorization, limit=1)
        if latest.ok and latest.messages:
            anchor = latest.messages[0]["id"]
    except Exception:  # noqa: BLE001 — không lấy được neo → giữ mặc định '0' (giống Node)
        pass

    try:
        session_id = await session.get_session_id()
    except Exception as exc:  # noqa: BLE001
        return ExecuteOutcome(
            outcome="network_error", command_text=command_text,
            resolve_level=resolve_level, resolved_user_id=user_id, error_exc=exc)

    try:
        res = await invoke_slash_command(
            http_client, api_base=config.api_base, token=config.user_token, command=command,
            guild_id=config.guild_id, channel_id=config.channel_id, session_id=session_id,
            user_id=user_id, amount=point, nonce=attempt["nonce"])
    except Exception as exc:  # noqa: BLE001
        return ExecuteOutcome(
            outcome="network_error", command_text=command_text,
            resolve_level=resolve_level, resolved_user_id=user_id, error_exc=exc)

    if res.ok:
        return ExecuteOutcome(
            outcome="invoked", command_text=command_text, anchor_message_id=anchor,
            http_status=res.status, resolve_level=resolve_level, resolved_user_id=user_id,
            application_id=command.application_id)
    return ExecuteOutcome(
        outcome="http_error", http_status=res.status, command_text=command_text,
        resolve_level=resolve_level, resolved_user_id=user_id)
