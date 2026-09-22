"""Slash command qua USER token — dịch 1-1 từ `src/discord/slash-commands.js`.

`Authorization: <token>` THÔ (KHÔNG tiền tố `Bot `) vì slash command chỉ do người dùng tự kích hoạt.
Options `member` (type 6) / `amount` (type 4) lấy từ `application-command-index`; `204` hoặc `2xx`
⇒ `ok=True`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from discord.http import DiscordHttpClient


@dataclass(frozen=True)
class SlashCommand:
    id: str
    version: str
    application_id: str
    name: str
    guild_id: str | None
    member_option_name: str
    member_option_type: int
    amount_option_name: str
    amount_option_type: int


async def fetch_guild_command(
    client: DiscordHttpClient, *, api_base: str, token: str, guild_id: str, command_name: str,
) -> SlashCommand | None:
    url = f"{api_base}/guilds/{guild_id}/application-command-index"
    res = await client.request("GET", url, headers={"Authorization": token}, retry_on_network_error=False)
    if res.status_code >= 400:
        return None
    try:
        data = res.json()
    except Exception:  # noqa: BLE001 — body không parse được → coi như không có lệnh
        data = {}
    cmd = next((c for c in data.get("application_commands", []) if c.get("name") == command_name), None)
    if cmd is None:
        return None
    options = cmd.get("options") or []
    member_opt = next((o for o in options if o.get("type") == 6 or o.get("name") in ("member", "user")), None)
    amount_opt = next((o for o in options if o.get("type") == 4 or o.get("name") in ("amount", "xp", "coins")), None)
    return SlashCommand(
        id=cmd["id"], version=cmd["version"], application_id=cmd["application_id"], name=cmd["name"],
        guild_id=cmd.get("guild_id"),
        member_option_name=member_opt["name"] if member_opt else "member",
        member_option_type=member_opt["type"] if member_opt else 6,
        amount_option_name=amount_opt["name"] if amount_opt else "amount",
        amount_option_type=amount_opt["type"] if amount_opt else 4,
    )


@dataclass(frozen=True)
class InvokeResult:
    ok: bool
    status: int
    error: str | None = None


async def invoke_slash_command(
    client: DiscordHttpClient, *, api_base: str, token: str, command: SlashCommand,
    guild_id: str, channel_id: str, session_id: str, user_id: str, amount: int, nonce: str,
) -> InvokeResult:
    url = f"{api_base}/interactions"
    data: dict[str, Any] = {
        "version": command.version, "id": command.id, "name": command.name, "type": 1,
        "options": [
            {"type": command.member_option_type, "name": command.member_option_name, "value": user_id},
            {"type": command.amount_option_type, "name": command.amount_option_name, "value": int(amount)},
        ],
    }
    if command.guild_id:
        data["guild_id"] = command.guild_id
    payload = {
        "type": 2, "application_id": command.application_id, "guild_id": guild_id,
        "channel_id": channel_id, "session_id": session_id, "data": data, "nonce": nonce,
    }
    res = await client.request(
        "POST", url, headers={"Authorization": token, "Content-Type": "application/json"},
        json=payload, retry_on_network_error=False,
    )
    if res.status_code == 204 or res.is_success:
        return InvokeResult(ok=True, status=res.status_code)
    try:
        err = res.json()
    except Exception:  # noqa: BLE001
        err = {}
    return InvokeResult(ok=False, status=res.status_code, error=err.get("message") or res.reason_phrase)
