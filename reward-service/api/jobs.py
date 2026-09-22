"""Endpoint `/v1/reward/jobs/*` — plan C.3. Chủ sở hữu = account trong token; `admin` đọc mọi job
(pause/stop cũng cho phép admin — xem ma trận quyền A.3.3).

Mô hình job đa người dùng: mỗi job thuộc đúng 1 `account_id`; cấu hình (guild/channel/command/
pattern/timeouts) được CHỤP từ `reward_discord_credentials` + `overrides` vào `reward_jobs` lúc tạo
— sửa credentials sau đó KHÔNG ảnh hưởng job đã tạo. Chạy job qua `engine/worker_manager.py`
(D.3) — mỗi job 1 task nền + 1 `GatewaySession` riêng.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import hashlib
import io
import json
import logging
import re
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from openpyxl import Workbook
from openpyxl.styles import Font

from domain.idempotency import item_key
from domain.item_state import assert_transition
from domain.parser import ParsedRow, normalize_username, parse_input, validate_rows
from engine.job_runner import LockHeldError
from engine.worker_manager import CommandNotFoundError, CredentialInvalidError, TooManyJobsError, WorkerManager
from security import AuthContext, Security
from store.pool import Pool
from store.repositories import attempts as attempts_repo
from store.repositories import credentials as credentials_repo
from store.repositories import distributions as dist_repo
from store.repositories import events as events_repo
from store.repositories import items as items_repo
from store.repositories import jobs as jobs_repo
from store.repositories import lock as lock_repo

logger = logging.getLogger("reward.jobs")

_RUNNABLE_STATUSES = ("validated", "paused", "stopped")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)



class OverridesBody(BaseModel):
    guild_id: str | None = None
    channel_id: str | None = None
    command_name: str | None = None
    confirm_mode: str | None = None
    success_pattern: str | None = None
    failure_pattern: str | None = None
    leveling_bot_id: str | None = None
    delay_ms: int | None = Field(None, ge=0)
    jitter_ms: int | None = Field(None, ge=0)
    max_item_retries: int | None = Field(None, ge=0)
    unknown_pause_threshold: int | None = Field(None, ge=1)


class ItemIn(BaseModel):
    username: str
    point: int


class JobCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    raw_text: str | None = None
    items: list[ItemIn] | None = None
    overrides: OverridesBody | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "JobCreateBody":
        if (self.raw_text is None) == (self.items is None):
            raise ValueError("Phải cung cấp đúng một trong hai: raw_text hoặc items")
        return self


class ResolveBody(BaseModel):
    to: Literal["success", "pending"]
    note: str = ""


def _rows_from_body(body: JobCreateBody) -> list[ParsedRow]:
    if body.raw_text is not None:
        return parse_input(body.raw_text)
    rows: list[ParsedRow] = []
    for i, it in enumerate(body.items or []):
        valid = bool(it.username.strip()) and it.point > 0
        rows.append(ParsedRow(
            i, it.username, normalize_username(it.username), it.point if valid else None, valid))
    return rows


def _source_hash(body: JobCreateBody) -> str:
    raw = body.raw_text if body.raw_text is not None else json.dumps(
        [(it.username, it.point) for it in (body.items or [])])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_job_config(guild_id: str | None, channel_id: str | None, confirm_mode: str,
                          success_pattern: str | None, failure_pattern: str | None) -> None:
    if not guild_id or not channel_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Thiếu guild_id/channel_id — cấu hình Discord (credentials) chưa đủ hoặc overrides thiếu")
    if confirm_mode not in ("off", "reply"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirm_mode phải là 'off' hoặc 'reply'")
    for pattern in (success_pattern, failure_pattern):
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Regex không hợp lệ: {exc}") from exc


def _job_summary(job: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "id": job["id"], "name": job["name"], "status": job["status"], "total_items": job["total_items"],
        "counts": counts, "created_at": job["created_at"], "started_at": job["started_at"],
        "finished_at": job["finished_at"], "owner_username": job.get("owner_username"),
    }


def make_router(security: Security, pool: Pool, worker_manager: WorkerManager) -> APIRouter:
    router = APIRouter(prefix="/v1/reward/jobs", tags=["reward-jobs"])
    Reward = Depends(security.require_reward_user)
    Active = Depends(security.require_active_account)

    async def _owned_job(conn, job_id: int, ctx: AuthContext) -> dict[str, Any]:
        job = await jobs_repo.get_job(conn, job_id)
        if job is None or (ctx.token_role != "admin" and job["account_id"] != ctx.account_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy job")
        return job

    # ── Tạo + xem ────────────────────────────────────────────────────────────

    @router.post("", status_code=status.HTTP_201_CREATED, summary="Tạo job mới — parse + validate ngay")
    async def create_job(body: JobCreateBody, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            cred = await credentials_repo.get_by_account(conn, ctx.account_id)

        ov = body.overrides or OverridesBody()
        guild_id = ov.guild_id or (cred["guild_id"] if cred else None)
        channel_id = ov.channel_id or (cred["channel_id"] if cred else None)
        command_name = ov.command_name or (cred["command_name"] if cred else "give-xp")
        confirm_mode = ov.confirm_mode or (cred["confirm_mode"] if cred else "reply")
        success_pattern = ov.success_pattern if ov.success_pattern is not None else (
            cred["success_pattern"] if cred else None)
        failure_pattern = ov.failure_pattern if ov.failure_pattern is not None else (
            cred["failure_pattern"] if cred else None)
        leveling_bot_id = ov.leveling_bot_id if ov.leveling_bot_id is not None else (
            cred["leveling_bot_id"] if cred else None)
        delay_ms = ov.delay_ms if ov.delay_ms is not None else (cred["delay_ms"] if cred else 3000)
        jitter_ms = ov.jitter_ms if ov.jitter_ms is not None else (cred["jitter_ms"] if cred else 500)
        max_item_retries = ov.max_item_retries if ov.max_item_retries is not None else 3
        unknown_pause_threshold = ov.unknown_pause_threshold if ov.unknown_pause_threshold is not None else 5

        _validate_job_config(guild_id, channel_id, confirm_mode, success_pattern, failure_pattern)

        async with pool.acquire() as conn:
            job_id = await jobs_repo.create_job(
                conn, account_id=ctx.account_id, name=body.name, status="draft",
                guild_id=guild_id, channel_id=channel_id, command_name=command_name,
                confirm_mode=confirm_mode, success_pattern=success_pattern, failure_pattern=failure_pattern,
                leveling_bot_id=leveling_bot_id, delay_ms=delay_ms, jitter_ms=jitter_ms,
                max_item_retries=max_item_retries, unknown_pause_threshold=unknown_pause_threshold,
                total_items=0, source_name=body.name, source_hash=_source_hash(body))
            await events_repo.add_event(conn, job_id=job_id, type="created", actor=ctx.account_id)

            result = await _run_validation(conn, job_id, _rows_from_body(body))

        return {"job_id": job_id, **result}

    @router.get("", summary="Danh sách job — discord chỉ thấy job của mình")
    async def list_jobs(
        status_: str | None = Query(None, alias="status"), limit: int = 50, offset: int = 0,
        account_id: str | None = None, ctx: AuthContext = Reward,
    ) -> dict:
        owner = account_id if ctx.token_role == "admin" else ctx.account_id
        async with pool.acquire() as conn:
            rows, total = await jobs_repo.list_jobs(
                conn, account_id=owner, status=status_, limit=min(max(limit, 1), 200), offset=max(offset, 0))
            out = []
            for row in rows:
                counts = await items_repo.counts(conn, row["id"])
                out.append(_job_summary(row, counts))
        return {"jobs": out, "total": total}

    @router.get("/{job_id}", summary="Chi tiết job + counts 8 chỉ số + issues")
    async def get_job(job_id: int, ctx: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
            counts = await items_repo.counts(conn, job_id)
            issues = await jobs_repo.list_issues(conn, job_id)
        return {**job, "counts": counts, "issues": issues}

    @router.get("/{job_id}/items", summary="Trạng thái từng item")
    async def list_items(
        job_id: int, status_: str | None = Query(None, alias="status"), limit: int = 50, offset: int = 0,
        q: str | None = None, ctx: AuthContext = Reward,
    ) -> dict:
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)
            rows, total = await items_repo.list_page(
                conn, job_id, status=status_, q=q, limit=min(max(limit, 1), 200), offset=max(offset, 0))
        return {"items": rows, "total": total}

    @router.get("/{job_id}/items/{item_id}/attempts", summary="Vết điều tra: mọi attempt của item")
    async def list_attempts(job_id: int, item_id: int, ctx: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)
            item = await items_repo.get_item(conn, item_id)
            if item is None or item["job_id"] != job_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy item")
            attempts = await attempts_repo.list_by_item(conn, item_id)
        return {"attempts": attempts}

    @router.get("/{job_id}/events", summary="Nhật ký sự kiện job")
    async def list_events(job_id: int, after_id: int | None = None, ctx: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)
            events = await events_repo.list_events(conn, job_id, after_id=after_id)
        return {"events": events}

    @router.get("/{job_id}/export.csv", summary="Xuất CSV kết quả job")
    async def export_csv(job_id: int, ctx: AuthContext = Reward):
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)
            rows = await items_repo.list_all(conn, job_id)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "row_index", "username", "discord_user_id", "point", "status", "confirmation_level",
            "failure_code", "first_sent_at", "finalized_at"])
        for r in rows:
            writer.writerow([
                r["row_index"], r["raw_username"], r["resolved_user_id"], r["point"], r["status"],
                r["confirmation_level"], r["failure_code"], r["first_sent_at"], r["finalized_at"]])
        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="job_{job_id}.csv"'}
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)

    @router.get("/{job_id}/export.xlsx", summary="Xuất Excel (.xlsx) kết quả job")
    async def export_xlsx(job_id: int, ctx: AuthContext = Reward):
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)
            rows = await items_repo.list_all(conn, job_id)
        wb = Workbook()
        ws = wb.active
        ws.title = "Kết quả"
        cols = ["row_index", "username", "discord_user_id", "point", "status", "confirmation_level",
                "failure_code", "first_sent_at", "finalized_at"]
        ws.append(cols)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rows:
            ws.append([
                r["row_index"], r["raw_username"], r["resolved_user_id"], r["point"], r["status"],
                r["confirmation_level"], r["failure_code"],
                str(r["first_sent_at"]) if r["first_sent_at"] else None,
                str(r["finalized_at"]) if r["finalized_at"] else None,
            ])
        for col_cells in ws.columns:
            width = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(width + 2, 40)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="job_{job_id}.xlsx"'}
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers)

    # ── SSE ──────────────────────────────────────────────────────────────────

    @router.get("/{job_id}/stream", summary="SSE realtime: counts + item + job")
    async def stream(job_id: int, ctx: AuthContext = Reward) -> StreamingResponse:
        async with pool.acquire() as conn:
            await _owned_job(conn, job_id, ctx)

        async def gen() -> AsyncIterator[str]:
            last_counts: dict | None = None
            last_status: str | None = None
            last_event_id = 0
            last_ping = 0.0
            loop = asyncio.get_event_loop()
            while True:
                async with pool.acquire() as conn:
                    job = await jobs_repo.get_job(conn, job_id)
                    if job is None:
                        break
                    counts = await items_repo.counts(conn, job_id)
                    new_events = await events_repo.list_events(conn, job_id, after_id=last_event_id)
                if counts != last_counts:
                    last_counts = counts
                    yield f"event: counts\ndata: {json.dumps(counts)}\n\n"
                if job["status"] != last_status:
                    last_status = job["status"]
                    yield f"event: job\ndata: {json.dumps({'status': job['status']})}\n\n"
                for ev in new_events:
                    last_event_id = max(last_event_id, ev["id"])
                    if ev["type"] in ("auto_pause", "operator_resolve", "stopped", "recovery"):
                        yield f"event: item\ndata: {json.dumps({'type': ev['type']}, default=str)}\n\n"
                if job["status"] in ("completed", "stopped", "invalid"):
                    yield ": bye\n\n"
                    break
                now = loop.time()
                if now - last_ping >= 15.0:
                    last_ping = now
                    yield ": ping\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ── Validate / run / pause / resume / stop ──────────────────────────────

    @router.post("/{job_id}/validate", summary="Chạy lại validate cho job draft/invalid")
    async def validate_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
            if job["status"] not in ("draft", "invalid", "validated"):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, f"Không thể validate job ở trạng thái '{job['status']}'")
            existing_items = await items_repo.list_all(conn, job_id)
            rows = [
                ParsedRow(r["row_index"], r["raw_username"], r["normalized_username"], r["point"], True)
                for r in existing_items
            ]
            await jobs_repo.clear_issues(conn, job_id)
            issues = validate_rows(rows)
            new_status = "invalid" if any(i.severity == "error" for i in issues) else "validated"
            await jobs_repo.insert_issues(
                conn, job_id, [(i.row_index, i.severity, i.code, i.message) for i in issues])
            await jobs_repo.set_status(conn, job_id, new_status, validated_at=_now())
            counts = await items_repo.counts(conn, job_id)
        return {"status": new_status, "issues": [i.__dict__ for i in issues], "counts": counts}

    @router.post("/{job_id}/run", status_code=status.HTTP_202_ACCEPTED, summary="Chạy job (validated/paused/stopped)")
    async def run_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
        if job["status"] not in _RUNNABLE_STATUSES:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Không thể chạy job ở trạng thái '{job['status']}'")
        try:
            await worker_manager.start(job_id)
        except TooManyJobsError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
        except CredentialInvalidError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except LockHeldError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except CommandNotFoundError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return {"status": "running"}

    @router.post("/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED, summary="= run trên job paused")
    async def resume_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        return await run_job(job_id, ctx, _r)  # type: ignore[arg-type]

    @router.post("/{job_id}/pause", summary="Dừng sau item hiện tại; item còn lại giữ pending")
    async def pause_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
        if job["status"] != "running":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Job không ở trạng thái running (hiện tại: {job['status']})")
        paused_in_proc = await worker_manager.request_pause(job_id)
        if not paused_in_proc:
            # Không chạy trong tiến trình này (hiếm, single-worker) — vẫn cập nhật DB cho nhất quán.
            async with pool.acquire() as conn:
                await jobs_repo.set_status(conn, job_id, "paused")
        else:
            await worker_manager.wait_stopped(job_id, timeout_s=30.0)
        async with pool.acquire() as conn:
            final = await jobs_repo.get_job(conn, job_id)
        return {"status": final["status"]}

    @router.post("/{job_id}/stop", summary="Dừng hẳn")
    async def stop_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
        if job["status"] not in ("running", "paused"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Job không thể dừng ở trạng thái '{job['status']}'")
        if job["status"] == "running":
            stopped_in_proc = await worker_manager.request_stop(job_id)
            if stopped_in_proc:
                await worker_manager.wait_stopped(job_id, timeout_s=30.0)
        async with pool.acquire() as conn:
            await jobs_repo.set_status(conn, job_id, "stopped")
            await lock_repo.release(conn, job_id=job_id)
            await events_repo.add_event(conn, job_id=job_id, type="stopped", actor=ctx.account_id)
            final = await jobs_repo.get_job(conn, job_id)
        return {"status": final["status"]}

    # ── Item resolve (operator) ─────────────────────────────────────────────

    @router.post("/{job_id}/items/{item_id}/resolve", summary="Operator resolve item 'unknown'")
    async def resolve_item(
        job_id: int, item_id: int, body: ResolveBody, ctx: AuthContext = Active, _r: AuthContext = Reward,
    ) -> dict:
        async with pool.acquire() as conn, conn.transaction():
            job = await _owned_job(conn, job_id, ctx)
            item = await items_repo.get_item(conn, item_id)
            if item is None or item["job_id"] != job_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy item")
            try:
                assert_transition(item["status"], body.to, actor="operator")
            except ValueError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

            if body.to == "success":
                if not item["resolved_user_id"]:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Không thể xác nhận thành công: item chưa resolve được discord_user_id")
                await items_repo.finalize(
                    conn, item_id=item_id, status="success", confirmation_level="none",
                    resolved_by="operator")
                # Cần 1 attempt để tham chiếu — dùng attempt gần nhất của item (luôn tồn tại: write-ahead).
                attempts = await attempts_repo.list_by_item(conn, item_id)
                attempt_id = attempts[-1]["id"] if attempts else None
                if attempt_id is not None:
                    await dist_repo.insert_once(
                        conn, item_id=item_id, attempt_id=attempt_id, job_id=job_id,
                        account_id=job["account_id"], discord_user_id=item["resolved_user_id"],
                        point=item["point"], evidence={"level": "operator", "note": body.note})
            else:
                await items_repo.set_status(conn, item_id, "pending")

            await events_repo.add_event(
                conn, job_id=job_id, type="operator_resolve", actor=ctx.account_id,
                payload={"item_id": item_id, "to": body.to, "note": body.note})
        return {"ok": True, "status": body.to}

    # ── Xoá ──────────────────────────────────────────────────────────────────

    @router.delete("/{job_id}", summary="Xoá job draft/invalid/validated không có phân phối")
    async def delete_job(job_id: int, ctx: AuthContext = Active, _r: AuthContext = Reward) -> dict:
        async with pool.acquire() as conn:
            job = await _owned_job(conn, job_id, ctx)
            if job["status"] not in ("draft", "invalid", "validated"):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, f"Không thể xoá job ở trạng thái '{job['status']}'")
            dist_count = await dist_repo.count_for_job(conn, job_id)
            if dist_count > 0:
                raise HTTPException(status.HTTP_409_CONFLICT, "Không thể xoá: job đã có phân phối điểm")
            await jobs_repo.delete_job(conn, job_id)
        return {"ok": True}

    return router


async def _run_validation(conn, job_id: int, rows: list[ParsedRow]) -> dict[str, Any]:
    """Chèn item hợp lệ + `reward_validation_issues`, trả `{status, total_items, issues, counts}`."""
    issues = validate_rows(rows)
    valid_rows = [r for r in rows if r.valid]
    to_insert = [
        {
            "row_index": r.row_index, "raw_username": r.raw_username,
            "normalized_username": r.normalized_username, "point": r.point,
            "idempotency_key": item_key(
                job_id=job_id, row_index=r.row_index, normalized_username=r.normalized_username, point=r.point),
        }
        for r in valid_rows
    ]
    if to_insert:
        await items_repo.insert_items(conn, job_id, to_insert)
    await jobs_repo.set_totals(conn, job_id, len(to_insert))
    await jobs_repo.insert_issues(
        conn, job_id, [(i.row_index, i.severity, i.code, i.message) for i in issues])
    new_status = "invalid" if any(i.severity == "error" for i in issues) else "validated"
    await jobs_repo.set_status(conn, job_id, new_status, validated_at=_now())
    await events_repo.add_event(conn, job_id=job_id, type="validated", actor="system")
    counts = await items_repo.counts(conn, job_id)
    return {
        "status": new_status, "total_items": len(to_insert),
        "issues": [i.__dict__ for i in issues], "counts": counts,
    }


