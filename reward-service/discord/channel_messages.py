"""Đọc/ghi message kênh bằng USER token — dịch 1-1 từ `src/discord/channel-messages.js`.

URL khi KHÔNG có `after` khớp từng ký tự với Node: `{api_base}/channels/{channel_id}/messages?limit=N`.
Không tự retry mạng khi đọc trong vòng confirm (`retry_on_network_error=False` luôn được truyền bởi
`engine/confirmation.py` và `engine/executor_slash.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from discord.http import DiscordHttpClient


@dataclass(frozen=True)
class MessagesResult:
    ok: bool
    status: int
    messages: list[dict] | None


async def fetch_channel_messages(
    client: DiscordHttpClient, *, api_base: str, channel_id: str, authorization: str,
    limit: int, after: str | None = None,
) -> MessagesResult:
    url = f"{api_base}/channels/{channel_id}/messages?limit={limit}"
    if after is not None:
        url += f"&after={after}"
    res = await client.request("GET", url, headers={"Authorization": authorization}, retry_on_network_error=False)
    if res.is_success:
        return MessagesResult(ok=True, status=res.status_code, messages=res.json())
    return MessagesResult(ok=False, status=res.status_code, messages=None)


@dataclass(frozen=True)
class PostMessageResult:
    ok: bool
    status: int
    message_id: str | None
    error: str | None = None


async def post_channel_message(
    client: DiscordHttpClient, *, api_base: str, channel_id: str, authorization: str,
    content: str, nonce: str | None = None, enforce_nonce: bool = False,
) -> PostMessageResult:
    url = f"{api_base}/channels/{channel_id}/messages"
    body: dict[str, Any] = {"content": content}
    if nonce is not None:
        body["nonce"] = nonce
    if enforce_nonce:
        body["enforce_nonce"] = True
    res = await client.request(
        "POST", url, headers={"Authorization": authorization, "Content-Type": "application/json"},
        json=body, retry_on_network_error=False,
    )
    try:
        parsed = res.json()
    except Exception:  # noqa: BLE001
        parsed = {}
    if res.is_success:
        return PostMessageResult(ok=True, status=res.status_code, message_id=parsed.get("id"))
    return PostMessageResult(ok=False, status=res.status_code, message_id=None, error=parsed.get("message"))
