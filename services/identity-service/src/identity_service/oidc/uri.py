"""Kiểm tra URI của OIDC client và chống SSRF cho back-channel logout. Hàm thuần, không I/O ngoài DNS."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URI_LEN = 2048
_DEV_ENVS = ("dev", "test")
_METADATA_IPS = {ipaddress.ip_address(x) for x in ("169.254.169.254", "169.254.170.2", "100.100.100.200",
                                                   "fd00:ec2::254", "192.0.0.192")}


def is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_client_uri(uri: object, *, env: str, label: str = "URI") -> str:
    """Trả lại `uri` nếu hợp lệ cho redirect/logout/back-channel; ném ValueError(thông điệp tiếng Việt) nếu không.

    Luật: https, hoặc http tới loopback (chỉ ngoài production); không wildcard, không fragment, không userinfo,
    không ký tự điều khiển/khoảng trắng/backslash/%-encode trong host; không scheme lạ (`javascript:`, `data:`...).
    """
    if not isinstance(uri, str) or not uri:
        raise ValueError(f"{label} phải là chuỗi không rỗng")
    if len(uri) > MAX_URI_LEN:
        raise ValueError(f"{label} quá dài")
    if not uri.isascii() or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in uri) or "\\" in uri:
        raise ValueError(f"{label} chứa ký tự không hợp lệ")
    if "*" in uri:
        raise ValueError(f"{label} không được chứa wildcard")
    if "#" in uri:
        raise ValueError(f"{label} không được có fragment")
    if not (uri.startswith("https://") or uri.startswith("http://")):
        raise ValueError(f"{label} phải bắt đầu bằng https:// (hoặc http:// tới loopback ở dev)")
    try:
        parts = urlsplit(uri)
        _ = parts.port  # ném ValueError nếu cổng sai
    except ValueError:
        raise ValueError(f"{label} không phải URL hợp lệ") from None
    host = parts.hostname or ""
    if not host or "@" in parts.netloc or "%" in parts.netloc:
        raise ValueError(f"{label} phải có host và không được chứa userinfo")
    if parts.scheme == "http":
        if env == "production":
            raise ValueError(f"{label} phải dùng https:// ở production")
        if not is_loopback_host(host):
            raise ValueError(f"{label} http:// chỉ cho phép tới loopback")
    return uri


def validate_uri_list(values: object, *, env: str, label: str, min_items: int = 0, max_items: int = 20) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} phải là danh sách")
    if len(values) < min_items:
        raise ValueError(f"{label} cần ít nhất {min_items} giá trị")
    if len(values) > max_items:
        raise ValueError(f"{label} tối đa {max_items} giá trị")
    out: list[str] = []
    for v in values:
        v = validate_client_uri(v, env=env, label=label)
        if v not in out:
            out.append(v)
    return out


# ── SSRF ────────────────────────────────────────────────────────────────────────

class UnsafeTarget(Exception):
    """Địa chỉ đích bị chặn (không bao giờ kết nối)."""


def check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, env: str, allow_private: bool) -> None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:169.254.169.254 không được lách qua dạng IPv6
    if ip.is_loopback:  # xét TRƯỚC `is_reserved` vì ::1 nằm trong khối ::/8 "reserved" của IPv6
        if env not in _DEV_ENVS:
            raise UnsafeTarget("loopback chỉ được phép ở dev/test")
        return
    if ip in _METADATA_IPS or ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise UnsafeTarget("địa chỉ đích bị cấm (link-local/metadata/unspecified/multicast/reserved)")
    if not ip.is_global and not allow_private:
        raise UnsafeTarget("địa chỉ mạng nội bộ bị cấm")


@dataclass(frozen=True)
class SafeTarget:
    scheme: str
    host: str          # host gốc (dùng cho header Host và SNI)
    ip: str            # IP đã kiểm duyệt — kết nối thẳng tới IP này (chống DNS rebinding)
    port: int
    path_query: str


async def resolve_safe(url: str, *, env: str, allow_private: bool) -> SafeTarget:
    """Phân tích URL, resolve DNS, kiểm TẤT CẢ địa chỉ trả về; ném UnsafeTarget nếu bất kỳ địa chỉ nào bị cấm."""
    try:
        validate_client_uri(url, env=env, label="URL")
    except ValueError as exc:
        raise UnsafeTarget(str(exc)) from None
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise UnsafeTarget("không resolve được host") from None
        addresses = []
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip not in addresses:
                addresses.append(ip)
    if not addresses:
        raise UnsafeTarget("không resolve được host")
    for ip in addresses:
        check_ip(ip, env=env, allow_private=allow_private)
    if parts.scheme == "http" and not all(ip.is_loopback for ip in addresses):
        raise UnsafeTarget("http:// chỉ cho phép tới loopback")
    path_query = parts.path or "/"
    if parts.query:
        path_query += "?" + parts.query
    return SafeTarget(parts.scheme, host, str(addresses[0]), port, path_query)
