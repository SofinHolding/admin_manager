"""Danh sách NHIỀU YouTube API key / proxy người dùng nhập (mỗi dòng một mục): đọc, chuẩn hoá, che bí mật khi hiển thị và xoay vòng.

Thuần (không mạng, không DB) — có test: tests/test_pools.py. Kiểm tra sống/chết nằm ở `health.py`.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

# Khoá API YouTube (Google): "AIza" + 35 ký tự [A-Za-z0-9_-]
_YT_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")
_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$|^\[[0-9A-Fa-f:]+\]$")


def split_entries(text: str, *, also_commas: bool = False) -> list[str]:
    """Tách văn bản nhiều dòng thành các mục: bỏ dòng trống / dòng chú thích `#`, cắt khoảng trắng, bỏ trùng (giữ thứ tự).
    `also_commas`: coi dấu phẩy/chấm phẩy là dấu ngăn (an toàn với key; KHÔNG dùng cho proxy vì mật khẩu có thể chứa dấu phẩy)."""
    seps = r"[\r\n,;]+" if also_commas else r"[\r\n]+"
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(seps, text or ""):
        item = part.strip()
        if not item or item.startswith("#") or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def normalize_youtube_key(raw: str) -> str | None:
    key = (raw or "").strip()
    return key if _YT_KEY_RE.match(key) else None


def _valid_host_port(host: str, port: str) -> bool:
    return bool(host and _HOST_RE.match(host) and port.isdigit() and 1 <= int(port) <= 65535)


def normalize_proxy(raw: str) -> str | None:
    """Về dạng chuẩn `scheme://[user:pass@]host:port`; None nếu không đọc được.

    Chấp nhận: `scheme://[user:pass@]host:port` (http, https, socks4, socks5, socks5h), `user:pass@host:port`, `host:port`,
    `host:port:user:pass`. Không ghi scheme => `http`. Tài khoản/mật khẩu giữ nguyên (mã hoá % khi cần để URL hợp lệ)."""
    text = (raw or "").strip()
    if not text:
        return None
    scheme = "http"
    rest = text
    if "://" in text:
        scheme, rest = text.split("://", 1)
        scheme = scheme.lower()
        if scheme not in _PROXY_SCHEMES:
            return None
    user = password = ""
    host_port = rest
    if "@" in rest:
        creds, host_port = rest.rsplit("@", 1)
        user, _, password = creds.partition(":")
        user, password = unquote(user), unquote(password)
    else:
        parts = rest.split(":")
        if len(parts) == 4:               # host:port:user:pass
            host_port = f"{parts[0]}:{parts[1]}"
            user, password = parts[2], parts[3]
        elif len(parts) != 2:
            return None
    host, _, port = host_port.rpartition(":")
    if not _valid_host_port(host, port):
        return None
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    return f"{scheme}://{auth}{host}:{port}"


def parse_keys(text: str) -> tuple[list[str], list[str]]:
    """`(key hợp lệ, dòng không hợp lệ)` từ văn bản nhiều dòng."""
    good: list[str] = []
    bad: list[str] = []
    for item in split_entries(text, also_commas=True):
        key = normalize_youtube_key(item)
        (good if key else bad).append(key or item)
    return list(dict.fromkeys(good)), bad


def parse_proxies(text: str) -> tuple[list[str], list[str]]:
    """`(proxy chuẩn hoá, dòng không hợp lệ)`; hai dòng khác cách viết nhưng cùng proxy chỉ giữ một."""
    good: list[str] = []
    bad: list[str] = []
    for item in split_entries(text):
        proxy = normalize_proxy(item)
        (good if proxy else bad).append(proxy or item)
    return list(dict.fromkeys(good)), bad


def merge_entries(kind: str, existing: list[str], text: str) -> dict:
    """Thêm các dòng trong `text` vào danh sách `existing` (kind: "youtube-keys" | "proxies"). Trả `{items, added, duplicate, bad}`:
    mục trùng bị bỏ, dòng không hợp lệ nằm trong `bad` để giao diện giữ lại cho người dùng sửa."""
    good, bad = (parse_keys if kind == "youtube-keys" else parse_proxies)(text)
    have = set(existing)
    new = [g for g in good if g not in have]
    return {"items": [*existing, *new], "added": len(new), "duplicate": len(good) - len(new), "bad": bad}


def mask_key(key: str) -> str:
    return key if len(key) <= 10 else f"{key[:6]}…{key[-4:]}"


def mask_proxy(proxy: str) -> str:
    """Che mật khẩu: `socks5://user:***@host:port`."""
    p = urlsplit(proxy)
    if not p.username:
        return proxy
    return f"{p.scheme}://{unquote(p.username)}:***@{p.hostname}:{p.port}"


def force_remote_dns(proxy: str | None) -> str | None:
    """`socks5://` -> `socks5h://` để DNS đi QUA proxy (nhiều nhà cung cấp proxy xoay đòi vậy; resolve local rồi nhờ proxy kết nối
    tới IP đó dễ bị từ chối). Scheme khác giữ nguyên. Tắt bằng env `PROXY_FORCE_SOCKS5H=0`."""
    if not proxy:
        return None
    if proxy.startswith("socks5://") and (os.environ.get("PROXY_FORCE_SOCKS5H") or "1").strip() != "0":
        return "socks5h://" + proxy[len("socks5://"):]
    return proxy


class Pool:
    """Xoay vòng các mục khoẻ; mục bị báo lỗi nghỉ `cooldown_s` giây rồi được thử lại.

    `pick()` trả mục kế tiếp đang khoẻ (None nếu mọi mục đang nghỉ hoặc danh sách rỗng). Dùng chung cho YouTube key (hết quota) và
    proxy (lỗi kết nối)."""

    def __init__(self, items: list[str], *, cooldown_s: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._items = list(dict.fromkeys(items))
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._until: dict[str, float] = {}
        self._next = 0

    def __len__(self) -> int:
        return len(self._items)

    def healthy(self) -> list[str]:
        now = self._clock()
        return [i for i in self._items if self._until.get(i, 0.0) <= now]

    def pick(self) -> str | None:
        now = self._clock()
        n = len(self._items)
        for step in range(n):
            idx = (self._next + step) % n
            item = self._items[idx]
            if self._until.get(item, 0.0) <= now:
                self._next = (idx + 1) % n
                return item
        return None

    def bad(self, item: str, cooldown_s: float | None = None) -> None:
        if item in self._items:
            self._until[item] = self._clock() + (self._cooldown_s if cooldown_s is None else cooldown_s)

    def ok(self, item: str) -> None:
        self._until.pop(item, None)


def _env_list(*names: str) -> list[str]:
    for name in names:
        value = os.environ.get(name, "")
        if value.strip():
            return split_entries(value, also_commas=True)
    return []


def load_pools(settings_path: str | None = None) -> tuple[list[str], list[str]]:
    """`(youtube_api_keys, proxies)` từ file cấu hình của tool (`youtube_api_keys`, `proxies`), nạp thêm khoá/proxy cũ dạng một
    chuỗi (`youtube_api_key`, `proxy`) của bản trước, rồi biến môi trường `YOUTUBE_API_KEY(S)` / `PROXY` (chỉ khi file không có)."""
    data: dict = {}
    if settings_path and Path(settings_path).is_file():
        try:
            data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    def listed(new_key: str, legacy_key: str, *, commas: bool) -> list[str]:
        items = data.get(new_key)
        out = [str(i) for i in items] if isinstance(items, list) else []
        legacy = data.get(legacy_key)
        if isinstance(legacy, str) and legacy.strip():
            out += split_entries(legacy, also_commas=commas)   # mật khẩu proxy có thể chứa dấu phẩy: không tách proxy theo dấu phẩy
        return out

    keys = [k for k in (normalize_youtube_key(i) for i in listed("youtube_api_keys", "youtube_api_key", commas=True) or
                        _env_list("YOUTUBE_API_KEYS", "YOUTUBE_API_KEY")) if k]
    proxies = [p for p in (normalize_proxy(i) for i in listed("proxies", "proxy", commas=False) or _env_list("PROXIES", "PROXY")) if p]
    return list(dict.fromkeys(keys)), list(dict.fromkeys(proxies))
