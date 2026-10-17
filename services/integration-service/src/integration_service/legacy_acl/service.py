"""Nghiệp vụ của Legacy ACL: nhận lời gọi theo hợp đồng cũ, dịch thành thao tác canonical.

Nguyên tắc:
  * MỘT đường ghi: mọi thay đổi resource đi qua `ingest.merge` (merge_resources / remove_resource) dưới cùng khoá thứ tự
    commit, cùng `revision`, cùng sự kiện `resource.changed` — Analytics thấy dữ liệu y như từ Tool mới.
  * Run Queue legacy dùng `jobs` canonical làm nguồn sự thật về trạng thái.
  * Ownership luôn lấy từ token (`principal.sub`); body không bao giờ quyết định owner (schema từ chối `owner*`).
  * Legacy chỉ thấy/sửa resource `origin <> 'tool'`: không ghi đè dữ liệu do Tool đồng bộ.
Hình dạng request/response/status/`{detail}` giữ nguyên admin-server (xem mapper/errors).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

import asyncpg
from manager_sdk import Principal

from integration_service.config import Settings
from integration_service.db import Database
from integration_service.errors import ApiProblem
from integration_service.feed import repository as outbox
from integration_service.ingest import merge, schemas, validate
from integration_service.jobs import repository as jobs_repo
from integration_service.jobs import schemas as job_schemas
from integration_service.jobs.service import COLLECT_JOB_CAPABILITY, COLLECT_JOB_KIND, COLLECT_JOB_PRIORITY, JobService
from integration_service.legacy_acl import mapper, repository
from integration_service.legacy_acl.errors import LegacyError
from integration_service.platforms import repository as registry
from integration_service.ratelimit import FixedWindowLimiter
from integration_service.security import iso, new_id

LINK_CODE_TTL = timedelta(minutes=15)
_NOTHING = "Không có gì để cập nhật"
CAP_MANUAL_CHANNEL_MGMT = "capability:manual_channel_mgmt"   # identity_service/permissions/capabilities.py


class LegacyService:
    def __init__(self, db: Database, settings: Settings, now: Callable[[], datetime], jobs: JobService,
                 redeem_limiter: FixedWindowLimiter) -> None:
        self._db = db
        self._settings = settings
        self._now = now
        self._jobs = jobs
        self._redeem = redeem_limiter

    # ── Kiểm tra dùng chung (thông điệp = của legacy) ────────────────────────────────────

    @staticmethod
    async def _web_policy(conn: asyncpg.Connection, platform: str) -> dict:
        policy = await repository.get_policy(conn, platform)
        if not policy or not policy["web_entry_enabled"]:
            raise LegacyError(400, f"Nền tảng '{platform}' chưa được phép nhập từ website")
        return policy

    @staticmethod
    async def _owned_source(conn: asyncpg.Connection, source_id: str, principal: Principal, *, lock: bool = False) -> dict:
        source = await repository.get_source(conn, source_id, lock=lock)
        if not source:
            raise LegacyError(404, "Không tìm thấy nguồn (Source)")
        if source["owner_account_id"] != principal.sub:
            raise LegacyError(403, "Không có quyền thao tác nguồn này")
        return source

    @staticmethod
    async def _owned_resource(conn: asyncpg.Connection, resource_id: str, principal: Principal) -> dict:
        await outbox.acquire_order_lock(conn)   # THỨ TỰ KHOÁ: advisory (commit order) trước khoá dòng, như đường Tool
        row = await repository.get_resource(conn, resource_id, lock=True)
        if not row:
            raise LegacyError(404, "Không tìm thấy resource")
        if row["owner_account_id"] != principal.sub:
            raise LegacyError(403, "Không có quyền thao tác resource này")
        return row

    async def _audit(self, conn: asyncpg.Connection, account_id: str, event: str, target_type: str, target_id: str,
                     payload: dict | None = None) -> None:
        await repository.insert_audit(conn, account_id=account_id, event_type=event, target_type=target_type,
                                      target_id=target_id, payload=payload or {}, now=self._now())

    # ── Ghi resource: DUY NHẤT qua ingest.merge ────────────────────────────────────────

    async def _merge_one(self, conn: asyncpg.Connection, *, owner: str, platform: str, type_key: str, external_id: str,
                         fields: dict, legacy: dict, state: str, mode_legacy: str,
                         client_id: str, new_row: bool, now: datetime, observed: datetime | None = None) -> str:
        item: dict = {"platform": platform, "resource_type": type_key, "external_id": external_id,
                      "observed_at": iso(observed or now), "state": state, "extensions": {mapper.LEGACY_NS: legacy}}
        for key, limit in (("display_name", 200), ("username", 200), ("url", 500)):
            if fields.get(key):
                item[key] = fields[key][:limit]   # display_name mặc định = input_ref (≤500) nhưng canonical chỉ nhận ≤200
        if fields.get("country"):
            item["country"] = {"name": fields["country"][:100]}
        staged, rejections, _ = validate.validate_items("resources", [item], default_observed_at=observed or now,
                                                        model=schemas.ResourceUpsertItem)
        if rejections:   # không bỏ sót im lặng: dữ liệu legacy không biểu diễn được ở canonical => 422 kiểu FastAPI
            raise LegacyError(422, [{"type": "value_error", "loc": ["body"], "msg": rejections[0].message}])
        await outbox.acquire_order_lock(conn)
        stats = await merge.merge_resources(
            conn, owner=owner, client_id=client_id, platform=platform, items=staged, snapshot_id=None, now=now,
            origin=mapper.ORIGIN_WEB if new_row else None,
            execution_mode=mapper.MODE_TO_CANONICAL[mode_legacy])
        await merge.emit_resource_changes(conn, owner, platform, stats)
        return stats.outcomes[0][1]

    @staticmethod
    def _legacy_block(raw_type: str, input_ref: str, mode_legacy: str, base: dict | None = None) -> dict:
        base = base or {}
        return {"resource_type": raw_type, "input_ref": input_ref, "execution_mode": mode_legacy,
                "mapping_status": base.get("mapping_status", "unmapped"),
                "verification_status": base.get("verification_status", "unverified"),
                "canonical_external_id": base.get("canonical_external_id", "")}

    async def _create_one(self, conn: asyncpg.Connection, owner: str, platform: str, raw_type: str, input_ref: str,
                          fields: dict, source: dict, mode_legacy: str, now: datetime) -> tuple[dict | None, str]:
        """(hàng canonical, trạng thái): created | existing | tool_owned."""
        type_key, external_id = mapper.normalize_key(raw_type), mapper.external_id_of(input_ref)
        await outbox.acquire_order_lock(conn)   # đọc-rồi-ghi không bị chen ngang
        existing = await repository.find_by_key(conn, owner, platform, type_key, external_id)
        if existing is not None and existing["origin"] == "tool":
            return None, "tool_owned"   # không ghi đè dữ liệu do Tool đồng bộ
        if existing is not None and not existing["removed"]:
            await repository.add_assignment(conn, assignment_id=new_id("asg"), resource_id=existing["id"],
                                            source_id=source["id"], owner=owner, now=now)
            return await repository.get_resource(conn, existing["id"]), "existing"
        rid = await self._merge_one(
            conn, owner=owner, platform=platform, type_key=type_key, external_id=external_id, fields=fields,
            legacy=self._legacy_block(raw_type, input_ref, mode_legacy), state="active", mode_legacy=mode_legacy,
            client_id=existing["client_id"] if existing else mapper.LEGACY_CLIENT, new_row=existing is None, now=now)
        await repository.add_assignment(conn, assignment_id=new_id("asg"), resource_id=rid, source_id=source["id"],
                                        owner=owner, now=now)
        return await repository.get_resource(conn, rid), "created"

    async def _rewrite(self, conn: asyncpg.Connection, row: dict, updates: dict, now: datetime, *,
                       mode_legacy: str | None = None) -> None:
        """Ghi lại resource với thay đổi `updates` (hiển thị) và/hoặc `execution_mode`, giữ nguyên khoá tự nhiên."""
        view = mapper.resource_to_legacy(row)
        view.update(updates)
        mode = mode_legacy or view["execution_mode"]
        legacy = self._legacy_block(view["resource_type"], view["input_ref"], mode, mapper.legacy_ext(row))
        await self._merge_one(
            conn, owner=row["owner_account_id"], platform=row["platform"], type_key=row["resource_type"],
            external_id=row["external_id"], fields=view, legacy=legacy, state=row["state"], mode_legacy=mode,
            client_id=row["client_id"], new_row=False, now=now,
            observed=max(now, row["observed_at"]))   # sửa tay luôn thắng LWW kể cả khi đồng hồ Tool chạy trước
        if mode != "fallback_local":   # không còn chạy bằng Fallback Tool => huỷ job đang chờ
            await jobs_repo.cancel_pending_for_resource(conn, row["id"], now)

    async def _ensure_collect_job(self, conn: asyncpg.Connection, principal: Principal, resource_id: str,
                                  now: datetime) -> None:
        """User có `capability:manual_channel_mgmt` (không cần Tool cá nhân) tự khai báo kênh => tự tạo job
        `collect` cho Shared Collector nhặt (PHASE 3, docs/architecture/shared-collector-design-v1.md).
        Idempotent: `dedupe_key` theo resource + `jobs_collect_resource_active_idx` (0005) chặn job trùng kể cả
        khi có đường tạo job khác cùng resource không qua `dedupe_key`. Vượt hạn mức job của owner: bỏ qua lặng
        lẽ, không chặn việc thêm kênh — Shared Collector sẽ bắt kịp ở lượt sau.
        PHASE 4: `resource_owner_id=principal.sub` (= owner của resource, xem `JobService.create`) cho phép
        collector push dữ liệu hộ qua header `X-Delegated-Job` (snapshots/router.py `_delegated`)."""
        if not principal.has_perm(CAP_MANUAL_CHANNEL_MGMT):
            return
        try:
            await self._jobs.create(
                conn, principal, job_schemas.JobCreate(
                    resource_id=resource_id, kind=COLLECT_JOB_KIND, priority=COLLECT_JOB_PRIORITY,
                    required_capability=COLLECT_JOB_CAPABILITY, dedupe_key=f"collect:{resource_id}"),
                created_by=principal.sub, now=now, resource_owner_id=principal.sub)
        except (ApiProblem, asyncpg.UniqueViolationError):
            pass

    # ── /v1/resources ────────────────────────────────────────────────────────────────────

    async def available_platforms(self) -> dict:
        async with self._db.connection() as conn:
            policies = await repository.list_policies(conn)
        return {"items": [{"platform": p["platform"], "display_name": p["display_name"] or p["platform"]}
                          for p in policies if p["web_entry_enabled"]]}

    async def list_sources(self, principal: Principal) -> dict:
        async with self._db.connection() as conn:
            return {"items": [mapper.source_to_legacy(r) for r in await repository.list_sources(conn, principal.sub)]}

    async def create_source(self, principal: Principal, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            await self._web_policy(conn, body.platform)
            row = await repository.insert_source(
                conn, source_id=new_id("src"), owner=principal.sub, name=body.name.strip(), platform=body.platform,
                project_key=body.project_key.strip(), default_country=body.default_country.strip(), now=now)
        return mapper.source_to_legacy(row)

    async def update_source(self, principal: Principal, source_id: str, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            await outbox.acquire_order_lock(conn)   # advisory trước khoá dòng Source (đường tạo resource đi theo thứ tự này)
            source = await self._owned_source(conn, source_id, principal, lock=True)
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if not updates:
                raise LegacyError(400, _NOTHING)
            await repository.update_source(conn, source_id, updates, now)
        return {"ok": True}

    async def list_resources(self, principal: Principal, filters: dict) -> dict:
        async with self._db.connection() as conn:
            return {"items": await self._listing(conn, principal.sub, filters)}

    async def _listing(self, conn: asyncpg.Connection, owner: str | None, filters: dict) -> list[dict]:
        rows = await repository.list_resources(conn, owner, filters)
        sources = await repository.source_ids_for(conn, [r["id"] for r in rows])
        return [{**mapper.resource_to_legacy(r), "source_ids": sources.get(r["id"], [])} for r in rows]

    async def create_resource(self, principal: Principal, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            source = await self._owned_source(conn, body.source_instance_id, principal)
            policy = await self._web_policy(conn, body.platform)
            mode = "fallback_local" if policy["fallback_enabled"] else "disabled"
            input_ref = body.input_ref.strip()
            fields = {"username": body.username.strip(), "url": body.url.strip(),
                      "display_name": body.display_name.strip() or input_ref, "country": body.country.strip()}
            row, status = await self._create_one(conn, principal.sub, body.platform, body.resource_type, input_ref,
                                                 fields, source, mode, now)
            if status == "tool_owned":
                raise LegacyError(409, "Resource đã tồn tại do Tool đồng bộ; không thể nhập tay đè lên")
            if status == "created":
                await self._audit(conn, principal.sub, "resource_created", "resource", row["id"],
                                  {"platform": body.platform, "input_ref": input_ref, "origin": mapper.ORIGIN_WEB,
                                   "execution_mode": mode})
            if mode == "fallback_local":
                await self._ensure_collect_job(conn, principal, row["id"], now)
            return mapper.resource_to_legacy(row)

    async def import_resources(self, principal: Principal, body) -> dict:
        now = self._now()
        created: list[dict] = []
        skipped: list[dict] = []
        seen: set[str] = set()
        async with self._db.transaction() as conn:
            source = await self._owned_source(conn, body.source_instance_id, principal)
            policy = await self._web_policy(conn, body.platform)
            mode = "fallback_local" if policy["fallback_enabled"] else "disabled"
            for raw in body.lines:
                line = raw.strip()
                if not line:
                    continue
                if line in seen:
                    skipped.append({"input_ref": line, "reason": "duplicate_in_request"})
                    continue
                seen.add(line)
                fields = {"username": "", "url": line, "display_name": line, "country": ""}
                row, status = await self._create_one(conn, principal.sub, body.platform, body.resource_type, line,
                                                     fields, source, mode, now)
                if status == "created":
                    created.append(mapper.resource_to_legacy(row))
                    await self._audit(conn, principal.sub, "resource_created", "resource", row["id"],
                                      {"platform": body.platform, "input_ref": line, "origin": mapper.ORIGIN_WEB,
                                       "execution_mode": mode})
                else:   # đã có (canonical là danh tính duy nhất: không nhân đôi) hoặc do Tool quản lý
                    skipped.append({"input_ref": line, "reason": "already_exists" if status == "existing"
                                    else "managed_by_tool"})
        return {"created": created, "skipped": skipped, "created_count": len(created), "skipped_count": len(skipped)}

    async def update_resource(self, principal: Principal, resource_id: str, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            row = await self._owned_resource(conn, resource_id, principal)
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if not updates:
                raise LegacyError(400, _NOTHING)
            await self._rewrite(conn, row, updates, now)
            await self._audit(conn, principal.sub, "resource_updated", "resource", resource_id, dict(updates))
        return {"ok": True}

    async def delete_resource(self, principal: Principal, resource_id: str) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            await self._owned_resource(conn, resource_id, principal)
            if not await merge.remove_resource(conn, resource_id, now):
                raise LegacyError(404, "Không tìm thấy resource")
            await repository.delete_assignments(conn, resource_id)
            await jobs_repo.cancel_pending_for_resource(conn, resource_id, now)
            await self._audit(conn, principal.sub, "resource_deleted", "resource", resource_id)
        return {"ok": True}

    # ── /v1/ops: inventory ───────────────────────────────────────────────────────────────

    async def _with_owner_names(self, conn: asyncpg.Connection, rows: list[dict]) -> list[dict]:
        names = await repository.usernames(conn, sorted({r["owner_account_id"] for r in rows}))
        return [{**r, "owner_username": names.get(r["owner_account_id"], "")} for r in rows]

    async def inventory(self, filters: dict, owner: str) -> dict:
        async with self._db.connection() as conn:
            rows = await self._listing(conn, owner or None, filters)
            return {"items": await self._with_owner_names(conn, rows)}

    async def inventory_revision(self, owner: str) -> dict:
        async with self._db.connection() as conn:
            latest, count = await repository.inventory_revision(conn, owner or None)
        return {"revision": f"{mapper.legacy_iso(latest)}.{count}"}

    async def inventory_users(self) -> dict:
        async with self._db.connection() as conn:
            return {"items": await repository.list_inventory_owners(conn)}

    async def policies(self) -> dict:
        async with self._db.connection() as conn:
            return {"items": [mapper.policy_to_legacy(p) for p in await repository.list_policies(conn)]}

    async def ops_sources(self, owner: str) -> dict:
        async with self._db.connection() as conn:
            rows = [mapper.source_to_legacy(r) for r in await repository.list_sources(conn, owner or None)]
            return {"items": await self._with_owner_names(conn, rows)}

    async def update_policy(self, principal: Principal, platform: str, body) -> dict:
        if not mapper._KEY.match(platform):   # platform là khoá canonical (registry/Tool dùng cùng định danh)
            raise LegacyError(400, "Platform phải dạng chữ thường/số/gạch dưới, bắt đầu bằng chữ cái")
        now = self._now()
        async with self._db.transaction() as conn:
            if platform not in await registry.enabled_platforms(conn):
                # Nền tảng do Admin/dash_admin tạo ở Platform Registry; policy chỉ chỉnh HÀNH VI của nền tảng đã có.
                raise LegacyError(404, f"Nền tảng '{platform}' chưa được tạo (hoặc đang tắt) trong danh mục nền tảng")
            await repository.upsert_policy(conn, platform, body.web_entry_enabled, body.fallback_enabled,
                                           body.adapter_status, principal.sub, now)
            row = await repository.get_policy(conn, platform)
            await self._audit(conn, principal.sub, "platform_policy_changed", "policy", platform,
                              {"web_entry_enabled": row["web_entry_enabled"], "fallback_enabled": row["fallback_enabled"],
                               "adapter_status": row["adapter_status"]})
        return mapper.policy_to_legacy(row)

    async def set_execution_mode(self, principal: Principal, resource_id: str, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            await outbox.acquire_order_lock(conn)
            row = await repository.get_resource(conn, resource_id, lock=True)
            if not row:
                raise LegacyError(404, "Không tìm thấy resource")
            if row["owner_account_id"] != principal.sub and not principal.is_admin:
                raise LegacyError(403, "Không có quyền đổi chế độ resource này")
            await self._rewrite(conn, row, {}, now, mode_legacy=body.execution_mode)
            await self._audit(conn, principal.sub, "execution_mode_changed", "resource", resource_id,
                              {"execution_mode": body.execution_mode})
            # Khi chuyển sang fallback_local → tạo collect-job cho Shared Collector thu thập hộ.
            # _rewrite() đã cancel pending-job khi mode != fallback_local (dòng 154-155).
            if body.execution_mode == "fallback_local":
                await self._ensure_collect_job(conn, principal, resource_id, now)
        return {"ok": True}

    # ── /v1/ops: Run Queue ───────────────────────────────────────────────────────────────

    async def create_batch(self, principal: Principal, body) -> dict:
        now = self._now()
        async with self._db.transaction() as conn:
            row = await repository.insert_batch(conn, batch_id=new_id("bat"), created_by=principal.sub,
                                                platform=body.platform, name=body.name.strip(), now=now)
            await self._audit(conn, principal.sub, "batch_created", "batch", row["id"],
                              {"platform": body.platform, "name": row["name"]})
        return mapper.batch_to_legacy(row)

    async def list_batches(self, principal: Principal, mine_only: bool) -> dict:
        async with self._db.connection() as conn:
            rows = await repository.list_batches(conn, principal.sub if mine_only else None)
        return {"items": [mapper.batch_to_legacy(r) for r in rows]}

    async def get_batch(self, batch_id: str) -> dict:
        async with self._db.connection() as conn:
            batch = await repository.get_batch(conn, batch_id)
            if not batch:
                raise LegacyError(404, "Không tìm thấy batch")
            items = await repository.list_items(conn, batch_id)
        return {**mapper.batch_to_legacy(batch), "items": [mapper.item_to_legacy(i) for i in items]}

    async def add_items(self, principal: Principal, batch_id: str, body) -> dict:
        """Chỉ thêm resource thoả điều kiện; còn lại bị SKIP kèm lý do. Adapter sẵn sàng => tạo job canonical."""
        now = self._now()
        added: list[str] = []
        skipped: list[dict] = []
        async with self._db.transaction() as conn:
            batch = await repository.get_batch(conn, batch_id)
            if not batch:
                raise LegacyError(404, "Không tìm thấy batch")
            for rid in body.resource_ids:
                res = await repository.get_resource(conn, rid)
                reason = None
                if not res:
                    reason = "resource_not_found"
                elif res["platform"] != batch["platform"]:
                    reason = "platform_mismatch"
                elif res["state"] != "active":
                    reason = "resource_disabled"
                elif res["execution_mode"] != mapper.MODE_TO_CANONICAL["fallback_local"]:
                    reason = "execution_mode_not_fallback_local"
                policy = await repository.get_policy(conn, res["platform"]) if res and not reason else None
                if res and not reason and (not policy or not policy["fallback_enabled"]):
                    reason = "platform_fallback_disabled"
                if reason:
                    skipped.append({"resource_id": rid, "reason": reason})
                    continue
                item_id = new_id("itm")
                job_id = None
                status = "queued" if policy["adapter_status"] == "ready" else "blocked_no_adapter"
                if status == "queued":
                    try:
                        job, _ = await self._jobs.create(
                            conn, principal, job_schemas.JobCreate(
                                resource_id=rid, kind="resource.sync", dedupe_key=f"legacy-item:{item_id}",
                                payload={"batch_id": batch_id, "item_id": item_id}),
                            created_by=batch["created_by"], now=now)
                        job_id = job["id"]
                    except ApiProblem as exc:   # vd vượt hạn mức job của owner: không giả lập thành công
                        skipped.append({"resource_id": rid, "reason": str(exc.code.value).lower()})
                        continue
                assignment = await repository.first_assignment_id(conn, rid)
                await repository.insert_item(conn, item_id=item_id, batch_id=batch_id, resource_id=rid,
                                             assignment_id=assignment, owner=res["owner_account_id"],
                                             platform=res["platform"], status=status, job_id=job_id, now=now)
                await self._audit(conn, batch["created_by"], "item_queued", "item", item_id,
                                  {"batch_id": batch_id, "resource_id": rid, "status": status})
                added.append(item_id)
        return {"added": added, "skipped": skipped}

    async def cancel_item(self, principal: Principal, item_id: str) -> bool:
        now = self._now()
        async with self._db.transaction() as conn:
            item = await repository.get_item(conn, item_id, lock=True)
            if not item or mapper.item_to_legacy(item)["status"] in mapper.ITEM_TERMINAL:
                return False
            if item["job_id"]:
                await self._jobs.cancel(conn, principal, item["job_id"], now=now)
            await repository.set_item_status(conn, item_id, "cancelled", now)
            await self._audit(conn, principal.sub, "item_cancelled", "item", item_id)
        return True

    # ── /v1/viewer/links (pairing) ───────────────────────────────────────────────────────

    async def create_link_code(self, principal: Principal) -> dict:
        now = self._now()
        code, expires = secrets.token_hex(4).upper(), now + LINK_CODE_TTL
        async with self._db.transaction() as conn:
            await repository.insert_link_code(conn, code, principal.sub, now, expires)
        return {"code": code, "expires_at": mapper.legacy_iso(expires)}

    async def list_links(self, principal: Principal) -> dict:
        async with self._db.connection() as conn:
            rows = await repository.list_links(conn, principal.sub)
        return {"items": [{**r, "linked_at": mapper.legacy_iso(r["linked_at"])} for r in rows]}

    async def revoke_link(self, principal: Principal, tool_user_id: str) -> dict:
        async with self._db.transaction() as conn:
            if not await repository.revoke_link(conn, principal.sub, tool_user_id):
                raise LegacyError(404, "Không tìm thấy liên kết")
        return {"ok": True}

    async def redeem(self, body, ip: str) -> dict:
        """Mã là bằng chứng uỷ quyền (một lần, TTL 15 phút) + rate limit theo IP chống dò. Không cần JWT dashboard."""
        if not self._redeem.hit(f"{ip}:link-redeem"):
            retry = self._redeem.retry_after(f"{ip}:link-redeem")
            raise LegacyError(429, f"Quá nhiều lần thử. Đợi {retry} giây.", {"Retry-After": str(retry)})
        async with self._db.transaction() as conn:
            account = await repository.redeem_code(conn, body.code.strip().upper(), body.tool_user_id.strip(),
                                                   self._now())
        if account is None:
            raise LegacyError(400, "Mã không hợp lệ hoặc đã hết hạn")
        return {"ok": True}
