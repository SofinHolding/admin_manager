"""Lấy TẤT CẢ link video của một kênh TikTok: lặp qua cổng chống bot ngẫu nhiên, xoay proxy + dấu vân tay TLS, phân loại lỗi, lưu DB + xuất .txt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels_db  # noqa: E402
import tiktok_links as tl  # noqa: E402

CHANNEL = "https://www.tiktok.com/@sk1kr"
BLOCKED = RuntimeError("ERROR: [tiktok:user] sk1kr: Unable to extract secondary user ID")


def _entries(n: int) -> list[dict]:
    return [{"id": str(9000 - i), "url": f"https://www.tiktok.com/@sk1kr/video/{9000 - i}", "title": f"v{i}", "timestamp": 1792215000 + i,
             "view_count": 100 + i} for i in range(n)]


class Gate:
    """`extract` giả: cổng chống bot chặn `blocked_first` lượt đầu rồi cho qua; ghi lại (proxy, dấu vân tay) của từng lượt."""

    def __init__(self, blocked_first: int = 0, entries: list[dict] | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str | None, str]] = []
        self.blocked_first, self.entries, self.error = blocked_first, _entries(3) if entries is None else entries, error

    def __call__(self, url: str, proxy: str | None, impersonate: str, limit: int) -> list[dict]:
        self.calls.append((proxy, impersonate))
        if self.error:
            raise self.error
        if len(self.calls) <= self.blocked_first:
            raise BLOCKED
        return self.entries


def fetch(gate: Gate, proxies: list[str], **kw) -> dict:
    return tl.fetch_links(CHANNEL, proxies=proxies, extract=gate, pause_s=0, start=0, **kw)


# ── nhận dạng kênh ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", ["https://www.tiktok.com/@sk1kr", "https://www.tiktok.com/@sk1kr/video/123?lang=en", "@sk1kr", "sk1kr"])
def test_channel_url_from_common_inputs(ref):
    assert tl.channel_url(ref) == CHANNEL


def test_channel_url_rejects_garbage_with_a_friendly_error():
    with pytest.raises(tl.TikTokError):
        tl.channel_url("https://example.com/x")


def test_error_classification_matches_real_ytdlp_messages():
    assert tl.classify_error(Exception("This user does not have any videos posted")) == "empty"
    assert tl.classify_error(Exception("This account is private")) == "private"
    assert tl.classify_error(Exception("Unable to extract secondary user ID")) == "retry"
    assert tl.classify_error(Exception("curl: (35) TLS connect error")) == "retry"


# ── lặp qua cổng chống bot ───────────────────────────────────────────────────────────────

def test_keeps_trying_past_random_blocks_and_returns_the_full_list():
    gate = Gate(blocked_first=5, entries=_entries(393))
    out = fetch(gate, ["http://a:1", "http://b:2"])
    assert out["kind"] == "ok" and len(out["videos"]) == 393 and out["attempts"] == 6


def test_every_attempt_changes_proxy_and_tls_fingerprint():
    gate = Gate(blocked_first=4)
    fetch(gate, ["http://a:1", "http://b:2"])
    assert [p for p, _ in gate.calls] == ["http://a:1", "http://b:2", "http://a:1", "http://b:2", "http://a:1"]
    assert [i for _, i in gate.calls] == ["chrome-136", "chrome-131", "chrome-124", "chrome-136", "chrome-131"]


def test_without_proxies_it_goes_direct_and_still_varies_fingerprint():
    gate = Gate(blocked_first=2)
    out = fetch(gate, [])
    assert [p for p, _ in gate.calls] == [None, None, None] and out["via"] == "trực tiếp"


def test_gives_up_after_max_attempts_with_an_actionable_message():
    gate = Gate(blocked_first=10_000)
    with pytest.raises(tl.TikTokError) as exc:
        fetch(gate, ["http://a:1"], max_attempts=7)
    assert len(gate.calls) == 7 and "7 lượt" in str(exc.value) and "proxy" in str(exc.value)


def test_new_calls_start_from_different_proxies_so_parallel_jobs_spread_out():
    seen = set()
    for _ in range(40):
        gate = Gate()
        tl.fetch_links(CHANNEL, proxies=[f"http://p{i}:1" for i in range(9)], extract=gate, pause_s=0)
        seen.add(gate.calls[0][0])
    assert len(seen) > 3


def test_empty_channel_is_a_result_not_an_error():
    out = fetch(Gate(error=RuntimeError("This user does not have any videos posted")), ["http://a:1"])
    assert out["kind"] == "empty" and out["videos"] == []


def test_private_channel_stops_immediately():
    gate = Gate(error=RuntimeError("This account is private"))
    with pytest.raises(tl.TikTokError, match="riêng tư"):
        fetch(gate, ["http://a:1", "http://b:2"])
    assert len(gate.calls) == 1


def test_empty_listing_without_the_no_videos_message_counts_as_blocked_and_is_retried():
    calls: list[int] = []

    def extract(url, proxy, imp, limit):
        calls.append(1)
        return [] if len(calls) < 3 else _entries(2)
    out = tl.fetch_links(CHANNEL, proxies=[], extract=extract, pause_s=0, start=0)
    assert len(out["videos"]) == 2 and len(calls) == 3


def test_listing_dedupes_keeps_order_and_converts_timestamp_to_iso():
    out = fetch(Gate(entries=_entries(3) + [_entries(1)[0]]), [])
    assert [v["video_id"] for v in out["videos"]] == ["9000", "8999", "8998"]
    assert out["videos"][0]["published_at"].startswith("2026-10-") and out["videos"][0]["views"] == 100


def test_proxy_password_is_never_in_the_result_or_error():
    out = fetch(Gate(), ["socks5://u:TOPSECRET@9.9.9.9:1080"])
    assert "TOPSECRET" not in json.dumps(out)
    with pytest.raises(tl.TikTokError) as exc:
        fetch(Gate(blocked_first=99), ["socks5://u:TOPSECRET@9.9.9.9:1080"], max_attempts=2)
    assert "TOPSECRET" not in str(exc.value)


# ── Lưu DB cục bộ + xuất .txt ────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "channels.db"
    monkeypatch.setattr(channels_db, "DB_PATH", str(path))
    conn = channels_db._get_conn()
    channels_db.act_init(conn, {})
    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": [
        {"id": "c1", "owner_account_id": "u1", "owner_username": "hieu", "platform": "tiktok", "resource_type": "channel",
         "canonical_external_id": "sk1kr", "input_ref": CHANNEL, "display_name": "sk1kr", "url": CHANNEL, "mapping_status": "mapped",
         "execution_mode": "shared", "enabled": True}]})
    conn.close()
    return path


def test_run_fetch_saves_links_exports_txt_and_is_idempotent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "EXPORT_DIR", tmp_path / "out")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"proxies": []}), encoding="utf-8")
    monkeypatch.setattr(tl, "_extract_flat", lambda url, proxy, imp, limit: _entries(4))
    monkeypatch.setattr(tl.time, "sleep", lambda s: None)
    r1 = tl.run_fetch({"channel_id": "c1", "url": CHANNEL}, settings_path=cfg)
    r2 = tl.run_fetch({"channel_id": "c1", "url": CHANNEL}, settings_path=cfg)
    assert (r1["found"], r1["added"], r1["total"]) == (4, 4, 4) and (r2["added"], r2["total"]) == (0, 4)
    lines = Path(r1["export"]).read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("/video/8997") and len(lines) == 4       # mới đăng nhất (timestamp lớn nhất) ở đầu
    conn = channels_db._get_conn()
    try:
        ch = channels_db.act_list_channels(conn, {})["channels"][0]
    finally:
        conn.close()
    assert ch["video_count"] == 4


def test_run_fetch_returns_error_for_unknown_channel_instead_of_raising(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "EXPORT_DIR", tmp_path / "out")
    monkeypatch.setattr(tl, "_extract_flat", lambda url, proxy, imp, limit: _entries(1))
    assert "error" in tl.run_fetch({"channel_id": "nope", "url": CHANNEL}, settings_path=tmp_path / "none.json")


def test_run_fetch_reports_blocking_as_error_not_exception(db, tmp_path, monkeypatch):
    def blocked(*a):
        raise BLOCKED
    monkeypatch.setattr(tl, "_extract_flat", blocked)
    monkeypatch.setattr(tl, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(tl.time, "sleep", lambda s: None)
    r = tl.run_fetch({"channel_id": "c1", "url": CHANNEL}, settings_path=tmp_path / "none.json")
    assert "3 lượt" in r["error"]


def test_later_fetch_does_not_wipe_known_title_with_blank(db):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "Tiêu đề", "views": 10}]})
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u", "title": "", "views": None}]})
        row = channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"][0]
    finally:
        conn.close()
    assert row["title"] == "Tiêu đề" and row["views"] == 10


def test_videos_disappear_when_their_channel_leaves_the_shared_list(db):
    conn = channels_db._get_conn()
    try:
        channels_db.act_save_channel_videos(conn, {"channel_id": "c1", "videos": [{"video_id": "1", "url": "u"}]})
        channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "hieu"}], "channels": []})
        assert channels_db.act_list_channel_videos(conn, {"channel_id": "c1"})["videos"] == []
    finally:
        conn.close()
