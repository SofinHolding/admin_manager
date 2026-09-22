"""Fake Discord đầy đủ cho luồng slash (offline, `127.0.0.1`) — port từ `test/reward/helpers/fake.js`.

REST (FastAPI qua `uvicorn.Server`): `GET /guilds/{id}/application-command-index`,
`POST /interactions` (theo `interaction_plan`: mặc định `204`, số nguyên = status lỗi, chuỗi `'hang'`
= treo mãi mãi để test timeout), `GET /channels/{id}/messages` (đọc `store` nội bộ, hỗ trợ `after`).
Gateway (`websockets.serve`): `HELLO` (`op10`) → nhận `IDENTIFY` (`op2`) → trả `READY` kèm
`session_id` → `op8` `REQUEST_GUILD_MEMBERS` → trả `GUILD_MEMBERS_CHUNK` từ `members` (khoá theo
`query` hạ thường). Không có test nào của reward-service gọi Discord thật — chỉ `127.0.0.1`.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import uvicorn
import websockets
from fastapi import FastAPI, Request, Response

APP_ID = "app1"


@dataclass
class FakeDiscord:
    interaction_plan: list[Any] | None = None
    reply_plan: list[dict[str, Any] | None] | None = None
    members: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    command_name: str = "give-xp"
    on_request: Callable[[str, str], None] | None = None
    valid_tokens: dict[str, dict[str, Any]] | None = None
    """`None` ⇒ MỌI token coi là hợp lệ (trả `{"id":"u1","username":"tester"}`). Cung cấp dict ⇒
    CHỈ token có mặt trong dict mới `200`, còn lại `401` — dùng để test `credentials` verify."""

    def __post_init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []
        self.interaction_tokens: list[str] = []
        self.gets: list[str] = []
        self.store: list[dict[str, Any]] = []
        self.app_id = APP_ID
        self.api_base = ""
        self.gateway_url = ""
        self._seq = itertools.count(7000)
        self._ok_count = 0
        self._app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._ws_server: Any = None

    def _command(self) -> dict[str, Any]:
        return {
            "id": "c1", "version": "v1", "application_id": APP_ID, "name": self.command_name,
            "guild_id": "G", "options": [{"name": "member", "type": 6}, {"name": "amount", "type": 4}],
        }

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/users/@me")
        async def users_me(request: Request) -> Response:
            if self.on_request:
                self.on_request("GET", "/users/@me")
            token = request.headers.get("authorization") or ""
            if self.valid_tokens is not None:
                info = self.valid_tokens.get(token)
                if info is None:
                    return Response(
                        status_code=401, media_type="application/json",
                        content=json.dumps({"message": "401: Unauthorized"}))
                return Response(status_code=200, media_type="application/json", content=json.dumps(info))
            return Response(
                status_code=200, media_type="application/json",
                content=json.dumps({"id": "u1", "username": "tester"}))

        @app.get("/guilds/{guild_id}/application-command-index")
        async def command_index(guild_id: str) -> dict[str, Any]:
            if self.on_request:
                self.on_request("GET", "/guilds/.../application-command-index")
            return {"application_commands": [self._command()]}

        @app.post("/interactions")
        async def interactions(request: Request) -> Response:
            if self.on_request:
                self.on_request("POST", "/interactions")
            self.interaction_tokens.append(request.headers.get("authorization") or "")
            payload = await request.json()
            self.interactions.append(payload)
            idx = len(self.interactions) - 1
            plan: Any = 204
            if self.interaction_plan:
                plan = self.interaction_plan[min(idx, len(self.interaction_plan) - 1)]
            if plan == "hang":
                await asyncio.sleep(3600)
                return Response(status_code=204)
            if isinstance(plan, int) and plan != 204:
                return Response(
                    status_code=plan, media_type="application/json",
                    content=json.dumps({"message": f"err {plan}"}))

            options = {o["name"]: o["value"] for o in payload["data"]["options"]}
            member = options.get("member")
            amount = options.get("amount")
            rp = self.reply_plan[self._ok_count] if self.reply_plan and self._ok_count < len(self.reply_plan) else {"ok": True}
            self._ok_count += 1
            if rp:
                rid = str(next(self._seq))
                content = rp.get("text") or f"\u2705 {amount} XP has been given to <@!{member}>"
                self.store.append({
                    "id": rid, "author": {"bot": True, "id": rp.get("authorId") or APP_ID},
                    "content": "", "embeds": [{"description": content}],
                })
            return Response(status_code=204)

        @app.get("/channels/{channel_id}/messages")
        async def messages(channel_id: str, request: Request) -> list[dict[str, Any]]:
            if self.on_request:
                self.on_request("GET", f"/channels/{channel_id}/messages")
            self.gets.append(str(request.url.query))
            after = request.query_params.get("after")
            msgs = self.store
            if after and after != "0":
                msgs = [m for m in msgs if int(m["id"]) > int(after)]
            return list(reversed(msgs))

        return app

    def interactions_for(self, user_id: str) -> list[dict[str, Any]]:
        out = []
        for p in self.interactions:
            for o in p["data"]["options"]:
                if o["name"] == "member" and o["value"] == user_id:
                    out.append(p)
        return out

    async def _gateway_handler(self, ws: Any) -> None:
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 45000}}))
        async for raw in ws:
            try:
                m = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if m.get("op") == 2:
                await ws.send(json.dumps({
                    "op": 0, "s": 1, "t": "READY", "d": {"session_id": "sess1", "user": {"id": "self"}},
                }))
            elif m.get("op") == 8:
                q = (m.get("d", {}).get("query") or "").lower()
                await ws.send(json.dumps({
                    "op": 0, "t": "GUILD_MEMBERS_CHUNK",
                    "d": {"guild_id": m["d"]["guild_id"], "members": self.members.get(q, []), "not_found": []},
                }))

    async def start(self) -> None:
        config = uvicorn.Config(self._app, host="127.0.0.1", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        sock = config.bind_socket()
        rest_port = sock.getsockname()[1]
        self._server_task = asyncio.create_task(self._server.serve(sockets=[sock]))
        while not self._server.started:
            await asyncio.sleep(0.01)

        self._ws_server = await websockets.serve(self._gateway_handler, "127.0.0.1", 0)
        ws_port = self._ws_server.sockets[0].getsockname()[1]

        self.api_base = f"http://127.0.0.1:{rest_port}"
        self.gateway_url = f"ws://127.0.0.1:{ws_port}/"

    async def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=5)
            except Exception:  # noqa: BLE001 — dọn tài nguyên test, không quan trọng lý do
                self._server_task.cancel()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()


async def start_fake(**kwargs: Any) -> FakeDiscord:
    fake = FakeDiscord(**kwargs)
    await fake.start()
    return fake
