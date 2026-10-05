"""Client gọi API CÔNG KHAI của Identity (outbound): proxy auth, danh sách user, cấp/thu permission.

Quy tắc outbound của platform: timeout ngắn, retry có backoff mũ + jitter với số lần GIỚI HẠN, lỗi nguồn ⇒
`IdentityUnavailable` (router đổi thành 503 `UNAVAILABLE`). KHÔNG BAO GIỜ log mật khẩu/token/Authorization.

Retry an toàn: GET/PUT/DELETE (idempotent) thử lại với lỗi mạng, timeout, 502/503/504. POST (login/refresh/logout)
chỉ thử lại khi request CHƯA tới Identity (lỗi kết nối) hoặc Identity trả 503 — tránh gửi đôi thao tác có tác dụng phụ.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

from manager_sdk import backoff_delays

logger = logging.getLogger("reward.identity")

REWARD_ACCESS = "reward:access"
_RETRY_STATUS = frozenset({502, 503, 504})


class IdentityUnavailable(Exception):
    """Identity không trả lời được sau khi đã thử lại (chết, quá chậm, 5xx liên tục)."""


class IdentityClient:
    def __init__(
        self, base_url: str, *, timeout: float = 5.0, retries: int = 2, backoff_base: float = 0.3,
        http_client: httpx.AsyncClient | None = None, sleep=asyncio.sleep,
    ) -> None:
        self._base = base_url.rstrip("/")
        # Kết nối tối đa 1.5s: Identity chết (cổng đóng) không được làm request của người dùng chờ lâu.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 1.5))
        self._retries = max(0, retries)
        self._backoff_base = backoff_base
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http_client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def request(
        self, method: str, path: str, *, authorization: str | None = None, json: Any = None,
        real_ip: str | None = None, extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = dict(extra_headers or {})
        if authorization:
            headers["Authorization"] = authorization
        if real_ip:
            headers["X-Real-IP"] = real_ip
        idempotent = method.upper() in ("GET", "PUT", "DELETE", "HEAD")
        delays = list(backoff_delays(self._retries, base=self._backoff_base, cap=5.0))
        last = "chưa thử"
        for attempt in range(self._retries + 1):
            try:
                response = await self._http.request(
                    method, self._base + path, json=json, headers=headers, timeout=self._timeout)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = type(exc).__name__  # request chưa tới Identity ⇒ luôn an toàn để thử lại
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                if not idempotent:
                    break
            else:
                if response.status_code in _RETRY_STATUS and (idempotent or response.status_code == 503):
                    last = f"HTTP {response.status_code}"
                else:
                    return response
            if attempt < self._retries:
                await self._sleep(delays[attempt])
        logger.warning("Identity không khả dụng: %s %s (%s)", method, path.split("?")[0], last)
        raise IdentityUnavailable(f"Identity không khả dụng ({last})")

    # ── Thao tác mức cao ────────────────────────────────────────────────────────

    async def list_users(self, authorization: str) -> httpx.Response:
        return await self.request("GET", "/v1/admin/users", authorization=authorization)

    async def set_permission(
        self, authorization: str, account_id: str, permission: str, *, grant: bool,
    ) -> httpx.Response:
        path = f"/v1/admin/accounts/{quote(account_id, safe='')}/permissions/{quote(permission, safe=':')}"
        return await self.request("PUT" if grant else "DELETE", path, authorization=authorization)
