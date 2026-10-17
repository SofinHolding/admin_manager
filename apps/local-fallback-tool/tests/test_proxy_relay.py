"""Relay cục bộ: Chrome không biết proxy có mật khẩu, nên relay không-mật-khẩu đứng giữa và tự xác thực với proxy thật."""
from __future__ import annotations

import base64
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_cookie  # noqa: E402
import proxy_relay  # noqa: E402


class FakeUpstream:
    """Proxy "thật" giả lập: ghi lại tài khoản + đích nhận được, rồi echo dữ liệu."""

    def __init__(self, kind: str, accept_auth: bool = True) -> None:
        self.kind, self.accept_auth = kind, accept_auth
        self.seen: dict = {}
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._socks if self.kind == "socks5" else self._http, args=(conn,), daemon=True).start()

    def _socks(self, c: socket.socket) -> None:
        with c:
            ver, n = c.recv(2)
            c.recv(n)
            c.sendall(bytes([5, 2]))
            head = c.recv(2)
            user = c.recv(head[1]).decode()
            plen = c.recv(1)[0]
            pw = c.recv(plen).decode()
            self.seen["auth"] = (user, pw)
            if not self.accept_auth:
                c.sendall(bytes([1, 1]))
                return
            c.sendall(bytes([1, 0]))
            req = c.recv(4)
            if req[3] == 3:
                ln = c.recv(1)[0]
                host = c.recv(ln).decode()
            else:
                host = socket.inet_ntoa(c.recv(4))
            port = int.from_bytes(c.recv(2), "big")
            self.seen["target"] = (host, port)
            c.sendall(bytes([5, 0, 0, 1, 0, 0, 0, 0, 0, 0]))
            data = c.recv(100)
            c.sendall(b"echo:" + data)

    def _http(self, c: socket.socket) -> None:
        with c:
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += c.recv(4096)
            self.seen["head"] = buf.decode()
            if buf.startswith(b"CONNECT"):
                c.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                c.sendall(b"echo:" + c.recv(100))
            else:
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")


@pytest.fixture
def relay_for():
    made: list = []

    def make(cls, upstream: FakeUpstream, user="alice", pw="p@ss"):
        relay = cls("127.0.0.1", upstream.port, user, pw)
        relay.start()
        made.append((relay, upstream))
        return relay
    yield make
    for relay, up in made:
        relay.stop()
        up.sock.close()


def _recv_until(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def test_socks5_relay_authenticates_upstream_and_carries_traffic(relay_for):
    up = FakeUpstream("socks5")
    relay = relay_for(proxy_relay.Socks5AuthRelay, up)
    with socket.create_connection(("127.0.0.1", relay.local_port), timeout=5) as s:
        s.sendall(bytes([5, 1, 0]))                              # Chrome: chỉ biết "không xác thực"
        assert s.recv(2) == bytes([5, 0])
        s.sendall(bytes([5, 1, 0, 3, 12]) + b"example.test" + (443).to_bytes(2, "big"))
        assert _recv_until(s, 10)[1] == 0                        # CONNECT thành công
        s.sendall(b"ping")
        assert _recv_until(s, 9) == b"echo:ping"
    assert up.seen["auth"] == ("alice", "p@ss") and up.seen["target"] == ("example.test", 443)


def test_socks5_relay_reports_failure_when_upstream_rejects_credentials(relay_for):
    up = FakeUpstream("socks5", accept_auth=False)
    relay = relay_for(proxy_relay.Socks5AuthRelay, up)
    with socket.create_connection(("127.0.0.1", relay.local_port), timeout=5) as s:
        s.sendall(bytes([5, 1, 0]))
        s.recv(2)
        s.sendall(bytes([5, 1, 0, 3, 12]) + b"example.test" + (443).to_bytes(2, "big"))
        assert _recv_until(s, 10)[1] != 0                        # Chrome nhận lỗi, không bị treo


def test_http_relay_adds_proxy_authorization_for_connect_and_plain_requests(relay_for):
    up = FakeUpstream("http")
    relay = relay_for(proxy_relay.HttpAuthRelay, up)
    token = base64.b64encode(b"alice:p@ss").decode()
    with socket.create_connection(("127.0.0.1", relay.local_port), timeout=5) as s:
        s.sendall(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n")
        assert b"200" in s.recv(100)
        s.sendall(b"hello")
        assert _recv_until(s, 10) == b"echo:hello"
    assert f"Proxy-Authorization: Basic {token}" in up.seen["head"]

    up2 = FakeUpstream("http")
    relay2 = relay_for(proxy_relay.HttpAuthRelay, up2)
    with socket.create_connection(("127.0.0.1", relay2.local_port), timeout=5) as s:
        s.sendall(b"GET http://example.test/ HTTP/1.1\r\nHost: example.test\r\n\r\n")
        assert b"ok" in s.recv(200)
    assert f"Proxy-Authorization: Basic {token}" in up2.seen["head"]


# ── Cờ --proxy-server cho Chrome ─────────────────────────────────────────────────────────

def test_no_proxy_means_no_flag():
    assert browser_cookie.proxy_server_flag(None) == (None, None)


def test_passwordless_proxy_goes_straight_to_chrome_with_socks5h_collapsed():
    assert browser_cookie.proxy_server_flag("socks5h://1.2.3.4:1080") == ("socks5://1.2.3.4:1080", None)
    assert browser_cookie.proxy_server_flag("http://1.2.3.4:3128") == ("http://1.2.3.4:3128", None)


def test_proxy_with_password_is_replaced_by_a_local_relay_that_hides_the_password():
    flag, relay = browser_cookie.proxy_server_flag("socks5://alice:p%40ss@1.2.3.4:1080")
    try:
        assert flag == f"socks5://127.0.0.1:{relay.local_port}" and "alice" not in flag and "p@ss" not in flag
    finally:
        relay.stop()


def test_unsupported_scheme_with_password_is_a_clear_error():
    with pytest.raises(browser_cookie.BrowserError):
        browser_cookie.proxy_server_flag("socks4://u:p@1.2.3.4:1080")


def test_only_tiktok_cookies_are_kept():
    raw = [{"name": "ttwid", "value": "1", "domain": ".tiktok.com", "path": "/"}, {"name": "x", "value": "2", "domain": ".google.com"},
           {"name": "", "value": "3", "domain": ".tiktok.com"}]
    assert [c["name"] for c in browser_cookie._tiktok_cookies(raw)] == ["ttwid"]
