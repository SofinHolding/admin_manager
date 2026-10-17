"""Phân quyền (token RS256 offline) và hình dạng response của alias legacy (so với admin-server legacy)."""

import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from analytics_testsupport import ADMIN, AUD, ISS, OWN_A, OWN_B

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}
LEGACY_SPEC = json.loads((Path(__file__).resolve().parents[3] / "docs" / "api" / "legacy" /
                          "admin-server.openapi.json").read_text(encoding="utf-8"))


# ── Xác thực ────────────────────────────────────────────────────────────────────

def bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


async def test_missing_or_malformed_credentials_are_401(seeded):
    c = seeded.client
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer rac"},
                    {"Authorization": "Bearer a.b.c"}):
        r = await c.get("/v1/analytics/summary", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID", headers
        assert r.json()["meta"]["request_id"]
        legacy = await c.get("/v1/viewer/summary", headers=headers)
        assert legacy.status_code == 401 and set(legacy.json()) == {"detail"}


@pytest.mark.parametrize("over", [{"exp": 1}, {"aud": ["khac"]}, {"iss": "http://gia-mao.test"},
                                  {"nbf": 4_000_000_000}], ids=["expired", "wrong-aud", "wrong-iss", "future-nbf"])
async def test_bad_token_claims_are_401(seeded, over):
    token = seeded.issuer.token(sub=OWN_A, **over)
    r = await seeded.client.get("/v1/analytics/summary", headers=bearer(token))
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"


async def test_forged_tokens_are_rejected(seeded):
    evil = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = seeded.issuer.token(sub=ADMIN, role="admin", key=evil)          # kid thật, chữ ký của kẻ tấn công
    none_tok = jwt.encode({"sub": ADMIN, "role": "admin", "exp": int(time.time()) + 60}, key=None, algorithm="none")
    hs = jwt.encode({"iss": ISS, "aud": [AUD], "sub": ADMIN, "role": "admin", "iat": int(time.time()),
                     "nbf": int(time.time()), "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256",
                    headers={"kid": seeded.issuer.kid})
    for token in (forged, none_tok, hs):
        r = await seeded.client.get("/v1/analytics/admin/dead-letters", headers=bearer(token))
        assert r.status_code == 401, token[:20]


async def test_token_verification_is_offline_identity_down_still_works(seeded):
    ok = await seeded.get("/v1/analytics/summary", **RANGE)
    assert ok.status_code == 200
    seeded.issuer.up = False                         # Identity chết: dùng JWKS đã cache
    assert (await seeded.get("/v1/analytics/summary", **RANGE)).status_code == 200
    unknown_kid = seeded.issuer.token(sub=OWN_A)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(jwt.decode(unknown_kid, options={"verify_signature": False}), other, algorithm="RS256",
                       headers={"kid": "kid-la"})
    r = await seeded.client.get("/v1/analytics/summary", headers=bearer(token))
    assert r.status_code == 401                      # kid lạ khi không lấy được JWKS -> 401, không 500


async def test_admin_endpoints_require_admin_or_dash_admin(seeded):
    admin_paths = [("GET", "/v1/analytics/admin/country-aliases"), ("GET", "/v1/analytics/admin/country-canonicals"),
                   ("POST", "/v1/analytics/admin/cache/flush"), ("POST", "/v1/analytics/admin/rebuild"),
                   ("POST", "/v1/analytics/admin/reconcile"), ("GET", "/v1/analytics/admin/dead-letters"),
                   ("POST", "/v1/analytics/admin/dead-letters/replay"), ("POST", "/v1/analytics/admin/sync"),
                   ("GET", "/v1/admin/country-aliases"), ("GET", "/v1/admin/country-canonicals"),
                   ("POST", "/v1/admin/cache/flush")]
    for method, path in admin_paths:
        for role in ("viewer", "discord"):
            r = await seeded.client.request(method, path, headers=seeded.as_user(OWN_A, role))
            assert r.status_code == 403, (path, role)
            if path.startswith("/v1/analytics"):
                assert r.json()["error"]["code"] == "SCOPE_DENIED"
            else:
                assert set(r.json()) == {"detail"}
        anon = await seeded.client.request(method, path)
        assert anon.status_code == 401, path
        for role in ("admin", "dash_admin"):
            r = await seeded.client.request(method, path, headers=seeded.as_user(ADMIN, role))
            assert r.status_code == 200, (path, role, r.text)


async def test_non_admin_roles_are_scoped_to_self_for_every_data_endpoint(seeded):
    for role in ("viewer", "discord", "weird-role"):
        body = (await seeded.get("/v1/analytics/summary", sub=OWN_B, role=role, owner_account_id=OWN_A,
                                 **RANGE)).json()
        assert body["total_views"] == 999 and body["filter"]["owner_account_id"] == OWN_B


async def test_viewer_cannot_read_other_owners_data_through_any_endpoint(seeded):
    for ep in ("summary", "traffic", "top-channels", "views-by-country", "channel-breakdown", "country-tags",
               "compare/channel", "compare/owner", "compare/platform", "compare/group",
               "compare/country", "platform-tags"):
        for params in ({}, {"owner_account_id": OWN_A}, {"owner_account_id": ""}):
            r = await seeded.get("/v1/analytics/" + ep, sub=OWN_B, **params, **RANGE)
            assert r.status_code == 200, ep
            text = r.text
            assert "pg1" not in text and "ch1" not in text and OWN_A not in text.replace(
                '"owner_account_id": null', ""), (ep, params)


async def test_validation_errors_do_not_reflect_input(seeded):
    secret = "tuyet-mat-" + "x" * 20
    r = await seeded.get("/v1/analytics/top-channels", limit=secret)
    assert r.status_code == 422 and secret not in r.text
    legacy = await seeded.get("/v1/viewer/top-channels", limit=secret)
    assert legacy.status_code == 422 and secret not in legacy.text and set(legacy.json()) == {"detail"}


async def test_hostile_filter_values_are_harmless(seeded):
    for value in ("' OR 1=1 --", "'; DROP TABLE read_content; --", "a\x00b", "%", "\\", "é" * 40):
        r = await seeded.get("/v1/analytics/summary", country=value, platform=value, group_id=value,
                             platform_tag=value, **RANGE)
        assert r.status_code == 200, value
        assert r.json()["total_views"] == 0
    assert (await seeded.get("/v1/analytics/summary", **RANGE)).json()["total_views"] == 560


# ── Hình dạng alias legacy ──────────────────────────────────────────────────────

def legacy_ops() -> set[tuple[str, str]]:
    ops = set()
    for path, item in LEGACY_SPEC["paths"].items():
        if path.startswith("/v1/viewer") and not path.startswith("/v1/viewer/links"):
            ops |= {(m.upper(), path) for m in item}
        elif path.startswith(("/v1/admin/cache", "/v1/admin/country-")):
            ops |= {(m.upper(), path) for m in item}
    return ops


async def test_every_legacy_route_of_this_service_exists_with_same_method(seeded):
    spec = (await seeded.client.get("/openapi.json")).json()
    ours = {(m.upper(), p) for p, item in spec["paths"].items() for m in item}
    missing = sorted(op for op in legacy_ops() if op not in ours)
    assert not missing, missing
    assert len(legacy_ops()) >= 20          # đã đọc đúng file legacy (không phải tập rỗng)


async def test_every_legacy_query_param_is_accepted_by_alias(seeded):
    spec = (await seeded.client.get("/openapi.json")).json()
    for path, item in LEGACY_SPEC["paths"].items():
        if not (path.startswith("/v1/viewer") and not path.startswith("/v1/viewer/links")) and not path.startswith(
                ("/v1/admin/cache", "/v1/admin/country-")):
            continue
        for method, op in item.items():
            legacy_params = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
            ours = {p["name"] for p in spec["paths"][path][method].get("parameters", []) if p["in"] == "query"}
            assert legacy_params <= ours, (method, path, legacy_params - ours)


SUMMARY_KEYS = {"total_views", "total_views_prev", "videos_posted", "videos_posted_prev", "active_channels",
                "managed_channels", "days_in_range", "linked", "last_synced_at"}


async def test_legacy_viewer_alias_shapes(seeded):
    g = seeded.get

    s = (await g("/v1/viewer/summary", **RANGE)).json()
    assert set(s) == SUMMARY_KEYS | {"filter", "freshness"}
    assert s["last_synced_at"] == s["freshness"]["committed_at"]
    assert set(s["freshness"]) == {"as_of", "committed_at", "snapshot_id", "revision", "lag_seconds", "stale"}

    t = (await g("/v1/viewer/traffic", **RANGE)).json()
    assert set(t) == {"scope", "scope_label", "country", "days", "totals", "filter", "freshness"}
    assert set(t["days"][0]) == {"date", "views", "interactions", "comments", "videos"}
    assert set(t["totals"]) == {"views", "interactions", "comments", "videos"}

    top = (await g("/v1/viewer/top-channels", **RANGE)).json()
    assert set(top) == {"items", "filter", "freshness"}
    assert set(top["items"][0]) == {"id", "name", "platform", "country", "views"}

    vbc = (await g("/v1/viewer/views-by-country", **RANGE)).json()
    assert {"countries", "days", "totals", "filter", "freshness"} <= set(vbc)
    assert set(vbc["days"][0]) >= {"date"}

    tags = (await g("/v1/viewer/country-tags")).json()
    assert set(tags) == {"items", "filter", "freshness"} and set(tags["items"][0]) == {"id", "name"}
    ptags = (await g("/v1/viewer/platform-tags")).json()
    assert ptags["items"] == []
    metrics = (await g("/v1/viewer/metrics")).json()
    assert set(metrics["metrics"][0]) == {"key", "label", "ready", "note"}

    cb = (await g("/v1/viewer/channel-breakdown")).json()
    assert set(cb) == {"total", "by_platform", "filter", "freshness"} and set(cb["total"]) == {"managed", "active"}
    fb = cb["by_platform"][0]
    assert set(fb) == {"platform", "managed", "active", "by_country"}
    assert set(fb["by_country"][0]) == {"country", "managed", "active"}
    assert set(cb["by_platform"][1]) == {"platform", "managed", "active", "by_country"}


async def test_legacy_and_new_paths_return_identical_bodies(seeded):
    for ep in ("summary", "traffic", "top-channels", "views-by-country", "country-tags", "channel-breakdown", "metrics",
               "platform-tags"):
        a = (await seeded.get("/v1/analytics/" + ep, **RANGE)).json()
        b = (await seeded.get("/v1/viewer/" + ep, **RANGE)).json()
        for body in (a, b):
            body.pop("freshness")
        assert a == b, ep


async def test_legacy_group_and_admin_shapes(seeded):
    c, h = seeded.client, seeded.as_user(OWN_A)
    created = (await c.post("/v1/viewer/groups", json={"name": "Nhóm 1"}, headers=h)).json()
    assert set(created) == {"id", "account_id", "name", "created_at", "updated_at"} and created["account_id"] == OWN_A
    member = (await c.post(f"/v1/viewer/groups/{created['id']}/members", headers=h,
                           json={"channel_ref": "pg1", "platform_tag": "tiktok", "country_override": "Chile"})).json()
    assert member == {"group_id": created["id"], "channel_ref": "pg1", "platform_tag": "tiktok",
                      "country_override": "Chile"}
    listing = (await c.get("/v1/viewer/groups", headers=h)).json()
    assert set(listing) == {"items"} and set(listing["items"][0]) == {"id", "account_id", "name", "created_at",
                                                                        "updated_at", "members"}
    assert listing["items"][0]["members"] == [{"channel_ref": "pg1", "platform_tag": "tiktok",
                                                "country_override": "Chile"}]
    assert (await c.patch(f"/v1/viewer/groups/{created['id']}", json={"name": "Mới"}, headers=h)).json() == {"ok": True}
    assert (await c.delete(f"/v1/viewer/groups/{created['id']}/members/pg1", headers=h)).json() == {"ok": True}
    assert (await c.delete(f"/v1/viewer/groups/{created['id']}", headers=h)).json() == {"ok": True}

    a = seeded.as_user(ADMIN, "admin")
    canon = (await c.post("/v1/admin/country-canonicals", json={"name": "Brazil"}, headers=a)).json()
    assert set(canon) == {"name", "created_at"}
    assert (await c.get("/v1/admin/country-canonicals", headers=a)).json()["items"][0].keys() == {"name", "created_at"}
    alias = (await c.post("/v1/admin/country-aliases", json={"alias": "BR", "canonical": "Brazil"}, headers=a)).json()
    assert alias == {"alias": "br", "canonical": "Brazil"}
    assert (await c.get("/v1/admin/country-aliases", headers=a)).json()["items"][0].keys() == {"alias", "canonical",
                                                                                                "created_at"}
    assert (await c.patch("/v1/admin/country-canonicals/Brazil", json={"name": "Brasil"}, headers=a)).json() == {
        "ok": True, "name": "Brasil"}
    assert (await c.get("/v1/admin/country-aliases", headers=a)).json()["items"][0]["canonical"] == "Brasil"
    assert (await c.delete("/v1/admin/country-aliases/br", headers=a)).json() == {"ok": True}
    assert (await c.delete("/v1/admin/country-canonicals/Brasil", headers=a)).json() == {"ok": True}
    assert (await c.post("/v1/admin/cache/flush", headers=a)).json() == {"ok": True, "removed": 0, "enabled": False}
