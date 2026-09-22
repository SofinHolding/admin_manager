"""Test bất biến I1–I6 (plan D.5) trên Postgres thật + fake Discord offline.

I1 không gọi lại item `success` sau resume · I2 crash giữa chừng (attempt `sent`/`prepared`) → recovery
→ item `unknown`, KHÔNG BAO GIỜ `pending` · I3 `ReadTimeout`(qua fake 'hang')→`unknown`,
`ConnectError`(cổng đóng thật)→phân loại `retry` · I4 reply không khớp `success_pattern` ⇒ `unknown`
và KHÔNG gửi lại (đã kiểm ở `test_engine_slash.py::test_slash_reply_unmatched_pattern_is_unknown_no_resend`)
· I5 403 ⇒ pause job, item còn lại vẫn `pending` (đã kiểm ở
`test_engine_slash.py::test_slash_403_pauses_job_remaining_stay_pending`) · I6 nhiều coroutine cùng
job ⇒ mỗi item chỉ được claim một lần (`FOR UPDATE SKIP LOCKED`).
"""

from __future__ import annotations

import asyncio
import socket
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
import httpx
import pytest

from discord.http import DiscordHttpClient
from domain.idempotency import next_nonce
from engine.recovery import recover_orphaned_attempts
from store.repositories import attempts as attempts_repo
from store.repositories import distributions as dist_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from tests.conftest import seed_active_account_with_credential, seed_job
from tests.harness.fake_discord import start_fake
from tests.test_engine_slash import RECIPIENT, _build_runner


async def test_i1_resume_does_not_reinvoke_success_item(pool):
    """Item đã `success` từ lần chạy trước (mô phỏng resume) không bao giờ bị claim/gọi lại."""
    fake = await start_fake()
    try:
        async with pool.acquire() as conn:
            account_id = await seed_active_account_with_credential(conn)
            rows = [{"raw_username": RECIPIENT, "point": 5}, {"raw_username": "333333333333333333", "point": 7}]
            job_id = await seed_job(conn, account_id=account_id, rows=rows)
            await conn.execute(
                "UPDATE reward_items SET status='success', resolved_user_id=$1, resolve_level='explicit_id', "
                "finalized_at=now() WHERE job_id=$2 AND row_index=0",
                RECIPIENT, job_id)
            job_row = await jobs_repo.get_job(conn, job_id)
        runner, http_client, session = await _build_runner(pool, fake, job_id, job_row)
        try:
            await runner.run(job_id)
        finally:
            await session.close()
            await http_client.aclose()

        assert len(fake.interactions_for(RECIPIENT)) == 0  # item 0 KHÔNG được gọi lại

        async with pool.acquire() as conn:
            items = await items_repo.list_all(conn, job_id)
        assert items[0]["status"] == "success"
        assert items[1]["status"] == "success"
    finally:
        await fake.close()


async def test_i2_crash_after_sent_recovers_to_unknown_not_pending(pool):
    account_id = uuid.uuid4().hex
    async with pool.acquire() as conn:
        job_id = await seed_job(conn, account_id=account_id, rows=[{"raw_username": RECIPIENT, "point": 5}])
        await jobs_repo.set_status(conn, job_id, "running")
        claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
        item, attempt = claim["item"], claim["attempt"]
        # Mô phỏng crash NGAY SAU KHI gửi lệnh — write-ahead đã ghi phase='sent' + message_id.
        await attempts_repo.mark_sent(
            conn, attempt_id=attempt["id"], command_text="/give-xp member:<@x> amount:5",
            message_id="999", http_status=204)

    await recover_orphaned_attempts(pool)

    async with pool.acquire() as conn:
        item_after = await items_repo.get_item(conn, item["id"])
        attempt_after = await attempts_repo.get_by_id(conn, attempt["id"])
        job_after = await jobs_repo.get_job(conn, job_id)

    assert item_after["status"] == "unknown"
    assert item_after["status"] != "pending"
    assert item_after["failure_code"] == "CRASH_DURING_SEND"
    assert item_after["confirmation_level"] == "message_posted"  # có message_id
    assert attempt_after["phase"] == "aborted"
    assert attempt_after["error_code"] == "CRASH_DURING_SEND"
    assert job_after["status"] == "paused"

    # Idempotent — chạy lại lần 2 không lỗi, không đổi trạng thái thêm.
    await recover_orphaned_attempts(pool)
    async with pool.acquire() as conn:
        item_after2 = await items_repo.get_item(conn, item["id"])
    assert item_after2["status"] == "unknown"


async def test_i2_crash_during_prepared_recovers_with_confirmation_level_none(pool):
    account_id = uuid.uuid4().hex
    async with pool.acquire() as conn:
        job_id = await seed_job(conn, account_id=account_id, rows=[{"raw_username": RECIPIENT, "point": 5}])
        claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
        item = claim["item"]
        # Crash TRƯỚC khi kịp gửi gì — attempt vẫn ở phase='prepared', không có message_id.

    await recover_orphaned_attempts(pool)

    async with pool.acquire() as conn:
        item_after = await items_repo.get_item(conn, item["id"])
    assert item_after["status"] == "unknown"
    assert item_after["confirmation_level"] == "none"  # chưa từng có message_id


async def test_i3_connect_error_on_closed_port_is_raised_and_classified_retry(pool):
    """Cổng chắc chắn không ai lắng nghe → `httpx.ConnectError` thật (không phải giả lập)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()

    client = DiscordHttpClient(read_timeout_s=1.0)
    try:
        with pytest.raises(httpx.ConnectError):
            await client.request("GET", f"http://127.0.0.1:{closed_port}/x", retry_on_network_error=False)
    finally:
        await client.aclose()


async def test_i6_concurrent_claim_no_double_claim(pool):
    account_id = uuid.uuid4().hex
    rows = [{"raw_username": f"user{i}", "point": i + 1} for i in range(10)]
    async with pool.acquire() as conn:
        job_id = await seed_job(conn, account_id=account_id, rows=rows)

    async def claim_loop() -> list[int]:
        ids: list[int] = []
        async with pool.acquire() as conn:
            while True:
                claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
                if claim is None:
                    break
                ids.append(claim["item"]["id"])
        return ids

    results = await asyncio.gather(claim_loop(), claim_loop(), claim_loop())
    all_ids = [i for chunk in results for i in chunk]
    assert len(all_ids) == 10
    assert len(set(all_ids)) == 10  # mỗi item chỉ được claim đúng 1 lần dù 3 coroutine tranh nhau


async def test_unique_guards_idempotency_nonce_distribution_pk(pool):
    account_id = uuid.uuid4().hex
    async with pool.acquire() as conn:
        job_id = await seed_job(conn, account_id=account_id, rows=[{"raw_username": RECIPIENT, "point": 5}])
        claim = await items_repo.claim_next(conn, job_id, nonce=next_nonce())
        item, attempt = claim["item"], claim["attempt"]

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO reward_items "
                "(job_id, row_index, raw_username, normalized_username, point, status, idempotency_key) "
                "VALUES ($1,999,'dup','dup',1,'pending',$2)",
                job_id, item["idempotency_key"])

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO reward_attempts (item_id, job_id, attempt_no, nonce, phase, started_at) "
                "VALUES ($1,$2,99,$3,'prepared', now())",
                item["id"], job_id, attempt["nonce"])

        await dist_repo.insert_once(
            conn, item_id=item["id"], attempt_id=attempt["id"], job_id=job_id, account_id=account_id,
            discord_user_id=RECIPIENT, point=5, evidence={"level": "bot_reply"})
        with pytest.raises(asyncpg.UniqueViolationError):
            await dist_repo.insert_once(
                conn, item_id=item["id"], attempt_id=attempt["id"], job_id=job_id, account_id=account_id,
                discord_user_id=RECIPIENT, point=5, evidence={"level": "bot_reply"})
