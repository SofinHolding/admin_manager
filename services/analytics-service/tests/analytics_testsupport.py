"""Hạ tầng test dùng chung (cũng được tests/contract/test_analytics_stats_parity.py import).

- `FakeIntegration`: app FastAPI nhỏ TUÂN THỦ hợp đồng `docs/api/internal-feed-contract-v1.md` (feed + canonical export).
  Chỉ phát dữ liệu của snapshot ĐÃ COMMIT; có thể "commit" thêm revision để mô phỏng snapshot mới. KHÔNG phải mock của analytics.
- `FakeIdentityFeed`: feed account của Identity.
- `Issuer`: Identity giả với khoá RSA thật, ký token RS256 thật, phục vụ JWKS.
- `FlakyTransport`: mô phỏng nguồn chết bằng lỗi kết nối thật của httpx.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from datetime import datetime, timezone

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, HTTPException, Query

ISS, AUD = "http://identity.test", "manager-api"
KINDS = ("projects", "resources", "content", "metrics", "events")


# ── Identity giả: JWKS + token thật ─────────────────────────────────────────────

class Issuer:
    def __init__(self, kid: str = "kid-1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.up = True

    @staticmethod
    def _b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    def jwks(self) -> dict:
        nums = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
                          "n": self._b64(nums.n), "e": self._b64(nums.e)}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("identity down")
        return httpx.Response(200, json=self.jwks())

    def token(self, *, sub: str, role: str = "viewer", key=None, **over) -> str:
        now = int(time.time())
        payload = {"iss": ISS, "aud": [AUD], "sub": sub, "username": "u-" + sub[:4], "role": role, "perms": [],
                   "client_type": "web", "sid": "s1", "jti": uuid.uuid4().hex, "iat": now, "nbf": now,
                   "exp": now + 900, **over}
        return jwt.encode(payload, key or self.key, algorithm="RS256", headers={"kid": self.kid})

    def headers(self, sub: str, role: str = "viewer", **over) -> dict:
        return {"Authorization": "Bearer " + self.token(sub=sub, role=role, **over)}

    def http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# ── Nguồn feed giả ──────────────────────────────────────────────────────────────

class FlakyTransport(httpx.AsyncBaseTransport):
    """Bọc ASGITransport; khi `source.down` thì ném lỗi kết nối THẬT của httpx (như tiến trình đã chết)."""

    def __init__(self, source: "FakeFeedServer") -> None:
        self.source = source
        self.inner = httpx.ASGITransport(app=source.app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.source.request_count += 1
        if self.source.down:
            raise httpx.ConnectError("nguồn đã chết")
        return await self.inner.handle_async_request(request)


class FakeFeedServer:
    def __init__(self, service_key: str) -> None:
        self.key = service_key
        self.seq = 0
        self.events: list[dict] = []
        self.down = False
        self.request_count = 0
        self.duplicate_next = False   # giao hàng at-least-once: trả trùng event
        self.app = FastAPI()
        self._routes()

    def _auth(self, key: str) -> None:
        if key != self.key:
            raise HTTPException(401, "bad key")

    def _routes(self) -> None:
        @self.app.get("/internal/v1/feed")
        async def feed(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), types: str = Query(""),
                       x_service_key: str = Header("")) -> dict:
            self._auth(x_service_key)
            wanted = {t for t in types.split(",") if t}
            rows = [e for e in self.events if e["seq"] > after and (not wanted or e["type"] in wanted)]
            page = rows[:limit]
            if self.duplicate_next and page:
                page = page + page[:1]
            return {"events": page, "next_cursor": page[-1]["seq"] if page else after,
                    "has_more": len(rows) > limit, "head": self.seq}

    def emit(self, type_: str, data: dict, *, subject: str = "x:1", event_id: str | None = None) -> dict:
        self.seq += 1
        event = {"seq": self.seq, "id": event_id or "evt_" + uuid.uuid4().hex, "type": type_, "subject": subject,
                 "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "data_version": 1,
                 "data": data}
        self.events.append(event)
        return event

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=FlakyTransport(self))


class FakeIdentityFeed(FakeFeedServer):
    def account(self, account_id: str, type_: str = "account.registered", **data) -> dict:
        base = {"account_id": account_id}
        if type_ == "account.registered":
            base.update(username="user-" + account_id[:4], role="viewer", status="active")
        return self.emit(type_, {**base, **data}, subject="acc:" + account_id)


class FakeIntegration(FakeFeedServer):
    """Canonical store theo khoá tự nhiên; mỗi `commit` = một snapshot với revision toàn cục tăng dần."""

    def __init__(self, service_key: str) -> None:
        self.head = 0
        self.store: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self.staging: list[dict] = []
        self.page_overflow = 0     # số lần đã trả trang dài hơn limit (để test phân trang)
        super().__init__(service_key)
        self._canon_routes()

    # -- khoá tự nhiên (viết lại độc lập với service) --
    @staticmethod
    def key_of(kind: str, it: dict) -> str:
        o = it["owner_account_id"]
        if kind == "projects":
            return f"{o}|{it['project_key']}"
        if kind == "resources":
            return f"{o}|{it['platform']}|{it['external_id']}"
        if kind == "content":
            return f"{o}|{it['platform']}|{it['resource_external_id']}|{it['external_id']}"
        if kind == "metrics":
            return f"{o}|{it['platform']}|{it['resource_external_id']}|{it['date']}"
        return f"{o}|{it['external_id']}"

    @classmethod
    def line_of(cls, kind: str, i: dict) -> str:
        """Dòng checksum (viết lại độc lập với service): khoá|revision|removed[|số liệu hiển thị]."""
        def n(v):
            return "n" if v is None else str(v)
        line = f"{cls.key_of(kind, i)}|{i['revision']}|{1 if i['removed'] else 0}"
        if kind == "content":
            m = i.get("metrics") or {}
            line += f"|{n(m.get('views'))},{n(m.get('reactions'))},{n(m.get('comments'))}"
        elif kind == "metrics":
            line += f"|{n((i.get('metrics') or {}).get('views'))}"
        return line

    def _canon_routes(self) -> None:
        @self.app.get("/internal/v1/canonical/summary")
        async def summary(x_service_key: str = Header("")) -> dict:
            self._auth(x_service_key)
            return self.summary()

        @self.app.get("/internal/v1/canonical/{kind}")
        async def canonical(kind: str, after_revision: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=1000),
                            x_service_key: str = Header("")) -> dict:
            self._auth(x_service_key)
            if kind not in KINDS:
                raise HTTPException(404, "kind")
            rows = sorted((i for i in self.store[kind].values() if i["revision"] > after_revision),
                          key=lambda i: (i["revision"], self.key_of(kind, i)))
            page = rows[:limit]
            if len(rows) > limit:
                # không bao giờ cắt ngang một revision: trả trọn revision cuối của trang
                last_rev = page[-1]["revision"]
                page = [i for i in rows if i["revision"] <= last_rev]
                self.page_overflow += len(page) > limit
            has_more = len(page) < len(rows)
            return {"items": page, "next_revision": page[-1]["revision"] if page else after_revision,
                    "has_more": has_more, "head": self.head}

    def summary(self) -> dict:
        counts, checks = {}, {}
        for kind in KINDS:
            items = list(self.store[kind].values())
            counts[kind] = {"total": len(items), "active": sum(1 for i in items if not i["removed"])}
            lines = sorted(self.line_of(kind, i) for i in items)
            checks[kind] = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        return {"head_revision": self.head, "counts": counts, "checksum": checks}

    # -- ghi (mô phỏng Integration) --
    def commit(self, *, owner: str, platform: str = "", projects=(), resources=(), content=(), metrics=(),
               events=(), snapshot_id: str | None = None, observed_at: str = "2026-10-05T09:00:00Z",
               committed_at: str | None = None, emit: bool = True) -> int:
        """Commit nguyên tử một snapshot: mọi bản ghi nhận CÙNG một revision mới. Trả revision."""
        self.head += 1
        rev = self.head
        for kind, batch in (("projects", projects), ("resources", resources), ("content", content),
                            ("metrics", metrics), ("events", events)):
            for raw in batch:
                item = {"owner_account_id": owner, "platform": platform, "observed_at": observed_at, "removed": False,
                        **raw, "revision": rev}
                self.store[kind][self.key_of(kind, item)] = item
        if emit:
            committed = committed_at or observed_at
            self.emit("snapshot.committed", {
                "snapshot_id": snapshot_id or "snp_" + uuid.uuid4().hex[:10], "owner_account_id": owner,
                "client_id": "cli_x", "platform": platform, "snapshot_type": "full", "observed_at": observed_at,
                "committed_at": committed, "report_timezone": "UTC",
                "counts": {"resources": len(resources), "content": len(content), "metrics": len(metrics),
                           "events": len(events)}, "removed": {}, "revision_from": rev, "revision_to": rev})
        return rev

    def remove(self, kind: str, **keyparts) -> int:
        """Đánh dấu một bản ghi `removed` (vắng mặt trong snapshot full) bằng revision mới."""
        match = [i for i in self.store[kind].values() if all(i.get(k) == v for k, v in keyparts.items())]
        assert match, f"không có bản ghi {kind} khớp {keyparts}"
        self.head += 1
        for i in match:
            i["removed"] = True
            i["revision"] = self.head
        self.emit("snapshot.committed", {"snapshot_id": "snp_rm" + uuid.uuid4().hex[:6],
                                         "committed_at": "2026-10-05T09:30:00Z", "revision_to": self.head})
        return self.head

    def stage(self, **kwargs) -> dict:
        """Snapshot `building`/`failed`: nằm ở staging, KHÔNG BAO GIỜ xuất hiện trong export."""
        self.staging.append(kwargs)
        return kwargs

    def update_item(self, kind: str, **changes) -> int:
        """Cập nhật một bản ghi đã có (khoá = các trường có trong `changes` khớp) — vd số view mới của bài cũ."""
        keys = {k: v for k, v in changes.items() if k in ("owner_account_id", "platform", "external_id",
                                                           "resource_external_id", "date", "project_key")}
        match = [i for i in self.store[kind].values() if all(i.get(k) == v for k, v in keys.items())]
        assert len(match) == 1, f"khoá không xác định duy nhất: {keys} -> {len(match)}"
        self.head += 1
        match[0].update(changes)
        match[0]["revision"] = self.head
        self.emit("snapshot.committed", {"snapshot_id": "snp_up" + uuid.uuid4().hex[:6],
                                         "committed_at": "2026-10-05T09:40:00Z", "revision_to": self.head})
        return self.head


# ── Dựng bản ghi canonical ngắn gọn ─────────────────────────────────────────────

def res(ext, name, *, platform, country="", projects=None, managed=None, active=None, state=None, **kw) -> dict:
    out = {"external_id": ext, "platform": platform, "resource_type": "page", "display_name": name,
           "country": {"name": country} if country else None}
    if projects is not None:
        out["projects"] = [{"project_key": k, "enabled": e} for k, e in projects]
    if managed is not None:
        out["managed"] = managed
    if active is not None:
        out["active"] = active
    if state:
        out["state"] = state
    out.update(kw)
    return out


def post(ext, resource, day, views, reactions=None, comments=None, *, platform="facebook", **kw) -> dict:
    return {"external_id": ext, "resource_external_id": resource, "platform": platform, "published_date": day,
            "metrics": {"views": views, "reactions": reactions, "comments": comments}, **kw}


def daily(resource, day, views, *, platform="youtube") -> dict:
    return {"resource_external_id": resource, "platform": platform, "date": day, "metrics": {"views": views}}


def event(ext, resource, day, kind="post_ok", *, platform="facebook", project_key="") -> dict:
    return {"external_id": ext, "resource_external_id": resource, "platform": platform, "kind": kind,
            "occurred_date": day, "project_key": project_key}


def project(key, name, country="", enabled=True) -> dict:
    return {"project_key": key, "name": name, "platform": "", "country": {"name": country} if country else None,
            "enabled": enabled}


OWN_A = "a" * 32
OWN_B = "b" * 32
ADMIN = "d" * 32


def seed_world(fake: FakeIntegration) -> None:
    """Bộ dữ liệu tính tay dùng chung nhiều test (xem tests/test_stats_exact.py để biết số liệu kỳ vọng)."""
    fake.commit(
        owner=OWN_A, platform="facebook", observed_at="2026-10-05T08:00:00Z",
        projects=[project("itl", "ITL", "Brazil"), project("sib", "Sible", "")],
        resources=[
            res("pg1", "Page One", platform="facebook", country="Brazil", projects=[("itl", True), ("sib", True)]),
            res("pg2", "Page Two", platform="facebook", country="", projects=[("itl", True)]),
            res("pg3", "Page Three", platform="facebook", country="Peru", projects=[("sib", False)]),
        ],
        content=[
            post("p1", "pg1", "2026-10-01", 100, 10, 1), post("p2", "pg1", "2026-10-01", 50, 5, 2),
            post("p3", "pg1", "2026-10-02", 200, 20, 4), post("p4", "pg2", "2026-10-02", 30, 3, 0),
            post("p5", "pg3", "2026-10-03", 70, 7, 7), post("p6", "pg2", "2026-10-03", None, None, None),
            post("p7", "pg1", "2026-09-30", 11, 1, 1),
        ],
        events=[
            event("e1", "pg1", "2026-10-01", project_key="itl"), event("e2", "pg1", "2026-10-02", project_key="itl"),
            event("e3", "pg2", "2026-10-02", project_key="itl"), event("e4", "pg3", "2026-10-03", project_key="sib"),
            event("e6", "pg1", "2026-10-01", "post_fail", project_key="itl"),
            event("e7", "pg1", "2026-09-30", project_key="itl"),
        ])
    fake.commit(
        owner=OWN_A, platform="youtube", observed_at="2026-10-05T08:30:00Z",
        resources=[res("ch1", "Chan One", platform="youtube", country="Peru", managed=True, active=True,
                       resource_type="channel"),
                   res("ch2", "Chan Two", platform="youtube", country="", managed=True, active=True,
                       resource_type="channel")],
        metrics=[daily("ch1", "2026-10-01", 40), daily("ch1", "2026-10-02", 0), daily("ch2", "2026-10-01", 60),
                 daily("ch2", "2026-10-03", 10)],
        events=[event("e5", "ch1", "2026-10-01", platform="youtube")])
    fake.commit(
        owner=OWN_B, platform="facebook", observed_at="2026-10-05T08:45:00Z",
        resources=[res("pgB", "Page Bee", platform="facebook", country="Chile", managed=True, active=True)],
        content=[post("pb1", "pgB", "2026-10-01", 999, 9, 9)],
        events=[event("eb1", "pgB", "2026-10-01")])
