"""Dựng payload ingest và chạy trọn luồng snapshot qua API thật (dùng chung cho các file test)."""

from __future__ import annotations

from conftest import Tool, ts

FULL_SCOPE = {"resources": True, "content_window": {"from": "2026-09-01", "to": "2026-10-31"},
              "metrics_window": {"from": "2026-09-01", "to": "2026-10-31"},
              "events_window": {"from": "2026-09-01", "to": "2026-10-31"}}


def res(ext: str = "r1", *, obs: int = 0, rtype: str = "page", **kw) -> dict:
    return {"external_id": ext, "resource_type": rtype, "observed_at": ts(obs), **kw}


def post(ext: str, *, res_ext: str = "r1", date: str = "2026-10-04", obs: int = 0, **metrics) -> dict:
    return {"external_id": ext, "resource_external_id": res_ext, "published_date": date,
            "metrics": metrics or {"views": 100}, "observed_at": ts(obs)}


def daily(res_ext: str, date: str, *, obs: int = 0, **metrics) -> dict:
    return {"resource_external_id": res_ext, "date": date, "metrics": metrics or {"views": 10}, "observed_at": ts(obs)}


def event(ext: str, *, res_ext: str = "r1", kind: str = "post_ok", date: str = "2026-10-04") -> dict:
    return {"external_id": ext, "resource_external_id": res_ext, "kind": kind, "occurred_date": date}


async def new_snapshot(tool: Tool, *, platform: str = "facebook", type: str = "full", obs: int = 0,
                       scope: dict | None = None, expected: dict | None = None, tz: str = "Asia/Ho_Chi_Minh",
                       idem=None, expect_status: int = 201) -> str:
    body = {"platform": platform, "snapshot_type": type, "observed_at": ts(obs), "report_timezone": tz,
            "expected_counts": expected or {}}
    if scope is not None:
        body["scope"] = scope
    r = await tool.post("/v1/integrations/snapshots", body, idem=idem)
    assert r.status_code == expect_status, r.text
    return r.json()["data"]["snapshot_id"] if expect_status == 201 else ""


async def push(tool: Tool, sid: str, kind: str, items: list[dict], *, idem=None, expect: int = 200):
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/{kind}", {"items": items}, idem=idem)
    assert r.status_code == expect, r.text
    return r.json()["data"] if expect == 200 else r.json()


async def commit(tool: Tool, sid: str, *, expect: int = 200, idem=None, **body):
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/commit", body or None, idem=idem)
    assert r.status_code == expect, r.text
    return r.json()["data"] if expect == 200 else r.json()


async def ingest_full(tool: Tool, *, resources=None, content=None, metrics=None, events=None, obs: int = 0,
                      scope: dict | None = FULL_SCOPE, type: str = "full", **commit_body) -> dict:
    """Snapshot trọn gói: tạo, đẩy từng loại, commit. Trả data của commit."""
    resources, content, metrics, events = resources or [], content or [], metrics or [], events or []
    expected = {k: len(v) for k, v in (("resources", resources), ("content", content), ("metrics", metrics),
                                       ("events", events)) if v}
    sid = await new_snapshot(tool, obs=obs, scope=scope if type == "full" else None, expected=expected, type=type)
    for kind, items in (("resources", resources), ("content", content), ("metrics", metrics), ("events", events)):
        if items:
            await push(tool, sid, kind, items)
    out = await commit(tool, sid, **commit_body)
    out["_sid"] = sid
    return out


async def canonical(env, kind: str, *, after: int = 0, limit: int = 1000, key: str | None = None) -> dict:
    from conftest import FEED_KEY_ANALYTICS
    r = await env.http.get(f"/internal/v1/canonical/{kind}", params={"after_revision": after, "limit": limit},
                           headers={"X-Service-Key": key or FEED_KEY_ANALYTICS})
    assert r.status_code == 200, r.text
    return r.json()
