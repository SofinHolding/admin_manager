"""Client cho Integration API (machine plane) — SDK mà MỌI Tool bên ngoài (kể cả Fallback Tool) dùng.

Bám `docs/api/integration-api-v1.md` và `docs/api/ingest-data-contract-v1.md`. Điểm quan trọng:
  * `Idempotency-Key` ổn định theo (thao tác, nội dung): retry vì timeout gửi lại ĐÚNG khoá + ĐÚNG body ⇒ server trả lại response cũ, không nhân đôi.
  * Chỉ retry lỗi mạng / 5xx / 429 (có backoff + giới hạn); lỗi 4xx là lỗi của caller ⇒ ném ngay `IntegrationError` mang `error.code`.
  * Tự chia batch ≤ 500 bản ghi, tự tính `expected_counts`, commit sau cùng. Không tự chọn owner: owner suy ra từ API key ở server.
  * Không bao giờ đưa API key vào exception / log.
  * `handshake()` tự nhớ `installation_id` trả về và gửi `X-Installation-Id` cho MỌI request sau đó (bắt buộc kể từ
    device binding 2026-10-14 — credential chưa handshake => 403 CREDENTIAL_NOT_ACTIVATED). Có thể truyền sẵn
    `installation_id=` lúc khởi tạo nếu đã handshake ở lần chạy trước (tránh gọi lại /handshake mỗi lần khởi động).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import httpx

from manager_sdk.feed_client import backoff_delays

logger = logging.getLogger("manager_sdk.integration")

CONTRACT_VERSION = "2026-10-01"
MAX_BATCH = 500


class IntegrationError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def _stable_key(*parts: Any) -> str:
    """Khoá idempotency xác định theo nội dung (cùng thao tác + cùng body ⇒ cùng khoá)."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return "sdk-" + hashlib.sha256(blob.encode()).hexdigest()[:40]


def _chunks(items: Sequence[dict], size: int = MAX_BATCH) -> Iterable[Sequence[dict]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def iso(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class IntegrationClient:
    def __init__(self, base_url: str, api_key: str, *, contract_version: str = CONTRACT_VERSION,
                 timeout: float = 15.0, retries: int = 3, http_client: httpx.AsyncClient | None = None,
                 sleep=asyncio.sleep, backoff_base: float = 0.5, user_agent: str = "manager-sdk/1",
                 installation_id: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._version = contract_version
        self._timeout = timeout
        self._retries = retries
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http_client is None
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._ua = user_agent
        # Ghi nhớ từ lần `handshake()` gần nhất (hoặc truyền sẵn nếu đã handshake trước đó, vd lần chạy trước).
        # BẮT BUỘC ở mọi request khác /handshake kể từ thiết kế tool-key 2026-10-14 (device binding).
        self.installation_id = installation_id

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ── HTTP lõi ────────────────────────────────────────────────────────────────

    def _headers(self, idempotency_key: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._key}", "X-Contract-Version": self._version,
                   "X-Request-Id": "sdk_" + uuid.uuid4().hex, "User-Agent": self._ua}
        if self.installation_id:
            headers["X-Installation-Id"] = self.installation_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def _request(self, method: str, path: str, *, json_body: Any = None, idempotency_key: str | None = None,
                       params: dict | None = None) -> dict:
        delays = list(backoff_delays(self._retries, base=self._backoff_base))
        last = "chưa thử"
        for attempt in range(self._retries + 1):
            try:
                r = await self._http.request(method, self._base + path, json=json_body, params=params,
                                             headers=self._headers(idempotency_key), timeout=self._timeout)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = type(exc).__name__  # không đưa URL/header vào thông điệp
            else:
                if r.status_code < 400:
                    body = r.json() if r.content else {}
                    return body.get("data", body) if isinstance(body, dict) else body
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    retry_after = r.headers.get("retry-after")
                    if retry_after and retry_after.isdigit() and attempt < self._retries:
                        await self._sleep(min(float(retry_after), 30.0))
                        continue
                else:
                    raise self._to_error(r)
            if attempt < self._retries:
                await self._sleep(delays[attempt])
        raise IntegrationError(503, "UNAVAILABLE", f"Integration API không khả dụng sau {self._retries + 1} lần thử ({last})")

    @staticmethod
    def _to_error(r: httpx.Response) -> IntegrationError:
        try:
            body = r.json()
            err = body.get("error") or {}
            return IntegrationError(r.status_code, err.get("code", "SERVER_ERROR"), err.get("message", r.reason_phrase),
                                    err.get("details"))
        except ValueError:
            return IntegrationError(r.status_code, "SERVER_ERROR", f"HTTP {r.status_code}")

    # ── Handshake / platforms / resources ───────────────────────────────────────

    async def handshake(self, *, device_id: str, hostname: str, os_name: str, app_version: str, tool_name: str,
                        platforms: list[str], capabilities: list[str]) -> dict:
        body = {"installation": {"device_id": device_id, "hostname": hostname, "os": os_name, "app_version": app_version},
                "tool": {"tool_name": tool_name, "contract_version": self._version},
                "platforms": platforms, "capabilities": capabilities}
        data = await self._request("POST", "/v1/integrations/handshake", json_body=body,
                                   idempotency_key=_stable_key("handshake", body))
        if isinstance(data, dict) and data.get("installation_id"):
            self.installation_id = data["installation_id"]  # mọi request SAU đây tự gửi X-Installation-Id
        return data

    async def platforms(self) -> list[dict]:
        """Nền tảng + mã client này được gửi (`key`, `aliases`, `codes`). Mọi snapshot/resource PHẢI kèm `platform` = một mã
        trong `codes`; server chuẩn hoá về `key` để Dashboard gom đúng nền tảng."""
        return await self._request("GET", "/v1/integrations/platforms")

    async def resources_batch(self, items: Sequence[dict]) -> list[dict]:
        out = []
        for chunk in _chunks(list(items)):
            body = {"items": list(chunk)}
            out.append(await self._request("POST", "/v1/integrations/resources/batch", json_body=body,
                                           idempotency_key=_stable_key("resources", body)))
        return out

    # ── Snapshot ────────────────────────────────────────────────────────────────

    async def create_snapshot(self, *, platform: str, snapshot_type: str, observed_at: datetime | str,
                              report_timezone: str, expected_counts: dict, scope: dict | None = None) -> dict:
        body: dict[str, Any] = {"platform": platform, "snapshot_type": snapshot_type, "observed_at": iso(observed_at),
                                "report_timezone": report_timezone, "expected_counts": expected_counts}
        if scope:
            body["scope"] = scope
        return await self._request("POST", "/v1/integrations/snapshots", json_body=body,
                                   idempotency_key=_stable_key("snapshot", body))

    async def _upload(self, snapshot_id: str, kind: str, items: Sequence[dict]) -> list[dict]:
        results = []
        for chunk in _chunks(list(items)):
            body = {"items": list(chunk)}
            results.append(await self._request("POST", f"/v1/integrations/snapshots/{snapshot_id}/{kind}",
                                               json_body=body, idempotency_key=_stable_key(snapshot_id, kind, body)))
        return results

    async def add_resources(self, snapshot_id: str, items: Sequence[dict]) -> list[dict]:
        return await self._upload(snapshot_id, "resources", items)

    async def add_content(self, snapshot_id: str, items: Sequence[dict]) -> list[dict]:
        return await self._upload(snapshot_id, "content", items)

    async def add_metrics(self, snapshot_id: str, items: Sequence[dict]) -> list[dict]:
        return await self._upload(snapshot_id, "metrics", items)

    async def add_events(self, snapshot_id: str, items: Sequence[dict]) -> list[dict]:
        return await self._upload(snapshot_id, "events", items)

    async def commit(self, snapshot_id: str, *, accept_partial: bool = False) -> dict:
        body = {"accept_partial": accept_partial}
        return await self._request("POST", f"/v1/integrations/snapshots/{snapshot_id}/commit", json_body=body,
                                   idempotency_key=_stable_key("commit", snapshot_id, body))

    async def get_snapshot(self, snapshot_id: str) -> dict:
        return await self._request("GET", f"/v1/integrations/snapshots/{snapshot_id}")

    async def push_snapshot(self, *, platform: str, observed_at: datetime | str, report_timezone: str,
                            snapshot_type: str = "full", scope: dict | None = None,
                            resources: Sequence[dict] = (), content: Sequence[dict] = (),
                            metrics: Sequence[dict] = (), events: Sequence[dict] = (),
                            accept_partial: bool = False) -> dict:
        """Luồng đầy đủ: tạo → tải lên theo lô → commit. Trả về kết quả commit. Lỗi giữa chừng ⇒ ném lỗi, snapshot KHÔNG commit."""
        counts = {"resources": len(resources), "content": len(content), "metrics": len(metrics), "events": len(events)}
        counts = {k: v for k, v in counts.items() if v}
        snap = await self.create_snapshot(platform=platform, snapshot_type=snapshot_type, observed_at=observed_at,
                                          report_timezone=report_timezone, expected_counts=counts, scope=scope)
        sid = snap["snapshot_id"]
        if resources:
            await self.add_resources(sid, resources)
        if content:
            await self.add_content(sid, content)
        if metrics:
            await self.add_metrics(sid, metrics)
        if events:
            await self.add_events(sid, events)
        return await self.commit(sid, accept_partial=accept_partial)

    async def push_by_platform(self, data_by_platform: Mapping[str, Mapping[str, Sequence[dict]]], *,
                               observed_at: datetime | str, report_timezone: str, snapshot_type: str = "full",
                               accept_partial: bool = False) -> dict[str, dict]:
        """Tool có dữ liệu của NHIỀU nền tảng: MỖI nền tảng một snapshot riêng, kèm đúng mã nền tảng của dữ liệu đó.

        `data_by_platform = {"<mã nền tảng>": {"resources": [...], "content": [...], "metrics": [...], "events": [...]}}`
        (mã lấy từ `platforms()` → `codes`). Trả `{mã: kết quả commit}`. Mã sai ⇒ server từ chối `PLATFORM_NOT_ALLOWED` và
        KHÔNG lưu gì cho mã đó; dừng ở nền tảng lỗi đầu tiên (nền tảng đã commit trước đó giữ nguyên)."""
        out: dict[str, dict] = {}
        for platform, data in data_by_platform.items():
            unknown = set(data) - {"resources", "content", "metrics", "events"}
            if unknown:
                raise ValueError(f"loại dữ liệu lạ cho {platform}: {sorted(unknown)}")
            if not any(data.get(k) for k in ("resources", "content", "metrics", "events")):
                continue
            out[platform] = await self.push_snapshot(
                platform=platform, observed_at=observed_at, report_timezone=report_timezone, snapshot_type=snapshot_type,
                resources=data.get("resources", ()), content=data.get("content", ()), metrics=data.get("metrics", ()),
                events=data.get("events", ()), accept_partial=accept_partial)
        return out

    # ── Jobs (Fallback Tool) ────────────────────────────────────────────────────

    async def claim_jobs(self, *, installation_id: str, platforms: list[str], capabilities: list[str],
                         limit: int = 20, lease_seconds: int = 300) -> list[dict]:
        body = {"installation_id": installation_id, "platforms": platforms, "capabilities": capabilities,
                "limit": limit, "lease_seconds": lease_seconds}
        data = await self._request("POST", "/v1/integrations/jobs/claim", json_body=body)  # claim: KHÔNG idempotent theo nội dung
        return data.get("jobs", []) if isinstance(data, dict) else data

    async def complete_job(self, job_id: str, result: dict | None = None) -> dict:
        body = {"result": result or {}}
        return await self._request("POST", f"/v1/integrations/jobs/{job_id}/complete", json_body=body,
                                   idempotency_key=_stable_key("job-complete", job_id, body))

    async def fail_job(self, job_id: str, *, error_code: str, message: str = "", retryable: bool = True) -> dict:
        body = {"error_code": error_code, "message": message, "retryable": retryable}
        return await self._request("POST", f"/v1/integrations/jobs/{job_id}/fail", json_body=body,
                                   idempotency_key=_stable_key("job-fail", job_id, body))
