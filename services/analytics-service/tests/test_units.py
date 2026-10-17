"""Test thuần (không cần hạ tầng): ánh xạ canonical, trục ngày, FilterContext, checksum, cache key."""

from datetime import date

import pytest
from manager_contracts import ApiError
from manager_sdk import Principal

from analytics_service.cache import build_key
from analytics_service.dates import days_between, previous_period
from analytics_service.filters import FilterQuery, build_filter_context
from analytics_service.readmodels.canonical import (KINDS, BadItem, checksum_line, checksum_lines, natural_key,
                                                    to_row)

A, B = "a" * 32, "b" * 32
TODAY = date(2026, 10, 5)


def principal(sub=A, role="viewer") -> Principal:
    return Principal(sub=sub, username="u", role=role)


def query(**kw) -> FilterQuery:
    return FilterQuery(**{"date_from": "", "date_to": "", "owner_account_id": "", "platform": "", "country": "",
                          "group_id": "", "platform_tag": "", **kw})


# ── FilterContext ───────────────────────────────────────────────────────────────

def test_viewer_is_forced_to_self_whatever_the_query_says():
    for asked in ("", B, "x", A):
        ctx = build_filter_context(query(owner_account_id=asked), principal(A, "viewer"), today=TODAY)
        assert ctx.owner_account_id == A and ctx.scope == f"own:{A}"
    for role in ("discord", "unknown"):
        assert build_filter_context(query(owner_account_id=B), principal(A, role), today=TODAY).owner_account_id == A


def test_admin_defaults_to_all_owners_or_the_chosen_one():
    for role in ("admin", "dash_admin"):
        assert build_filter_context(query(), principal(A, role), today=TODAY).owner_account_id is None
        assert build_filter_context(query(), principal(A, role), today=TODAY).scope == "all"
        assert build_filter_context(query(owner_account_id=B), principal(A, role), today=TODAY).owner_account_id == B


def test_default_dates_and_validation():
    ctx = build_filter_context(query(), principal(), today=TODAY)
    assert (ctx.date_from, ctx.date_to) == ("2026-09-06", "2026-10-05")
    only_to = build_filter_context(query(date_to="2026-03-31"), principal(), today=TODAY)
    assert only_to.date_from == "2026-03-02"
    for bad in ({"date_from": "2026-13-01"}, {"date_to": "hôm qua"}, {"date_from": "2026/10/01"}):
        with pytest.raises(ApiError) as exc:
            build_filter_context(query(**bad), principal(), today=TODAY)
        assert exc.value.status_code == 422


def test_cache_hash_excludes_owner_but_includes_every_other_filter():
    base = build_filter_context(query(), principal(A), today=TODAY)
    other_owner = build_filter_context(query(), principal(B), today=TODAY)
    assert base.cache_hash() == other_owner.cache_hash() and base.scope != other_owner.scope      # owner ở scope
    for field in ("platform", "country", "group_id", "platform_tag", "date_from", "date_to"):
        changed = build_filter_context(query(**{field: "2026-01-01" if field.startswith("date") else "zzz"}),
                                       principal(A), today=TODAY)
        assert changed.cache_hash() != base.cache_hash(), field
    assert base.cache_hash({"limit": 5}) != base.cache_hash({"limit": 6})


def test_cache_key_contains_seal_scope_endpoint_and_hash():
    key = build_key(7, "own:" + A, "summary", "abc")
    assert key == f"analytics:v1:7:own:{A}:summary:abc"
    assert build_key(8, "own:" + A, "summary", "abc") != key and build_key(7, "all", "summary", "abc") != key


# ── Trục ngày ───────────────────────────────────────────────────────────────────

def test_days_between_matches_legacy_rules():
    assert days_between("2026-10-01", "2026-10-03") == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert days_between("2026-10-03", "2026-10-01") == []                 # đảo -> rỗng, KHÔNG tự đảo
    assert days_between("2025-01-01", "2026-10-01") == []                 # > 400 ngày -> rỗng
    assert len(days_between("2025-08-27", "2026-10-01")) == 401
    assert days_between("x", "y") == [] and days_between("2026-10-01", "2026-10-01") == ["2026-10-01"]


def test_previous_period_has_same_length_and_is_adjacent():
    assert previous_period("2026-10-01", "2026-10-03") == ("2026-09-28", "2026-09-30")
    assert days_between(*previous_period("2026-10-03", "2026-10-01")) == []


# ── Canonical -> hàng read model ────────────────────────────────────────────────

def test_content_null_is_preserved_and_zero_is_zero():
    row = to_row("content", {"owner_account_id": A, "platform": "fb", "resource_external_id": "r", "external_id": "p",
                             "published_date": "2026-10-01", "metrics": {"views": None, "reactions": 0},
                             "revision": 3, "observed_at": "2026-10-05T08:00:00Z"})
    assert row["views"] is None and row["reactions"] == 0 and row["comments"] is None
    assert row["revision"] == 3 and row["removed"] is False and row["observed_at"].tzinfo is not None


@pytest.mark.parametrize("kind,item", [
    ("content", {"owner_account_id": A, "platform": "fb", "resource_external_id": "r", "external_id": "p",
                 "published_date": "2026-13-45", "metrics": {}, "revision": 1}),
    ("content", {"owner_account_id": A, "platform": "fb", "resource_external_id": "r", "external_id": "p",
                 "published_date": "2026-10-01", "metrics": {"views": -1}, "revision": 1}),
    ("content", {"owner_account_id": A, "platform": "fb", "resource_external_id": "r", "external_id": "p",
                 "published_date": "2026-10-01", "metrics": {"views": 1.5}, "revision": 1}),
    ("content", {"owner_account_id": A, "platform": "fb", "resource_external_id": "r", "external_id": "p",
                 "published_date": "2026-10-01", "metrics": {"views": True}, "revision": 1}),
    ("resources", {"owner_account_id": A, "platform": "fb", "revision": 1}),
    ("resources", {"owner_account_id": "", "platform": "fb", "external_id": "x", "revision": 1}),
    ("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x"}),
    ("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x\u0000y", "revision": 1}),
    ("events", {"owner_account_id": A, "external_id": "e", "kind": "post_ok", "occurred_date": "nope", "revision": 1}),
    ("metrics", {"owner_account_id": A, "platform": "yt", "resource_external_id": "c", "revision": 1}),
    ("khong-co", {"owner_account_id": A, "revision": 1}),
])
def test_invalid_canonical_items_are_rejected(kind, item):
    with pytest.raises(BadItem):
        to_row(kind, item)


def test_resource_flags_and_country_forms():
    row = to_row("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x", "revision": 1,
                               "country": {"code": "BR", "name": "Brazil"}, "managed": True, "active": "yes"})
    assert row["country"] == "Brazil" and row["managed"] is True and row["active"] is None
    assert to_row("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x", "revision": 1,
                                "country": "peru"})["country"] == "peru"
    assert to_row("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x", "revision": 1,
                                "country_code": "CL"})["country"] == "CL"
    gone = to_row("resources", {"owner_account_id": A, "platform": "fb", "external_id": "x", "revision": 2,
                                "removed": True, "state": "disabled"})
    assert gone["removed"] is True and gone["state"] == "disabled"


def test_checksum_is_order_independent_and_sensitive():
    rows = [to_row("events", {"owner_account_id": A, "external_id": f"e{i}", "kind": "post_ok",
                              "occurred_date": "2026-10-01", "revision": i}) for i in range(1, 5)]
    lines = [checksum_line("events", r) for r in rows]
    assert checksum_lines(lines) == checksum_lines(list(reversed(lines)))
    assert checksum_lines(lines) != checksum_lines(lines[:-1])
    bumped = [checksum_line("events", {**rows[0], "revision": 99})] + lines[1:]
    flagged = [checksum_line("events", {**rows[0], "removed": True})] + lines[1:]
    assert len({checksum_lines(lines), checksum_lines(bumped), checksum_lines(flagged)}) == 3
    # khoá tự nhiên khớp UNIQUE của Integration (migration 0002): events gồm platform/resource_type/resource; vắng => rỗng
    assert natural_key("events", rows[0]) == f"{A}||||e1"
    full = to_row("events", {"owner_account_id": A, "platform": "fb", "resource_type": "page", "resource_external_id": "r",
                             "external_id": "e1", "kind": "post_ok", "occurred_date": "2026-10-01", "revision": 1})
    assert natural_key("events", full) == f"{A}|fb|page|r|e1"
    content = to_row("content", {"owner_account_id": A, "platform": "fb", "resource_type": "page",
                                 "resource_external_id": "r", "external_id": "p", "published_date": "2026-10-01",
                                 "metrics": {"views": None, "reactions": 2}, "revision": 1})
    assert checksum_line("content", content) == f"{A}|fb|page|r|p|1|0|n,2,n"
    res = to_row("resources", {"owner_account_id": A, "platform": "fb", "resource_type": "page", "external_id": "x",
                               "revision": 3})
    assert checksum_line("resources", res) == f"{A}|fb|page|x|3|0"
    met = to_row("metrics", {"owner_account_id": A, "platform": "yt", "resource_type": "channel",
                             "resource_external_id": "c", "date": "2026-10-02", "metrics": {"views": 0}, "revision": 4})
    assert checksum_line("metrics", met) == f"{A}|yt|channel|c|2026-10-02|4|0|0"
    assert set(KINDS) == {"resources", "content", "metrics", "events"}
