#!/usr/bin/env python3
"""
system_api.py — Ánh xạ (tích hợp) MỌI endpoint hệ thống vào tool.

Nguồn sự thật: `system-endpoints.json` (địa chỉ máy chủ nhúng sẵn + danh sách endpoint mặt phẳng MÁY). Mọi lời gọi của tool
(collect.py, kiểm tra kết nối trong Electron) đi qua `SystemApi`, KHÔNG nơi nào tự ghép đường dẫn.

CLI (Electron gọi qua stdin, không để key lên dòng lệnh):
  echo '{"gateway":"http://...","api_key":"mpk_..."}' | python system_api.py probe   # kiểm tra từng endpoint an toàn
  python system_api.py catalog                                                        # in danh sách endpoint (JSON)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import re
import sys
import time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

log = logging.getLogger("collect")

ENDPOINTS_FILE = Path(__file__).with_name("system-endpoints.json")
BATCH_KINDS = ("resources", "content", "events", "metrics")
BATCH_SIZE = 500

# Kênh do collector dùng chung thu hộ. `primary_tool` = kênh do Tool CÁ NHÂN của chủ kênh đẩy lên (user có tính năng tool riêng):
# tool dùng chung KHÔNG kéo về, không thu hộ.
SHARED_MODE = "fallback_tool"


class PushError(Exception):
    """Đẩy số liệu lên thất bại: job phải fail kèm lý do, không được coi là hoàn tất."""


def load_catalog(path: Path = ENDPOINTS_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    names = [e["name"] for e in data["endpoints"]]
    if len(names) != len(set(names)):
        raise ValueError("system-endpoints.json: tên endpoint bị trùng")
    return data


# ── Ánh xạ số liệu đo được -> hợp đồng ingest (thuần, có test: tests/test_system_api.py) ─────────────────────────────

# ── Ngày theo múi giờ báo cáo ─────────────────────────────────────────────────────────────────────────────
#
# Mọi ngày gửi lên (`published_date` của bài, `date` của chỉ số kênh) tính theo `report_timezone` CỐ ĐỊNH của tool = giờ Việt Nam, để
# khớp ngày người dùng thấy trên dashboard. Server từ chối đổi múi giờ giữa các snapshot của cùng Tool (`TIMEZONE_CHANGED`), nên giá
# trị nằm trong system-endpoints.json, không cho cấu hình riêng từng máy.

# Quốc gia chỉ có MỘT múi giờ: dùng khi nguồn trả giờ địa phương KHÔNG kèm offset ("2026-10-17 03:00:00"). Quốc gia nhiều múi giờ
# hoặc không có trong bảng => hiểu là UTC (không đoán).
REGION_TZ = {
    "VN": "Asia/Ho_Chi_Minh", "KR": "Asia/Seoul", "JP": "Asia/Tokyo", "TH": "Asia/Bangkok", "SG": "Asia/Singapore",
    "MY": "Asia/Kuala_Lumpur", "PH": "Asia/Manila", "TW": "Asia/Taipei", "HK": "Asia/Hong_Kong", "IN": "Asia/Kolkata",
    "PK": "Asia/Karachi", "BD": "Asia/Dhaka", "NG": "Africa/Lagos", "EG": "Africa/Cairo", "KE": "Africa/Nairobi",
}
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_tz(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:   # Windows không có CSDL múi giờ nếu thiếu gói `tzdata`
        if name == "Asia/Ho_Chi_Minh":
            return timezone(timedelta(hours=7), "ICT")   # Việt Nam UTC+7 cố định, không có giờ mùa hè
        if name == "UTC":
            return timezone.utc
        raise


def report_tz() -> tzinfo:
    return _load_tz(CATALOG["report_timezone"])


def _instant(value: Any, region: str | None) -> datetime | None:
    """Một mốc thời gian lấy từ nền tảng -> datetime có múi giờ; None nếu không đọc được."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        n = n / 1000 if n > 1e11 else n          # mili-giây
        try:
            return datetime.fromtimestamp(n, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:                    # giờ địa phương của nguồn: dùng múi giờ của quốc gia nếu chỉ có một
            name = REGION_TZ.get((region or "").strip().upper())
            try:
                dt = dt.replace(tzinfo=_load_tz(name) if name else timezone.utc)
            except ZoneInfoNotFoundError:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def to_report_date(value: Any, region: str | None = None, *, fallback: datetime | None = None) -> str:
    """Ngày `YYYY-MM-DD` theo giờ Việt Nam của một mốc thời gian lấy từ nền tảng.

    - Số (epoch giây hoặc mili-giây) hoặc chuỗi số, ISO có offset/`Z`: mốc TUYỆT ĐỐI => đổi thẳng sang giờ Việt Nam. `region` không ảnh hưởng
      (vd TikTok `create_time` epoch + `region: KR`: 03:00 sáng 17/10 giờ Hàn = 01:00 17/10 giờ Việt Nam).
    - ISO KHÔNG offset: giờ địa phương của nguồn => hiểu theo múi giờ của `region` (nếu quốc gia chỉ có một múi giờ), không rõ thì UTC.
    - Chỉ có ngày (`2026-10-17`): giữ nguyên, không có giờ để quy đổi.
    - Không đọc được: ngày của `fallback` (thời điểm đo, mặc định bây giờ) theo giờ Việt Nam."""
    if isinstance(value, str) and _DATE_ONLY.match(value.strip()):
        return value.strip()
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value.strip()):   # `YYYYMMDD` (vd upload_date của yt-dlp), không phải epoch
        s = value.strip()
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    dt = _instant(value, region) or fallback or datetime.now(timezone.utc)
    return dt.astimezone(report_tz()).date().isoformat()


# Chỉ số của KÊNH (cấu trúc đã thiết kế sẵn, adapter nền tảng điền khi lấy được). Khoá = Metric Registry:
#   followers = số người theo dõi, videos_posted = tổng số video, likes = tổng lượt thích của kênh.
# Giá trị là SỐ TUYỆT ĐỐI tại thời điểm đo; server ghi theo ngày (daily_metrics) nên có lịch sử theo ngày.
CHANNEL_METRIC_KEYS = ("followers", "videos_posted", "likes")


def build_content_items(resource_ext: str, items: list[dict], now: datetime, platform: str | None = None) -> list[dict]:
    """Số liệu đo được -> `content` của hợp đồng ingest, gắn `resource_external_id` của KÊNH trong job.

    `views/likes/comments/shares` -> metrics `views/reactions/comments/shares` (khoá canonical của Metric Registry).
    `title/duration_seconds/cover_url` (nếu adapter có) -> `extensions.<platform>` để hiển thị/tra cứu sau này.
    Bản ghi thiếu external_id bị bỏ (không đoán). Trùng external_id: bản sau thắng. Số âm/không phải số nguyên bị bỏ.
    """
    out: dict[str, dict] = {}
    for it in items:
        ext = str(it.get("external_id") or "").strip()
        if not ext:
            continue
        if len(ext) > 200:
            ext = "h_" + hashlib.sha1(ext.encode("utf-8")).hexdigest()
        metrics = {key: int(it[src]) for key, src in (("views", "views"), ("reactions", "likes"), ("comments", "comments"),
                                                       ("shares", "shares"))
                   if isinstance(it.get(src), (int, float)) and not isinstance(it.get(src), bool) and it[src] >= 0}
        row = {"external_id": ext, "resource_external_id": resource_ext,
               "published_date": to_report_date(it.get("published_at"), it.get("region"), fallback=now), "metrics": metrics,
               "observed_at": now.isoformat()}
        if it.get("url"):
            row["url"] = str(it["url"])[:500]
        extra = {k: (str(it[k])[:300] if k != "duration_seconds" else it[k])
                 for k in ("title", "duration_seconds", "cover_url")
                 if it.get(k) not in (None, "") and (k != "duration_seconds" or isinstance(it[k], int))}
        region = str(it.get("region") or "").strip().upper()[:8]   # quốc gia của bài (nguồn trả kèm), để hiển thị/tra cứu
        if region:
            extra["region"] = region
        if extra and platform:
            row["extensions"] = {platform: extra}
        out[ext] = row
    return list(out.values())


def build_channel_metrics(resource_ext: str, stats: dict | None, now: datetime) -> list[dict]:
    """Chỉ số của kênh -> MỘT bản ghi `metrics` (khoá `(kênh, ngày giờ Việt Nam)`); rỗng nếu adapter không có chỉ số nào hợp lệ."""
    metrics = {k: int(stats[k]) for k in CHANNEL_METRIC_KEYS
               if stats and isinstance(stats.get(k), (int, float)) and not isinstance(stats.get(k), bool) and stats[k] >= 0}
    if not metrics:
        return []
    return [{"resource_external_id": resource_ext, "date": now.astimezone(report_tz()).date().isoformat(), "metrics": metrics,
             "observed_at": now.isoformat()}]


CATALOG = load_catalog()
DEFAULT_GATEWAY: str = CATALOG["default_gateway"]
ENDPOINTS: dict[str, dict] = {e["name"]: e for e in CATALOG["endpoints"]}


def endpoint_path(name: str, **params: str) -> str:
    """Đường dẫn của endpoint `name`, điền `{tham_số}` (đã mã hoá URL)."""
    return ENDPOINTS[name]["path"].format(**{k: quote(str(v), safe="") for k, v in params.items()})


def device_id() -> str:
    return f"shared-collector-{socket.gethostname()}"


def shared_view(items: list[dict]) -> dict:
    """Từ danh sách kênh của MỌI owner (`GET /resources`), chỉ giữ kênh của user dùng tool dùng chung.

    Một kênh thuộc tool dùng chung khi `execution_mode == fallback_tool`. User có Tool riêng đẩy kênh qua `primary_tool` nên không
    xuất hiện. Trả `owners` (chỉ owner còn ≥1 kênh dùng chung), `channels` (đã ánh xạ sang hình dạng DB cục bộ) và `skipped`
    (số owner / kênh bị bỏ + lý do) để giao diện báo rõ, không im lặng."""
    owners: dict[str, dict] = {}
    channels: list[dict] = []
    skipped_personal: dict[str, int] = {}
    skipped_other = 0
    for r in items:
        if r.get("resource_type") == "group":
            continue
        mode, oid = r.get("execution_mode"), r.get("owner_account_id") or ""
        if mode != SHARED_MODE:
            if mode == "primary_tool":
                skipped_personal[oid] = skipped_personal.get(oid, 0) + 1
            else:
                skipped_other += 1
            continue
        o = owners.setdefault(oid, {"id": oid, "username": r.get("owner_username") or oid, "resource_count": 0})
        o["resource_count"] += 1
        channels.append({
            "id": r["id"], "owner_account_id": oid, "owner_username": o["username"], "platform": r.get("platform", ""),
            "resource_type": r.get("resource_type", ""), "canonical_external_id": r.get("external_id", ""),
            "input_ref": r.get("username") or r.get("external_id", ""), "display_name": r.get("display_name", ""),
            "url": r.get("url", ""), "mapping_status": r.get("mapping_status", ""),
            "execution_mode": mode, "enabled": r.get("enabled", True)})
    personal_only = [oid for oid in skipped_personal if oid not in owners]
    return {"owners": sorted(owners.values(), key=lambda o: o["username"].lower()), "channels": channels,
            "skipped": {"personal_tool_owners": len(personal_only), "personal_tool_channels": sum(skipped_personal.values()),
                        "other_channels": skipped_other}}


class SystemApi:
    """HTTP client tới integration-service qua gateway bằng machine credential (mọi endpoint trong system-endpoints.json)."""

    def __init__(self, gateway: str, api_key: str, timeout: float = 20.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=gateway.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self.installation_id: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, name: str, *, json_body: Any = None, params: dict | None = None,
                    headers: dict | None = None, **path_params: str) -> httpx.Response:
        e = ENDPOINTS[name]
        return await self._http.request(e["method"], endpoint_path(name, **path_params), json=json_body,
                                        params=params, headers=headers)

    # ── Kết nối ──────────────────────────────────────────────────────────────

    async def health(self) -> dict:
        r = await self._http.get(endpoint_path("health"), headers={"Authorization": ""})
        r.raise_for_status()
        return r.json()

    async def handshake(self, platforms: list[str]) -> str:
        """Khai báo installation → lấy installation_id. Idempotent theo device_id (gọi lại chỉ cập nhật last_seen_at).

        Bắt buộc trên MỌI request máy khác /handshake kể từ device binding: thiếu `X-Installation-Id` khớp installation đã bind
        => 403 CREDENTIAL_NOT_ACTIVATED/INSTALLATION_MISMATCH."""
        host = socket.gethostname()
        r = await self._call("handshake", json_body={
            "installation": {"device_id": device_id(), "hostname": host, "os": "windows", "app_version": "collect.py/1.0"},
            "platforms": platforms,
            "capabilities": ["data.collect"],
        })
        r.raise_for_status()
        self.installation_id = r.json()["data"]["installation_id"]
        self._http.headers["X-Installation-Id"] = self.installation_id
        log.info("Handshake OK — installation_id=%s", self.installation_id)
        return self.installation_id

    async def allowed_platforms(self) -> list[str]:
        """Khoá nền tảng key này được phép gửi (giao của `allowed_platforms` và Platform Registry đang bật)."""
        r = await self._call("platforms")
        r.raise_for_status()
        return [p["key"] for p in r.json().get("data", [])]

    # ── Đọc dữ liệu hệ thống ─────────────────────────────────────────────────

    async def list_resource_owners(self) -> list[dict]:
        r = await self._call("resource_owners")
        r.raise_for_status()
        return r.json().get("data", {}).get("users", [])

    async def list_resources(self, owner_account_id: str = "", platform: str = "", resource_type: str = "") -> dict:
        """`{items, owner_account_id, is_shared}`; key dùng chung lọc thêm theo owner."""
        q = {k: v for k, v in (("owner_account_id", owner_account_id), ("platform", platform),
                               ("resource_type", resource_type)) if v}
        r = await self._call("resources_list", params=q)
        r.raise_for_status()
        return r.json().get("data", {})

    async def pull_shared(self) -> dict:
        """Kéo từ website CHỈ user/kênh dùng tool dùng chung (xem `shared_view`) bằng MỘT lần gọi `GET /resources`."""
        return shared_view((await self.list_resources()).get("items", []))

    # ── Worker ───────────────────────────────────────────────────────────────

    async def register_worker(self, platforms: list[str]) -> dict:
        r = await self._call("worker_register", json_body={
            "worker_id": device_id()[:64], "capabilities": ["data.collect"], "platforms": platforms})
        r.raise_for_status()
        return r.json()["data"]

    async def worker_heartbeat(self, worker_id: str | None = None) -> dict:
        r = await self._call("worker_heartbeat", json_body={}, worker_id=worker_id or device_id()[:64])
        r.raise_for_status()
        return r.json()["data"]

    # ── Job ──────────────────────────────────────────────────────────────────

    async def enqueue_jobs(self, resource_ids: list[str]) -> dict:
        """Xin server tạo job thu thập cho các kênh ĐÃ KÉO (một đợt thu thập). Server chỉ tự tạo MỘT job khi user thêm kênh,
        nên đây là cách duy nhất để một kênh được thu lại mỗi ngày. Gọi lại an toàn (kênh đang có job thì không tạo thêm).
        Trả `{created, already_active, skipped:[{resource_id, reason}]}`; chia lô ≤200 theo giới hạn của server."""
        total = {"created": 0, "already_active": 0, "skipped": []}
        for i in range(0, len(resource_ids), 200):
            r = await self._call("jobs_enqueue", json_body={"resource_ids": resource_ids[i:i + 200]})
            r.raise_for_status()
            data = r.json().get("data", {})
            total["created"] += data.get("created", 0)
            total["already_active"] += data.get("already_active", 0)
            total["skipped"] += data.get("skipped", [])
        return total

    async def claim_jobs(self, platforms: list[str], limit: int = 10, resource_ids: list[str] | None = None) -> list[dict]:
        """Claim collect-job. `resource_ids` (không rỗng): chỉ nhận job của các kênh đó — tool chỉ chạy kênh ĐÃ KÉO."""
        if not self.installation_id:
            raise RuntimeError("Chưa handshake — gọi handshake() trước")
        body = {"installation_id": self.installation_id, "platforms": platforms, "capabilities": ["data.collect"],
                "limit": min(limit, 20), "lease_seconds": 300}
        if resource_ids:
            body["resource_ids"] = resource_ids[:500]
        r = await self._call("jobs_claim", json_body=body)
        if r.status_code == 204:
            return []
        r.raise_for_status()
        data = r.json().get("data", {})
        return data.get("jobs", []) if isinstance(data, dict) else []

    async def complete_job(self, job: dict, result: dict | None = None) -> None:
        """Báo job xong. Dữ liệu đã commit trước đó nên lỗi ở bước này chỉ cảnh báo (job sẽ hết lease và được xử lý lại,
        upsert theo khoá tự nhiên nên không nhân đôi số liệu)."""
        r = await self._call("job_complete", json_body={"result": result or {}, "lease_token": job.get("lease_token")},
                             job_id=job["job_id"])
        if r.status_code >= 400:
            log.warning("complete job=%s thất bại: HTTP %s %s", job["job_id"], r.status_code, r.text[:200])

    async def fail_job(self, job: dict, reason: str, *, retryable: bool = True) -> None:
        r = await self._call("job_fail", json_body={
            "error_code": "COLLECT_FAILED", "message": reason[:500], "retryable": retryable,
            "lease_token": job.get("lease_token")}, job_id=job["job_id"])
        if r.status_code >= 400:
            log.warning("fail job=%s không ghi được: HTTP %s %s", job["job_id"], r.status_code, r.text[:200])

    async def heartbeat(self, job: dict) -> None:
        await self._call("job_heartbeat", json_body={"lease_seconds": 300, "lease_token": job.get("lease_token")},
                         job_id=job["job_id"])

    # ── Ghi dữ liệu (ủy quyền theo job) ──────────────────────────────────────

    async def _checked(self, r: httpx.Response, what: str) -> dict:
        """Mọi bước đẩy dữ liệu PHẢI được kiểm: lỗi HTTP hay `rejections` đều làm job thất bại (không mất số liệu im lặng)."""
        if r.status_code >= 400:
            raise PushError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
        return r.json().get("data") or {}

    async def snapshot_status(self, snapshot_id: str) -> dict:
        return await self._checked(await self._call("snapshot_status", snapshot_id=snapshot_id), "trạng thái snapshot")

    async def send_batch(self, snapshot_id: str, kind: str, rows: list[dict], job_id: str) -> None:
        """Gửi `rows` vào snapshot theo lô ≤500, kiểm `rejected` từng lô. `kind` ∈ resources|content|events|metrics."""
        if kind not in BATCH_KINDS:
            raise ValueError(f"loại batch không hợp lệ: {kind}")
        for i in range(0, len(rows), BATCH_SIZE):
            res = await self._checked(await self._call(
                f"snapshot_{kind}", json_body={"items": rows[i:i + BATCH_SIZE]},
                headers={"X-Delegated-Job": job_id}, snapshot_id=snapshot_id), f"gửi {kind}")
            if res.get("rejected"):
                raise PushError(f"server từ chối {res['rejected']} bản ghi {kind}: {res.get('rejections', [])[:3]}")

    async def upsert_resources(self, items: list[dict], job_id: str) -> dict:
        """Upsert kênh ngoài snapshot (`resources/batch`); client dùng chung bắt buộc kèm job ủy quyền."""
        return await self._checked(await self._call(
            "resources_batch", json_body={"items": items}, headers={"X-Delegated-Job": job_id}), "upsert kênh")

    async def push_snapshot(self, job: dict, items: list[dict], channel_stats: dict | None = None) -> dict:
        """Đẩy số liệu của MỘT kênh (job) lên dashboard bằng ingest ủy quyền.

        Gồm: danh sách video của kênh (`content`, mỗi video kèm metrics) và — nếu adapter lấy được — chỉ số của chính kênh
        (`metrics` theo ngày: followers/videos_posted/likes). Ánh xạ: `platform` và `resource_external_id` lấy từ chính job
        (resource do user nhập, đã gắn nền tảng) — không phải id Tool tự dò. Snapshot `delta` + `expected_counts`: server kiểm
        đủ số bản ghi mới commit, thiếu thì lỗi.
        """
        job_id, platform = job["job_id"], job["platform"]
        resource_ext = (job.get("resource") or {}).get("external_id")
        if not job_id or not platform or not resource_ext:
            raise PushError("job thiếu job_id/platform/resource.external_id — không thể ánh xạ kênh")
        now = datetime.now(timezone.utc)
        content = build_content_items(resource_ext, items, now, platform)
        channel = build_channel_metrics(resource_ext, channel_stats, now)
        if not content and not channel:
            raise PushError("không có bản ghi hợp lệ để đẩy")
        hdr = {"X-Delegated-Job": job_id}
        attempt = job.get("attempt") or 1
        expected = {k: len(v) for k, v in (("content", content), ("metrics", channel)) if v}
        snap = await self._checked(await self._call(
            "snapshot_create", headers={**hdr, "Idempotency-Key": f"collect-{job_id}-{attempt}"},
            json_body={"platform": platform, "snapshot_type": "delta", "observed_at": now.isoformat(),
                       "report_timezone": CATALOG["report_timezone"], "expected_counts": expected}), "tạo snapshot")
        sid = snap["snapshot_id"]
        for kind, rows in (("metrics", channel), ("content", content)):
            await self.send_batch(sid, kind, rows, job_id)
        return await self._checked(await self._call(
            "snapshot_commit", headers=hdr, json_body={}, snapshot_id=sid), "commit")


# ── Kiểm tra kết nối từng endpoint ───────────────────────────────────────────

def _result(e: dict, status: str, http: int | None = None, ms: int | None = None, detail: str = "") -> dict:
    return {"name": e["name"], "method": e["method"], "path": e["path"], "scope": e["scope"], "summary": e["summary"],
            "status": status, "http": http, "ms": ms, "detail": detail}


def _verdict(r: httpx.Response) -> tuple[bool, str]:
    try:
        body = r.json()
    except ValueError:
        return False, "phản hồi không phải JSON (có thể sai địa chỉ máy chủ)"
    if r.status_code < 400 and isinstance(body, dict) and body.get("ok") is True:
        return True, ""
    err = (body.get("error") if isinstance(body, dict) else None) or {}
    return False, f"{err.get('code', '')} {err.get('message', '')}".strip() or f"HTTP {r.status_code}"


async def probe_all(gateway: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    """Chạy thử mọi endpoint AN TOÀN (đọc / idempotent); endpoint ghi hoặc đổi trạng thái job được liệt kê là `skipped`.

    Không bao giờ ném lỗi mạng ra ngoài: mỗi endpoint một kết quả để giao diện hiển thị đúng chỗ hỏng."""
    out: list[dict] = []
    api = SystemApi(gateway, api_key, timeout=8.0)
    if transport is not None:
        api._http = httpx.AsyncClient(base_url=gateway.rstrip("/"), transport=transport,
                                      headers={"Authorization": f"Bearer {api_key}"})
    host = device_id()[:64]
    try:
        for e in CATALOG["endpoints"]:
            if e["probe"] != "safe":
                out.append(_result(e, "skipped", detail="có tác dụng phụ (ghi / đổi trạng thái) — chỉ chạy khi thu thập thật"))
                continue
            if e["auth"] and not api_key:
                out.append(_result(e, "skipped", detail="chưa có API key"))
                continue
            if e["name"] not in ("health", "handshake") and e["auth"] and not api.installation_id:
                out.append(_result(e, "skipped", detail="handshake chưa thành công"))
                continue
            t0 = time.monotonic()
            try:
                if e["name"] == "health":
                    r = await api._http.get(endpoint_path("health"), headers={"Authorization": ""})
                elif e["name"] == "handshake":
                    r = await api._call("handshake", json_body={
                        "installation": {"device_id": device_id(), "hostname": socket.gethostname(), "os": "windows",
                                         "app_version": "collect.py/1.0"},
                        "platforms": [], "capabilities": ["data.collect"]})
                elif e["name"] == "worker_register":
                    r = await api._call("worker_register", json_body={"worker_id": host, "capabilities": ["data.collect"],
                                                                      "platforms": []})
                elif e["name"] == "worker_heartbeat":
                    r = await api._call("worker_heartbeat", json_body={}, worker_id=host)
                else:
                    r = await api._call(e["name"])
            except httpx.HTTPError as exc:
                out.append(_result(e, "fail", None, int((time.monotonic() - t0) * 1000), f"không kết nối được: {exc}"[:200]))
                continue
            ok, why = _verdict(r)
            if ok and e["name"] == "handshake":
                api.installation_id = r.json()["data"]["installation_id"]
                api._http.headers["X-Installation-Id"] = api.installation_id
            out.append(_result(e, "ok" if ok else "fail", r.status_code, int((time.monotonic() - t0) * 1000), why))
    finally:
        await api.aclose()
    return out


def use_utf8_stdio() -> None:
    """stdin/stdout/stderr mặc định là codepage hệ thống (Windows: cp1252) khi bị chuyển hướng: tiếng Việt làm
    UnicodeEncodeError (JSON kết quả, log). Mọi điểm vào của tool gọi hàm này trước khi đọc/in."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):   # stdin có thể vắng/đóng khi chạy không cửa sổ
            stream.reconfigure(encoding="utf-8")


async def pull_shared(gateway: str, api_key: str) -> dict:
    """Handshake rồi kéo user/kênh dùng tool dùng chung. Lỗi hệ thống trả `{"error": ...}` (không ném) cho giao diện hiển thị."""
    api = SystemApi(gateway, api_key, timeout=15.0)
    try:
        await api.handshake([])
        return await api.pull_shared()
    except httpx.HTTPStatusError as exc:
        try:
            msg = exc.response.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        return {"error": f"HTTP {exc.response.status_code} {msg}".strip()}
    except httpx.HTTPError as exc:
        return {"error": f"không kết nối được {gateway}: {exc}"[:300]}
    finally:
        await api.aclose()


def main() -> None:
    use_utf8_stdio()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "catalog":
        print(json.dumps(CATALOG, ensure_ascii=False))
    elif cmd in ("probe", "shared"):
        cfg = json.loads(sys.stdin.read() or "{}")
        gateway = (cfg.get("gateway") or DEFAULT_GATEWAY).rstrip("/")
        key = cfg.get("api_key") or ""
        if cmd == "probe":
            print(json.dumps({"gateway": gateway, "results": asyncio.run(probe_all(gateway, key))}, ensure_ascii=False))
        else:
            print(json.dumps(asyncio.run(pull_shared(gateway, key)) if key else {"error": "Chưa cấu hình API key"},
                             ensure_ascii=False))
    else:
        print("usage: system_api.py catalog | probe | shared (stdin JSON {gateway, api_key})", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
