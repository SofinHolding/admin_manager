"""Danh sách nhiều YouTube key / proxy: phân tích văn bản nhiều dòng, xoay vòng, nạp cấu hình cũ, và kiểm tra key/proxy còn sống."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import health  # noqa: E402
import pools  # noqa: E402

KEY_A = "AIza" + "A" * 35
KEY_B = "AIza" + "B" * 35


# ── Phân tích văn bản ────────────────────────────────────────────────────────────────────

def test_parse_keys_one_per_line_reports_invalid_lines_and_dedupes():
    good, bad = pools.parse_keys(f"{KEY_A}\n\n# ghi chú\n  {KEY_B}  \n{KEY_A}\nkhông-phải-key")
    assert good == [KEY_A, KEY_B]
    assert bad == ["không-phải-key"]


def test_parse_proxies_accepts_common_spellings_and_dedupes_same_proxy():
    good, bad = pools.parse_proxies("\n".join([
        "socks5://u:p@1.2.3.4:1080", "1.2.3.4:1080:u:p", "u:p@1.2.3.4:1080",   # cùng một proxy viết 3 kiểu (khác scheme => khác)
        "5.6.7.8:3128", "ftp://x:21", "chỉ-chữ"]))
    assert good == ["socks5://u:p@1.2.3.4:1080", "http://u:p@1.2.3.4:1080", "http://5.6.7.8:3128"]
    assert bad == ["ftp://x:21", "chỉ-chữ"]


def test_proxy_password_with_special_chars_survives_roundtrip_and_is_masked():
    [proxy], _ = pools.parse_proxies("socks5://user:p@ss,w:rd@9.9.9.9:1080")
    assert pools.mask_proxy(proxy) == "socks5://user:***@9.9.9.9:1080"
    assert "p@ss,w:rd" not in proxy and "p%40ss%2Cw%3Ard" in proxy       # mã hoá % để URL hợp lệ, không mất ký tự


def test_socks5_gets_remote_dns_unless_disabled(monkeypatch):
    assert pools.force_remote_dns("socks5://h:1") == "socks5h://h:1"
    assert pools.force_remote_dns("http://h:1") == "http://h:1"
    monkeypatch.setenv("PROXY_FORCE_SOCKS5H", "0")
    assert pools.force_remote_dns("socks5://h:1") == "socks5://h:1"


# ── Pool xoay vòng ───────────────────────────────────────────────────────────────────────

def test_pool_rotates_and_a_bad_item_rests_then_returns():
    now = [0.0]
    pool = pools.Pool(["a", "b", "c"], cooldown_s=100, clock=lambda: now[0])
    assert [pool.pick() for _ in range(4)] == ["a", "b", "c", "a"]
    pool.bad("b")
    assert [pool.pick() for _ in range(3)] == ["c", "a", "c"]
    now[0] = 101
    assert "b" in {pool.pick() for _ in range(3)}


def test_pool_with_every_item_resting_gives_none():
    pool = pools.Pool(["a"], cooldown_s=100, clock=lambda: 0.0)
    pool.bad("a")
    assert pool.pick() is None and pools.Pool([]).pick() is None


# ── Nạp cấu hình ─────────────────────────────────────────────────────────────────────────

def test_load_pools_merges_new_lists_with_legacy_single_values(tmp_path, monkeypatch):
    for name in ("YOUTUBE_API_KEY", "YOUTUBE_API_KEYS", "PROXY", "PROXIES"):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"youtube_api_keys": [KEY_A], "youtube_api_key": KEY_B, "proxies": ["1.1.1.1:80"], "proxy": "socks5://u:a,b@2.2.2.2:1080"}),
                   encoding="utf-8")
    keys, proxies = pools.load_pools(str(cfg))
    assert keys == [KEY_A, KEY_B]
    assert proxies == ["http://1.1.1.1:80", "socks5://u:a%2Cb@2.2.2.2:1080"]   # mật khẩu có dấu phẩy KHÔNG bị tách thành hai proxy


def test_load_pools_falls_back_to_environment_only_when_file_has_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEYS", f"{KEY_A},{KEY_B}")
    monkeypatch.setenv("PROXY", "3.3.3.3:8080")
    assert pools.load_pools(str(tmp_path / "missing.json")) == ([KEY_A, KEY_B], ["http://3.3.3.3:8080"])
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"youtube_api_keys": [KEY_B]}), encoding="utf-8")
    assert pools.load_pools(str(cfg))[0] == [KEY_B]


# ── Kiểm tra key YouTube ─────────────────────────────────────────────────────────────────

def _yt_client(status: int, body: dict | None = None, exc: Exception | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if exc:
            raise exc
        return httpx.Response(status, json=body or {})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _err(status: int, reason: str, message: str = "") -> tuple[int, dict]:
    return status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}


def test_youtube_key_states():
    assert health.check_youtube_key(KEY_A, client=_yt_client(200, {"items": []}))["state"] == "alive"
    assert health.check_youtube_key(KEY_A, client=_yt_client(*_err(403, "quotaExceeded")))["state"] == "quota"
    assert health.check_youtube_key(KEY_A, client=_yt_client(*_err(400, "keyInvalid", "API key not valid")))["state"] == "dead"
    assert health.check_youtube_key(KEY_A, client=_yt_client(*_err(403, "accessNotConfigured")))["state"] == "dead"
    assert health.check_youtube_key(KEY_A, client=_yt_client(500, {}))["state"] == "error"


def test_youtube_key_network_failure_is_error_not_dead_and_never_leaks_the_key():
    r = health.check_youtube_key(KEY_A, client=_yt_client(200, exc=httpx.ConnectError(f"boom https://x/?key={KEY_A}")))
    assert r["state"] == "error" and KEY_A not in r["detail"]


# ── Kiểm tra proxy ───────────────────────────────────────────────────────────────────────

def test_proxy_alive_reports_exit_ip():
    r = health.check_proxy("socks5://h:1", fetch=lambda p, url, t: (200, json.dumps({"ip": "8.8.4.4"})))
    assert r["state"] == "alive" and r["ip"] == "8.8.4.4"


def test_proxy_alive_parses_cloudflare_trace_format():
    r = health.check_proxy("http://h:1", fetch=lambda p, url, t: (200, "fl=1\nip=9.9.9.9\nts=1"))
    assert r["state"] == "alive" and r["ip"] == "9.9.9.9"


def test_proxy_failures_are_classified_and_never_leak_the_password():
    secret = "http://user:S3CR3T@h:1"

    def refuse(p, url, t):
        raise OSError(f"Failed to connect to {secret} Connection refused")

    def slow(p, url, t):
        raise TimeoutError("operation timed out")

    assert health.check_proxy(secret, fetch=refuse)["state"] == "dead"
    assert health.check_proxy(secret, fetch=slow)["state"] == "dead"
    assert health.check_proxy(secret, fetch=lambda p, u, t: (407, ""))["detail"] == "Proxy từ chối tài khoản/mật khẩu"
    assert "S3CR3T" not in health.check_proxy(secret, fetch=refuse)["detail"]   # `item` giữ chuỗi gốc để giao diện ghép dòng; `detail` thì không


def test_check_many_keeps_input_order(monkeypatch):
    monkeypatch.setattr(health, "check_proxy", lambda p: {"item": p, "state": "alive", "detail": "", "ms": 0})
    assert [r["item"] for r in health.check_many("proxies", ["z:1", "a:2", "m:3"], workers=3)] == ["z:1", "a:2", "m:3"]
