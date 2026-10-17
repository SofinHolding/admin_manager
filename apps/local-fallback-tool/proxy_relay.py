"""Relay proxy cục bộ KHÔNG cần xác thực, tự xác thực với proxy THẬT có tài khoản/mật khẩu — để Chrome dùng được proxy có mật khẩu.

Vì sao cần:
- SOCKS5: Chrome/Chromium không bao giờ tự xác thực username/password cho SOCKS5 (giới hạn của Chromium; CDP `Fetch.authRequired` chỉ áp dụng
  cho proxy HTTP). Port từ youtube_video `app/sources/socks5_relay.py` (đã kiểm chứng thật trên proxy SOCKS5 có mật khẩu).
- HTTP/HTTPS: tránh phải bắt `Fetch.authRequired` bất đồng bộ trên socket CDP; relay thêm sẵn `Proxy-Authorization: Basic ...`.

Chrome trỏ vào `127.0.0.1:<local_port>`; relay đứng giữa. yt-dlp / httpx / curl_cffi tự xác thực proxy nên KHÔNG cần relay.
"""
from __future__ import annotations

import base64
import logging
import socket
import threading

logger = logging.getLogger("collect")

_VER = 0x05
_AUTH_NONE, _AUTH_USERPASS = 0x00, 0x02
_CMD_CONNECT = 0x01
_ATYP_IPV4, _ATYP_DOMAIN, _ATYP_IPV6 = 0x01, 0x03, 0x04
_CONNECT_TIMEOUT_S = 5.0
_ACCEPT_POLL_S = 0.5
_MAX_HEAD = 65536


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _bridge(a: socket.socket, b: socket.socket) -> None:
    """Chuyển dữ liệu hai chiều cho tới khi một phía đóng."""
    a.settimeout(None)
    b.settimeout(None)
    t = threading.Thread(target=_pump, args=(a, b), daemon=True)
    t.start()
    _pump(b, a)
    t.join(timeout=5)


class _LocalRelay:
    """Khung chung: mở cổng cục bộ ngẫu nhiên, mỗi kết nối một luồng; lớp con cài `_handle`."""

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self._upstream = (host, port)
        self._username, self._password = username, password
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self.local_port = 0

    def start(self) -> int:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self._server.settimeout(_ACCEPT_POLL_S)
        self.local_port = int(self._server.getsockname()[1])
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.local_port

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._safe_handle, args=(client,), daemon=True).start()

    def _safe_handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(_CONNECT_TIMEOUT_S)
            upstream = self._handle(client)
        except OSError as exc:
            logger.debug("Relay proxy: kết nối lỗi (bỏ qua): %s", exc)
        finally:
            for s in (client, upstream):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _handle(self, client: socket.socket) -> socket.socket | None:   # pragma: no cover — lớp con
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass


# ── SOCKS5 ───────────────────────────────────────────────────────────────────────────────

def auth_handshake(sock: socket.socket, username: str, password: str, timeout: float = _CONNECT_TIMEOUT_S) -> bool:
    """Bắt tay xác thực username/password (RFC 1929) với server SOCKS5 trên `sock` đã kết nối TCP."""
    sock.settimeout(timeout)
    sock.sendall(bytes([_VER, 1, _AUTH_USERPASS]))
    method = _recv_exact(sock, 2)
    if method is None or method[0] != _VER or method[1] != _AUTH_USERPASS:
        return False
    u, p = username.encode("utf-8"), password.encode("utf-8")
    sock.sendall(bytes([0x01, len(u)]) + u + bytes([len(p)]) + p)
    resp = _recv_exact(sock, 2)
    return resp is not None and resp[1] == 0x00


def _read_connect_request(sock: socket.socket) -> bytes | None:
    """Đọc trọn một yêu cầu CONNECT từ Chrome; trả bytes thô để chuyển y hệt cho upstream."""
    header = _recv_exact(sock, 4)
    if header is None or header[0] != _VER or header[1] != _CMD_CONNECT:
        return None
    atyp = header[3]
    if atyp == _ATYP_IPV4:
        addr = _recv_exact(sock, 4)
    elif atyp == _ATYP_IPV6:
        addr = _recv_exact(sock, 16)
    elif atyp == _ATYP_DOMAIN:
        length = _recv_exact(sock, 1)
        domain = None if length is None else _recv_exact(sock, length[0])
        addr = None if domain is None else length + domain
    else:
        return None
    port = None if addr is None else _recv_exact(sock, 2)
    return None if port is None else header + addr + port


def _upstream_connect(upstream: socket.socket, username: str, password: str, request: bytes) -> bool:
    """Xác thực + gửi CONNECT; đọc hết BND.ADDR/BND.PORT để không lẫn vào luồng dữ liệu."""
    if not auth_handshake(upstream, username, password):
        return False
    upstream.sendall(request)
    head = _recv_exact(upstream, 4)
    if head is None or head[1] != 0x00:
        return False
    if head[3] == _ATYP_IPV4:
        rest = _recv_exact(upstream, 6)
    elif head[3] == _ATYP_IPV6:
        rest = _recv_exact(upstream, 18)
    elif head[3] == _ATYP_DOMAIN:
        length = _recv_exact(upstream, 1)
        rest = None if length is None else _recv_exact(upstream, length[0] + 2)
    else:
        return False
    return rest is not None


class Socks5AuthRelay(_LocalRelay):
    def _handle(self, client: socket.socket) -> socket.socket | None:
        greeting = _recv_exact(client, 2)
        if greeting is None or greeting[0] != _VER or (greeting[1] and _recv_exact(client, greeting[1]) is None):
            return None
        client.sendall(bytes([_VER, _AUTH_NONE]))                      # phía Chrome luôn no-auth
        request = _read_connect_request(client)
        if request is None:
            return None
        upstream = socket.create_connection(self._upstream, timeout=_CONNECT_TIMEOUT_S)
        if not _upstream_connect(upstream, self._username, self._password, request):
            client.sendall(bytes([_VER, 0x01, 0x00, _ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
            return upstream
        client.sendall(bytes([_VER, 0x00, 0x00, _ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
        _bridge(client, upstream)
        return upstream


# ── HTTP / HTTPS ─────────────────────────────────────────────────────────────────────────

def _read_head(sock: socket.socket) -> bytes | None:
    """Đọc tới hết `\\r\\n\\r\\n` (đầu yêu cầu/phản hồi HTTP); dữ liệu thừa phía sau (nếu có) vẫn nằm trong kết quả."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk or len(buf) > _MAX_HEAD:
            return None
        buf += chunk
    return buf


class HttpAuthRelay(_LocalRelay):
    """Proxy HTTP cục bộ: thêm `Proxy-Authorization: Basic` rồi chuyển lên proxy HTTP thật (CONNECT cho HTTPS)."""

    def _auth_header(self) -> bytes:
        token = base64.b64encode(f"{self._username}:{self._password}".encode("utf-8"))
        return b"Proxy-Authorization: Basic " + token

    def _handle(self, client: socket.socket) -> socket.socket | None:
        head = _read_head(client)
        if head is None:
            return None
        first, _, rest = head.partition(b"\r\n")
        method, target = first.split(b" ")[0], first.split(b" ")[1] if first.count(b" ") >= 2 else b""
        upstream = socket.create_connection(self._upstream, timeout=_CONNECT_TIMEOUT_S)
        if method.upper() == b"CONNECT":
            upstream.sendall(b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n" + self._auth_header() + b"\r\n\r\n")
            reply = _read_head(upstream)
            if reply is None or b" 200" not in reply.split(b"\r\n", 1)[0]:
                client.sendall(reply.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n" if reply else b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return upstream
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _bridge(client, upstream)
            return upstream
        upstream.sendall(first + b"\r\n" + self._auth_header() + b"\r\n" + rest)   # HTTP thường: chèn header rồi chuyển tiếp
        _bridge(client, upstream)
        return upstream
