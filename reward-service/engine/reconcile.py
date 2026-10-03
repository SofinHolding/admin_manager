"""Đối soát item `unknown` với LỊCH SỬ KÊNH Discord — tìm câu trả lời thật của bot sau khi item đã được gửi.

Vì sao cần: `unknown` nghĩa là "đã gửi nhưng chưa thấy xác nhận trong cửa sổ chờ", KHÔNG có nghĩa là chưa cộng
điểm (reply tới muộn, tin bot còn rỗng lúc quét, `POST /interactions` timeout nhưng Discord vẫn thực thi…). Trước
đây item `unknown` kẹt vĩnh viễn vì không có cách nào kiểm lại. Ở đây ta quét kênh từ lúc gửi, ghép reply theo
NGƯỜI NHẬN (`<@!id>` trong reply) + SỐ ĐIỂM + `success_pattern`:

* khớp success ⇒ item `success` + ghi sổ `reward_distributions` (không gửi lại ⇒ không cộng đôi);
* khớp failure_pattern ⇒ item `failed`;
* quét HẾT kênh tới hiện tại, đã quá `grace_s` kể từ lúc gửi mà không có reply khớp ⇒ `reconcile_result='no_reply'`
  (bằng chứng để cho phép gửi lại an toàn); chưa đủ bằng chứng ⇒ giữ nguyên `unknown`.

Neo quét: `reward_attempts.message_id` (id message mới nhất TRƯỚC khi gọi interactions) nếu có, ngược lại suy ra
snowflake từ thời điểm gửi (attempt cũ chưa lưu neo). Mỗi reply chỉ được dùng cho 1 item.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from discord.channel_messages import fetch_channel_messages
from discord.http import DiscordHttpClient
from domain.defaults import DEFAULT_SUCCESS_PATTERN
from engine.confirmation import _text_of
from store.pool import Pool
from store.repositories import attempts as attempts_repo
from store.repositories import distributions as dist_repo
from store.repositories import items as items_repo

logger = logging.getLogger("reward.engine.reconcile")

_DISCORD_EPOCH_MS = 1420070400000
_PAGE = 100
_MAX_PAGES = 30
_SINCE_MARGIN_S = 5.0  # lùi nhẹ khỏi thời điểm gửi để không sót reply đến sát giờ


def snowflake_from_datetime(dt: datetime.datetime) -> str:
    """Snowflake nhỏ nhất ứng với thời điểm `dt` (dùng làm tham số `after` của GET messages)."""
    ms = int(dt.timestamp() * 1000) - _DISCORD_EPOCH_MS
    return str(max(ms, 0) << 22)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


_THOUSANDS = re.compile(r"(?<=\d)[,.](?=\d{3}(?!\d))")
_MENTION = re.compile(r"<@[!&]?\d+>")


def _has_amount(text: str, point: int) -> bool:
    """`14,000 XP` / `14.000 XP` / `14000 XP` đều khớp point=14000; không khớp nếu chỉ là một phần của số khác
    hoặc nằm trong ID của mention (`<@!…>`)."""
    cleaned = _THOUSANDS.sub("", _MENTION.sub(" ", text))
    return re.search(rf"(?<!\d){point}(?!\d)", cleaned) is not None


@dataclass
class ReconcileSummary:
    checked: int = 0
    success: int = 0
    failed: int = 0
    no_reply: int = 0       # đã kết luận: kênh không có reply khớp ⇒ an toàn gửi lại
    waiting: int = 0        # chưa quá thời gian chờ ⇒ chưa kết luận
    inconclusive: int = 0   # không quét hết được kênh (lỗi/đạt giới hạn trang) ⇒ không kết luận

    def merge(self, other: "ReconcileSummary") -> None:
        for f in ("success", "failed", "no_reply"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        self.checked = max(self.checked, other.checked)
        self.waiting = other.waiting
        self.inconclusive = other.inconclusive


def _since_id(row: dict[str, Any]) -> str:
    anchor = row.get("anchor")
    if anchor and anchor != "0":
        return str(anchor)
    sent_at = row["sent_at"]
    return snowflake_from_datetime(sent_at - datetime.timedelta(seconds=_SINCE_MARGIN_S))


async def _scan(
    http_client: DiscordHttpClient, *, api_base: str, channel_id: str, authorization: str,
    since_id: str, leveling_bot_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Các tin của bot sau `since_id` (tăng dần theo id) + cờ đã quét hết tới hiện tại."""
    out: list[dict[str, Any]] = []
    after = since_id
    for _ in range(_MAX_PAGES):
        res = await fetch_channel_messages(
            http_client, api_base=api_base, channel_id=channel_id, authorization=authorization,
            limit=_PAGE, after=after)
        if not res.ok or res.messages is None:
            return out, False
        msgs = sorted(res.messages, key=lambda m: int(m["id"]))
        for m in msgs:
            author = m.get("author") or {}
            if not author.get("bot"):
                continue
            if leveling_bot_id and author.get("id") != leveling_bot_id:
                continue
            out.append(m)
        if len(res.messages) < _PAGE:
            return out, True
        after = msgs[-1]["id"]
    return out, False


def _match(
    msgs: list[dict[str, Any]], *, since_id: str, user_id: str, point: int, used: set[str],
    success_pattern: re.Pattern, failure_pattern: re.Pattern | None,
) -> tuple[str, dict[str, Any]] | None:
    success: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    for m in msgs:
        if m["id"] in used or int(m["id"]) <= int(since_id):
            continue
        text = _text_of(m)
        if user_id not in text:
            continue
        if success_pattern.search(text) and _has_amount(text, point):
            success = success or m
        elif failure_pattern is not None and failure_pattern.search(text):
            failure = failure or m
    if success is not None and failure is not None:
        return None  # mâu thuẫn ⇒ không kết luận (giống CONFLICTING_REPLIES khi xác nhận)
    if success is not None:
        return "success", success
    if failure is not None:
        return "failed", failure
    return None


async def _one_pass(
    pool: Pool, http_client: DiscordHttpClient, *, job: dict[str, Any], authorization: str, api_base: str,
    success_pattern: re.Pattern, failure_pattern: re.Pattern | None, leveling_bot_id: str | None,
    grace_s: float, only_item_ids: set[int] | None,
) -> tuple[ReconcileSummary, float]:
    """Một lượt quét + ghi kết quả. Trả `(tóm tắt, số giây còn phải chờ để đủ grace cho item chưa kết luận)`."""
    summary = ReconcileSummary()
    async with pool.acquire() as conn:
        rows = await items_repo.list_unknown_for_reconcile(conn, job["id"])
    if only_item_ids is not None:
        rows = [r for r in rows if r["id"] in only_item_ids]
    summary.checked = len(rows)
    if not rows:
        return summary, 0.0

    sinces = {r["id"]: _since_id(r) for r in rows}
    earliest = min(sinces.values(), key=int)
    msgs, complete = await _scan(
        http_client, api_base=api_base, channel_id=job["channel_id"], authorization=authorization,
        since_id=earliest, leveling_bot_id=leveling_bot_id)

    used: set[str] = set()
    remaining_wait = 0.0
    now = _now()
    for row in rows:
        hit = _match(
            msgs, since_id=sinces[row["id"]], user_id=row["resolved_user_id"], point=row["point"], used=used,
            success_pattern=success_pattern, failure_pattern=failure_pattern)
        if hit is not None:
            kind, msg = hit
            used.add(msg["id"])
            excerpt = _text_of(msg)[:500]
            author_id = (msg.get("author") or {}).get("id")
            async with pool.acquire() as conn, conn.transaction():
                if kind == "success":
                    await attempts_repo.finalize(
                        conn, attempt_id=row["attempt_id"], phase="confirmed", reply_message_id=msg["id"],
                        reply_author_id=author_id, reply_excerpt=excerpt, link_mode="reconcile")
                    await items_repo.finalize(
                        conn, item_id=row["id"], status="success", confirmation_level="bot_reply",
                        resolved_user_id=row["resolved_user_id"], resolved_by="system", first_sent_at=row["sent_at"])
                    await dist_repo.insert_once(
                        conn, item_id=row["id"], attempt_id=row["attempt_id"], job_id=job["id"],
                        account_id=job["account_id"], discord_user_id=row["resolved_user_id"], point=row["point"],
                        evidence={"level": "bot_reply", "reconciled": True, "reply_message_id": msg["id"],
                                  "excerpt": excerpt})
                    summary.success += 1
                else:
                    await attempts_repo.finalize(
                        conn, attempt_id=row["attempt_id"], phase="rejected", reply_message_id=msg["id"],
                        reply_author_id=author_id, reply_excerpt=excerpt, link_mode="reconcile",
                        error_code="BOT_REJECTED")
                    await items_repo.finalize(
                        conn, item_id=row["id"], status="failed", confirmation_level="bot_reply",
                        failure_code="BOT_REJECTED", failure_message=excerpt,
                        resolved_user_id=row["resolved_user_id"], first_sent_at=row["sent_at"])
                    summary.failed += 1
            continue

        age_s = (now - row["sent_at"]).total_seconds()
        if not complete:
            summary.inconclusive += 1
        elif age_s < grace_s:
            summary.waiting += 1
            remaining_wait = max(remaining_wait, grace_s - age_s)
        else:
            async with pool.acquire() as conn:
                await items_repo.mark_reconciled(conn, row["id"], "no_reply")
            summary.no_reply += 1
    return summary, remaining_wait


async def reconcile_job(
    pool: Pool, http_client: DiscordHttpClient, *, job: dict[str, Any], authorization: str, api_base: str,
    success_pattern: re.Pattern | None, failure_pattern: re.Pattern | None, leveling_bot_id: str | None,
    grace_s: float, wait_for_grace: bool = False, should_stop: Callable[[], bool] = lambda: False,
) -> ReconcileSummary:
    """Đối soát mọi item `unknown` của job. `wait_for_grace=True`: item mới gửi (chưa đủ `grace_s`) được chờ rồi
    quét lại đúng 1 lần nữa (ngắt ngay nếu `should_stop()`)."""
    sp = success_pattern or re.compile(DEFAULT_SUCCESS_PATTERN)
    kwargs = dict(
        job=job, authorization=authorization, api_base=api_base, success_pattern=sp,
        failure_pattern=failure_pattern, leveling_bot_id=leveling_bot_id, grace_s=grace_s)
    summary, wait_s = await _one_pass(pool, http_client, only_item_ids=None, **kwargs)
    if wait_for_grace and summary.waiting > 0 and wait_s > 0:
        deadline = _now() + datetime.timedelta(seconds=wait_s)
        while _now() < deadline and not should_stop():
            await asyncio.sleep(min(0.25, max(0.0, (deadline - _now()).total_seconds())))
        if not should_stop():
            second, _ = await _one_pass(pool, http_client, only_item_ids=None, **kwargs)
            summary.merge(second)
    logger.info(
        "[Reward] Job %s đối soát: %s item unknown → success=%s failed=%s no_reply=%s chờ=%s chưa kết luận=%s",
        job["id"], summary.checked, summary.success, summary.failed, summary.no_reply, summary.waiting,
        summary.inconclusive)
    return summary
