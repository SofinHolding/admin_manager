"""Pipeline đồng bộ: cập nhật metric lịch sử, removed, freshness, idempotent, replay/backfill, dead-letter, nguồn chết,
mất event, rebuild, reconcile, projection account.

Nguồn dữ liệu = FakeIntegration/FakeIdentityFeed tuân thủ hợp đồng feed (chỉ phát snapshot đã commit)."""

import httpx
import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B, post, res
from analytics_service.config import Settings
from analytics_service.feed_consumer.consumer import FeedConsumer
from analytics_service.readmodels import repository as repo
from analytics_service.readmodels.apply import apply_canonical
from manager_sdk import FeedClient

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}
ADMIN_H = {"sub": ADMIN, "role": "admin"}


async def summary(w, **kw):
    return (await w.get("/v1/analytics/summary", **{**RANGE, **kw})).json()


async def snapshot_of_answers(w) -> dict:
    """Mọi con số hiển thị (không gồm freshness) để so sánh trước/sau."""
    out = {}
    for ep in ("summary", "traffic", "top-channels", "views-by-country", "channel-breakdown", "country-tags"):
        body = (await w.get("/v1/analytics/" + ep, **RANGE)).json()
        body.pop("freshness")
        out[ep] = body
    return out


# ── Cập nhật lịch sử / loại bỏ ──────────────────────────────────────────────────

async def test_updating_old_post_metric_changes_its_published_day(seeded):
    before = (await seeded.get("/v1/analytics/traffic", **RANGE)).json()
    assert before["days"][0]["views"] == 250
    # p1 đăng 10-01; hôm nay (10-05) số view cộng dồn của nó tăng 100 -> 400
    seeded.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                                   external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
    await seeded.sync()
    after = (await seeded.get("/v1/analytics/traffic", **RANGE)).json()
    assert [d["views"] for d in after["days"]] == [550, 230, 80]        # CHỈ ngày đăng cũ (10-01) đổi
    assert after["totals"]["views"] == 860
    assert (await summary(seeded))["total_views"] == 860
    top = (await seeded.get("/v1/analytics/top-channels", **RANGE)).json()["items"]
    assert top[0]["id"] == "pg1" and top[0]["views"] == 650


async def test_older_revision_never_overwrites_newer_row(seeded):
    """Bản canonical cũ đến muộn (revision nhỏ hơn) không làm số liệu lùi."""
    seeded.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                                   external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
    await seeded.sync()                                              # p1 đang ở revision 4
    stale = {"owner_account_id": OWN_A, "platform": "facebook", "resource_external_id": "pg1", "external_id": "p1",
             "published_date": "2026-10-01", "metrics": {"views": 1}, "revision": 2, "removed": False}
    async with seeded.db.transaction() as conn:
        await apply_canonical(conn, {"content": [stale]})
    assert (await summary(seeded))["total_views"] == 860              # bản revision 2 bị bỏ qua
    same = {**stale, "metrics": {"views": 400, "reactions": 10, "comments": 1}, "revision": 4}
    async with seeded.db.transaction() as conn:                       # áp lại CÙNG revision: idempotent
        await apply_canonical(conn, {"content": [same]})
    assert (await summary(seeded))["total_views"] == 860


async def test_removed_content_and_resources_disappear_from_totals(seeded):
    seeded.integration.remove("content", external_id="p3")
    await seeded.sync()
    assert (await summary(seeded))["total_views"] == 360        # mất 200 của p3
    seeded.integration.remove("resources", platform="facebook", external_id="pg3")
    await seeded.sync()
    s = await summary(seeded)
    assert s["total_views"] == 290 and s["managed_channels"] == 4          # p5 (70) của pg3 biến mất theo kênh
    top_ids = [i["id"] for i in (await seeded.get("/v1/analytics/top-channels", **RANGE)).json()["items"]]
    assert "pg3" not in top_ids
    seeded.integration.remove("resources", platform="youtube", external_id="ch2")
    await seeded.sync()
    assert (await summary(seeded))["total_views"] == 220       # số liệu theo ngày của ch2 (70) biến mất theo kênh
    # lịch sử còn trong read model (removed=true), chỉ loại khỏi số liệu
    rows = await seeded.fetch("SELECT removed FROM read_content WHERE external_id = 'p3'")
    assert rows[0]["removed"] is True


async def test_deleted_post_state_is_excluded(seeded):
    seeded.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg2",
                                   external_id="p4", state="deleted")
    await seeded.sync()
    assert (await summary(seeded))["total_views"] == 530


async def test_staged_building_or_failed_snapshots_never_appear(seeded):
    seeded.integration.stage(owner=OWN_A, content=[post("px", "pg1", "2026-10-02", 123456)])
    head = seeded.integration.head
    await seeded.sync()
    assert (await summary(seeded))["total_views"] == 560
    assert (await seeded.fetch("SELECT 1 FROM read_content WHERE external_id = 'px'")) == []
    assert (await seeded.fetch("SELECT seq FROM feed_cursor WHERE source = 'canonical.content'"))[0]["seq"] == head


# ── Freshness ───────────────────────────────────────────────────────────────────

async def test_freshness_before_and_after_commit(seeded):
    seeded.integration.commit(owner=OWN_A, platform="facebook", observed_at="2026-10-05T09:58:00Z",
                              committed_at="2026-10-05T09:59:00Z", snapshot_id="snp_latest",
                              content=[post("pn", "pg1", "2026-10-03", 5, 0, 0)])
    await seeded.sync()
    f = (await seeded.get("/v1/analytics/summary", **RANGE)).json()["freshness"]
    assert f["snapshot_id"] == "snp_latest" and f["committed_at"] == "2026-10-05T09:59:00Z"
    assert f["as_of"] == "2026-10-05T09:58:00Z" and f["revision"] == seeded.integration.head
    assert f["lag_seconds"] == 60.0 and f["stale"] is False
    seeded.clock.advance(hours=2)
    f2 = (await seeded.get("/v1/analytics/summary", **RANGE)).json()["freshness"]
    assert f2["lag_seconds"] == 7260.0 and f2["stale"] is True            # quá ANALYTICS_STALE_AFTER_S=3600
    # as_of theo phạm vi lọc: viewer B chỉ thấy snapshot của B
    fb = (await seeded.get("/v1/analytics/summary", sub=OWN_B, **RANGE)).json()["freshness"]
    assert fb["as_of"] == "2026-10-05T08:45:00Z"


async def test_freshness_is_not_null_for_empty_system_and_marks_stale(world):
    body = (await world.get("/v1/analytics/summary")).json()
    assert body["freshness"]["stale"] is True and body["freshness"]["snapshot_id"] is None
    assert body["total_views"] == 0 and body["linked"] is False


async def test_freshness_endpoint_reports_cursors_and_dead_letters_to_admin_only(seeded):
    admin = (await seeded.get("/v1/analytics/freshness", **ADMIN_H)).json()
    assert admin["cursors"]["canonical.content"] == seeded.integration.head
    assert admin["dead_letters"] == {"open": 0, "total": 0} and admin["sources"]["integration"]["ok"] is True
    viewer = (await seeded.get("/v1/analytics/freshness")).json()
    assert "cursors" not in viewer and "freshness" in viewer


# ── Idempotent / replay / backfill ──────────────────────────────────────────────

async def test_consumer_is_idempotent_and_noop_does_not_touch_seal(seeded):
    answers = await snapshot_of_answers(seeded)
    seal = await seeded.seal()
    counts = {t: (await seeded.fetch(f"SELECT count(*) AS n FROM {t}"))[0]["n"]
              for t in ("read_content", "read_resources", "read_daily_metrics", "read_events", "processed_events")}
    for _ in range(3):
        r = await seeded.sync()
        assert r["integration"]["ok"] and r["integration"]["events"] == 0
    assert await seeded.seal() == seal
    assert await snapshot_of_answers(seeded) == answers
    for t, n in counts.items():
        assert (await seeded.fetch(f"SELECT count(*) AS n FROM {t}"))[0]["n"] == n


async def test_replaying_the_whole_feed_does_not_double_count(seeded):
    answers = await snapshot_of_answers(seeded)
    processed = (await seeded.fetch("SELECT count(*) AS n FROM processed_events"))[0]["n"]
    seal = await seeded.seal()
    async with seeded.db.transaction() as conn:
        await repo.set_cursor(conn, "integration", 0)           # tua lại toàn bộ feed
    r = await seeded.sync()
    assert r["integration"]["events"] == 0 and r["integration"]["duplicates"] == processed
    assert await snapshot_of_answers(seeded) == answers and await seeded.seal() == seal


async def test_duplicate_events_in_one_page_are_applied_once(world):
    world.integration.duplicate_next = True
    world.integration.commit(owner=OWN_A, platform="facebook", resources=[res("pg1", "P", platform="facebook",
                                                                              managed=True, active=True)],
                             content=[post("p1", "pg1", "2026-10-01", 10, 1, 1)])
    r = await world.sync()
    assert r["integration"]["ok"]
    assert (await world.fetch("SELECT count(*) AS n FROM processed_events"))[0]["n"] == 1
    assert (await summary(world))["total_views"] == 10


async def test_backfill_from_zero_gives_same_result_as_incremental(seeded):
    seeded.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                                   external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
    seeded.integration.remove("content", external_id="p4")
    await seeded.sync()
    incremental = await snapshot_of_answers(seeded)
    # instance MỚI hoàn toàn (DB rỗng) đồng bộ từ revision 0
    async with seeded.db.transaction() as conn:
        await conn.execute("TRUNCATE feed_cursor, processed_events, read_resources, read_content, "
                           "read_daily_metrics, read_events")
    assert (await summary(seeded))["total_views"] == 0
    await seeded.sync()
    assert await snapshot_of_answers(seeded) == incremental


async def test_rebuild_endpoint_resets_revision_and_rebuilds_atomically(seeded):
    answers = await snapshot_of_answers(seeded)
    # thêm "rác" mà nguồn không còn (hàng mồ côi) -> rebuild phải dọn
    async with seeded.db.transaction() as conn:
        await conn.execute("INSERT INTO read_content (owner_account_id, platform, resource_external_id, external_id,"
                           " published_date, views, revision) VALUES ($1,'facebook','pg1','orphan','2026-10-02',777,1)",
                           OWN_A)
    assert (await summary(seeded))["total_views"] == 560 + 777
    r = await seeded.client.post("/v1/analytics/admin/rebuild", headers=seeded.as_user(ADMIN, "admin"))
    assert r.status_code == 200 and r.json()["head_revision"] == seeded.integration.head
    assert await snapshot_of_answers(seeded) == answers


async def test_rebuild_with_dead_source_changes_nothing(seeded):
    answers = await snapshot_of_answers(seeded)
    seeded.integration.down = True
    r = await seeded.client.post("/v1/analytics/admin/rebuild", headers=seeded.as_user(ADMIN, "admin"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
    assert await snapshot_of_answers(seeded) == answers


async def test_lost_events_are_recovered_by_revision_sync(world):
    world.integration.commit(owner=OWN_A, platform="facebook", emit=False,           # KHÔNG phát event
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("p1", "pg1", "2026-10-01", 42, 1, 1)])
    assert world.integration.events == []
    await world.sync()
    s = await summary(world)
    assert s["total_views"] == 42 and s["freshness"]["revision"] == 1


async def test_all_kinds_are_applied_atomically_to_one_head(world):
    """Không bao giờ thấy trạng thái nửa snapshot: mọi kind cùng đạt head sau một lần áp dụng."""
    world.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("p1", "pg1", "2026-10-01", 10)], events=[])
    world.integration.commit(owner=OWN_A, platform="facebook", content=[post("p2", "pg1", "2026-10-02", 20)])
    await world.sync()
    cur = {r["source"]: r["seq"] for r in await world.fetch("SELECT source, seq FROM feed_cursor")}
    assert {cur[f"canonical.{k}"] for k in ("resources", "content", "metrics", "events")} == {2}


async def test_large_revision_group_is_not_split_across_pages(world):
    for i in range(7):
        world.integration.commit(owner=OWN_A, platform="facebook", emit=False,
                                 content=[post(f"p{i}", "pg1", "2026-10-01", 1) for _ in range(1)])
    world.integration.commit(owner=OWN_A, platform="facebook",
                             content=[post(f"big{i}", "pg1", "2026-10-02", 1) for i in range(25)])
    world.app.state.container.consumer.settings = Settings(**{**world.app.state.container.settings.__dict__,
                                                              "canonical_page_size": 5})
    await world.sync()
    assert (await world.fetch("SELECT count(*) AS n FROM read_content"))[0]["n"] == 32
    assert world.integration.page_overflow >= 1


# ── Nguồn chết / lỗi ────────────────────────────────────────────────────────────

async def test_dead_source_keeps_cursor_serves_stale_data_and_recovers(seeded):
    # snapshot MỚI (lag 10s) để chứng minh `stale` ở dưới đến từ nguồn chết, không phải do tuổi dữ liệu
    seeded.integration.commit(owner=OWN_A, platform="facebook", observed_at="2026-10-05T09:59:00Z",
                              committed_at="2026-10-05T09:59:50Z", content=[post("p9", "pg1", "2026-10-03", 0)])
    await seeded.sync()
    assert (await summary(seeded))["freshness"]["stale"] is False
    answers = await snapshot_of_answers(seeded)
    cursors = [(r["source"], r["seq"]) for r in await seeded.fetch("SELECT source, seq FROM feed_cursor ORDER BY source")]
    seeded.integration.down = True
    seeded.identity_feed.down = True
    for _ in range(3):
        r = await seeded.sync()                      # KHÔNG ném, không crash
        assert r["integration"]["ok"] is False and r["identity"]["ok"] is False
    assert [(r["source"], r["seq"]) for r in
            await seeded.fetch("SELECT source, seq FROM feed_cursor ORDER BY source")] == cursors
    s = await summary(seeded)                          # vẫn đọc dữ liệu cũ, kèm stale
    assert s["total_views"] == 560 and s["freshness"]["stale"] is True
    assert (await seeded.client.get("/health/ready")).status_code == 200      # ready không phụ thuộc nguồn
    src = (await seeded.get("/v1/analytics/freshness", **ADMIN_H)).json()["sources"]["integration"]
    assert src["ok"] is False and src["last_error"]
    # nguồn sống lại + có dữ liệu mới -> đuổi kịp
    seeded.integration.down = False
    seeded.identity_feed.down = False
    seeded.integration.commit(owner=OWN_A, platform="facebook", observed_at="2026-10-05T09:59:00Z",
                              committed_at="2026-10-05T09:59:30Z", content=[post("p8", "pg1", "2026-10-03", 1000)])
    r = await seeded.sync()
    assert r["integration"]["ok"] is True
    s = await summary(seeded)
    assert s["total_views"] == 1560 and s["freshness"]["stale"] is False
    assert answers != await snapshot_of_answers(seeded)


async def test_garbage_and_error_responses_from_source_do_not_crash(db, keys, clock):
    settings = Settings(port=1, log_level="INFO", database_url="postgresql://x", redis_url="",
                        consumer_backoff_base_s=0.0, consumer_max_attempts=2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/feed"):
            return httpx.Response(200, content=b"<html>not json</html>")
        return httpx.Response(500)

    client = FeedClient("http://x.test", keys["integration"], retries=0,
                        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    consumer = FeedConsumer(db, settings, integration=client, now=clock)
    for _ in range(2):
        out = await consumer.run_once()
        assert out["integration"]["ok"] is False and out["integration"]["error"] == "source"
    wrong_shape = FeedClient("http://x.test", keys["integration"], retries=0, http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": True}))))
    out = await FeedConsumer(db, settings, integration=wrong_shape, now=clock).run_once()
    assert out["integration"]["ok"] is False


# ── Dead letter ─────────────────────────────────────────────────────────────────

async def test_poison_event_goes_to_dead_letter_after_bounded_retries_and_queue_continues(world, monkeypatch):
    import analytics_service.feed_consumer.consumer as mod
    real = mod.apply_identity_event
    calls = {"n": 0}

    async def flaky(conn, event):
        if event["data"].get("account_id") == "poison":
            calls["n"] += 1
            raise RuntimeError("không xử lý được")
        return await real(conn, event)

    monkeypatch.setattr(mod, "apply_identity_event", flaky)
    world.identity_feed.account("poison")
    world.identity_feed.account("good-1")
    await world.sync()
    assert calls["n"] == 3                                            # đúng ngưỡng ANALYTICS_CONSUMER_MAX_ATTEMPTS
    projection = await world.fetch("SELECT account_id FROM account_projection")
    assert [r["account_id"] for r in projection] == ["good-1"]        # event sau KHÔNG bị chặn
    dl = await world.fetch("SELECT id, source, attempts, error, replayed_at FROM dead_letters")
    assert len(dl) == 1 and dl[0]["attempts"] == 3 and dl[0]["replayed_at"] is None and "RuntimeError" in dl[0]["error"]
    cursor = (await world.fetch("SELECT seq FROM feed_cursor WHERE source = 'identity'"))[0]["seq"]
    assert cursor == 2
    # còn lỗi -> replay không làm gì được, DL vẫn mở
    again = await world.consumer.replay_dead_letters()
    assert again["replayed"] == 0 and again["failed"] == 1
    # hết lỗi -> replay thành công
    monkeypatch.setattr(mod, "apply_identity_event", real)
    listing = (await world.client.get("/v1/analytics/admin/dead-letters", headers=world.as_user(ADMIN, "admin"))).json()
    assert listing["open"] == 1 and listing["items"][0]["type"] == "account.registered"
    r = await world.client.post("/v1/analytics/admin/dead-letters/replay", headers=world.as_user(ADMIN, "admin"))
    assert r.json()["replayed"] == 1
    assert {r["account_id"] for r in await world.fetch("SELECT account_id FROM account_projection")} == {"poison", "good-1"}
    assert (await world.fetch("SELECT count(*) AS n FROM dead_letters WHERE replayed_at IS NULL"))[0]["n"] == 0
    assert (await world.fetch("SELECT count(*) AS n FROM processed_events WHERE source = 'identity'"))[0]["n"] == 2


async def test_malformed_event_and_bad_canonical_item_are_dead_lettered_not_blocking(world):
    world.identity_feed.emit("account.registered", {"username": "thieu-account-id"})       # thiếu account_id
    world.identity_feed.account("ok-1")
    world.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("good", "pg1", "2026-10-01", 5), post("bad-date", "pg1", "khong-phai-ngay", 9),
                                      post("bad-metric", "pg1", "2026-10-01", -3)])
    r = await world.sync()
    assert r["integration"]["rejected"] == 2 and r["identity"]["dead"] == 1
    kinds = sorted(d["source"] for d in await world.fetch("SELECT source FROM dead_letters"))
    assert kinds == ["canonical", "canonical", "identity"]
    assert (await summary(world))["total_views"] == 5            # bản ghi tốt vẫn vào
    assert [x["account_id"] for x in await world.fetch("SELECT account_id FROM account_projection")] == ["ok-1"]


async def test_integration_event_without_valid_type_is_dead_lettered_and_does_not_wedge_the_consumer(world):
    """Event Integration thiếu/sai `type` từng làm `mark_processed` ném KeyError ở bước áp dụng nguyên tử => rollback vô hạn,
    consumer kẹt mãi ở event đó. Phải: event hỏng vào dead_letters, event hợp lệ vẫn được áp, con trỏ tiến."""
    world.integration.ignore_type_filter = True
    world.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("c1", "pg1", "2026-10-01", 5)])             # seq 1: snapshot.committed hợp lệ
    bad_missing = world.integration.emit_raw(subject="x:1")                            # seq 2: thiếu `type`
    bad_type = world.integration.emit_raw(type=123, subject="x:2")                     # seq 3: type không phải chuỗi
    good = world.integration.emit("resource.changed", {"resource_id": "r", "revision": 1})   # seq 4: hợp lệ
    r = await world.sync()
    assert r["integration"]["ok"] is True and r["integration"]["dead"] == 2 and r["integration"]["events"] == 2
    dead = {d["id"] for d in await world.fetch("SELECT id FROM dead_letters WHERE source = 'integration'")}
    assert dead == {bad_missing["id"], bad_type["id"]}
    done = {d["id"] for d in await world.fetch("SELECT id FROM processed_events WHERE source = 'integration'")}
    assert good["id"] in done and bad_missing["id"] not in done
    assert (await world.fetch("SELECT seq FROM feed_cursor WHERE source = 'integration'"))[0]["seq"] == 4
    assert (await summary(world))["total_views"] == 5                                  # dữ liệu hợp lệ vẫn được áp
    again = await world.sync()                                                         # vòng sau: không kẹt, không lặp
    assert again["integration"]["ok"] is True and again["integration"]["dead"] == 0 and again["integration"]["events"] == 0


async def test_events_without_integer_seq_are_dead_lettered_not_silently_dropped(world):
    world.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("c1", "pg1", "2026-10-01", 5)])
    world.identity_feed.account("ok-1")
    world.integration.raw_inject = [{"seq": "7", "id": "evt_strseq", "type": "snapshot.committed", "data": {}},
                                    {"seq": None, "id": "evt_noseq", "type": "resource.changed", "data": {}}, "khong-phai-object"]
    world.identity_feed.raw_inject = [{"seq": 2.5, "id": "evt_floatseq", "type": "account.registered", "data": {}}]
    r = await world.sync()
    assert r["integration"]["ok"] and r["identity"]["ok"]
    dead = await world.fetch("SELECT id, source, seq, error FROM dead_letters ORDER BY source, id")
    assert len(dead) == 4 and all(d["id"].startswith("bad:") for d in dead)          # id khác nhau: không đè nhau
    assert {d["source"] for d in dead} == {"integration", "identity"} and all("seq" in d["error"] for d in dead)
    assert (await summary(world))["total_views"] == 5                                 # event/dữ liệu hợp lệ vẫn áp
    assert [x["account_id"] for x in await world.fetch("SELECT account_id FROM account_projection")] == ["ok-1"]


async def test_replaying_an_old_identity_event_never_overwrites_a_newer_value(world, monkeypatch):
    """Dead-letter của event CŨ được replay sau khi event MỚI hơn cùng account đã áp: không đảo ngược cột đã mới hơn;
    cột khác của cùng event vẫn áp (không bỏ cả event)."""
    import analytics_service.feed_consumer.consumer as mod
    real = mod.apply_identity_event
    poison: set[str] = set()

    async def flaky(conn, event, **kw):
        if event["id"] in poison:
            raise RuntimeError("lỗi tạm thời")
        return await real(conn, event, **kw)

    monkeypatch.setattr(mod, "apply_identity_event", flaky)
    acc = "c" * 32
    first = world.identity_feed.account(acc, "account.registered", username="ten-moi", role="viewer", status="active")
    poison.add(first["id"])
    world.identity_feed.account(acc, "account.role_changed", role="admin")                 # seq 2: áp
    stale_status = world.identity_feed.account(acc, "account.status_changed", status="suspended")
    poison.add(stale_status["id"])
    world.identity_feed.account(acc, "account.status_changed", status="active")            # seq 4: áp, MỚI hơn seq 3
    await world.sync()
    row = (await world.fetch("SELECT username, role, status FROM account_projection WHERE account_id = $1", acc))[0]
    assert (row["role"], row["status"], row["username"]) == ("admin", "active", "")          # registered/suspended chưa áp
    monkeypatch.setattr(mod, "apply_identity_event", real)
    out = await world.consumer.replay_dead_letters()
    assert out["replayed"] == 2 and out["failed"] == 0
    row = (await world.fetch("SELECT username, role, status FROM account_projection WHERE account_id = $1", acc))[0]
    assert row["status"] == "active"                       # `suspended` (seq 3) cũ hơn `active` (seq 4): KHÔNG áp lại
    assert row["role"] == "admin"                          # `registered` (role=viewer) cũ hơn role_changed: không đảo
    assert row["username"] == "ten-moi"                    # nhưng cột chưa ai ghi (username) vẫn được điền


async def test_database_outage_does_not_dead_letter_or_lose_events(world, monkeypatch):
    world.identity_feed.account("acc-1")
    import asyncpg
    import analytics_service.feed_consumer.consumer as mod
    real = mod.apply_identity_event

    async def db_down(conn, event):
        raise asyncpg.PostgresConnectionError("db down")

    monkeypatch.setattr(mod, "apply_identity_event", db_down)
    r = await world.sync()
    assert r["identity"]["ok"] is False and r["identity"]["error"] == "database"
    assert (await world.fetch("SELECT count(*) AS n FROM dead_letters"))[0]["n"] == 0     # sự cố hạ tầng ≠ event độc
    monkeypatch.setattr(mod, "apply_identity_event", real)
    await world.sync()
    assert [x["account_id"] for x in await world.fetch("SELECT account_id FROM account_projection")] == ["acc-1"]


# ── Projection account ──────────────────────────────────────────────────────────

async def test_account_projection_feeds_person_comparison(seeded):
    f = seeded.identity_feed
    f.account(OWN_A, username="alice")
    f.account(OWN_B, username="bob")
    await seeded.sync()
    own = (await seeded.get("/v1/analytics/compare/owner", **ADMIN_H, **RANGE)).json()["items"]
    assert [(i["name"], i["total"]) for i in own] == [("bob", 999), ("alice", 560)]
    f.account(OWN_B, "account.status_changed", status="suspended", previous="active")
    await seeded.sync()
    own = (await seeded.get("/v1/analytics/compare/owner", **ADMIN_H, **RANGE)).json()["items"]
    assert {i["name"] for i in own} == {"bob", "alice"}            # suspended: VẪN giữ dữ liệu
    f.account(OWN_B, "account.deleted")
    await seeded.sync()
    own = (await seeded.get("/v1/analytics/compare/owner", **ADMIN_H, **RANGE)).json()["items"]
    assert [i["name"] for i in own] == ["alice"]                     # deleted: ẩn khỏi so sánh người
    all_ = (await seeded.get("/v1/analytics/summary", **ADMIN_H, **RANGE)).json()
    assert all_["total_views"] == 560 + 999                          # nhưng tổng hệ thống không đổi
    f.account(OWN_A, "account.role_changed", role="dash_admin", previous="viewer")
    f.account(OWN_A, "account.profile_updated", username="alice2")
    await seeded.sync()
    row = (await seeded.fetch("SELECT username, role FROM account_projection WHERE account_id = $1", OWN_A))[0]
    assert (row["username"], row["role"]) == ("alice2", "dash_admin")


# ── Reconciliation ──────────────────────────────────────────────────────────────

async def reconcile(w):
    r = await w.client.post("/v1/analytics/admin/reconcile", headers=w.as_user(ADMIN, "admin"))
    assert r.status_code == 200, r.text
    return r.json()


async def test_reconcile_matches_when_in_sync(seeded):
    rep = await reconcile(seeded)
    assert rep["ok"] is True and rep["differences"] == [] and rep["behind"] is False
    assert rep["head_revision"] == {"source": seeded.integration.head, "local": seeded.integration.head}
    assert all(k["count_match"] and k["checksum_match"] for k in rep["kinds"].values())
    assert rep["kinds"]["content"]["local"]["counts"] == {"total": 8, "active": 8}


@pytest.mark.parametrize("tamper,kind,what", [
    ("DELETE FROM read_content WHERE external_id = 'p3'", "content", "count"),
    ("UPDATE read_content SET views = 999999 WHERE external_id = 'p3'", "content", "checksum"),     # lệch giá trị
    ("UPDATE read_resources SET removed = true WHERE external_id = 'pg2'", "resources", "count"),
    ("UPDATE read_daily_metrics SET revision = revision + 5 WHERE resource_external_id = 'ch1'", "metrics", "checksum"),
    ("DELETE FROM read_events WHERE external_id = 'e1'", "events", "count"),
])
async def test_reconcile_detects_deliberate_drift(seeded, tamper, kind, what):
    async with seeded.db.transaction() as conn:
        await conn.execute(tamper)
    rep = await reconcile(seeded)
    assert rep["ok"] is False
    assert (kind, what) in {(d["kind"], d["type"]) for d in rep["differences"]}
    assert rep["kinds"][kind]["checksum_match"] is False
    # kinds không bị đụng vẫn khớp
    assert all(v["checksum_match"] for k, v in rep["kinds"].items() if k != kind)
    # rebuild chữa được
    assert (await seeded.client.post("/v1/analytics/admin/rebuild", headers=seeded.as_user(ADMIN, "admin"))).status_code == 200
    assert (await reconcile(seeded))["ok"] is True


async def test_reconcile_reports_when_behind_and_requires_admin(seeded):
    seeded.integration.commit(owner=OWN_A, platform="facebook", emit=False, content=[post("late", "pg1", "2026-10-02", 1)])
    rep = await reconcile(seeded)
    assert rep["ok"] is False and rep["behind"] is True
    assert (await seeded.client.post("/v1/analytics/admin/reconcile", headers=seeded.as_user(OWN_A))).status_code == 403


async def test_daily_metric_resend_overwrites_not_adds(seeded):
    seeded.integration.update_item("metrics", owner_account_id=OWN_A, platform="youtube", resource_external_id="ch1",
                                   date="2026-10-01", metrics={"views": 90})
    await seeded.sync()
    assert (await summary(seeded))["total_views"] == 560 + 50            # 40 -> 90 (ghi đè, không cộng dồn thành 130)
