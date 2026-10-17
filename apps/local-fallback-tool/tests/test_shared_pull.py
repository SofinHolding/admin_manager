"""Kéo dữ liệu từ website CHỈ cho user dùng tool dùng chung; user có Tool riêng (primary_tool) không được kéo về."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_api  # noqa: E402


def _res(rid: str, owner: str, mode: str, **over) -> dict:
    return {"id": rid, "owner_account_id": f"acc_{owner}", "owner_username": owner, "platform": "youtube",
            "resource_type": "unspecified", "external_id": f"ext_{rid}", "display_name": f"Kênh {rid}", "username": "",
            "url": "", "mapping_status": "unmapped", "execution_mode": mode, "enabled": True, **over}


ITEMS = [
    _res("a1", "view_user", "fallback_tool"),
    _res("a2", "view_user", "fallback_tool", platform="tiktok"),
    _res("b1", "tool_user", "primary_tool"),                                   # chỉ có Tool riêng
    _res("b2", "tool_user", "primary_tool"),
    _res("m1", "mixed_user", "primary_tool"),                                  # vừa Tool riêng vừa khai báo trên web
    _res("m2", "mixed_user", "fallback_tool"),
    _res("g1", "view_user", "fallback_tool", resource_type="group"),           # group không phải kênh
    _res("d1", "view_user", "disabled"),                                       # đã tắt thu thập
]


def test_only_users_of_the_shared_tool_are_pulled_and_the_rest_is_reported():
    v = system_api.shared_view(ITEMS)
    assert [o["username"] for o in v["owners"]] == ["mixed_user", "view_user"]      # tool_user không xuất hiện
    assert {o["username"]: o["resource_count"] for o in v["owners"]} == {"view_user": 2, "mixed_user": 1}
    assert sorted(c["id"] for c in v["channels"]) == ["a1", "a2", "m2"]              # không có kênh primary_tool / group / disabled
    assert all(c["execution_mode"] == "fallback_tool" for c in v["channels"])
    # mixed_user vẫn còn kênh dùng chung nên KHÔNG tính là "user chỉ dùng Tool riêng"
    assert v["skipped"] == {"personal_tool_owners": 1, "personal_tool_channels": 3, "other_channels": 1}


def test_channels_use_the_shape_the_local_db_expects():
    ch = system_api.shared_view([_res("a1", "view_user", "fallback_tool", username="@kenh")])["channels"][0]
    assert ch["canonical_external_id"] == "ext_a1" and ch["input_ref"] == "@kenh"      # username ưu tiên hơn external_id
    assert ch["owner_account_id"] == "acc_view_user" and ch["owner_username"] == "view_user"


def test_pull_makes_one_unfiltered_call_and_never_queries_owners_it_would_drop():
    seen: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"ok": True, "data": {"items": ITEMS, "is_shared": True}})

    api = system_api.SystemApi("http://gw", "mpk_a.b")
    api._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    out = asyncio.run(api.pull_shared())
    assert seen == [("/v1/integrations/resources", {})] and len(out["channels"]) == 3


def test_pull_reports_server_and_network_failures_as_error_not_exception(monkeypatch):
    async def fake_handshake(self, platforms):
        raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://gw"), response=httpx.Response(
            401, json={"ok": False, "error": {"code": "AUTH_INVALID", "message": "Thiếu hoặc sai API key"}}))

    monkeypatch.setattr(system_api.SystemApi, "handshake", fake_handshake)
    assert asyncio.run(system_api.pull_shared("http://gw", "mpk_x.y")) == {"error": "HTTP 401 Thiếu hoặc sai API key"}

    async def down(self, platforms):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(system_api.SystemApi, "handshake", down)
    assert "refused" in asyncio.run(system_api.pull_shared("http://gw", "mpk_x.y"))["error"]
