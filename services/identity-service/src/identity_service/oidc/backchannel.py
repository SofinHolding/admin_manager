"""OIDC Back-Channel Logout 1.0: thu hồi phiên => POST `logout_token` (JWT RS256) tới `backchannel_logout_uri` của RP.

Kiến trúc: worker nền đọc outbox của CHÍNH identity theo con trỏ riêng (`oidc_cursor`), mở rộng mỗi
`session.revoked` thành các dòng giao hàng (client, sid) trong `oidc_backchannel_deliveries` (bền, unique), rồi giao:
  * retry backoff mũ + jitter, giới hạn số lần, hết lượt => `dead` (dead-letter), giữ lại để điều tra;
  * mỗi lần giao độc lập nhau: một receiver chết/chậm KHÔNG chặn client khác và KHÔNG làm worker crash;
  * chống SSRF (xem `oidc/uri.py`): chỉ https, hoặc http tới loopback ở dev/test; cấm link-local/metadata/unspecified;
    kết nối thẳng tới IP đã kiểm (chống DNS rebinding); KHÔNG theo redirect; timeout ngắn.
Không bao giờ log logout_token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from identity_service.config import Settings
from identity_service.db import Database
from identity_service.oidc import repository as repo
from identity_service.oidc.uri import UnsafeTarget, resolve_safe
from identity_service.outbox import repository as outbox
from identity_service.security import utcnow
from identity_service.tokens.service import TokenService

logger = logging.getLogger("identity.oidc.backchannel")

LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
LOGOUT_TOKEN_TTL_S = 120
_LEASE_S = 60
_BATCH = 50
_PARALLEL = 10


class DeliveryError(Exception):
    """Lần giao thất bại có thể thử lại."""


class BackchannelWorker:
    def __init__(self, db: Database, settings: Settings, tokens: TokenService, *,
                 now: Callable[[], datetime] = utcnow, rand: Callable[[], float] = random.random) -> None:
        self.db = db
        self.settings = settings
        self.tokens = tokens
        self.now = now
        self._rand = rand

    # ── bước 1: outbox -> hàng đợi giao ────────────────────────────────────────

    async def fan_out(self) -> int:
        enqueued = 0
        async with self.db.transaction() as conn:
            cursor = await repo.cursor_get(conn)  # FOR UPDATE: nhiều instance không mở rộng trùng một event
            events = await outbox.read(conn, after=cursor, limit=200, types=["session.revoked"])
            for ev in events:
                data = ev["data"] if isinstance(ev["data"], dict) else json.loads(ev["data"])
                targets = await repo.targets_for_session_revoked(conn, data.get("account_id", ""), data.get("sid"))
                for t in targets:
                    await repo.delivery_enqueue(conn, event_id=ev["id"], client_id=t["client_id"],
                                                account_id=t["account_id"], sid=t["sid"], now=self.now())
                    enqueued += 1
            if events:
                await repo.cursor_set(conn, events[-1]["seq"])
        return enqueued

    # ── bước 2: giao ───────────────────────────────────────────────────────────

    async def build_logout_token(self, client_id: str, account_id: str, sid: str) -> str:
        iat = int(time.time())
        return await self.tokens.sign(
            {"iss": self.settings.issuer, "aud": client_id, "iat": iat, "exp": iat + LOGOUT_TOKEN_TTL_S,
             "jti": uuid.uuid4().hex, "sub": account_id, "sid": sid, "events": {LOGOUT_EVENT: {}}},
            typ="logout+jwt")  # KHÔNG có `nonce` (OIDC Back-Channel Logout §2.4)

    async def _post(self, url: str, logout_token: str) -> None:
        s = self.settings
        target = await resolve_safe(url, env=s.env, allow_private=s.backchannel_allow_private)
        ip = f"[{target.ip}]" if ":" in target.ip else target.ip
        default_port = 443 if target.scheme == "https" else 80
        host_header = target.host if target.port == default_port else f"{target.host}:{target.port}"
        extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
        headers = {"Host": host_header, "Content-Type": "application/x-www-form-urlencoded",
                   "Cache-Control": "no-store", "User-Agent": "identity-service-backchannel/1"}
        timeout = httpx.Timeout(s.backchannel_timeout_s)
        try:
            # Kết nối thẳng tới IP đã kiểm (không resolve lại); SNI/Host vẫn là host gốc. Không proxy, không redirect.
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as http:
                async with http.stream("POST", f"{target.scheme}://{ip}:{target.port}{target.path_query}",
                                       content=urlencode({"logout_token": logout_token}), headers=headers,
                                       extensions=extensions) as resp:
                    status = resp.status_code
        except httpx.HTTPError as exc:
            raise DeliveryError(f"{type(exc).__name__}") from None
        if not 200 <= status < 300:
            raise DeliveryError(f"HTTP {status}")

    def _backoff(self, attempts: int) -> timedelta:
        s = self.settings
        delay = min(s.backchannel_backoff_cap_s, s.backchannel_backoff_base_s * (2 ** attempts))
        return timedelta(seconds=delay * (0.5 + self._rand() / 2))  # jitter 50..100%

    async def _deliver_one(self, d: dict) -> str:
        """Trả 'delivered' | 'retry' | 'dead'. Không ném lỗi ra ngoài."""
        error = ""
        dead = False
        try:
            async with self.db.connection() as conn:
                client = await repo.client_get(conn, d["client_id"])
            if client is None or not client["backchannel_logout_uri"]:
                raise UnsafeTarget("client không còn backchannel_logout_uri")
            token = await self.build_logout_token(d["client_id"], d["account_id"], d["sid"])
            await self._post(client["backchannel_logout_uri"], token)
        except UnsafeTarget as exc:
            error, dead = f"ssrf_blocked: {exc}", True   # cấu hình sai/độc hại: không thử lại
        except DeliveryError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — một receiver/DB lỗi không được làm sập worker
            error = f"{type(exc).__name__}"
            logger.warning("Giao logout_token lỗi bất ngờ: client=%s %s", d["client_id"], error)
        now = self.now()
        async with self.db.transaction() as conn:
            if not error:
                await repo.delivery_done(conn, d["id"], now)
                return "delivered"
            attempts = d["attempts"] + 1
            dead = dead or attempts >= self.settings.backchannel_max_attempts
            await repo.delivery_failed(conn, d["id"], error=error, next_attempt_at=now + self._backoff(d["attempts"]),
                                       dead=dead)
        if dead:
            logger.error("Back-channel logout bị dead-letter: client=%s sid=%s lỗi=%s", d["client_id"], d["sid"], error)
        return "dead" if dead else "retry"

    async def deliver_due(self) -> dict[str, int]:
        now = self.now()
        async with self.db.transaction() as conn:
            due = await repo.deliveries_lease(conn, now=now, lease_until=now + timedelta(seconds=_LEASE_S),
                                              limit=_BATCH)
        stats = {"delivered": 0, "retry": 0, "dead": 0}
        gate = asyncio.Semaphore(_PARALLEL)

        async def one(d: dict) -> None:
            async with gate:
                try:
                    stats[await self._deliver_one(d)] += 1
                except Exception as exc:  # noqa: BLE001 — lỗi ghi kết quả: lease sẽ hết hạn và thử lại, không dừng cả lô
                    logger.warning("Không ghi được kết quả giao back-channel: %s", type(exc).__name__)
                    stats["retry"] += 1

        await asyncio.gather(*(one(d) for d in due))
        return stats

    async def run_once(self) -> dict[str, int]:
        enqueued = await self.fan_out()
        stats = await self.deliver_due()
        stats["enqueued"] = enqueued
        return stats

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — DB tạm chết...: ghi log, thử lại vòng sau
                logger.warning("Back-channel worker lỗi vòng: %s", type(exc).__name__)
            await asyncio.sleep(self.settings.backchannel_interval_s)
