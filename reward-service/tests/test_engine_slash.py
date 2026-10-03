"""Test engine slash end-to-end trên fake Discord offline + Postgres thật.

Đối chiếu trực tiếp với `test/reward/slash.test.js` (Node, đã pass) — happy path (explicit id),
resolve qua Gateway op8, USER_NOT_FOUND, 403 → pause job, 5xx → unknown, interaction treo → timeout,
CONFIRM_MODE=off, reply không khớp pattern nào (I4) và reply khớp failure_pattern.
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from domain.defaults import DEFAULT_SUCCESS_PATTERN
from discord.gateway_session import GatewaySession
from discord.http import DiscordHttpClient
from discord.slash_commands import fetch_guild_command
from engine import confirmation as confirmation_engine
from engine import executor_slash
from engine.job_runner import JobRunner, JobRunnerConfig
from engine.token_pool import TokenPool, TokenSlot
from store.repositories import distributions as dist_repo
from store.repositories import events as events_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from store.repositories import tokens as tokens_repo
from tests.conftest import seed_active_account_with_credential, seed_job
from tests.harness.fake_discord import start_fake

RECIPIENT = "685096940144295977"


async def _build_runner(pool, fake, job_id, job_row, *, http_timeout_s=2.0, confirm_timeout_s=1.0,
                         confirm_poll_interval_s=0.05, tokens=("utok",), reconcile_grace_s=0.0, dead_gateway=()):
    http_client = DiscordHttpClient(read_timeout_s=http_timeout_s)
    command = await fetch_guild_command(
        http_client, api_base=fake.api_base, token=tokens[0],
        guild_id=job_row["guild_id"], command_name=job_row["command_name"])
    assert command is not None

    slots = []
    async with pool.acquire() as conn:
        for i, tok in enumerate(tokens):
            row = await tokens_repo.insert(
                conn, account_id=job_row["account_id"], label=f"t{i}", token_ciphertext="x",
                discord_user_id=None, discord_username=None, status="valid", last_error=None, set_verified=True)
            slots.append(TokenSlot(
                token_id=row["id"], label=row["label"], token=tok,
                session=GatewaySession(
                    gateway_url="ws://127.0.0.1:1/" if tok in dead_gateway else fake.gateway_url, token=tok)))
    token_pool = TokenPool(slots, delay_ms=job_row["delay_ms"], jitter_ms=job_row["jitter_ms"])

    executor_config = executor_slash.ExecutorConfig(
        api_base=fake.api_base, guild_id=job_row["guild_id"], channel_id=job_row["channel_id"],
        account_id=job_row["account_id"])
    confirmation_config = confirmation_engine.ConfirmationConfig(
        api_base=fake.api_base, channel_id=job_row["channel_id"],
        confirm_mode=job_row["confirm_mode"], confirm_timeout_s=confirm_timeout_s,
        confirm_poll_interval_s=confirm_poll_interval_s,
        success_pattern=re.compile(job_row["success_pattern"]) if job_row["success_pattern"] else None,
        failure_pattern=re.compile(job_row["failure_pattern"]) if job_row["failure_pattern"] else None,
        leveling_bot_id=job_row["leveling_bot_id"])
    runner_config = JobRunnerConfig(
        channel_id=job_row["channel_id"], account_id=job_row["account_id"],
        max_item_retries=job_row["max_item_retries"], unknown_pause_threshold=job_row["unknown_pause_threshold"],
        confirm_mode=job_row["confirm_mode"], success_pattern=confirmation_config.success_pattern,
        reconcile_grace_s=reconcile_grace_s)
    runner = JobRunner(
        pool=pool, config=runner_config, http_client=http_client, tokens=token_pool,
        command=command, executor_config=executor_config, confirmation_config=confirmation_config)
    return runner, http_client, token_pool


async def _run(pool, fake, *, rows, account_id=None, tokens=("utok",), reconcile_grace_s=0.0, dead_gateway=(),
               confirm_timeout_s=1.0, **job_kwargs):
    async with pool.acquire() as conn:
        account_id = account_id or await seed_active_account_with_credential(conn)
        job_id = await seed_job(conn, account_id=account_id, rows=rows, **job_kwargs)
        job_row = await jobs_repo.get_job(conn, job_id)
    runner, http_client, token_pool = await _build_runner(
        pool, fake, job_id, job_row, tokens=tokens, reconcile_grace_s=reconcile_grace_s,
        dead_gateway=dead_gateway, confirm_timeout_s=confirm_timeout_s)
    try:
        await runner.run(job_id)
    finally:
        await token_pool.close()
        await http_client.aclose()
    async with pool.acquire() as conn:
        items = await items_repo.list_all(conn, job_id)
        job_row = await jobs_repo.get_job(conn, job_id)
        dist_count = await dist_repo.count_for_job(conn, job_id)
    return job_row, items, dist_count


async def test_slash_happy_explicit_id_success(pool):
    fake = await start_fake()
    try:
        job_row, items, dist_count = await _run(pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}])
        assert job_row["status"] == "completed"
        assert items[0]["status"] == "success"
        assert items[0]["resolve_level"] == "explicit_id"
        assert dist_count == 1
        assert len(fake.interactions) == 1
        opts = {o["name"]: o["value"] for o in fake.interactions[0]["data"]["options"]}
        assert opts["member"] == RECIPIENT
        assert opts["amount"] == 5
        assert fake.interactions[0]["data"]["name"] == "give-xp"
    finally:
        await fake.close()


async def test_slash_resolve_via_gateway_op8(pool):
    members = {"nuki": [{"user": {"username": "nuki", "global_name": None, "id": RECIPIENT}, "nick": None}]}
    fake = await start_fake(members=members)
    try:
        job_row, items, dist_count = await _run(pool, fake, rows=[{"raw_username": "nuki", "point": 7}])
        assert items[0]["status"] == "success"
        assert items[0]["resolve_level"] == "member_search"
        assert items[0]["resolved_user_id"] == RECIPIENT
        assert len(fake.interactions_for(RECIPIENT)) == 1
    finally:
        await fake.close()


async def test_slash_user_not_found(pool):
    fake = await start_fake(members={})
    try:
        _job_row, items, _dist = await _run(pool, fake, rows=[{"raw_username": "ghost", "point": 1}])
        assert items[0]["failure_code"] == "USER_NOT_FOUND"
        assert items[0]["status"] == "failed"
        assert len(fake.interactions) == 0
    finally:
        await fake.close()


async def test_slash_403_pauses_job_remaining_stay_pending(pool):
    fake = await start_fake(interaction_plan=[403])
    try:
        job_row, items, _dist = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 1}, {"raw_username": "333333333333333333", "point": 2}])
        assert items[0]["failure_code"] == "MISSING_PERMISSION"
        assert items[0]["status"] == "failed"
        assert items[1]["status"] == "pending"  # I5: item còn lại GIỮ pending, không bị claim
        assert job_row["status"] == "paused"
    finally:
        await fake.close()


async def test_slash_5xx_unknown_no_distribution(pool):
    fake = await start_fake(interaction_plan=[500])
    try:
        _job_row, items, dist_count = await _run(pool, fake, rows=[{"raw_username": RECIPIENT, "point": 1}])
        assert items[0]["failure_code"] == "UPSTREAM_5XX"
        assert items[0]["status"] == "unknown"
        assert dist_count == 0
    finally:
        await fake.close()


async def test_slash_interaction_hangs_timeout_unknown(pool):
    fake = await start_fake(interaction_plan=["hang"])
    try:
        async with pool.acquire() as conn:
            account_id = await seed_active_account_with_credential(conn)
            job_id = await seed_job(conn, account_id=account_id, rows=[{"raw_username": RECIPIENT, "point": 1}])
            job_row = await jobs_repo.get_job(conn, job_id)
        runner, http_client, session = await _build_runner(pool, fake, job_id, job_row, http_timeout_s=0.3)
        try:
            await runner.run(job_id)
        finally:
            await session.close()
            await http_client.aclose()
        async with pool.acquire() as conn:
            items = await items_repo.list_all(conn, job_id)
        assert items[0]["failure_code"] == "SEND_RESULT_UNKNOWN"
        assert items[0]["status"] == "unknown"
    finally:
        await fake.close()


async def test_slash_confirm_mode_off(pool):
    fake = await start_fake()
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 1}], confirm_mode="off")
        assert items[0]["status"] == "unknown"
        assert items[0]["confirmation_level"] == "message_posted"
        assert dist_count == 0
    finally:
        await fake.close()


async def test_slash_reply_matches_failure_pattern(pool):
    fake = await start_fake(reply_plan=[{"text": "Bạn không có quyền dùng lệnh"}])
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 1}], failure_pattern="không có quyền")
        assert items[0]["status"] == "failed"
        assert dist_count == 0
    finally:
        await fake.close()


async def test_slash_reply_unmatched_pattern_is_unknown_no_resend(pool):
    # I4: reply không khớp success_pattern (và không có failure_pattern) ⇒ unknown, KHÔNG gửi lại.
    fake = await start_fake(reply_plan=[{"text": "Đã xử lý xong yêu cầu của bạn"}])
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 1}], success_pattern="has been given")
        assert items[0]["status"] == "unknown"
        assert items[0]["confirmation_level"] == "bot_reply_unmatched"
        assert dist_count == 0
        assert len(fake.interactions) == 1  # đúng 1 lần — item unknown không bao giờ tự gửi lại
    finally:
        await fake.close()


# ── Reply "đang xử lý" (interaction defer): tin rỗng được bot điền nội dung sau ──────────────────

async def test_slash_deferred_empty_reply_is_rescanned_until_filled(pool):
    # Bug thật (job 4 trên VPS): quét thấy tin bot RỖNG → chốt `bot_reply_unmatched` dù vài giây sau
    # bot điền "N XP has been given to". Phải quét lại đúng tin đó tới khi có nội dung.
    fake = await start_fake(reply_plan=[{"defer_s": 0.4}])
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}], success_pattern=r"\d+\s*XP has been given to")
        assert items[0]["status"] == "success"
        assert items[0]["confirmation_level"] == "bot_reply"
        assert dist_count == 1
        assert len(fake.interactions) == 1
    finally:
        await fake.close()


async def test_slash_reply_that_stays_empty_is_unknown_not_success(pool):
    fake = await start_fake(reply_plan=[{"defer_s": 30}])
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}], success_pattern="has been given")
        assert items[0]["status"] == "unknown"
        assert items[0]["failure_code"] == "BOT_REPLY_UNMATCHED"
        assert dist_count == 0
    finally:
        await fake.close()


async def test_default_pattern_matches_real_axolink_reply_with_thousands_separator(pool):
    # Câu trả lời thật của bot khi thành công (người dùng cung cấp): có dấu phẩy ngăn cách hàng nghìn.
    fake = await start_fake(reply_plan=[{"text": "\u2705 14,000 XP has been given to <@!981815742070669313>"}])
    try:
        _job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": "981815742070669313", "point": 14000}],
            success_pattern=DEFAULT_SUCCESS_PATTERN)
        assert items[0]["status"] == "success" and dist_count == 1
    finally:
        await fake.close()


# ── Xoay vòng nhiều token ─────────────────────────────────────────────────────────────────────

_ROWS4 = [{"raw_username": f"68509694014429597{i}", "point": 1} for i in range(4)]


async def test_multi_token_rotates_across_accounts(pool):
    fake = await start_fake()
    try:
        job_row, items, dist_count = await _run(pool, fake, rows=_ROWS4, tokens=("tokA", "tokB"))
        assert job_row["status"] == "completed"
        assert all(i["status"] == "success" for i in items)
        assert dist_count == 4
        sent = fake.interaction_tokens
        assert sorted(sent) == ["tokA", "tokA", "tokB", "tokB"]
        assert all(a != b for a, b in zip(sent, sent[1:]))  # xen kẽ: không dùng 1 token 2 lần liên tiếp
        async with pool.acquire() as conn:
            attempts = await conn.fetch("SELECT token_id FROM reward_attempts WHERE job_id=$1", job_row["id"])
        assert len({a["token_id"] for a in attempts}) == 2  # mỗi attempt ghi lại token đã gửi
    finally:
        await fake.close()


async def test_multi_token_rejected_token_fails_over_without_pausing_or_losing_items(pool):
    # Token "bad" bị Discord từ chối (401) → vô hiệu + item được gửi lại bằng token "good";
    # job KHÔNG pause, không item nào kẹt pending/failed, không tốn ngân sách retry.
    fake = await start_fake(interaction_plan_by_token={"bad": 401})
    try:
        job_row, items, dist_count = await _run(pool, fake, rows=_ROWS4, tokens=("bad", "good"))
        assert job_row["status"] == "completed"
        assert [i["status"] for i in items] == ["success"] * 4
        assert dist_count == 4
        assert fake.interaction_tokens.count("bad") == 1  # bị loại sau lần đầu
        assert fake.interaction_tokens.count("good") == 4
        assert items[0]["attempt_count"] == 1  # failover không tiêu ngân sách retry
        async with pool.acquire() as conn:
            bad = await conn.fetchrow("SELECT status, last_error FROM reward_discord_tokens WHERE label='t0'")
            events = await events_repo.list_events(conn, job_row["id"])
        assert bad["status"] == "invalid" and "AUTH_INVALID" in bad["last_error"]
        assert [e["type"] for e in events].count("token_disabled") == 1
        assert "auto_pause" not in [e["type"] for e in events]
    finally:
        await fake.close()


async def test_multi_token_all_rejected_pauses_and_keeps_rest_pending(pool):
    fake = await start_fake(interaction_plan=[403])
    try:
        job_row, items, _dist = await _run(pool, fake, rows=_ROWS4, tokens=("a", "b"))
        assert job_row["status"] == "paused"
        assert items[0]["status"] == "failed"  # token cuối cùng bị từ chối → item failed (như 1 token)
        assert [i["status"] for i in items[1:]] == ["pending"] * 3
        assert len(fake.interactions) == 2  # mỗi token thử đúng 1 lần rồi bị loại
    finally:
        await fake.close()


async def test_multi_token_unknown_streak_disables_only_that_token(pool):
    # Token "flaky" cho 5xx liên tiếp (unknown) → bị loại sau `unknown_pause_threshold`, các token còn lại
    # xử lý hết phần việc còn lại; job hoàn tất thay vì pause.
    fake = await start_fake(interaction_plan_by_token={"flaky": 500})
    rows = [{"raw_username": f"68509694014429597{i}", "point": 1} for i in range(6)]
    try:
        job_row, items, _dist = await _run(
            pool, fake, rows=rows, tokens=("flaky", "solid"), unknown_pause_threshold=2)
        assert job_row["status"] == "completed"
        statuses = [i["status"] for i in items]
        assert statuses.count("unknown") == 2 and statuses.count("success") == 4
        assert fake.interaction_tokens.count("flaky") == 2
    finally:
        await fake.close()


# ── Chống kẹt `unknown`: đối soát với lịch sử kênh, lỗi Gateway, chuỗi unknown toàn job ──────────

_SUCCESS = r"\d+\s*XP has been given to"


async def test_late_reply_is_found_by_final_sweep_and_item_becomes_success_without_resend(pool):
    # Bot trả lời SAU cửa sổ xác nhận (0.5s) — trước đây item kẹt `unknown` mãi. Giờ job chờ đủ thời gian đối soát
    # (3s), quét lại kênh thấy reply của đúng người nhận + đúng số điểm ⇒ success, KHÔNG gửi lại.
    fake = await start_fake(reply_plan=[{"late_s": 1.5}])
    try:
        job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}], success_pattern=_SUCCESS,
            reconcile_grace_s=3.0, confirm_timeout_s=0.5)
        assert job_row["status"] == "completed"
        assert items[0]["status"] == "success" and items[0]["confirmation_level"] == "bot_reply"
        assert items[0]["failure_code"] is None
        assert dist_count == 1
        assert len(fake.interactions) == 1  # không bao giờ gửi lại
        async with pool.acquire() as conn:
            att = await conn.fetchrow("SELECT phase, link_mode FROM reward_attempts WHERE item_id=$1", items[0]["id"])
            events = await events_repo.list_events(conn, job_row["id"])
        assert att["phase"] == "confirmed" and att["link_mode"] == "reconcile"
        assert "reconcile" in [e["type"] for e in events]
    finally:
        await fake.close()


async def test_no_reply_anywhere_is_marked_no_reply_and_never_resent_automatically(pool):
    fake = await start_fake(interaction_plan=[500])
    try:
        job_row, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}], success_pattern=_SUCCESS)
        assert items[0]["status"] == "unknown" and items[0]["failure_code"] == "UPSTREAM_5XX"
        assert items[0]["reconcile_result"] == "no_reply"  # đã quét hết kênh, không có reply ⇒ an toàn gửi lại
        assert dist_count == 0 and len(fake.interactions) == 1
        assert job_row["status"] == "completed"
    finally:
        await fake.close()


def _bot_msg(fake, text: str) -> None:
    fake.store.append({
        "id": str(next(fake._seq)), "author": {"bot": True, "id": fake.app_id}, "content": "",
        "embeds": [{"description": text}],
    })


async def test_reconcile_ignores_replies_for_other_user_or_other_amount(pool):
    fake = await start_fake(interaction_plan=[500])
    _bot_msg(fake, "\u2705 5 XP has been given to <@!685096940144295999>")   # đúng số điểm, SAI người
    _bot_msg(fake, f"\u2705 999 XP has been given to <@!{RECIPIENT}>")       # đúng người, SAI số điểm
    try:
        _job, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}], success_pattern=_SUCCESS)
        assert items[0]["status"] == "unknown" and items[0]["reconcile_result"] == "no_reply"
        assert dist_count == 0
    finally:
        await fake.close()


async def test_request_errored_but_discord_executed_is_recovered_from_channel_history(pool):
    # POST /interactions trả 5xx/timeout nhưng Discord ĐÃ chạy lệnh — bằng chứng nằm trong lịch sử kênh.
    fake = await start_fake(interaction_plan=[500])
    _bot_msg(fake, f"\u2705 14,000 XP has been given to <@!{RECIPIENT}>")
    try:
        _job, items, dist_count = await _run(
            pool, fake, rows=[{"raw_username": RECIPIENT, "point": 14000}], success_pattern=_SUCCESS)
        assert items[0]["status"] == "success" and dist_count == 1
        assert len(fake.interactions) == 1
    finally:
        await fake.close()


async def test_dead_gateway_token_never_turns_items_into_unknown(pool):
    # Gateway của token "dead" không kết nối được ⇒ lệnh CHƯA gửi ⇒ item được trả hàng đợi và gửi bằng token khác
    # (trước đây thành `unknown`/`failed` oan).
    fake = await start_fake()
    try:
        job_row, items, dist_count = await _run(
            pool, fake, rows=_ROWS4, tokens=("dead", "good"), dead_gateway=("dead",))
        assert job_row["status"] == "completed"
        assert [i["status"] for i in items] == ["success"] * 4 and dist_count == 4
        assert set(fake.interaction_tokens) == {"good"}
        async with pool.acquire() as conn:
            dead = await conn.fetchrow("SELECT status FROM reward_discord_tokens WHERE label='t0'")
        assert dead["status"] == "valid"  # lỗi mạng/Gateway không được ghi `invalid` vào DB
    finally:
        await fake.close()


async def test_403_disables_token_for_this_run_only_not_in_db(pool):
    fake = await start_fake(interaction_plan_by_token={"bad": 403})
    try:
        job_row, items, _dist = await _run(pool, fake, rows=_ROWS4, tokens=("bad", "good"))
        assert job_row["status"] == "completed" and [i["status"] for i in items] == ["success"] * 4
        async with pool.acquire() as conn:
            bad = await conn.fetchrow("SELECT status FROM reward_discord_tokens WHERE label='t0'")
        assert bad["status"] == "valid"  # 403 có thể do kênh/quyền, không chứng minh token hỏng
    finally:
        await fake.close()


async def test_unknown_streak_across_tokens_pauses_job_at_twice_threshold(pool):
    # 3 token đều cho 5xx: mỗi token riêng lẻ chưa chắc chạm ngưỡng nhưng chuỗi unknown TOÀN JOB phải dừng job
    # sớm (2×ngưỡng = 4), không đốt hết hàng đợi.
    fake = await start_fake(interaction_plan=[500])
    rows = [{"raw_username": f"68509694014429597{i}", "point": 1} for i in range(10)]
    try:
        job_row, items, _dist = await _run(
            pool, fake, rows=rows, tokens=("a", "b", "c"), unknown_pause_threshold=2)
        statuses = [i["status"] for i in items]
        assert job_row["status"] == "paused"
        assert statuses.count("unknown") == 4 and statuses.count("pending") == 6
    finally:
        await fake.close()
