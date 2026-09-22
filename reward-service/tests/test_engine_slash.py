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

from discord.gateway_session import GatewaySession
from discord.http import DiscordHttpClient
from discord.slash_commands import fetch_guild_command
from engine import confirmation as confirmation_engine
from engine import executor_slash
from engine.job_runner import JobRunner, JobRunnerConfig
from store.repositories import distributions as dist_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from tests.conftest import seed_active_account_with_credential, seed_job
from tests.harness.fake_discord import start_fake

RECIPIENT = "685096940144295977"


async def _build_runner(pool, fake, job_id, job_row, *, http_timeout_s=2.0, confirm_timeout_s=1.0,
                         confirm_poll_interval_s=0.05):
    http_client = DiscordHttpClient(read_timeout_s=http_timeout_s)
    session = GatewaySession(gateway_url=fake.gateway_url, token="utok")
    command = await fetch_guild_command(
        http_client, api_base=fake.api_base, token="utok",
        guild_id=job_row["guild_id"], command_name=job_row["command_name"])
    assert command is not None

    executor_config = executor_slash.ExecutorConfig(
        api_base=fake.api_base, guild_id=job_row["guild_id"], channel_id=job_row["channel_id"],
        user_token="utok", read_authorization="utok", account_id=job_row["account_id"])
    confirmation_config = confirmation_engine.ConfirmationConfig(
        api_base=fake.api_base, channel_id=job_row["channel_id"], read_authorization="utok",
        confirm_mode=job_row["confirm_mode"], confirm_timeout_s=confirm_timeout_s,
        confirm_poll_interval_s=confirm_poll_interval_s,
        success_pattern=re.compile(job_row["success_pattern"]) if job_row["success_pattern"] else None,
        failure_pattern=re.compile(job_row["failure_pattern"]) if job_row["failure_pattern"] else None,
        leveling_bot_id=job_row["leveling_bot_id"])
    runner_config = JobRunnerConfig(
        channel_id=job_row["channel_id"], account_id=job_row["account_id"],
        delay_ms=job_row["delay_ms"], jitter_ms=job_row["jitter_ms"],
        max_item_retries=job_row["max_item_retries"], unknown_pause_threshold=job_row["unknown_pause_threshold"],
        confirm_mode=job_row["confirm_mode"], success_pattern=confirmation_config.success_pattern)
    runner = JobRunner(
        pool=pool, config=runner_config, http_client=http_client, session=session,
        command=command, executor_config=executor_config, confirmation_config=confirmation_config)
    return runner, http_client, session


async def _run(pool, fake, *, rows, account_id=None, **job_kwargs):
    async with pool.acquire() as conn:
        account_id = account_id or await seed_active_account_with_credential(conn)
        job_id = await seed_job(conn, account_id=account_id, rows=rows, **job_kwargs)
        job_row = await jobs_repo.get_job(conn, job_id)
    runner, http_client, session = await _build_runner(pool, fake, job_id, job_row)
    try:
        await runner.run(job_id)
    finally:
        await session.close()
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
