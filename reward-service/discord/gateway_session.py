"""Gateway WebSocket bằng USER token — dịch từ `src/discord/gateway-session.js`.

Dùng để (1) lấy `session_id` cho `POST /interactions`, (2) tra thành viên qua `op 8`
`REQUEST_GUILD_MEMBERS` — cách DUY NHẤT resolve username bằng user token (REST `members/search`
cần bot token). IDENTIFY giữ nguyên `capabilities=30717` + properties Windows y hệt bản Node. Không
RESUME — mất kết nối thì IDENTIFY lại từ đầu. Ghép `GUILD_MEMBERS_CHUNK` theo FIFO (mỗi `op 8` một
hàng đợi, khớp thứ tự gửi — tương đương `memberWaiters.shift()` của Node).

BẮT BUỘC: token không bao giờ vào log/`repr`/message ngoại lệ của module này.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger("reward.discord.gateway")

_CONNECT_TIMEOUT_S = 15.0


class GatewaySession:
    def __init__(self, *, gateway_url: str, token: str) -> None:
        self._gateway_url = gateway_url
        self._token = token
        self._ws: Any = None
        self._session_id: str | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._ready_error: Exception | None = None
        self._member_waiters: list[asyncio.Queue] = []
        self._connect_lock = asyncio.Lock()

    def __repr__(self) -> str:  # token KHÔNG BAO GIỜ vào repr
        return f"GatewaySession(gateway_url={self._gateway_url!r}, connected={self._ws is not None})"

    def _identify_payload(self) -> str:
        return json.dumps({
            "op": 2,
            "d": {
                "token": self._token,
                "capabilities": 30717,
                "properties": {
                    "os": "Windows", "browser": "Discord Client", "release_channel": "stable",
                    "client_version": "1.0.9160", "os_version": "10.0.19045",
                    "os_arch": "x64", "system_locale": "en-US",
                },
                "presence": {"status": "online", "since": 0, "activities": [], "afk": False},
            },
        })

    async def _heartbeat_loop(self, interval_ms: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_ms / 1000)
                if self._ws is not None:
                    await self._ws.send(json.dumps({"op": 1, "d": None}))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — socket đóng giữa lúc heartbeat: không phải lỗi cần nổ
            pass

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                await self._handle(msg)
        except Exception as exc:  # noqa: BLE001 — kết nối đóng bất kỳ lý do gì → dọn state
            if not self._ready_event.is_set():
                self._ready_error = exc
                self._ready_event.set()
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self._session_id = None
            self._ws = None

    async def _handle(self, msg: dict) -> None:
        op = msg.get("op")
        if op == 10:
            interval = msg["d"]["heartbeat_interval"]
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))
            await self._ws.send(self._identify_payload())
        elif msg.get("t") == "READY":
            self._session_id = msg["d"]["session_id"]
            self._ready_event.set()
        elif msg.get("t") == "GUILD_MEMBERS_CHUNK":
            if self._member_waiters:
                waiter = self._member_waiters.pop(0)
                await waiter.put(msg["d"].get("members") or [])

    async def _connect(self) -> str:
        if self._session_id is not None and self._ws is not None:
            return self._session_id
        async with self._connect_lock:
            if self._session_id is not None and self._ws is not None:
                return self._session_id
            self._ready_event = asyncio.Event()
            self._ready_error = None
            self._ws = await websockets.connect(self._gateway_url, ping_interval=None, max_size=None)
            self._recv_task = asyncio.create_task(self._recv_loop())
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=_CONNECT_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("Gateway connection timed out after 15s.") from exc
            if self._ready_error is not None:
                raise self._ready_error
            assert self._session_id is not None
            return self._session_id

    async def get_session_id(self) -> str:
        return await self._connect()

    async def request_guild_members(
        self, *, guild_id: str, query: str, limit: int = 5, timeout_s: float = 8.0,
    ) -> list[dict]:
        """Tra thành viên bằng `op 8` (user token). Trả mảng member (rỗng nếu timeout/không có)."""
        await self._connect()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._member_waiters.append(queue)
        await self._ws.send(json.dumps({
            "op": 8, "d": {"guild_id": guild_id, "query": query, "limit": limit, "presences": False},
        }))
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                self._member_waiters.remove(queue)
            except ValueError:
                pass
            return []

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self._recv_task is not None:
            self._recv_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
        self._session_id = None
