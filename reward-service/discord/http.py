"""Client HTTP gọi Discord REST — dịch từ `src/discord/http.js:discordFetch`.

Giữ NGUYÊN thuật toán 429 (retry_after từ body → header → mặc định 5s; `wait = ceil(retry_after*1000)
+ 300 + retry*1000`; quá 10 lần → trả thẳng response 429) và cờ `retry_on_network_error` (mặc định
`True`, luồng reward luôn truyền `False` vì request không idempotent — xem `engine/executor_slash.py`,
`engine/confirmation.py`). Timeout theo pha (D.4): `httpx.Timeout(connect=5.0, write=10.0,
read=<REWARD_HTTP_TIMEOUT_S>, pool=5.0)`; `AsyncHTTPTransport(retries=0)` khai báo TƯỜNG MINH để
httpx không tự ý retry lỗi connect.

BẮT BUỘC: token Discord không bao giờ vào log hay message ngoại lệ của module này.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger("reward.discord.http")

_MAX_RATE_LIMIT_RETRIES = 10
_MAX_NETWORK_RETRIES = 5


class DiscordHttpClient:
    """Bọc `httpx.AsyncClient` với timeout theo pha + xử lý 429 nguyên văn thuật toán Node."""

    def __init__(self, *, read_timeout_s: float) -> None:
        transport = httpx.AsyncHTTPTransport(retries=0)
        timeout = httpx.Timeout(connect=5.0, write=10.0, read=read_timeout_s, pool=5.0)
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        retry_on_network_error: bool = True,
        headers: dict[str, str] | None = None,
        json: Any = None,
        _retry_count: int = 0,
    ) -> httpx.Response:
        """Gửi request; 429 tự retry (thuật toán gốc); lỗi mạng retry tối đa 5 lần nếu được phép."""
        try:
            response = await self._client.request(method, url, headers=headers, json=json)
        except httpx.HTTPError:
            if retry_on_network_error and _retry_count < _MAX_NETWORK_RETRIES:
                wait_s = 2 * (_retry_count + 1)
                logger.warning("[Network Error] Retrying in %ss...", wait_s)
                await asyncio.sleep(wait_s)
                return await self.request(
                    method, url, retry_on_network_error=retry_on_network_error,
                    headers=headers, json=json, _retry_count=_retry_count + 1,
                )
            raise

        if response.status_code == 429:
            if _retry_count > _MAX_RATE_LIMIT_RETRIES:
                logger.error("[Rate Limit] Exceeded max retries (10) for %s. Proceeding with rate limit failure.", url)
                return response

            retry_after = 5.0
            try:
                data = response.json()
                if isinstance(data, dict) and data.get("retry_after"):
                    retry_after = float(data["retry_after"])
            except Exception:  # noqa: BLE001 — body không parse được → thử header
                header_val = response.headers.get("retry-after")
                if header_val:
                    try:
                        retry_after = float(header_val)
                    except ValueError:
                        pass

            wait_ms = math.ceil(retry_after * 1000) + 300 + _retry_count * 1000
            if wait_ms <= 0:
                wait_ms = 5000 + _retry_count * 1000

            logger.warning("[Rate Limit] Discord returned 429. Retry #%s. Sleeping for %sms...", _retry_count + 1, wait_ms)
            await asyncio.sleep(wait_ms / 1000)
            return await self.request(
                method, url, retry_on_network_error=retry_on_network_error,
                headers=headers, json=json, _retry_count=_retry_count + 1,
            )

        return response
