"""Bài kiểm tra chấp nhận Mock Tool X: Tool MỚI TOÀN BỘ vào hệ thống mà KHÔNG sửa Manager.

Chuỗi đi qua tiến trình thật + Postgres/Redis DEV + gateway:
  đăng ký user kèm integration -> Identity phát event -> Integration provisioning client -> cấp credential ->
  handshake -> snapshot (resources/content/events) -> commit -> Analytics kéo canonical -> Dashboard API thấy đúng số.
"""

from __future__ import annotations


import httpx
import pytest
from conftest import ROOT, TOOL_NAME, TOOL_PLATFORM, day, wait_for
from manager_sdk.integration_client import IntegrationError

CHANNELS = [
    {"external_id": "x-ch-1", "display_name": "X Channel One", "country": {"code": "BR", "name": "Brazil"}},
    {"external_id": "x-ch-2", "display_name": "X Channel Two", "country": {"code": "VN", "name": "Vietnam"}},
]
POSTS = [
    {"external_id": "x-p-1", "resource_external_id": "x-ch-1", "published_date": day(1), "metrics": {"views": 1000, "reactions": 10}},
    {"external_id": "x-p-2", "resource_external_id": "x-ch-1", "published_date": day(2), "metrics": {"views": 500}},
    {"external_id": "x-p-3", "resource_external_id": "x-ch-2", "published_date": day(1), "metrics": {"views": 250, "comments": 5}},
]
EVENTS = [{"external_id": f"x-e-{i}", "resource_external_id": "x-ch-1", "kind": "post_ok", "occurred_date": day(1)} for i in range(2)]


def summary(api, who, **params) -> dict:
    r = api.http.get("/v1/analytics/summary", headers=who["headers"], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def top(api, who, **params) -> list[dict]:
    r = api.http.get("/v1/analytics/top-channels", headers=who["headers"], params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_manager_source_has_no_knowledge_of_the_new_tool():
    """Luật 4: không hardcode tên Tool. Tên/nền tảng của Mock Tool X không được xuất hiện trong mã nguồn Manager."""
    needles = ("toolx", "tool x", TOOL_PLATFORM)
    roots = [ROOT / "services", ROOT / "apps" / "manager-web" / "src", ROOT / "packages", ROOT / "services" / "reward-service", ROOT / "infra"]
    offenders = []
    for base in roots:
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in {".py", ".ts", ".tsx", ".json", ".sql", ".yml", ".yaml"}:
                continue
            if any(part in {"node_modules", "tests", "__pycache__", ".venv", "dist"} for part in p.parts):
                continue
            if p.name in {"dev_stack.py"}:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
            if any(n in text for n in needles):
                offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"Manager biết tên Tool mới: {offenders}"


def test_owner_was_provisioned_a_client_without_manual_steps(world):
    """Mã mời preset `viewer_tool` ⇒ capability:tool_integration ⇒ Integration tự tạo Tool cá nhân (không ai tạo tay)."""
    client = world["client"]
    assert client["ownership"] == "personal" and client["client_kind"] == "primary_tool"
    assert client["created_from"] == "provisioning" and client["tool_name"] == "Tool cá nhân"
    assert client["owner_account_id"] and TOOL_PLATFORM in client["allowed_platforms"]


def test_handshake_binds_installation_and_owner_comes_from_the_credential(world):
    hs = world["tool"].handshake()
    assert hs["installation_id"].startswith("ins_") and hs["owner_account_id"] == world["client"]["owner_account_id"]
    assert hs["allowed_platforms"] == [TOOL_PLATFORM]


def test_snapshot_commit_then_dashboard_shows_exact_numbers_with_no_manager_change(api, world):
    out = world["tool"].push(resources=CHANNELS, content=POSTS, events=EVENTS)
    assert out["status"] == "committed" and out["rejected_total"] == 0
    assert out["totals"]["resources"] == 2 and out["totals"]["content"] == 3
    owner = world["owner"]
    s = wait_for(lambda: (lambda d: d if d["total_views"] == 1750 else None)(summary(api, owner)), what="Analytics kéo canonical")
    # videos_posted = số BÀI theo ngày đăng (3: x-p-1, x-p-3 ngày hôm qua; x-p-2 hôm kia). 2 event post_ok cùng ngày với bài
    # nên bị bỏ qua (event chỉ là nguồn dự phòng cho ngày không có bài).
    assert s["videos_posted"] == 3 and s["freshness"]["stale"] is False and s["freshness"]["snapshot_id"] == out["snapshot_id"]
    channels = {c["id"]: c for c in top(api, owner)}
    assert set(channels) == {"x-ch-1", "x-ch-2"}
    assert channels["x-ch-1"]["views"] == 1500 and channels["x-ch-1"]["platform"] == TOOL_PLATFORM
    assert channels["x-ch-2"]["views"] == 250 and channels["x-ch-2"]["country"] == "Vietnam"


def test_filters_apply_to_the_new_tool_data_like_any_other(api, world):
    owner = world["owner"]
    assert summary(api, owner, platform=TOOL_PLATFORM)["total_views"] == 1750
    assert summary(api, owner, country="Brazil")["total_views"] == 1500
    assert summary(api, owner, platform="facebook")["total_views"] == 0
    assert [c["id"] for c in top(api, owner, country="Vietnam")] == ["x-ch-2"]


def test_another_user_cannot_see_the_owners_tool_data(api, world):
    assert summary(api, world["other"])["total_views"] == 0
    assert top(api, world["other"]) == []
    assert summary(api, world["admin"])["total_views"] >= 1750  # admin xem được toàn bộ


def test_resend_updates_to_latest_absolute_values_not_insert_only(api, world):
    """Số liệu là TUYỆT ĐỐI, bản mới thắng: gửi lại views post 1 = 1200 thì tổng là 1200+500+250, không phải cộng dồn."""
    newer = [{**POSTS[0], "metrics": {"views": 1200}}, POSTS[1], POSTS[2]]
    world["tool"].push(resources=CHANNELS, content=newer, events=EVENTS)
    wait_for(lambda: summary(api, world["owner"])["total_views"] == 1950, what="giá trị tuyệt đối mới nhất")


def test_full_snapshot_that_omits_a_channel_removes_it_from_the_dashboard(api, world):
    world["tool"].push(resources=CHANNELS[:1], content=POSTS[:2], events=EVENTS)
    wait_for(lambda: [c["id"] for c in top(api, world["owner"])] == ["x-ch-1"], what="kênh bị bỏ khỏi snapshot đầy đủ")
    assert summary(api, world["owner"])["total_views"] == 1500


def test_tool_cannot_write_outside_its_allowed_platform_or_spoof_owner(world, stack):
    tool = world["tool"]
    with pytest.raises(IntegrationError) as exc:
        tool.run(lambda c: c.create_snapshot(platform="facebook", snapshot_type="delta", observed_at="2026-10-05T00:00:00Z",
                                             report_timezone="UTC", expected_counts={}))
    assert exc.value.status == 403 and exc.value.code in {"PLATFORM_NOT_ALLOWED", "SCOPE_DENIED"}
    r = httpx.post(stack.url("gateway", "/v1/integrations/resources/batch"),
                   headers={"Authorization": f"Bearer {tool.api_key}", "X-Installation-Id": tool.installation_id or ""},
                   json={"items": [{"external_id": "evil", "resource_type": "channel", "owner_account_id": "someone-else",
                                    "observed_at": "2026-10-05T00:00:00Z"}]})
    assert r.status_code == 422, r.text


def test_machine_key_is_not_a_user_credential(api, world, stack):
    """Luật 7: API key của Tool không dùng được cho API người dùng và ngược lại."""
    key_headers = {"Authorization": f"Bearer {world['tool'].api_key}"}
    assert httpx.get(stack.url("gateway", "/v1/analytics/summary"), headers=key_headers).status_code == 401
    assert httpx.get(stack.url("gateway", "/v1/integrations/clients"), headers=key_headers).status_code in {401, 403}
    user_token = world["owner"]["headers"]
    r = httpx.post(stack.url("gateway", "/v1/integrations/snapshots"), headers=user_token,
                   json={"platform": TOOL_PLATFORM, "snapshot_type": "delta", "observed_at": "2026-10-05T00:00:00Z",
                         "report_timezone": "UTC", "expected_counts": {}})
    assert r.status_code in {401, 403}


def test_internal_endpoints_are_unreachable_through_the_gateway(stack):
    for path in ("/internal/v1/feed", "/internal/v1/canonical/summary", "/v1/../internal/v1/feed"):
        r = httpx.get(stack.url("gateway", path))
        assert r.status_code in {403, 404}, (path, r.status_code)
    assert httpx.get(stack.url("integration", "/internal/v1/feed")).status_code in {401, 403}  # trực tiếp: thiếu X-Service-Key
