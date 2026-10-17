#!/usr/bin/env python3
"""
health.py — kiểm tra YouTube API key và proxy còn sống không (nút "Kiểm tra" trong Cài đặt của tool).

CLI (Electron gọi, dữ liệu qua stdin để key/mật khẩu proxy không lên dòng lệnh):
  echo '{"items":["AIza...", "..."]}'  | python health.py youtube-keys
  echo '{"items":["socks5://u:p@h:1080"]}' | python health.py proxies
Kết quả: {"results":[{"item","state","detail","ms", "ip"?}]}  — `item` là chuỗi gốc để giao diện ghép dòng.

Thêm hàng loạt (mỗi dòng một mục; cùng quy tắc phân tích với collector):
  echo '{"existing":[...],"text":"dòng 1\ndòng 2"}' | python health.py merge youtube-keys|proxies
Kết quả: {"items":[...], "added":n, "duplicate":n, "bad":["dòng không hợp lệ"]}

state: alive | quota (key sống nhưng hết quota hôm nay) | dead (key sai / proxy không dùng được) | error (không kết luận được: mạng, 5xx).
Lỗi KHÔNG BAO GIỜ chứa key hay mật khẩu proxy.
"""
from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx

from pools import force_remote_dns

YT_PROBE_URL = "https://www.googleapis.com/youtube/v3/videos"
YT_PROBE_VIDEO = "dQw4w9WgXcQ"                 # video công khai, `part=id` = 1 đơn vị quota
PROXY_PROBES = ("https://api.ipify.org?format=json", "https://www.cloudflare.com/cdn-cgi/trace")
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}

Fetch = Callable[[str | None, str, float], tuple[int, str]]


# ── YouTube API key ──────────────────────────────────────────────────────────────────────

def classify_youtube(status: int, body: dict | None) -> tuple[str, str]:
    """(state, detail) từ phản hồi YouTube Data API cho lệnh dò `videos.list`."""
    if status == 200:
        return "alive", "Key hoạt động"
    err = (body or {}).get("error") or {}
    reasons = {e.get("reason") for e in (err.get("errors") or []) if isinstance(e, dict)}
    message = str(err.get("message") or "")
    if status == 403 and reasons & _QUOTA_REASONS:
        return "quota", "Hết quota hôm nay (sống, dùng lại được sau khi quota reset)"
    if status == 400 and ("keyInvalid" in reasons or "API key not valid" in message):
        return "dead", "Key không hợp lệ"
    if status == 403 and "accessNotConfigured" in reasons:
        return "dead", "YouTube Data API v3 chưa được bật cho project của key này"
    if status == 403 and ("ipRefererBlocked" in reasons or "referer" in message.lower()):
        return "dead", "Key bị giới hạn IP/referrer, không dùng được từ máy này"
    if status in (400, 401, 403):
        return "dead", f"YouTube từ chối key (HTTP {status})"
    return "error", f"YouTube trả HTTP {status}, chưa kết luận được"


def check_youtube_key(key: str, *, client: httpx.Client | None = None) -> dict:
    t0 = time.monotonic()
    own = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        r = http.get(YT_PROBE_URL, params={"part": "id", "id": YT_PROBE_VIDEO, "key": key})
        try:
            body = r.json()
        except ValueError:
            body = None
        state, detail = classify_youtube(r.status_code, body)
    except httpx.HTTPError as exc:   # không đưa URL (có key) vào thông báo
        state, detail = "error", f"Không kết nối được YouTube: {type(exc).__name__}"
    finally:
        if own:
            http.close()
    return {"item": key, "state": state, "detail": detail, "ms": int((time.monotonic() - t0) * 1000)}


# ── Proxy ────────────────────────────────────────────────────────────────────────────────

def _curl_fetch(proxy: str | None, url: str, timeout: float) -> tuple[int, str]:
    """GET qua proxy bằng curl_cffi (hỗ trợ http/https/socks4/socks5/socks5h kể cả SOCKS5 có mật khẩu, DNS qua proxy)."""
    from curl_cffi import requests as cr

    p = force_remote_dns(proxy)
    r = cr.get(url, proxies={"http": p, "https": p} if p else None, timeout=timeout, impersonate="chrome136")
    return r.status_code, r.text


def classify_proxy_exception(exc: Exception) -> tuple[str, str]:
    """Lỗi khi đi qua proxy -> (state, detail) không lộ mật khẩu."""
    low = str(exc).lower()
    if "407" in low or "authentication" in low or ("auth" in low and "proxy" in low):
        return "dead", "Proxy từ chối tài khoản/mật khẩu"
    if "timed out" in low or "timeout" in low:
        return "dead", "Proxy không phản hồi (quá thời gian)"
    if any(w in low for w in ("refused", "unreachable", "resolve", "socks", "proxy", "connect", "tunnel")):
        return "dead", "Không kết nối được tới proxy"
    return "error", f"Lỗi không rõ khi dùng proxy: {type(exc).__name__}"


def check_proxy(proxy: str, *, fetch: Fetch = _curl_fetch, timeout: float = 12.0) -> dict:
    """Sống = đi qua proxy tới được internet. `ip` = địa chỉ ra ngoài (khác IP máy bạn => proxy đang hoạt động đúng)."""
    t0 = time.monotonic()
    last: tuple[str, str] = ("error", "Chưa kiểm tra")
    for probe in PROXY_PROBES:
        try:
            status, text = fetch(proxy, probe, timeout)
        except Exception as exc:  # noqa: BLE001 — mọi lỗi curl/httpx đều phân loại bên dưới
            last = classify_proxy_exception(exc)
            if last[0] == "dead":
                break                     # lỗi ở chính proxy: thử trang dò khác cũng vô ích
            continue
        if status == 407:
            last = ("dead", "Proxy từ chối tài khoản/mật khẩu")
            break
        if 200 <= status < 300:
            ip = ""
            try:
                ip = str(json.loads(text).get("ip") or "")
            except ValueError:
                for line in text.splitlines():
                    if line.startswith("ip="):
                        ip = line[3:].strip()
            out = {"item": proxy, "state": "alive", "detail": f"Proxy hoạt động{f' (IP ra: {ip})' if ip else ''}",
                   "ms": int((time.monotonic() - t0) * 1000)}
            if ip:
                out["ip"] = ip
            return out
        last = ("error", f"Trang dò trả HTTP {status}, chưa kết luận được")
    return {"item": proxy, "state": last[0], "detail": last[1], "ms": int((time.monotonic() - t0) * 1000)}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────

def check_many(kind: str, items: list[str], *, workers: int = 8) -> list[dict]:
    fn = check_youtube_key if kind == "youtube-keys" else check_proxy
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items) or 1))) as pool:
        return list(pool.map(fn, items))


def main() -> None:
    from pools import merge_entries
    from system_api import use_utf8_stdio

    use_utf8_stdio()
    args = sys.argv[1:]
    merging = args[:1] == ["merge"]
    kind = args[1] if merging and len(args) > 1 else (args[0] if args else "")
    if kind not in ("youtube-keys", "proxies"):
        print("usage: health.py [merge] youtube-keys | proxies  (stdin JSON)", file=sys.stderr)
        sys.exit(2)
    payload = json.loads(sys.stdin.read() or "{}")
    if merging:
        existing = [str(i) for i in (payload.get("existing") or [])]
        print(json.dumps(merge_entries(kind, existing, str(payload.get("text") or "")), ensure_ascii=False))
        return
    items = [str(i) for i in (payload.get("items") or [])][:200]
    print(json.dumps({"results": check_many(kind, items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
