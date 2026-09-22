"""Xác nhận qua reply — dịch 1-1 từ `src/reward/engine/confirmation-listener.js`.

Quét CẢ `content` VÀ `embeds` (title/description/author.name/footer.text/fields) — nhiều bot leveling
trả kết quả trong embed. CHỈ khớp `success_pattern` ⇒ `success` (L2, mức xác nhận mạnh nhất). Có cả
success lẫn failure trong cùng cửa sổ poll ⇒ KHÔNG success (`CONFLICTING_REPLIES`, unknown).
`link_mode` ưu tiên `reply_ref` (message_reference trỏ đúng anchor) → `bot_id` (author khớp
`application_id` của lệnh) → `heuristic` (text chứa discord_user_id hoặc username). Hết giờ ⇒
`unknown/CONFIRM_TIMEOUT`. `confirm_mode='off'` ⇒ `unknown/message_posted`, KHÔNG gửi request nào.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from discord.channel_messages import fetch_channel_messages
from discord.http import DiscordHttpClient

logger = logging.getLogger("reward.engine.confirmation")


@dataclass(frozen=True)
class ConfirmationConfig:
    api_base: str
    channel_id: str
    read_authorization: str
    confirm_mode: str  # 'off' | 'reply'
    confirm_timeout_s: float
    confirm_poll_interval_s: float
    success_pattern: re.Pattern | None
    failure_pattern: re.Pattern | None
    leveling_bot_id: str | None


@dataclass
class ConfirmationResult:
    status: str  # 'success' | 'failed' | 'unknown'
    confirmation_level: str
    failure_code: str | None = None
    failure_message: str | None = None
    reply_message_id: str | None = None
    reply_author_id: str | None = None
    reply_excerpt: str | None = None
    link_mode: str | None = None


def _text_of(msg: dict[str, Any]) -> str:
    parts = [msg.get("content") or ""]
    for e in msg.get("embeds") or []:
        if e.get("title"):
            parts.append(e["title"])
        if e.get("description"):
            parts.append(e["description"])
        author = e.get("author") or {}
        if author.get("name"):
            parts.append(author["name"])
        footer = e.get("footer") or {}
        if footer.get("text"):
            parts.append(footer["text"])
        for f in e.get("fields") or []:
            if f.get("name"):
                parts.append(f["name"])
            if f.get("value"):
                parts.append(f["value"])
    return " \n ".join(p for p in parts if p)


def _link_of(msg: dict[str, Any], message_id: str, item: dict[str, Any], expected_bot_id: str | None) -> str | None:
    ref = msg.get("message_reference") or {}
    if ref.get("message_id") == message_id:
        return "reply_ref"
    author = msg.get("author") or {}
    if expected_bot_id and author.get("id") == expected_bot_id:
        return "bot_id"
    text = _text_of(msg)
    uid = item.get("resolved_user_id")
    uname = (item.get("normalized_username") or "").lower()
    if uid and uid in text:
        return "heuristic"
    if uname and uname in text.lower():
        return "heuristic"
    return None


async def await_confirmation(
    http_client: DiscordHttpClient, *, config: ConfirmationConfig, message_id: str,
    item: dict[str, Any], expected_bot_id: str | None = None,
) -> ConfirmationResult:
    if config.confirm_mode == "off":
        return ConfirmationResult(status="unknown", confirmation_level="message_posted", failure_code="CONFIRM_DISABLED")

    deadline = time.monotonic() + config.confirm_timeout_s
    after = message_id
    saw_candidate = False
    last_excerpt: str | None = None
    last_unmatched: dict[str, Any] | None = None
    success_hits: list[dict[str, Any]] = []
    failure_hits: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        res = await fetch_channel_messages(
            http_client, api_base=config.api_base, channel_id=config.channel_id,
            authorization=config.read_authorization, limit=10, after=after)
        if res.ok and res.messages is not None:
            msgs = sorted(res.messages, key=lambda m: int(m["id"]))
            for m in msgs:
                if int(m["id"]) > int(after):
                    after = m["id"]
                author = m.get("author") or {}
                if not author.get("bot"):
                    continue
                if config.leveling_bot_id and author.get("id") != config.leveling_bot_id:
                    continue
                link_mode = _link_of(m, message_id, item, expected_bot_id)
                if not link_mode:
                    continue

                saw_candidate = True
                text = _text_of(m)
                excerpt = text[:500]
                last_excerpt = excerpt
                if text.strip() == "":
                    logger.warning(
                        "[Reward] Reply của bot rỗng (content + embed) — có thể thiếu MESSAGE CONTENT INTENT.")
                rec = {"message_id": m["id"], "author_id": author.get("id"), "excerpt": excerpt, "link_mode": link_mode}
                if config.success_pattern and config.success_pattern.search(text):
                    success_hits.append(rec)
                elif config.failure_pattern and config.failure_pattern.search(text):
                    failure_hits.append(rec)
                else:
                    last_unmatched = rec

        if success_hits and failure_hits:
            break
        if success_hits:
            r = success_hits[0]
            return ConfirmationResult(
                status="success", confirmation_level="bot_reply", reply_message_id=r["message_id"],
                reply_author_id=r["author_id"], reply_excerpt=r["excerpt"], link_mode=r["link_mode"])
        if failure_hits:
            r = failure_hits[0]
            return ConfirmationResult(
                status="failed", confirmation_level="bot_reply", failure_code="BOT_REJECTED",
                failure_message=r["excerpt"], reply_message_id=r["message_id"], reply_author_id=r["author_id"],
                reply_excerpt=r["excerpt"], link_mode=r["link_mode"])
        await asyncio.sleep(config.confirm_poll_interval_s)

    if success_hits and failure_hits:
        r = success_hits[0]
        return ConfirmationResult(
            status="unknown", confirmation_level="bot_reply_unmatched", failure_code="CONFLICTING_REPLIES",
            reply_message_id=r["message_id"], reply_author_id=r["author_id"],
            reply_excerpt=f"[multi] {r['excerpt']}", link_mode=r["link_mode"])
    if saw_candidate:
        r = last_unmatched or {}
        return ConfirmationResult(
            status="unknown", confirmation_level="bot_reply_unmatched", failure_code="BOT_REPLY_UNMATCHED",
            reply_message_id=r.get("message_id"), reply_author_id=r.get("author_id"),
            reply_excerpt=last_excerpt, link_mode=r.get("link_mode"))
    return ConfirmationResult(status="unknown", confirmation_level="message_posted", failure_code="CONFIRM_TIMEOUT")
