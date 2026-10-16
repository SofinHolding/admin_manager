"""State machine snapshot + staging + commit atomic.

    building --commit--> committing --(1 transaction)--> committed
        |                    |-- lỗi hợp đồng (SNAPSHOT_INCOMPLETE) --> building (Tool gửi bù rồi commit lại)
        |                    '-- lỗi bất ngờ ----------------------> failed
        '-- không hoạt động quá SNAPSHOT_TTL ----------------------> abandoned

Bảo đảm: Tool chết trước commit => staging bị bỏ, canonical KHÔNG đổi. Commit chạy trong MỘT transaction: merge staging ->
canonical (LWW theo observed_at), suy luận `removed` (full + scope), gán `revision`, ghi outbox `snapshot.committed`, lật con trỏ
`current`. Lỗi giữa chừng => rollback toàn bộ.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from manager_contracts import ErrorCode

from integration_service.config import Settings
from integration_service.credentials.service import MachineContext
from integration_service.db import Database
from integration_service.errors import ApiProblem, Extra, conflict, forbidden, invalid, not_found
from integration_service.feed import events
from integration_service.feed import repository as outbox
from integration_service.http_utils import envelope
from integration_service.idempotency.guard import IdempotencyGuard, Replay
from integration_service.ingest import merge, schemas, validate
from integration_service.ingest.validate import Rejection, Staged
from integration_service.installations import repository as installations
from integration_service.platforms import repository as registry
from integration_service.security import new_id
from integration_service.snapshots import repository

logger = logging.getLogger("integration.snapshots")

KINDS = ("resources", "content", "metrics", "events")
MAX_BUILDING_PER_CLIENT = 50
MAX_REJECTION_ROWS = 10_000
_SCOPE_KIND = {"resources": "resources", "content_window": "content", "metrics_window": "metrics",
               "events_window": "events"}
_DB_DOWN = (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError, TimeoutError, asyncio.TimeoutError)


def public_snapshot(snap: dict) -> dict:
    return {"snapshot_id": snap["id"], "status": snap["status"], "platform": snap["platform"],
            "snapshot_type": snap["snapshot_type"], "observed_at": snap["observed_at"],
            "report_timezone": snap["report_timezone"], "scope": snap["scope"],
            "expected_counts": snap["expected_counts"], "is_current": snap["is_current"],
            "created_at": snap["created_at"], "updated_at": snap["updated_at"], "committed_at": snap["committed_at"]}


class SnapshotService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime]) -> None:
        self._db = db
        self._settings = settings
        self._now = now

    # ── Trợ giúp ──────────────────────────────────────────────────────────────────
    async def ensure_platform(self, conn: asyncpg.Connection, ctx: MachineContext, platform: str) -> None:
        if platform not in ctx.allowed_platforms or platform not in await registry.enabled_platforms(conn):
            raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform không nằm trong phạm vi client được phép")
        if ctx.delegated_platform is not None and platform != ctx.delegated_platform:
            # Ingest ủy quyền gắn với MỘT job = một nền tảng: không để số liệu của kênh này rơi sang nền tảng khác.
            raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Ingest ủy quyền phải dùng đúng platform của job",
                            {"job_platform": ctx.delegated_platform})

    async def expire_if_needed(self, snapshot_id: str) -> None:
        """Lazy: snapshot `building` quá TTL không hoạt động => `abandoned` (ghi trên kết nối riêng, trước khi xử lý)."""
        cutoff = self._now() - timedelta(seconds=self._settings.snapshot_ttl_s)
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE snapshots SET status = 'abandoned', updated_at = $3, failure_reason = 'ttl_expired' "
                "WHERE id = $1 AND status = 'building' AND updated_at < $2", snapshot_id, cutoff, self._now())

    @staticmethod
    def _not_building(snap: dict) -> ApiProblem:
        status = snap["status"]
        if status == "committed":
            return conflict(ErrorCode.SNAPSHOT_ALREADY_COMMITTED, "Snapshot đã được commit",
                            {"snapshot_id": snap["id"]})
        if status == "committing":
            return conflict(Extra.SNAPSHOT_COMMIT_IN_PROGRESS, "Snapshot đang được commit", {"snapshot_id": snap["id"]})
        return conflict(Extra.SNAPSHOT_NOT_WRITABLE, f"Snapshot ở trạng thái {status}, không nhận thêm dữ liệu",
                        {"snapshot_id": snap["id"], "status": status})

    async def _load(self, conn: asyncpg.Connection, ctx: MachineContext, snapshot_id: str, *, lock: bool) -> dict:
        snap = await repository.get_for_client(conn, snapshot_id, ctx.effective_owner, ctx.client_id, lock=lock)
        if snap is None:
            raise not_found(ErrorCode.SNAPSHOT_NOT_FOUND, "Không tìm thấy snapshot")
        await self.ensure_platform(conn, ctx, snap["platform"])
        return snap

    # ── Tạo snapshot ──────────────────────────────────────────────────────────────
    async def create(self, conn: asyncpg.Connection, ctx: MachineContext, body: schemas.SnapshotCreate,
                     installation_id: str | None) -> dict:
        platform = await registry.resolve(conn, body.platform)   # mã Tool gửi (chính/phụ) -> mã chính của nền tảng
        await self.ensure_platform(conn, ctx, platform)
        if ctx.delegated_resource_id is not None and body.snapshot_type != "delta":
            # `full` kèm scope sẽ đánh dấu removed mọi bản ghi vắng mặt: Tool dùng chung chỉ thấy MỘT kênh nên không được.
            raise invalid("Ingest ủy quyền chỉ được dùng snapshot_type=delta")
        try:
            ZoneInfo(body.report_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
            raise invalid("report_timezone không phải múi giờ IANA hợp lệ") from None
        previous = await repository.last_timezone(conn, ctx.effective_owner, ctx.client_id, platform)
        if previous is not None and previous != body.report_timezone:
            raise conflict(ErrorCode.TIMEZONE_CHANGED, "report_timezone khác snapshot trước của cùng Tool",
                           {"previous": previous})
        building = await conn.fetchval(
            "SELECT count(*) FROM snapshots WHERE client_id = $1 AND status = 'building'", ctx.client_id)
        if building >= MAX_BUILDING_PER_CLIENT:
            raise ApiProblem(Extra.LIMIT_EXCEEDED, "Quá nhiều snapshot đang building", status_code=409)
        if installation_id and not await installations.exists_for_client(conn, installation_id, ctx.client_id):
            installation_id = None
        scope = body.scope.model_dump(mode="json", by_alias=True, exclude_none=True) if body.scope else None
        snap = await repository.insert(
            conn, id=new_id("snp"), owner=ctx.effective_owner, client_id=ctx.client_id,
            installation_id=installation_id, platform=platform, snapshot_type=body.snapshot_type,
            observed_at=body.observed_at, report_timezone=body.report_timezone, scope=scope,
            expected_counts=body.expected_counts.model_dump(exclude_none=True), now=self._now())
        out = public_snapshot(snap)
        out["expires_at"] = snap["updated_at"] + timedelta(seconds=self._settings.snapshot_ttl_s)
        return out

    # ── Nhận batch vào staging ────────────────────────────────────────────────────
    async def stage(self, conn: asyncpg.Connection, ctx: MachineContext, snapshot_id: str, kind: str,
                    items: list, extra: dict) -> dict:
        await self.expire_if_needed(snapshot_id)
        snap = await self._load(conn, ctx, snapshot_id, lock=True)
        extra["snapshot_id"] = snapshot_id
        if snap["status"] != "building":
            raise self._not_building(snap)
        platform = snap["platform"]
        if ctx.delegated_resource_id is not None and kind == "resources":
            raise forbidden(ErrorCode.SCOPE_DENIED, "Ingest ủy quyền không được tạo/sửa resource, chỉ gửi số liệu của kênh trong job")
        known = await registry.enabled_metrics(conn) if kind in ("content", "metrics") else None
        staged, rejections, dupes = validate.validate_items(kind, items, known_metrics=known,
                                                            default_observed_at=snap["observed_at"])
        rejections = list(rejections)

        if ctx.delegated_resource_id is not None and staged:
            # MAPPING: số liệu Tool dùng chung kéo về phải gắn đúng resource (kênh) của job đã claim — khoá = external_id của
            # chính resource đó (không phải id Tool tự dò ra, vd UC… của YouTube), nên luôn rơi đúng nền tảng/kênh của user.
            job_ext = await conn.fetchval("SELECT external_id FROM resources WHERE id = $1", ctx.delegated_resource_id)
            foreign = [s for s in staged if s.payload["resource_external_id"] != job_ext]
            for s in foreign:
                rejections.append(Rejection(s.index, s.external_id, ErrorCode.RESOURCE_UNKNOWN.value,
                                            "resource_external_id không phải kênh của job đang xử lý", s.item_key))
            staged = [s for s in staged if s.payload["resource_external_id"] == job_ext]

        # Tham chiếu resource (content/metrics/events): phải có trong snapshot này hoặc trong kho canonical.
        canonical_ids: dict[str, str] = {}
        if kind != "resources" and staged:
            wanted = sorted({s.payload["resource_external_id"] for s in staged})
            canonical_ids, ambiguous = await merge.resolve_resources(conn, ctx.effective_owner, platform, wanted)
            in_snapshot = await repository.staged_external_ids(conn, snapshot_id, "resources", wanted)
            unknown = set(wanted) - set(canonical_ids) - in_snapshot
            if unknown:
                for s in staged:
                    ext = s.payload["resource_external_id"]
                    if ext in unknown:
                        why = ("resource_external_id khớp nhiều resource_type còn sống (không xác định được resource)"
                               if ext in ambiguous else
                               "resource_external_id chưa có trong snapshot hay kho, hoặc đã removed (gửi resource trước)")
                        rejections.append(Rejection(s.index, s.external_id, ErrorCode.RESOURCE_UNKNOWN.value, why,
                                                    s.item_key))
                staged = [s for s in staged if s.payload["resource_external_id"] not in unknown]

        verdicts = await self._classify(conn, ctx, snapshot_id, platform, kind, staged, canonical_ids)
        tally = {"created": 0, "updated": 0, "unchanged": 0, "stale": dupes}
        for v in verdicts:
            tally[v] += 1

        await repository.clear_rejections(conn, snapshot_id, kind, [s.item_key for s in staged])
        await repository.stage_many(conn, snapshot_id, kind,
                                    [(s.item_key, s.observed_at, s.payload, s.index, s.external_id) for s in staged])
        await repository.add_rejections(
            conn, snapshot_id, kind, [(r.index, r.external_id, r.code, r.message, r.item_key) for r in rejections],
            MAX_REJECTION_ROWS)
        await repository.touch(conn, snapshot_id, self._now())
        rejections.sort(key=lambda r: r.index)
        return {"received": len(items), "created": tally["created"], "updated": tally["updated"],
                "unchanged": tally["unchanged"], "stale_ignored": tally["stale"], "rejected": len(rejections),
                "rejections": [r.as_dict() for r in rejections]}

    async def _classify(self, conn, ctx, snapshot_id, platform, kind, staged: list[Staged],
                        canonical_ids: dict[str, str]) -> list[str]:
        """Báo trước kết quả hợp nhất (created/updated/unchanged/stale) cho từng bản ghi. Quyết định THẬT nằm ở commit."""
        if not staged:
            return []
        in_stage = await repository.staged_map(conn, snapshot_id, kind, [s.item_key for s in staged])
        if kind == "resources":
            keys = [(s.payload["resource_type"], s.payload["external_id"]) for s in staged]
            canonical = await merge.fetch_resources(conn, ctx.effective_owner, platform, keys)
            rows = [canonical.get(k) for k in keys]
            datas = [merge.resource_data(s.payload) for s in staged]
            immutable = False
        else:
            spec = merge.SPECS[kind]
            pairs = [(canonical_ids.get(s.payload["resource_external_id"]), spec.natural_of(s.payload)) for s in staged]
            lookup = [(rid, nat) for rid, nat in pairs if rid]
            canonical = await merge.fetch_children(conn, spec, lookup)
            rows = [canonical.get(p) if p[0] else None for p in pairs]
            datas = [spec.data_of(s.payload) for s in staged]
            immutable = spec.immutable
        out = []
        for s, row, data in zip(staged, rows, datas, strict=True):
            before = in_stage.get(s.item_key)
            if before is not None:  # đã có trong staging của chính snapshot này
                prev_payload = before["payload"]
                prev = {"observed_at": before["observed_at"], "removed": False,
                        "data": (merge.resource_data(prev_payload) if kind == "resources"
                                 else merge.SPECS[kind].data_of(prev_payload))}
                out.append(merge.decide(prev, s.observed_at, data, immutable=immutable))
            else:
                out.append(merge.decide(row, s.observed_at, data, immutable=immutable))
        return out

    # ── Xem trạng thái ────────────────────────────────────────────────────────────
    async def get(self, ctx: MachineContext, snapshot_id: str) -> dict:
        await self.expire_if_needed(snapshot_id)
        async with self._db.connection() as conn:
            snap = await self._load(conn, ctx, snapshot_id, lock=False)
            staged, rejected = await repository.counts(conn, snapshot_id)
            rows = await repository.rejection_rows(conn, snapshot_id, 100)
        out = public_snapshot(snap)
        out.update({"staged_counts": staged, "rejected_counts": rejected, "rejected_total": sum(rejected.values()),
                    "rejections": [{k: v for k, v in r.items() if v is not None} for r in rows],
                    "accept_partial": snap["accept_partial"], "result": snap["result"],
                    "failure_reason": snap["failure_reason"], "revision_from": snap["revision_from"],
                    "revision_to": snap["revision_to"],
                    "expires_at": snap["updated_at"] + timedelta(seconds=self._settings.snapshot_ttl_s)
                    if snap["status"] == "building" else None})
        return out

    # ── Commit ────────────────────────────────────────────────────────────────────
    async def commit(self, ctx: MachineContext, snapshot_id: str, req: schemas.CommitRequest,
                     guard: IdempotencyGuard, extra: dict) -> tuple[int, dict, bool]:
        """(status_code, envelope, replayed)."""
        extra["snapshot_id"] = snapshot_id
        await self.expire_if_needed(snapshot_id)
        replay = await self._precheck(guard)
        if replay:
            return replay.status_code, replay.body, True
        try:
            await self._claim(ctx, snapshot_id)
        except ApiProblem:
            replay = await self._precheck(guard)  # request trùng khoá vừa hoàn tất ở nơi khác => phát lại
            if replay:
                return replay.status_code, replay.body, True
            raise
        try:
            async with self._db.transaction() as conn:
                replay = await guard.reserve(conn)
                if replay:
                    return replay.status_code, replay.body, True
                body = envelope(await self._merge(conn, ctx, snapshot_id, req))
                await guard.complete(conn, 200, body)
            return 200, body, False
        except ApiProblem:
            await self._finish_failed_commit(snapshot_id, "building", None)  # lỗi hợp đồng: mở lại cho Tool gửi bù
            raise
        except _DB_DOWN:
            await self._finish_failed_commit(snapshot_id, "building", None)
            raise
        except Exception as exc:
            logger.exception("commit snapshot thất bại", extra={"snapshot_id": snapshot_id})
            await self._finish_failed_commit(snapshot_id, "failed", f"commit_error:{type(exc).__name__}")
            raise

    async def _precheck(self, guard: IdempotencyGuard) -> Replay | None:
        if not guard.enabled:
            return None
        async with self._db.connection() as conn:
            return await guard.precheck(conn)

    async def _claim(self, ctx: MachineContext, snapshot_id: str) -> None:
        async with self._db.transaction() as conn:
            snap = await self._load(conn, ctx, snapshot_id, lock=True)
            if snap["status"] != "building":
                raise self._not_building(snap)
            await repository.set_status(conn, snapshot_id, "committing", self._now(), expect="building")

    async def _finish_failed_commit(self, snapshot_id: str, status: str, reason: str | None) -> None:
        try:
            async with self._db.connection() as conn:
                await repository.set_status(conn, snapshot_id, status, self._now(), expect="committing", reason=reason)
        except Exception:  # noqa: BLE001 — DB chết: worker bảo trì sẽ xử lý snapshot kẹt ở committing
            logger.warning("Không cập nhật được trạng thái snapshot sau lỗi commit")

    async def _merge(self, conn: asyncpg.Connection, ctx: MachineContext, snapshot_id: str,
                     req: schemas.CommitRequest) -> dict:
        snap = await repository.get_for_client(conn, snapshot_id, ctx.effective_owner, ctx.client_id, lock=True)
        if snap is None or snap["status"] != "committing":
            raise conflict(Extra.SNAPSHOT_NOT_WRITABLE, "Snapshot không ở trạng thái committing")
        await self.ensure_platform(conn, ctx, snap["platform"])
        owner, client_id, platform = snap["owner_account_id"], snap["client_id"], snap["platform"]

        # 1) Kiểm hợp đồng — lệch thì KHÔNG đổi gì (rollback), snapshot quay về building.
        staged_counts, rejected_counts = await repository.counts(conn, snapshot_id)
        rejected_total = sum(rejected_counts.values())
        if rejected_total and not req.accept_partial:
            raise ApiProblem(ErrorCode.SNAPSHOT_INCOMPLETE, "Còn bản ghi bị từ chối (accept_partial=false)",
                             status_code=422, details={"rejected_total": rejected_total, "rejected": rejected_counts})
        mismatches = {}
        for kind, expected in snap["expected_counts"].items():
            have = staged_counts[kind] + rejected_counts[kind]
            if have != expected:
                mismatches[kind] = {"expected": expected, "received": have}
        if mismatches:
            raise ApiProblem(ErrorCode.SNAPSHOT_INCOMPLETE, "Số bản ghi không khớp expected_counts", status_code=422,
                             details={"mismatch": mismatches})
        staged = {k: merge.load_staged(await repository.staged_for_kind(conn, snapshot_id, k)) for k in KINDS}

        # 2) Merge staging -> canonical. Khoá thứ tự commit TRƯỚC khi đọc canonical (xem merge.py).
        now = self._now()
        await outbox.acquire_order_lock(conn)
        stats: dict[str, merge.MergeStats] = {}
        stats["resources"] = await merge.merge_resources(
            conn, owner=owner, client_id=client_id, platform=platform, items=staged["resources"],
            snapshot_id=snapshot_id, now=now, origin="tool")
        referenced = sorted({s.payload["resource_external_id"] for k in ("content", "metrics", "events")
                             for s in staged[k]})
        resource_ids = await merge.resolve_resource_ids(conn, owner, platform, referenced)
        for kind in ("content", "metrics", "events"):
            stats[kind] = await merge.merge_children(
                conn, merge.SPECS[kind], owner=owner, client_id=client_id, platform=platform, items=staged[kind],
                resource_ids=resource_ids, snapshot_id=snapshot_id, now=now)

        # 3) Suy luận removed cho snapshot full theo scope (trừ phần bị snapshot mới hơn bao phủ).
        removed = {k: 0 for k in KINDS}
        superseded: set[str] = set()
        if snap["snapshot_type"] == "full" and snap["scope"]:
            for newer in await repository.newer_full_scopes(conn, snap):
                superseded |= {kind for key, kind in _SCOPE_KIND.items() if newer.get(key)}
            scope = snap["scope"]
            if scope.get("resources") and "resources" not in superseded:
                removed["resources"] = await merge.infer_removed_resources(
                    conn, owner=owner, client_id=client_id, platform=platform,
                    present_ids=stats["resources"].present_ids, stats=stats["resources"], as_of=snap["observed_at"])
            for key, kind in (("content_window", "content"), ("metrics_window", "metrics"), ("events_window", "events")):
                window = scope.get(key)
                if window and kind not in superseded:
                    removed[kind] = await merge.infer_removed_children(
                        conn, merge.SPECS[kind], owner=owner, client_id=client_id, platform=platform,
                        window=(date.fromisoformat(window["from"]), date.fromisoformat(window["to"])),
                        present_ids=stats[kind].present_ids, stats=stats[kind], as_of=snap["observed_at"])
        warnings = []
        covered = {kind for key, kind in _SCOPE_KIND.items() if (snap["scope"] or {}).get(key)}
        if snap["snapshot_type"] == "full" and covered & superseded:
            warnings.append({"code": ErrorCode.SNAPSHOT_SUPERSEDED.value, "kinds": sorted(covered & superseded),
                             "message": "Có snapshot full mới hơn cùng phạm vi: bỏ qua phần suy luận removed"})

        # 4) Revision, trạng thái, current, outbox — cùng transaction.
        revisions = [r for st in stats.values() for r in st.revisions]
        head = await outbox.head_revision(conn)
        revision_from, revision_to = (min(revisions), max(revisions)) if revisions else (head, head)
        totals = {k: staged_counts[k] for k in KINDS}
        committed = await repository.mark_committed(
            conn, snapshot_id, now=now, result={"totals": totals, "removed": removed, "warnings": warnings,
                                                "stats": {k: v.as_dict() for k, v in stats.items()}},
            revision_from=revision_from, revision_to=revision_to, accept_partial=req.accept_partial)
        is_current = await repository.flip_current(conn, committed)
        await events.snapshot_committed(conn, committed, counts=totals, removed=removed,
                                        revision_from=revision_from, revision_to=revision_to)
        await repository.delete_staged(conn, snapshot_id)
        return {"snapshot_id": snapshot_id, "status": "committed", "totals": totals, "removed": removed,
                "rejected_total": rejected_total, "as_of": snap["observed_at"], "committed_at": now,
                "revision_from": revision_from, "revision_to": revision_to, "is_current": is_current,
                "stats": {k: v.as_dict() for k, v in stats.items()}, "warnings": warnings}

    # ── Bảo trì (worker + test) ───────────────────────────────────────────────────
    async def sweep(self) -> dict:
        """Đánh `abandoned` snapshot building quá TTL; `failed` cho snapshot kẹt ở committing; dọn staging rác."""
        now = self._now()
        async with self._db.transaction() as conn:
            abandoned = await repository.abandon_stale(conn, now - timedelta(seconds=self._settings.snapshot_ttl_s), now)
            failed = await repository.fail_stuck_committing(
                conn, now - timedelta(seconds=self._settings.committing_timeout_s), now)
            await repository.purge_dead_items(conn)
        return {"abandoned": abandoned, "failed": failed}

