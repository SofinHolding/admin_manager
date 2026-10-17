"""Lấy cookie + User-Agent TikTok từ MỘT Chrome/Edge cục bộ chạy ẩn (headless) — KHÔNG cần AdsPower.

Cơ chế (port từ youtube_video: `facebook_local_browser.py` + `antidetect/cdp.py` + `tiktok_scan.py::_collect_browser_info`):
1. Dò `chrome.exe` / `msedge.exe` trên máy (hoặc đường dẫn người dùng nhập), mở ẩn với thư mục hồ sơ tạm + cổng debug ngẫu nhiên.
2. Có proxy (từ danh sách proxy đã cài) thì trỏ Chrome vào proxy đó: proxy KHÔNG mật khẩu dùng thẳng; proxy CÓ mật khẩu đi qua relay cục bộ
   (`proxy_relay`) vì Chrome không tự xác thực SOCKS5 và CDP không trả lời được thách thức xác thực của HTTP proxy một cách ổn định.
   Không có proxy nào => đi thẳng bằng IP máy này (giao diện báo rõ).
3. Qua CDP: đặt UA sạch (bỏ chữ `Headless`), mở trang chủ TikTok (không cần đăng nhập vẫn nhận cookie phiên hợp lệ), đọc `Network.getAllCookies`.
4. yt-dlp dùng cookie + ĐÚNG UA này + ĐÚNG proxy này để liệt kê video kênh (TikTok chặn khi thiếu cookie hoặc UA/proxy lệch cookie).
Đóng Chrome, xoá thư mục tạm, dừng relay trong `finally` — không rò tiến trình.
"""
from __future__ import annotations

import itertools
import json
import os
import random
import shutil
import socket
import string
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from proxy_relay import HttpAuthRelay, Socks5AuthRelay, _LocalRelay

TIKTOK_URL = "https://www.tiktok.com/"
_CDP_READY_TIMEOUT_S = 30.0
_COMMON_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",     # Edge cùng nhân Chromium, cùng cờ + CDP
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


class BrowserError(Exception):
    """Lỗi mở trình duyệt / CDP / lấy cookie — thông báo thân thiện, không chứa mật khẩu proxy."""


def resolve_chrome_path(configured: str | None = None) -> str:
    if configured and os.path.isfile(configured):
        return configured
    for p in _COMMON_PATHS:
        if p and os.path.isfile(p):
            return p
    for name in ("chrome", "chrome.exe", "google-chrome", "chromium", "msedge", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise BrowserError("Không tìm thấy Chrome/Edge trên máy — nhập đường dẫn chrome.exe ở Cài đặt (mục Trình duyệt lấy cookie).")


# ── CDP tối giản ─────────────────────────────────────────────────────────────────────────

class CDP:
    """Một phiên CDP (JSON-RPC qua websocket) tới MỘT tab."""

    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        from websockets.sync.client import connect

        try:
            self._ws = connect(ws_url, open_timeout=timeout, max_size=32 * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Không kết nối được CDP: {type(exc).__name__}") from exc
        self._ids = itertools.count(1)
        self.timeout = timeout

    def send(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        cmd_id = next(self._ids)
        self._ws.send(json.dumps({"id": cmd_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise BrowserError(f"CDP timeout ở lệnh {method}")
            try:
                msg = json.loads(self._ws.recv(timeout=remain))
            except TimeoutError as exc:
                raise BrowserError(f"CDP timeout ở lệnh {method}") from exc
            if msg.get("id") == cmd_id:
                if "error" in msg:
                    raise BrowserError(f"CDP {method} lỗi: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})

    def eval_js(self, expression: str):
        return (self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True}).get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass


# ── Mở / đóng trình duyệt ────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def proxy_server_flag(proxy: str | None) -> tuple[str | None, _LocalRelay | None]:
    """`(giá trị --proxy-server, relay)` cho Chrome. Proxy có mật khẩu => relay cục bộ không mật khẩu; không có proxy => (None, None)."""
    if not proxy:
        return None, None
    p = urlsplit(proxy)
    scheme = p.scheme.lower()
    if not p.hostname or not p.port:
        raise BrowserError("Proxy không đúng dạng scheme://[user:pass@]host:port")
    if p.username:
        if scheme.startswith("socks5"):
            relay: _LocalRelay = Socks5AuthRelay(p.hostname, p.port, _unq(p.username), _unq(p.password or ""))
            return f"socks5://127.0.0.1:{relay.start()}", relay
        if scheme in ("http", "https"):
            relay = HttpAuthRelay(p.hostname, p.port, _unq(p.username), _unq(p.password or ""))
            return f"http://127.0.0.1:{relay.start()}", relay
        raise BrowserError(f"Proxy {scheme} có mật khẩu chưa hỗ trợ cho trình duyệt")
    return f"{'socks5' if scheme.startswith('socks5') else scheme}://{p.hostname}:{p.port}", None


def _unq(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


@dataclass
class Browser:
    endpoint: str
    process: subprocess.Popen
    temp_dir: str
    relay: _LocalRelay | None = None
    extra: dict = field(default_factory=dict)

    def user_agent(self) -> str:
        """UA thật của trình duyệt bỏ chữ `Headless` (dấu hiệu bot rõ nhất)."""
        ua = httpx.get(f"{self.endpoint}/json/version", timeout=5.0).json().get("User-Agent", "")
        return ua.replace("HeadlessChrome", "Chrome")

    def version(self) -> str:
        return str(httpx.get(f"{self.endpoint}/json/version", timeout=5.0).json().get("Browser", ""))

    def new_tab(self, url: str = "about:blank") -> CDP:
        try:
            data = httpx.put(f"{self.endpoint}/json/new?{url}", timeout=10.0).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BrowserError(f"Không mở được tab mới: {type(exc).__name__}") from exc
        ws = data.get("webSocketDebuggerUrl")
        if not ws:
            raise BrowserError("Trình duyệt không trả địa chỉ CDP cho tab mới")
        return CDP(ws)

    def close(self) -> None:
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass
        if self.relay is not None:
            self.relay.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def open_browser(chrome_path: str | None, proxy: str | None = None, *, headless: bool = True) -> Browser:
    chrome_path = resolve_chrome_path(chrome_path)
    flag, relay = proxy_server_flag(proxy)
    port = _free_port()
    temp_dir = os.path.join(tempfile.gettempdir(), "collector-browser-" + "".join(random.choices(string.ascii_letters + string.digits, k=12)))
    os.makedirs(temp_dir, exist_ok=True)
    args = [chrome_path, f"--remote-debugging-port={port}", f"--user-data-dir={temp_dir}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1280,900", "--disable-blink-features=AutomationControlled"]
    if headless:
        args += ["--headless=new", "--disable-gpu"]
    else:                                    # cửa sổ thật nhưng đặt ngoài màn hình: TikTok nghi ngờ trình duyệt headless
        args += ["--window-position=-32000,-32000"]
    if flag:
        args.append(f"--proxy-server={flag}")
    try:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        if relay:
            relay.stop()
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise BrowserError(f"Không mở được trình duyệt: {exc.strerror or type(exc).__name__}") from exc
    browser = Browser(f"http://127.0.0.1:{port}", process, temp_dir, relay)
    deadline = time.monotonic() + _CDP_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{browser.endpoint}/json/version", timeout=2.0).raise_for_status()
            return browser
        except httpx.HTTPError:
            time.sleep(0.5)
    browser.close()
    raise BrowserError("Trình duyệt không sẵn sàng CDP sau khi mở — kiểm tra đường dẫn trình duyệt/proxy")


# ── Lấy phiên TikTok ─────────────────────────────────────────────────────────────────────

def _tiktok_cookies(raw: list[dict]) -> list[dict]:
    return [{"name": c.get("name"), "value": c.get("value"), "domain": c.get("domain"), "path": c.get("path") or "/"}
            for c in raw if "tiktok" in (c.get("domain") or "").lower() and c.get("name")]


def fetch_tiktok_session(chrome_path: str, proxy: str | None = None, *, nav_url: str = TIKTOK_URL) -> dict:
    """`{cookies, user_agent, proxy}`. Raise `BrowserError` nếu không mở được trình duyệt hoặc không nhận được cookie nào."""
    with open_browser(chrome_path, proxy) as browser:
        ua = browser.user_agent()
        tab = browser.new_tab("about:blank")
        try:
            tab.send("Network.enable")
            tab.send("Network.setUserAgentOverride", {"userAgent": ua})
            tab.send("Page.enable")
            try:
                tab.send("Page.navigate", {"url": nav_url}, timeout=30)
            except BrowserError:
                pass
            for _ in range(40):
                try:
                    if tab.eval_js("document.readyState") == "complete":
                        break
                except BrowserError:
                    pass
                time.sleep(0.5)
            time.sleep(2.0)
            cookies = _tiktok_cookies(tab.send("Network.getAllCookies", timeout=20).get("cookies") or [])
        finally:
            tab.close()
    if not cookies:
        raise BrowserError("Không lấy được cookie TikTok (trang không tải được — kiểm tra mạng/proxy hoặc captcha)")
    return {"cookies": cookies, "user_agent": ua, "proxy": proxy or ""}


def check_browser(configured_path: str | None = None, proxy: str | None = None) -> dict:
    """Nút "Kiểm tra trình duyệt" ở Cài đặt: mở thử rồi đóng; không đụng TikTok."""
    try:
        path = resolve_chrome_path(configured_path)
        with open_browser(path, proxy) as browser:
            return {"ok": True, "path": path, "version": browser.version(), "via_proxy": bool(proxy)}
    except BrowserError as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    """CLI cho Electron: `echo '{"chrome_path":"","proxy":""}' | python browser_cookie.py check` -> JSON kết quả `check_browser`."""
    import json
    import sys

    from system_api import use_utf8_stdio

    use_utf8_stdio()
    if len(sys.argv) < 2 or sys.argv[1] != "check":
        print("usage: browser_cookie.py check  (stdin JSON {chrome_path?, proxy?})", file=sys.stderr)
        sys.exit(2)
    cmd = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(check_browser(cmd.get("chrome_path") or None, cmd.get("proxy") or None), ensure_ascii=False))


if __name__ == "__main__":
    main()
