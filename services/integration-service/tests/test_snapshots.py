"""Hợp đồng ingest: state machine snapshot, expected_counts, LWW theo observed_at, null≠0, rejections, removed, TTL."""

from __future__ import annotations

import pytest
from conftest import FEED_KEY_ANALYTICS, ts
from helpers import (FULL_SCOPE, canonical, commit, daily, event, ingest_full, new_snapshot, post, push, res)

pytestmark = pytest.mark.needs_pg
S = "/v1/integrations/snapshots"


async def summary(env) -> dict:
    return (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()


async def snap_row(env, sid: str) -> dict:
    return (await env.rows("SELECT * FROM snapshots WHERE id = $1", sid))[0]


def by_ext(items: list[dict]) -> dict[str, dict]:
    return {i["external_id"]: i for i in items}


# ── State machine ───────────────────────────────────────────────────────────────

async def test_state_machine_building_to_committed_and_terminal_states(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 1})
    got = (await tool.get(f"{S}/{sid}")).json()["data"]
    assert got["status"] == "building" and got["is_current"] is False and got["staged_counts"]["resources"] == 0
    assert got["expires_at"] is not None
    await push(tool, sid, "resources", [res("r1")])
    assert (await tool.get(f"{S}/{sid}")).json()["data"]["staged_counts"]["resources"] == 1
    out = await commit(tool, sid)
    assert out["status"] == "committed" and out["is_current"] is True
    final = (await tool.get(f"{S}/{sid}")).json()["data"]
    assert final["status"] == "committed" and final["is_current"] is True and final["committed_at"]
    # commit lần 2 và ghi thêm vào snapshot đã commit
    again = await tool.post(f"{S}/{sid}/commit", {})
    assert again.status_code == 409 and again.json()["error"]["code"] == "SNAPSHOT_ALREADY_COMMITTED"
    assert again.json()["error"]["details"]["snapshot_id"] == sid
    late = await tool.post(f"{S}/{sid}/resources", {"items": [res("r2")]})
    assert late.status_code == 409 and late.json()["error"]["code"] == "SNAPSHOT_ALREADY_COMMITTED"
    assert await env.scalar("SELECT count(*) FROM resources") == 1


async def test_failed_and_abandoned_snapshots_do_not_accept_data(env):
    tool = await env.tool()
    for state in ("failed", "abandoned", "committing"):
        sid = await new_snapshot(tool, type="delta")
        await env.rows("UPDATE snapshots SET status = $2 WHERE id = $1", sid, state)
        r = await tool.post(f"{S}/{sid}/resources", {"items": [res("r1")]})
        expected = "SNAPSHOT_COMMIT_IN_PROGRESS" if state == "committing" else "SNAPSHOT_NOT_WRITABLE"
        assert r.status_code == 409 and r.json()["error"]["code"] == expected, state
        c = await tool.post(f"{S}/{sid}/commit", {})
        assert c.status_code == 409 and c.json()["error"]["code"] == expected, state
    assert await env.scalar("SELECT count(*) FROM resources") == 0


async def test_create_validation(env):
    tool = await env.tool()
    base = {"platform": "facebook", "snapshot_type": "full", "observed_at": ts(), "report_timezone": "UTC",
            "expected_counts": {}}
    bad = [({**base, "report_timezone": "Mars/Olympus"}, "RECORD_VALIDATION_FAILED"),
           ({**base, "snapshot_type": "delta", "scope": {"resources": True}}, "RECORD_VALIDATION_FAILED"),
           ({**base, "observed_at": "2026-10-05T10:00:00"}, "RECORD_VALIDATION_FAILED"),     # thiếu múi giờ
           ({**base, "observed_at": 1760000000}, "RECORD_VALIDATION_FAILED"),                 # số, không phải chuỗi
           ({**base, "snapshot_type": "partial"}, "RECORD_VALIDATION_FAILED"),
           ({**base, "expected_counts": {"resources": -1}}, "RECORD_VALIDATION_FAILED"),
           ({**base, "expected_counts": {"resources": True}}, "RECORD_VALIDATION_FAILED"),
           ({**base, "scope": {"content_window": {"from": "2026-10-05", "to": "2026-10-01"}}}, "RECORD_VALIDATION_FAILED"),
           ({**base, "project_key": "itl"}, "RECORD_VALIDATION_FAILED"),
           ({k: v for k, v in base.items() if k != "expected_counts"}, "RECORD_VALIDATION_FAILED")]
    for body, code in bad:
        r = await tool.post(S, body)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, body
    assert await env.scalar("SELECT count(*) FROM snapshots") == 0


async def test_report_timezone_cannot_change_between_committed_snapshots(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1")], type="delta")           # tz mặc định của helper: Asia/Ho_Chi_Minh
    r = await tool.post(S, {"platform": "facebook", "snapshot_type": "delta", "observed_at": ts(1),
                            "report_timezone": "America/Sao_Paulo", "expected_counts": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TIMEZONE_CHANGED"
    assert (await tool.post(S, {"platform": "facebook", "snapshot_type": "delta", "observed_at": ts(1),
                               "report_timezone": "Asia/Ho_Chi_Minh", "expected_counts": {}})).status_code == 201


# ── Staging: đếm created/updated/unchanged/stale/rejected ─────────────────────────

async def test_batch_result_classification_against_snapshot_and_canonical(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1")], content=[post("p1", views=10)], obs=0)
    sid = await new_snapshot(tool, type="delta", obs=10)
    d = await push(tool, sid, "resources", [res("r1", obs=10), res("r2", obs=10)])
    assert (d["created"], d["updated"], d["unchanged"], d["stale_ignored"]) == (1, 0, 1, 0)
    d = await push(tool, sid, "content", [post("p1", obs=10, views=10), post("p2", obs=10, views=1),
                                          post("p3", obs=10, views=1), post("p3", obs=5, views=2)])
    assert (d["received"], d["created"], d["updated"], d["unchanged"], d["stale_ignored"]) == (4, 2, 0, 1, 1)
    d = await push(tool, sid, "content", [post("p1", obs=10, views=99), post("p2", obs=3, views=7)])
    assert (d["updated"], d["stale_ignored"]) == (1, 1)         # p1 đổi số; p2 cũ hơn bản đã staged
    assert (await tool.get(f"{S}/{sid}")).json()["data"]["staged_counts"] == {"resources": 2, "content": 3, "metrics": 0,
                                                                              "events": 0}


async def test_stale_observed_at_is_ignored_and_numbers_never_go_backwards(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1", obs=0)], content=[post("p1", views=500, obs=10)], obs=10)
    out = await ingest_full(tool, resources=[res("r1", obs=0)], content=[post("p1", views=100, obs=5)], obs=20,
                            type="delta")
    assert out["stats"]["content"]["stale_ignored"] == 1 and out["stats"]["content"]["updated"] == 0
    item = by_ext((await canonical(env, "content"))["items"])["p1"]
    assert item["metrics"] == {"views": 500} and item["observed_at"] == "2026-10-05T10:10:00Z"


async def test_historical_post_with_higher_views_updates_old_published_date(env):
    """T2: bài đăng cũ có thêm lượt xem => số của NGÀY ĐĂNG CŨ đổi theo (chặn lỗi 'chỉ chèn bản ghi mới')."""
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1")], content=[post("old", date="2026-09-10", views=100)], obs=0)
    first_rev = by_ext((await canonical(env, "content"))["items"])["old"]["revision"]
    out = await ingest_full(tool, resources=[res("r1", obs=60)], content=[post("old", date="2026-09-10", views=900, obs=60)],
                            obs=60, type="delta")
    assert out["stats"]["content"]["updated"] == 1
    item = by_ext((await canonical(env, "content"))["items"])["old"]
    assert item["published_date"] == "2026-09-10" and item["metrics"] == {"views": 900} and item["revision"] > first_rev


async def test_null_is_not_zero_in_every_direction(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1")], content=[post("a", views=0, reactions=None), post("b", views=None)],
                      obs=0)
    items = by_ext((await canonical(env, "content"))["items"])
    assert items["a"]["metrics"] == {"views": 0, "reactions": None} and items["b"]["metrics"] == {"views": None}
    assert items["a"]["metrics"]["views"] is not None and items["b"]["metrics"]["views"] is None
    # đổi 0 -> null và null -> 0 là THAY ĐỔI THẬT (không bị coi là unchanged)
    out = await ingest_full(tool, resources=[res("r1", obs=5)],
                            content=[post("a", views=None, reactions=None, obs=5), post("b", views=0, obs=5)],
                            obs=5, type="delta")
    assert out["stats"]["content"]["updated"] == 2 and out["stats"]["content"]["unchanged"] == 0
    items = by_ext((await canonical(env, "content"))["items"])
    assert items["a"]["metrics"] == {"views": None, "reactions": None} and items["b"]["metrics"] == {"views": 0}
    mets = await ingest_full(tool, metrics=[daily("r1", "2026-10-04", views=None)], obs=6, type="delta")
    assert mets["totals"]["metrics"] == 1
    assert (await canonical(env, "metrics"))["items"][0]["metrics"] == {"views": None}


async def test_unknown_metric_and_unknown_resource_are_rejected_with_codes(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    await push(tool, sid, "resources", [res("r1")])
    d = await push(tool, sid, "content", [
        post("ok", res_ext="r1"), {**post("m", res_ext="r1"), "metrics": {"views": 1, "coin_la": 3}},
        post("orphan", res_ext="khong-co"), {**post("ext", res_ext="r1"), "extensions": {"facebook": {"coin_la": 3}}}])
    assert d["created"] == 2 and d["rejected"] == 2
    codes = {x["external_id"]: x["code"] for x in d["rejections"]}
    assert codes == {"m": "METRIC_UNKNOWN", "orphan": "RESOURCE_UNKNOWN"}
    m = await push(tool, sid, "metrics", [daily("ghost", "2026-10-04", views=1), daily("r1", "2026-10-04", views=1)])
    assert m["rejections"][0]["code"] == "RESOURCE_UNKNOWN" and m["created"] == 1
    e = await push(tool, sid, "events", [event("e1", res_ext="ghost"), event("e2")])
    assert e["rejections"][0]["code"] == "RESOURCE_UNKNOWN" and e["created"] == 1


async def test_record_validation_variants(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    await push(tool, sid, "resources", [res("r1")])
    ok = post("ok")
    bad = [{**ok, "external_id": "b1", "published_date": "2026-02-30"}, {**ok, "external_id": "b2", "published_date": "05/10/2026"},
           {**ok, "external_id": "b3", "metrics": {"views": -1}}, {**ok, "external_id": "b4", "metrics": {"views": 1.5}},
           {**ok, "external_id": "b5", "metrics": {"views": "10"}}, {**ok, "external_id": "b6", "metrics": {"views": True}},
           {**ok, "external_id": "b7", "metrics": {"Views": 1}}, {**ok, "external_id": "b8", "observed_at": "bad"},
           {**ok, "external_id": "b9", "unknown_field": 1}, {**ok, "external_id": "b10", "state": "zombie"},
           {**ok, "external_id": "", "published_date": "2026-10-04"}, {k: v for k, v in ok.items() if k != "metrics"}]
    d = await push(tool, sid, "content", [ok, *bad])
    assert d["created"] == 1 and d["rejected"] == len(bad)
    assert all(x["code"] == "RECORD_VALIDATION_FAILED" for x in d["rejections"])
    assert "10" not in "".join(x["message"] for x in d["rejections"] if x["external_id"] == "b5")  # không phản chiếu giá trị
    assert {"views": 7.0} and (await push(tool, sid, "content", [{**ok, "external_id": "f", "metrics": {"views": 7.0}}]))["created"] == 1


# ── expected_counts / rejections / accept_partial ─────────────────────────────────

async def test_expected_count_mismatch_blocks_commit_and_changes_nothing(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 3, "content": 1}, scope=FULL_SCOPE)
    await push(tool, sid, "resources", [res("r1"), res("r2")])
    await push(tool, sid, "content", [post("p1")])
    r = await tool.post(f"{S}/{sid}/commit", {})
    assert r.status_code == 422 and r.json()["error"]["code"] == "SNAPSHOT_INCOMPLETE"
    assert r.json()["error"]["details"]["mismatch"] == {"resources": {"expected": 3, "received": 2}}
    for table in ("resources", "content_items", "projects"):
        assert await env.scalar(f"SELECT count(*) FROM {table}") == 0
    assert await env.scalar("SELECT count(*) FROM outbox WHERE type = 'snapshot.committed'") == 0
    assert (await snap_row(env, sid))["status"] == "building"              # mở lại cho Tool gửi bù
    await push(tool, sid, "resources", [res("r3")])
    assert (await commit(tool, sid))["totals"] == {"resources": 3, "content": 1, "metrics": 0, "events": 0}


async def test_too_many_records_is_also_a_mismatch(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 1})
    await push(tool, sid, "resources", [res("r1"), res("r2")])
    r = await tool.post(f"{S}/{sid}/commit", {})
    assert r.status_code == 422 and r.json()["error"]["details"]["mismatch"]["resources"] == {"expected": 1, "received": 2}


async def test_partial_rejection_semantics(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 1, "content": 3}, type="delta")
    await push(tool, sid, "resources", [res("r1")])
    d = await push(tool, sid, "content", [post("good1"), {**post("bad"), "metrics": {"views": -5}}, post("good2")])
    assert d["rejected"] == 1
    # accept_partial=false (mặc định): còn rejection thì KHÔNG commit
    r = await tool.post(f"{S}/{sid}/commit", {})
    assert r.status_code == 422 and r.json()["error"]["code"] == "SNAPSHOT_INCOMPLETE"
    assert r.json()["error"]["details"]["rejected_total"] == 1
    assert await env.scalar("SELECT count(*) FROM content_items") == 0
    st = (await tool.get(f"{S}/{sid}")).json()["data"]
    assert st["rejected_total"] == 1 and st["rejections"][0]["external_id"] == "bad" and st["status"] == "building"
    # accept_partial=true: commit phần hợp lệ, phần bị từ chối KHÔNG vào canonical
    out = await commit(tool, sid, accept_partial=True)
    assert out["rejected_total"] == 1 and out["totals"]["content"] == 2
    assert set(by_ext((await canonical(env, "content"))["items"])) == {"good1", "good2"}


async def test_resending_a_corrected_record_clears_its_rejection(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 1, "content": 1}, type="delta")
    await push(tool, sid, "resources", [res("r1")])
    assert (await push(tool, sid, "content", [{**post("p1"), "metrics": {"views": "x"}}]))["rejected"] == 1
    assert (await tool.get(f"{S}/{sid}")).json()["data"]["rejected_total"] == 1
    assert (await push(tool, sid, "content", [post("p1", views=5)]))["created"] == 1
    assert (await tool.get(f"{S}/{sid}")).json()["data"]["rejected_total"] == 0
    assert (await commit(tool, sid))["totals"]["content"] == 1


async def test_accept_partial_counts_rejected_toward_expected_total(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, expected={"resources": 1, "content": 5}, type="delta")
    await push(tool, sid, "resources", [res("r1")])
    await push(tool, sid, "content", [post("a"), post("b"), {**post("c"), "metrics": {"views": -1}}])
    r = await tool.post(f"{S}/{sid}/commit", {"accept_partial": True})      # 2 hợp lệ + 1 bị từ chối = 3 != 5
    assert r.status_code == 422 and r.json()["error"]["details"]["mismatch"]["content"] == {"expected": 5, "received": 3}


async def test_commit_body_validation(env):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    for body in ({"accept_partial": "yes"}, {"accept_partial": 1}, {"bogus": 1}, {"owner_account_id": "x"}):
        r = await tool.post(f"{S}/{sid}/commit", body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED", body
    assert (await tool.post(f"{S}/{sid}/commit", content=b"")).status_code == 200      # body trống được phép


# ── Suy luận removed / superseded ────────────────────────────────────────────────

async def test_full_snapshot_marks_absent_records_removed_inside_scope_only(env):
    tool = await env.tool()
    await ingest_full(
        tool, resources=[res("r1"), res("r2")],
        content=[post("keep"), post("gone"), post("outside", date="2025-01-01")],
        metrics=[daily("r1", "2026-10-01"), daily("r1", "2026-10-02")], events=[event("e1"), event("e2")], obs=0)
    before = await summary(env)
    assert before["counts"]["content"] == {"total": 3, "active": 3}
    out = await ingest_full(tool, resources=[res("r1", obs=10)], content=[post("keep", obs=10)],
                            metrics=[daily("r1", "2026-10-01", obs=10)], events=[event("e1")], obs=10)
    assert out["removed"] == {"resources": 1, "content": 1, "metrics": 1, "events": 1}
    items = by_ext((await canonical(env, "content"))["items"])
    assert items["gone"]["removed"] is True and items["keep"]["removed"] is False
    assert items["outside"]["removed"] is False                  # ngoài content_window: không bị suy luận
    assert by_ext((await canonical(env, "resources"))["items"])["r2"]["removed"] is True
    after = await summary(env)
    assert after["counts"]["content"] == {"total": 3, "active": 2} and after["counts"]["resources"] == {"total": 2, "active": 1}
    assert after["head_revision"] > before["head_revision"]
    ev = (await env.rows("SELECT data FROM outbox WHERE type = 'snapshot.committed' ORDER BY seq"))[-1]["data"]
    assert ev["removed"] == {"resources": 1, "content": 1, "metrics": 1, "events": 1}


async def test_removed_record_comes_back_when_resent(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1"), res("r2")], obs=0)
    await ingest_full(tool, resources=[res("r1", obs=5)], obs=5)
    assert by_ext((await canonical(env, "resources"))["items"])["r2"]["removed"] is True
    await ingest_full(tool, resources=[res("r1", obs=9), res("r2", obs=9)], obs=9)
    r2 = by_ext((await canonical(env, "resources"))["items"])["r2"]
    assert r2["removed"] is False and r2["observed_at"] == "2026-10-05T10:09:00Z"


async def test_delta_snapshot_and_scopeless_full_never_infer_removed(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1"), res("r2")], content=[post("p1"), post("p2")], obs=0)
    out = await ingest_full(tool, resources=[res("r1", obs=5)], content=[post("p1", obs=5)], obs=5, type="delta")
    assert out["removed"] == {"resources": 0, "content": 0, "metrics": 0, "events": 0}
    out = await ingest_full(tool, resources=[res("r1", obs=6)], obs=6, scope=None)       # full nhưng KHÔNG khai scope
    assert out["removed"]["resources"] == 0
    only_res = await ingest_full(tool, resources=[res("r1", obs=7)], obs=7, scope={"resources": True})
    assert only_res["removed"] == {"resources": 1, "content": 0, "metrics": 0, "events": 0}   # scope chỉ gồm resources


async def test_removal_never_touches_records_owned_by_another_client_or_owner(env):
    a = await env.tool()
    a2 = await env.tool(user={"sub": a.owner, "headers": env.user(sub=a.owner)["headers"]})   # cùng owner, client khác
    b = await env.tool()
    await ingest_full(a2, resources=[res("shared"), res("only_a2")], obs=0)
    await ingest_full(b, resources=[res("only_b")], obs=0)
    await ingest_full(a, resources=[res("only_a")], obs=5)                 # full scope resources của client a
    removed = {i["external_id"] for i in (await canonical(env, "resources"))["items"] if i["removed"]}
    assert removed == set()                                              # a không xoá được bản ghi của a2 hay b


async def test_full_snapshot_never_removes_records_observed_after_it(env):
    """R5 chỉ suy luận `removed` cho bản ghi có observed_at <= observed_at của snapshot full: bản ghi MỚI HƠN (do snapshot
    delta/batch ghi trong lúc full đang building) là bằng chứng Tool còn nhìn thấy nó, không được xoá."""
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1"), res("old")], content=[post("p1"), post("p_old")],
                      metrics=[daily("r1", "2026-10-01")], events=[event("e_old")], obs=0)
    full = await new_snapshot(tool, obs=10, scope=FULL_SCOPE,
                              expected={"resources": 1, "content": 1, "metrics": 1})
    # trong lúc F(T1=+10) đang building: ghi X và nội dung mới ở T2=+30 (delta commit + batch trực tiếp)
    await ingest_full(tool, type="delta", obs=30, resources=[res("X", obs=30)],
                      content=[post("pX", res_ext="X", obs=30)], metrics=[daily("X", "2026-10-02", obs=30)])
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "Y", "observed_at": ts(31)}]})
    assert r.status_code == 200, r.text
    await push(tool, full, "resources", [res("r1", obs=10)])
    await push(tool, full, "content", [post("p1", obs=10)])
    await push(tool, full, "metrics", [daily("r1", "2026-10-01", obs=10)])
    out = await commit(tool, full)
    assert out["removed"] == {"resources": 1, "content": 1, "metrics": 0, "events": 1}     # chỉ old / p_old / e_old
    res_items = by_ext((await canonical(env, "resources"))["items"])
    assert res_items["X"]["removed"] is False and res_items["Y"]["removed"] is False
    assert res_items["old"]["removed"] is True and res_items["r1"]["removed"] is False
    content = by_ext((await canonical(env, "content"))["items"])
    assert content["pX"]["removed"] is False and content["p_old"]["removed"] is True
    metrics = (await canonical(env, "metrics"))["items"]
    assert [m["removed"] for m in metrics if m["resource_external_id"] == "X"] == [False]


async def test_older_full_snapshot_is_superseded_and_cannot_remove_or_override_newer_data(env):
    tool = await env.tool()
    older = await new_snapshot(tool, obs=0, scope={"resources": True}, expected={"resources": 1})
    await push(tool, older, "resources", [res("r1", obs=0, display_name="CU")])
    await ingest_full(tool, resources=[res("r1", obs=30, display_name="MOI"), res("r2", obs=30)], obs=30,
                      scope={"resources": True})
    out = await commit(tool, older)                    # snapshot cũ hơn commit SAU
    assert out["removed"]["resources"] == 0            # r2 KHÔNG bị xoá nhầm
    assert [w["code"] for w in out["warnings"]] == ["SNAPSHOT_SUPERSEDED"] and out["warnings"][0]["kinds"] == ["resources"]
    items = by_ext((await canonical(env, "resources"))["items"])
    assert items["r1"]["display_name"] == "MOI" and items["r2"]["removed"] is False
    assert out["is_current"] is False                  # con trỏ current không lùi về snapshot cũ
    cur = await env.rows("SELECT id FROM snapshots WHERE is_current")
    assert len(cur) == 1 and cur[0]["id"] != older


# ── Tham chiếu resource của content/metrics/events ───────────────────────────────────

async def test_children_cannot_attach_to_a_removed_resource_until_it_is_resent(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1"), res("r2")], obs=0)
    await ingest_full(tool, resources=[res("r1", obs=5)], obs=5)                  # r2 bị removed
    assert by_ext((await canonical(env, "resources"))["items"])["r2"]["removed"] is True
    sid = await new_snapshot(tool, type="delta", obs=6, scope=None)
    out = await push(tool, sid, "content", [post("pz", res_ext="r2", obs=6), post("pa", res_ext="r1", obs=6)])
    assert out["rejected"] == 1 and out["rejections"][0]["code"] == "RESOURCE_UNKNOWN"
    assert out["rejections"][0]["external_id"] == "pz"
    await commit(tool, sid, accept_partial=True)
    assert {i["external_id"] for i in (await canonical(env, "content"))["items"]} == {"pa"}   # pz KHÔNG gắn vào r2 đã removed
    # gửi lại resource (hồi sinh) CÙNG snapshot với nội dung => hợp lệ và gắn đúng
    out = await ingest_full(tool, type="delta", obs=9, resources=[res("r2", obs=9)], content=[post("pz", res_ext="r2", obs=9)])
    assert by_ext((await canonical(env, "resources"))["items"])["r2"]["removed"] is False
    c = by_ext((await canonical(env, "content"))["items"])
    assert c["pz"]["resource_external_id"] == "r2" and c["pz"]["removed"] is False


async def test_children_pick_the_live_resource_and_reject_ambiguous_resource_types(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("dup", rtype="page"), res("dup", rtype="profile")], obs=0, scope=None)
    sid = await new_snapshot(tool, type="delta", obs=1, scope=None)
    out = await push(tool, sid, "content", [post("c1", res_ext="dup", obs=1)])
    # hai resource_type cùng external_id đều còn sống và payload con không có resource_type => KHÔNG đoán
    assert out["rejected"] == 1 and out["rejections"][0]["code"] == "RESOURCE_UNKNOWN"
    # gỡ bản `page` (full snapshot chỉ còn profile) => bản sống duy nhất là `profile`, dù `page` mới cập nhật hơn
    await ingest_full(tool, resources=[res("dup", rtype="profile", obs=5)], obs=5)
    rows = {(i["resource_type"], i["removed"]) for i in (await canonical(env, "resources"))["items"]}
    assert rows == {("page", True), ("profile", False)}
    out = await ingest_full(tool, type="delta", obs=6, content=[post("c2", res_ext="dup", obs=6)])
    c = (await canonical(env, "content"))["items"]
    assert [(i["external_id"], i["resource_type"]) for i in c] == [("c2", "profile")]


# ── current pointer / outbox / revision ──────────────────────────────────────────

async def test_current_pointer_moves_only_on_successful_commit(env):
    tool = await env.tool()
    first = (await ingest_full(tool, resources=[res("r1")], obs=0))["_sid"]
    assert (await snap_row(env, first))["is_current"] is True
    pending = await new_snapshot(tool, obs=10, expected={"resources": 9})
    await push(tool, pending, "resources", [res("r1", obs=10)])
    await tool.post(f"{S}/{pending}/commit", {})                              # 422: không commit được
    assert (await snap_row(env, first))["is_current"] is True and (await snap_row(env, pending))["is_current"] is False
    second = (await ingest_full(tool, resources=[res("r1", obs=20)], obs=20))["_sid"]
    assert (await snap_row(env, first))["is_current"] is False and (await snap_row(env, second))["is_current"] is True
    # current theo (owner, client, platform, snapshot_type): delta không đụng full
    delta = (await ingest_full(tool, resources=[res("r1", obs=30)], obs=30, type="delta"))["_sid"]
    assert (await snap_row(env, second))["is_current"] is True and (await snap_row(env, delta))["is_current"] is True


async def test_snapshot_committed_event_and_revisions_are_consistent(env):
    tool = await env.tool()
    out = await ingest_full(tool, resources=[res("r1"), res("r2")], content=[post("p1")], obs=0)
    ev = (await env.rows("SELECT * FROM outbox WHERE type = 'snapshot.committed'"))[0]
    d = ev["data"]
    assert ev["id"].startswith("evt_") and ev["data_version"] == 1 and ev["subject"] == f"snap:{out['_sid']}"
    assert d["snapshot_id"] == out["_sid"] and d["owner_account_id"] == tool.owner and d["client_id"] == tool.client_id
    assert d["platform"] == "facebook" and d["snapshot_type"] == "full" and d["report_timezone"] == "Asia/Ho_Chi_Minh"
    assert d["counts"] == {"resources": 2, "content": 1, "metrics": 0, "events": 0}
    assert (d["revision_from"], d["revision_to"]) == (out["revision_from"], out["revision_to"]) == (1, 3)
    revs = []
    for kind in ("resources", "content"):
        revs += [i["revision"] for i in (await canonical(env, kind))["items"]]
    assert sorted(revs) == [1, 2, 3]
    assert "payload" not in str(d) and "metrics" not in d          # event không mang payload thô


async def test_staging_payload_is_deleted_after_commit_and_abandon(env):
    tool = await env.tool()
    out = await ingest_full(tool, resources=[res("r1")], content=[post("p1")])
    assert await env.scalar("SELECT count(*) FROM snapshot_items WHERE snapshot_id = $1", out["_sid"]) == 0


# ── Mất kết nối / TTL ────────────────────────────────────────────────────────────

async def test_tool_crash_before_commit_keeps_previous_current_data(env, clock):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1", display_name="V1")], content=[post("p1", views=100)], obs=0)
    base = await summary(env)
    crashed = await new_snapshot(tool, obs=10, scope=FULL_SCOPE, expected={"resources": 1, "content": 1})
    await push(tool, crashed, "resources", [res("r1", obs=10, display_name="V2-LOI")])
    await push(tool, crashed, "content", [post("p1", obs=10, views=999999)])
    # ...Tool chết ở đây, không bao giờ commit...
    assert await summary(env) == base                                         # canonical không đổi một bit
    assert by_ext((await canonical(env, "content"))["items"])["p1"]["metrics"] == {"views": 100}
    clock.advance(hours=7)
    from integration_service import maintenance
    swept = await maintenance.run_once(await _container(env))
    assert crashed in swept["abandoned"]
    assert (await snap_row(env, crashed))["status"] == "abandoned"
    assert await env.scalar("SELECT count(*) FROM snapshot_items WHERE snapshot_id = $1", crashed) == 0
    assert await summary(env) == base
    cur = await env.rows("SELECT id FROM snapshots WHERE is_current")
    assert len(cur) == 1 and cur[0]["id"] != crashed


async def _container(env):
    return env.http._transport.app.state.container


async def test_building_snapshot_is_abandoned_lazily_after_ttl_and_activity_extends_it(env, clock):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    clock.advance(hours=5)
    await push(tool, sid, "resources", [res("r1")])                           # còn hoạt động => gia hạn
    clock.advance(hours=5)
    await push(tool, sid, "resources", [res("r2")])                           # 10h tổng nhưng < 6h kể từ lần cuối
    clock.advance(hours=6, minutes=1)
    r = await tool.post(f"{S}/{sid}/resources", {"items": [res("r3")]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SNAPSHOT_NOT_WRITABLE"
    assert (await tool.get(f"{S}/{sid}")).json()["data"]["status"] == "abandoned"
    assert (await tool.post(f"{S}/{sid}/commit", {})).status_code == 409
    assert await env.scalar("SELECT count(*) FROM resources") == 0


async def test_sweeper_fails_snapshots_stuck_in_committing(env, clock):
    tool = await env.tool()
    sid = await new_snapshot(tool, type="delta")
    await env.rows("UPDATE snapshots SET status = 'committing', updated_at = $2 WHERE id = $1", sid, clock())
    from integration_service import maintenance
    assert (await maintenance.run_once(await _container(env)))["failed"] == []
    clock.advance(minutes=11)
    assert (await maintenance.run_once(await _container(env)))["failed"] == [sid]
    assert (await snap_row(env, sid))["status"] == "failed"


async def test_invalid_snapshot_id_formats_are_plain_404(env):
    tool = await env.tool()
    for sid in ("abc", "snp_zzz", "snp_" + "0" * 32, "../etc/passwd", "snp_" + "0" * 31 + "%00"):
        for r in (await tool.get(f"{S}/{sid}"), await tool.post(f"{S}/{sid}/commit", {})):
            assert r.status_code in (404, 405)
            assert r.status_code == 405 or r.json()["error"]["code"] in ("SNAPSHOT_NOT_FOUND", "NOT_FOUND")
    for r in (await tool.get(f"{S}/snp_{'0' * 32}"), await tool.get(f"{S}/abc")):
        assert r.json()["error"]["code"] == "SNAPSHOT_NOT_FOUND"


async def test_events_are_immutable_first_write_wins(env):
    tool = await env.tool()
    await ingest_full(tool, resources=[res("r1")], events=[event("e1", kind="post_ok")], obs=0)
    out = await ingest_full(tool, resources=[res("r1", obs=1)], events=[event("e1", kind="post_ok")], obs=1, type="delta")
    assert out["stats"]["events"]["unchanged"] == 1
    out = await ingest_full(tool, resources=[res("r1", obs=2)], events=[event("e1", kind="post_fail")], obs=2, type="delta")
    assert out["stats"]["events"]["stale_ignored"] == 1
    assert (await canonical(env, "events"))["items"][0]["kind"] == "post_ok"


async def test_two_platforms_need_two_snapshots_and_stay_separate(env):
    tool = await env.tool(platforms=["facebook", "youtube"])
    fb = await new_snapshot(tool, platform="facebook", type="delta")
    yt = await new_snapshot(tool, platform="youtube", type="delta")
    await push(tool, fb, "resources", [res("same")])
    await push(tool, yt, "resources", [res("same", rtype="channel")])
    await commit(tool, fb)
    await commit(tool, yt)
    items = (await canonical(env, "resources"))["items"]
    assert {(i["platform"], i["resource_type"]) for i in items} == {("facebook", "page"), ("youtube", "channel")}
    # content của youtube không thể trỏ vào resource của facebook
    sid = await new_snapshot(tool, platform="youtube", type="delta")
    d = await push(tool, sid, "content", [post("p", res_ext="nonexistent")])
    assert d["rejections"][0]["code"] == "RESOURCE_UNKNOWN"
