"""Consumer feed Integration -> kết quả provisioning: con trỏ bền, idempotent, dead-letter, nguồn chết, replay."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import replace

import pytest
from http_fixtures import FeedSource
from oidc_util import make_account

from identity_service.feed_consumer.consumer import SOURCE, IntegrationFeedConsumer, SourceError

pytestmark = pytest.mark.needs_pg

KEY = "feed-key-integration-0123456789abc"
OK, FAIL = "integration.client.provisioned", "integration.client.provision_failed"


@pytest.fixture
def source():
    s = FeedSource(KEY)
    yield s
    s.stop()


@pytest.fixture
def sleeps_log() -> list[float]:
    return []


@pytest.fixture
def make_consumer(db, settings, clock, sleeps_log):
    def make(src_url: str, **overrides) -> IntegrationFeedConsumer:
        cfg = replace(settings, integration_feed_url=src_url, integration_feed_key=KEY, feed_backoff_base_s=1.0,
                      feed_max_attempts=3, **overrides)

        async def fake_sleep(seconds: float) -> None:
            sleeps_log.append(seconds)
            await asyncio.sleep(0)

        return IntegrationFeedConsumer(db, cfg, now=clock, sleep=fake_sleep, rand=lambda: 1.0)
    return make


async def provision(db, username="nguoi_dung", *, client_id: str | None = None) -> tuple[str, str]:
    aid = await make_account(db, username)
    cid = client_id or "cli_" + uuid.uuid4().hex[:24]
    async with db.connection() as conn:
        await conn.execute("INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind) "
                           "VALUES ($1,$2,'T','primary_tool')", cid, aid)
    return aid, cid


def event(seq, etype, cid, aid, **data) -> dict:
    return {"seq": seq, "id": f"evt_{uuid.uuid4().hex}", "type": etype, "subject": f"cli:{cid}",
            "time": "2026-10-05T10:00:00Z", "data_version": 1,
            "data": {"client_id": cid, "account_id": aid, **data}}


async def status_of(db, cid) -> tuple[str, str]:
    async with db.connection() as conn:
        r = await conn.fetchrow("SELECT status, error_code FROM integration_provisioning WHERE client_id=$1", cid)
    return r["status"], r["error_code"]


async def cursor(db) -> int:
    async with db.connection() as conn:
        return await conn.fetchval("SELECT seq FROM feed_cursor WHERE source=$1", SOURCE) or 0


# ── áp dụng event ───────────────────────────────────────────────────────────────

async def test_provisioned_and_failed_events_update_status(db, source, make_consumer):
    a1, c1 = await provision(db, "u1")
    a2, c2 = await provision(db, "u2")
    source.events = [event(1, OK, c1, a1, status="provisioned"),
                     event(2, FAIL, c2, a2, error_code="PLATFORM_NOT_ALLOWED")]
    stats = await make_consumer(source.url).poll_once()
    assert stats["applied"] == 2 and stats["dead"] == 0
    assert await status_of(db, c1) == ("provisioned", "")
    assert await status_of(db, c2) == ("failed", "PLATFORM_NOT_ALLOWED")
    assert await cursor(db) == 2
    assert source.calls[0]["key"] == KEY and source.calls[0]["query"]["types"] == f"{OK},{FAIL}"


async def test_error_code_from_source_is_sanitized(db, source, make_consumer):
    a, c = await provision(db)
    source.events = [event(1, FAIL, c, a, error_code="<script>alert(1)</script>")]
    await make_consumer(source.url).poll_once()
    assert await status_of(db, c) == ("failed", "PROVISION_FAILED")


async def test_status_is_visible_through_accounts_me_integration(client, db, mailer, source, make_consumer):
    from conftest import token_for
    t = await token_for(client, db, mailer, username="co_tool")
    async with db.connection() as conn:
        cid = "cli_" + uuid.uuid4().hex[:24]
        await conn.execute("INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind) "
                           "VALUES ($1,$2,'T','primary_tool')", cid, t["id"])
    source.events = [event(1, OK, cid, t["id"])]
    await make_consumer(source.url).poll_once()
    r = await client.get("/v1/accounts/me/integration", headers=t["auth"])
    assert r.json() == {"client_id": cid, "status": "provisioned"}


# ── con trỏ bền, idempotent, replay ────────────────────────────────────────────

async def test_cursor_is_durable_and_next_poll_resumes_from_it(db, source, make_consumer):
    a, c = await provision(db)
    source.events = [event(5, OK, c, a)]
    await make_consumer(source.url).poll_once()
    source.calls.clear()
    stats = await make_consumer(source.url).poll_once()          # consumer MỚI (mô phỏng restart) đọc con trỏ từ DB
    assert stats["applied"] == 0 and source.calls[0]["query"]["after"] == "5"
    assert await cursor(db) == 5


async def test_replayed_events_are_idempotent_by_event_id(db, source, make_consumer):
    a, c = await provision(db)
    ok = event(1, OK, c, a)
    source.events = [ok]
    consumer = make_consumer(source.url)
    await consumer.poll_once()
    async with db.connection() as conn:
        await conn.execute("UPDATE integration_provisioning SET status='pending'")       # đổi tay để thấy tác dụng
        await conn.execute("UPDATE feed_cursor SET seq=0")                                 # backfill từ đầu
    stats = await consumer.poll_once()
    assert stats["duplicate"] == 1 and stats["applied"] == 0
    assert await status_of(db, c) == ("pending", "")                                      # event cũ KHÔNG áp dụng lại
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM processed_events WHERE id=$1", ok["id"]) == 1


async def test_two_consumers_racing_apply_each_event_once(db, source, make_consumer):
    pairs = [await provision(db, f"u{i}") for i in range(6)]
    source.events = [event(i + 1, OK, c, a) for i, (a, c) in enumerate(pairs)]
    results = await asyncio.gather(make_consumer(source.url).poll_once(), make_consumer(source.url).poll_once())
    assert sum(r["applied"] for r in results) == 6 and sum(r["duplicate"] for r in results) == 6
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM processed_events") == 6


async def test_pagination_pulls_every_page(db, source, make_consumer):
    async with db.connection() as conn:
        await make_account(db, "nhieu")
        aid = await conn.fetchval("SELECT id FROM accounts")
        cids = ["cli_" + uuid.uuid4().hex[:24] for _ in range(230)]
        await conn.executemany("INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind) "
                               "VALUES ($1,$2,'T','primary_tool')", [(c, aid) for c in cids])
    source.events = [event(i + 1, OK, c, aid) for i, c in enumerate(cids)]
    stats = await make_consumer(source.url).poll_once()
    assert stats["applied"] == 230 and stats["pages"] == 3 and await cursor(db) == 230


async def test_events_of_other_types_are_not_requested(db, source, make_consumer):
    a, c = await provision(db)
    source.events = [event(1, "credential.revoked", c, a), event(2, OK, c, a)]
    stats = await make_consumer(source.url).poll_once()
    assert stats["applied"] == 1


# ── lỗi xử lý: retry/backoff, dead-letter, không chặn event sau ───────────────

async def test_bad_event_goes_to_dead_letter_after_bounded_retries_and_does_not_block_the_rest(
        db, source, make_consumer, sleeps_log):
    a, c = await provision(db)
    bad = event(1, OK, "cli_khong_co_trong_he_thong", a)
    good = event(2, OK, c, a)
    source.events = [bad, good]
    stats = await make_consumer(source.url).poll_once()
    assert stats["dead"] == 1 and stats["applied"] == 1
    assert await status_of(db, c) == ("provisioned", "")                 # event sau vẫn được xử lý
    assert await cursor(db) == 2                                          # con trỏ đi qua event hỏng
    assert sleeps_log == [1.0, 2.0]                                       # 3 lần thử => 2 lần chờ, tăng theo cấp số nhân
    async with db.connection() as conn:
        dl = await conn.fetchrow("SELECT * FROM dead_letters")
        assert (dl["id"], dl["type"], dl["attempts"], dl["replayed_at"]) == (bad["id"], OK, 3, None)
        assert json.loads(dl["payload"])["data"]["client_id"] == "cli_khong_co_trong_he_thong"
        assert await conn.fetchval("SELECT count(*) FROM processed_events WHERE id=$1", bad["id"]) == 0


async def test_malformed_and_mismatched_events_are_dead_lettered(db, source, make_consumer):
    a, c = await provision(db)
    other, _ = await provision(db, "nguoi_khac")
    mismatch = event(1, OK, c, other)                                    # client_id không thuộc account này
    no_data = {"seq": 2, "id": "evt_" + uuid.uuid4().hex, "type": OK, "data": "x"}
    no_ids = {"seq": 3, "id": "evt_" + uuid.uuid4().hex, "type": FAIL, "data": {}}
    unknown = event(4, "integration.client.khac", c, a)
    source.events = [mismatch, no_data, no_ids, unknown]
    consumer = make_consumer(source.url)
    stats = await consumer.poll_once()
    assert stats["dead"] == 3                                            # loại event lạ bị lọc ngay ở nguồn (types=)
    assert await status_of(db, c) == ("pending", "")                     # không event hỏng nào đổi trạng thái


async def test_replay_dead_letters_after_the_cause_is_fixed(db, source, make_consumer):
    aid = await make_account(db, "nguoi_dung")
    cid = "cli_" + uuid.uuid4().hex[:24]
    source.events = [event(1, OK, cid, aid)]
    consumer = make_consumer(source.url)
    assert (await consumer.poll_once())["dead"] == 1
    assert (await consumer.replay_dead_letters())["failed"] == 1          # nguyên nhân chưa sửa: vẫn hỏng
    async with db.connection() as conn:
        await conn.execute("INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind) "
                           "VALUES ($1,$2,'T','primary_tool')", cid, aid)
    assert await consumer.replay_dead_letters() == {"replayed": 1, "failed": 0}
    assert await status_of(db, cid) == ("provisioned", "")
    assert await consumer.replay_dead_letters() == {"replayed": 0, "failed": 0}   # đã replay: không lặp lại
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT replayed_at IS NOT NULL FROM dead_letters") is True


# ── nguồn chết ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["fail", "garbage"])
async def test_source_errors_keep_cursor_and_recover(db, source, make_consumer, mode):
    a, c = await provision(db)
    source.events = [event(1, OK, c, a)]
    consumer = make_consumer(source.url)
    source.mode = mode
    with pytest.raises(SourceError):
        await consumer.poll_once()
    assert await cursor(db) == 0 and await status_of(db, c) == ("pending", "")
    source.mode = "ok"
    assert (await consumer.poll_once())["applied"] == 1


async def test_dead_source_url_raises_source_error_not_crash(db, make_consumer):
    from http_fixtures import dead_port_url
    consumer = make_consumer(dead_port_url().rsplit("/", 1)[0])
    with pytest.raises(SourceError):
        await consumer.poll_once()


async def test_wrong_service_key_is_a_source_error_and_key_is_not_logged(db, source, settings, clock, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = replace(settings, integration_feed_url=source.url, integration_feed_key="sai-khoa-0123456789abcdefgh",
                  feed_poll_interval_s=0.05)
    consumer = IntegrationFeedConsumer(db, cfg, now=clock)
    with pytest.raises(SourceError):
        await consumer.poll_once()
    assert "sai-khoa-0123456789abcdefgh" not in " ".join(r.getMessage() for r in caplog.records
                                                         if r.name.startswith("identity"))


async def test_run_forever_survives_dead_source_and_resumes(db, source, make_consumer, sleeps_log):
    a, c = await provision(db)
    source.events = [event(1, OK, c, a)]
    source.mode = "fail"
    consumer = make_consumer(source.url, feed_poll_interval_s=0.05)
    task = asyncio.create_task(consumer.run_forever())
    for _ in range(100):
        await asyncio.sleep(0.02)
        if len(sleeps_log) >= 3:
            break
    assert not task.done() and len(sleeps_log) >= 3                       # nguồn chết: tự thử lại, KHÔNG crash
    assert await status_of(db, c) == ("pending", "")
    source.mode = "ok"
    for _ in range(100):
        await asyncio.sleep(0.02)
        if (await status_of(db, c))[0] == "provisioned":
            break
    assert await status_of(db, c) == ("provisioned", "")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── bật/tắt theo cấu hình, chạy trong lifespan ────────────────────────────────

async def test_consumer_is_disabled_without_url_or_key(db, settings, clock):
    for url, key in (("", ""), ("http://127.0.0.1:9", ""), ("", KEY)):
        c = IntegrationFeedConsumer(db, replace(settings, integration_feed_url=url, integration_feed_key=key), now=clock)
        assert c.enabled is False
        await asyncio.wait_for(c.run_forever(), 1)                          # trả về ngay, không treo


async def test_lifespan_starts_consumer_task_only_when_configured(db, settings, mailer, clock, source):
    from identity_service.app import create_app

    off = create_app(settings, database=db, mailer=mailer, now=clock)
    async with off.router.lifespan_context(off):
        assert "integration-feed" not in {t.get_name() for t in asyncio.all_tasks()}
    on = create_app(replace(settings, integration_feed_url=source.url, integration_feed_key=KEY,
                            backchannel_enabled=True), database=db, mailer=mailer, now=clock)
    async with on.router.lifespan_context(on):
        names = {t.get_name() for t in asyncio.all_tasks()}
        assert "integration-feed" in names and "oidc-backchannel" in names
    await asyncio.sleep(0.05)
    assert "integration-feed" not in {t.get_name() for t in asyncio.all_tasks()}      # đã huỷ gọn khi tắt
    disabled = create_app(replace(settings, backchannel_enabled=False), database=db, mailer=mailer, now=clock)
    async with disabled.router.lifespan_context(disabled):
        assert "oidc-backchannel" not in {t.get_name() for t in asyncio.all_tasks()}


async def test_cli_replay_runs_against_the_database(db, source, make_consumer):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from conftest import TEST_FERNET_KEY, _dsn

    aid = await make_account(db, "nguoi_dung")
    cid = "cli_" + uuid.uuid4().hex[:24]
    source.events = [event(1, OK, cid, aid)]
    await make_consumer(source.url).poll_once()                            # -> dead letter
    async with db.connection() as conn:
        await conn.execute("INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind) "
                           "VALUES ($1,$2,'T','primary_tool')", cid, aid)
    svc = Path(__file__).resolve().parents[1]
    root = svc.parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "IDENTITY_DATABASE_URL": _dsn(),
           "IDENTITY_KEY_ENCRYPTION_KEY": TEST_FERNET_KEY, "IDENTITY_ENV": "test",
           "PYTHONPATH": os.pathsep.join(str(p) for p in (svc / "src", root / "packages" / "observability" / "src",
                                                          root / "packages" / "contracts" / "python"))}
    r = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "identity_service.feed_consumer", "replay"],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0 and "thành công=1" in r.stdout, r.stdout + r.stderr
    assert await status_of(db, cid) == ("provisioned", "")
