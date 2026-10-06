"""FeedConsumer: đồng bộ read model từ feed + canonical export. Xem docstring module `feed_consumer`.

Bảo đảm:
- At-least-once + idempotent: `processed_events(id)`; upsert theo revision nên replay không nhân đôi.
- Nguyên tử: áp read model + ghi event đã xử lý + dịch con trỏ + tăng seal trong MỘT transaction PostgreSQL.
- Nhất quán giữa các kind: kéo tất cả kind tới CÙNG một `head_revision` rồi áp một lần (không có trạng thái "nửa snapshot").
- Nguồn chết/hỏng: con trỏ giữ nguyên, ghi trạng thái nguồn (`stale`), KHÔNG ném ra vòng lặp nền.
- DB chết: bỏ qua vòng này (không dead-letter), thử lại sau.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from collections.abc import Callable
from datetime import datetime, timezone

import asyncpg
from manager_sdk import FeedClient, FeedError, backoff_delays

from analytics_service.config import Settings
from analytics_service.dates import iso_utc, parse_ts
from analytics_service.db import Database
from analytics_service.errors import DB_DOWN
from analytics_service.feed_consumer.handlers import (IDENTITY_TYPES, INTEGRATION_TYPES, apply_identity_event,
                                                       validate_integration_event)
from analytics_service.readmodels import repository as repo
from analytics_service.readmodels.apply import APPLY_LOCK_KEY, ApplyResult, apply_canonical, item_dead_letter_id
from analytics_service.readmodels.canonical import KINDS, to_row

logger = logging.getLogger("analytics.consumer")

MAX_PAGES_PER_CYCLE = 50


class SourceError(Exception):
    """Nguồn feed/canonical không dùng được (chết, từ chối khoá, trả sai định dạng)."""


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


class FeedConsumer:
    def __init__(self, db: Database, settings: Settings, *, integration: FeedClient | None = None,
                 identity: FeedClient | None = None, now: Callable[[], datetime] | None = None,
                 sleep=asyncio.sleep) -> None:
        self.db = db
        self.settings = settings
        self.integration = integration
        self.identity = identity
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self.cycles = 0

    # ── Tiện ích ────────────────────────────────────────────────────────────────

    async def _call(self, coro):
        """Chuẩn hoá MỌI lỗi phía nguồn thành SourceError (DB không đi qua đây)."""
        try:
            return await coro
        except FeedError as exc:
            raise SourceError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — JSON hỏng, lỗi giao vận lạ... đều coi là nguồn không dùng được
            raise SourceError(_safe_error(exc)) from exc

    async def _retry(self, fn) -> tuple[bool, int, str]:
        """Chạy `fn` tối đa `consumer_max_attempts` lần với backoff mũ + jitter. DB chết => ném ra (không dead-letter)."""
        attempts = self.settings.consumer_max_attempts
        delays = list(backoff_delays(max(attempts - 1, 0), base=self.settings.consumer_backoff_base_s, cap=10.0))
        last = ""
        for attempt in range(1, attempts + 1):
            try:
                await fn()
                return True, attempt, ""
            except DB_DOWN:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = _safe_error(exc)
                if attempt < attempts:
                    await self._sleep(delays[attempt - 1])
        return False, attempts, last

    async def _record_source(self, name: str, ok: bool, error: str = "") -> None:
        try:
            async with self.db.transaction() as conn:
                state = await repo.get_state(conn, "sources")
                state = {k: v for k, v in state.items() if not k.startswith("_")}
                entry = dict(state.get(name) or {})
                entry["ok"] = ok
                entry["last_attempt_at"] = iso_utc(self.now())
                if ok:
                    entry["last_ok_at"] = entry["last_attempt_at"]
                    entry["last_error"] = None
                else:
                    entry["last_error"] = error[:300]
                state[name] = entry
                await repo.put_state(conn, "sources", state)
        except DB_DOWN:
            pass

    # ── Một vòng đồng bộ ────────────────────────────────────────────────────────

    async def run_once(self) -> dict:
        """Một vòng: Identity rồi Integration. Không bao giờ ném (trừ huỷ tác vụ)."""
        async with self._lock:
            self.cycles += 1
            out: dict = {"identity": None, "integration": None}
            if self.identity is not None:
                out["identity"] = await self._guarded("identity", self._identity_cycle)
            if self.integration is not None:
                out["integration"] = await self._guarded("integration", self._integration_cycle)
            return out

    async def _guarded(self, name: str, cycle) -> dict:
        try:
            result = await cycle()
        except SourceError as exc:
            logger.warning("Nguồn %s không dùng được: %s", name, exc)
            await self._record_source(name, False, str(exc))
            return {"ok": False, "error": "source", "detail": str(exc)[:200]}
        except DB_DOWN as exc:
            logger.warning("DB không khả dụng khi đồng bộ %s: %s", name, type(exc).__name__)
            return {"ok": False, "error": "database"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — vòng lặp nền KHÔNG được chết
            logger.exception("Lỗi không lường trước khi đồng bộ %s", name)
            return {"ok": False, "error": "unexpected", "detail": _safe_error(exc)}
        await self._record_source(name, True)
        return {"ok": True, **result}

    # ── Identity ────────────────────────────────────────────────────────────────

    async def _identity_cycle(self) -> dict:
        async with self.db.connection() as conn:
            cursor = await repo.get_cursor(conn, "identity")
        handled = skipped = dead = 0
        for _ in range(MAX_PAGES_PER_CYCLE):
            page = await self._call(self.identity.feed(after=cursor, limit=self.settings.feed_page_size,
                                                       types=list(IDENTITY_TYPES)))
            events = self._events(page)
            await self._dead_letter_bad_events("identity", page)
            for event in events:
                result = await self._process_identity_event(event)
                handled += result == "ok"
                skipped += result == "dup"
                dead += result == "dead"
            new_cursor = self._cursor(page, cursor)
            if new_cursor != cursor:
                async with self.db.transaction() as conn:
                    await repo.set_cursor(conn, "identity", new_cursor)
                cursor = new_cursor
            if not page.get("has_more") or not events:
                break
        return {"handled": handled, "duplicates": skipped, "dead": dead, "cursor": cursor}

    async def _process_identity_event(self, event: dict) -> str:
        eid = event.get("id")
        if not isinstance(eid, str) or not eid:
            return await self._dead_letter_malformed("identity", event, "event thiếu `id`")
        async with self.db.connection() as conn:
            if eid in await repo.processed_ids(conn, [eid]):
                return "dup"
        changed = {"v": False}

        async def attempt() -> None:
            async with self.db.transaction() as conn:
                await asyncpg_lock(conn)
                changed["v"] = await apply_identity_event(conn, event)
                await repo.mark_processed(conn, "identity", [event])
                await repo.set_cursor(conn, "identity", max(int(event["seq"]), await repo.get_cursor(conn, "identity")))
                if changed["v"]:
                    await repo.bump_seal(conn)

        ok, attempts, error = await self._retry(attempt)
        if ok:
            return "ok"
        await self._dead_letter("identity", event, error, attempts)
        return "dead"

    # ── Integration ─────────────────────────────────────────────────────────────

    async def _integration_cycle(self) -> dict:
        async with self.db.connection() as conn:
            cursor = await repo.get_cursor(conn, "integration")
            canon_cursors = {k: await repo.get_cursor(conn, f"canonical.{k}") for k in KINDS}

        # 1) event mới
        events: list[dict] = []
        feed_cursor = cursor
        for _ in range(MAX_PAGES_PER_CYCLE):
            page = await self._call(self.integration.feed(after=feed_cursor, limit=self.settings.feed_page_size,
                                                          types=list(INTEGRATION_TYPES)))
            batch = self._events(page)
            await self._dead_letter_bad_events("integration", page)
            events += batch
            new_cursor = self._cursor(page, feed_cursor)
            progressed = new_cursor != feed_cursor
            feed_cursor = new_cursor
            if not page.get("has_more") or not batch or not progressed:
                break

        # 2) đối chiếu revision (bắt cả trường hợp mất event)
        summary = await self._call(self.integration.canonical_summary())
        head = summary.get("head_revision") if isinstance(summary, dict) else None
        if isinstance(head, bool) or not isinstance(head, int) or head < 0:
            raise SourceError("canonical/summary thiếu head_revision hợp lệ")
        behind = head > min(canon_cursors.values())

        # 3) kéo canonical tới CÙNG head
        pulled: dict[str, list[dict]] = {}
        if behind:
            pulled = await self._pull(canon_cursors, head)

        # 4) xác nhận event (retry -> dead letter), lọc trùng
        async with self.db.connection() as conn:
            seen = await repo.processed_ids(conn, [e["id"] for e in events if isinstance(e.get("id"), str)])
        good: list[dict] = []
        duplicates = dead = 0
        for event in events:
            eid = event.get("id")
            if not isinstance(eid, str) or not eid:
                dead += (await self._dead_letter_malformed("integration", event, "event thiếu `id`")) == "dead"
                continue
            if eid in seen:
                duplicates += 1
                continue

            async def check(ev=event) -> None:
                validate_integration_event(ev)

            ok, attempts, error = await self._retry(check)
            if ok:
                good.append(event)
            else:
                await self._dead_letter("integration", event, error, attempts)
                dead += 1

        # 5) áp dụng NGUYÊN TỬ
        applied = ApplyResult()
        async with self.db.transaction() as conn:
            await asyncpg_lock(conn)
            if behind:
                applied = await apply_canonical(conn, pulled)
                for kind in KINDS:
                    await repo.set_cursor(conn, f"canonical.{kind}", max(head, canon_cursors[kind]))
            new_events = await repo.mark_processed(conn, "integration", good)
            if feed_cursor > await repo.get_cursor(conn, "integration"):
                await repo.set_cursor(conn, "integration", feed_cursor)
            await self._update_snapshot_state(conn, good, head if behind else None)
            if applied.total or new_events:
                await repo.bump_seal(conn)
        return {"events": len(good), "duplicates": duplicates, "dead": dead, "applied": dict(applied.applied),
                "rejected": applied.rejected, "head": head, "cursor": feed_cursor}

    async def _pull(self, cursors: dict[str, int], head: int, *, from_zero: bool = False) -> dict[str, list[dict]]:
        pulled: dict[str, list[dict]] = {}
        for kind in KINDS:
            after = 0 if from_zero else cursors[kind]
            items: list[dict] = []
            for _ in range(10_000):
                page = await self._call(self.integration.canonical(kind, after_revision=after,
                                                                   limit=self.settings.canonical_page_size))
                batch = page.get("items") if isinstance(page, dict) else None
                if not isinstance(batch, list):
                    raise SourceError("canonical trả sai định dạng")
                items += [i for i in batch if not (isinstance(i, dict) and isinstance(i.get("revision"), int)
                                                    and i["revision"] > head)]
                nxt = page.get("next_revision")
                if not page.get("has_more") or not batch or not isinstance(nxt, int) or nxt <= after or nxt >= head:
                    break
                after = nxt
            pulled[kind] = items
        return pulled

    async def _update_snapshot_state(self, conn: asyncpg.Connection, good: list[dict], applied_head: int | None) -> None:
        snaps = [e for e in good if e.get("type") == "snapshot.committed"]
        state = await repo.get_state(conn, "snapshot")
        state = {k: v for k, v in state.items() if not k.startswith("_")}
        now_iso = iso_utc(self.now())
        changed = False
        if snaps:
            last = max(snaps, key=lambda e: int(e["seq"]))
            data = last["data"]
            committed = parse_ts(data.get("committed_at")) or parse_ts(last.get("time")) or self.now()
            if not state.get("committed_at") or committed >= (parse_ts(state["committed_at"]) or committed):
                state.update(snapshot_id=data.get("snapshot_id"), committed_at=iso_utc(committed),
                             observed_at=iso_utc(parse_ts(data.get("observed_at"))))
                changed = True
        if applied_head is not None:
            state["revision"] = applied_head
            state["applied_at"] = now_iso
            if not state.get("committed_at"):
                # Dữ liệu vào mà không có event (mất event): chưa biết thời điểm commit thật -> dùng lúc áp dụng.
                state["committed_at"] = now_iso
            changed = True
        if changed:
            await repo.put_state(conn, "snapshot", state)

    # ── Rebuild / replay / reconcile ───────────────────────────────────────────

    async def rebuild(self) -> dict:
        """Đặt revision về 0 và dựng lại read model: kéo TOÀN BỘ trước, rồi thay thế nguyên tử (nguồn chết => không đổi gì)."""
        if self.integration is None:
            raise SourceError("Integration chưa được cấu hình")
        async with self._lock:
            summary = await self._call(self.integration.canonical_summary())
            head = summary.get("head_revision") if isinstance(summary, dict) else None
            if isinstance(head, bool) or not isinstance(head, int):
                raise SourceError("canonical/summary thiếu head_revision hợp lệ")
            pulled = await self._pull({}, head, from_zero=True)
            async with self.db.transaction() as conn:
                await asyncpg_lock(conn)
                applied = await apply_canonical(conn, pulled, replace=True)
                for kind in KINDS:
                    await repo.set_cursor(conn, f"canonical.{kind}", head)
                state = await repo.get_state(conn, "snapshot")
                state = {k: v for k, v in state.items() if not k.startswith("_")}
                state["revision"] = head
                state["applied_at"] = iso_utc(self.now())
                state.setdefault("committed_at", state["applied_at"])
                await repo.put_state(conn, "snapshot", state)
                await repo.bump_seal(conn)
            return {"head_revision": head, "applied": dict(applied.applied), "rejected": applied.rejected}

    async def replay_dead_letters(self, limit: int = 100) -> dict:
        """Thử lại dead-letter còn mở: event thành công -> đánh dấu đã xử lý; bản ghi canonical -> áp vào read model."""
        async with self.db.connection() as conn:
            letters = await repo.open_dead_letters(conn, limit)
        replayed = failed = 0
        for dl in letters:
            try:
                await self._replay_one(dl)
                replayed += 1
            except DB_DOWN:
                raise
            except Exception as exc:  # noqa: BLE001
                failed += 1
                async with self.db.transaction() as conn:
                    await repo.fail_dead_letter(conn, dl["id"], _safe_error(exc))
        return {"replayed": replayed, "failed": failed, "remaining": len(letters) - replayed}

    async def _replay_one(self, dl: dict) -> None:
        payload = dl["payload"]
        source = dl["source"]
        async with self.db.transaction() as conn:
            await asyncpg_lock(conn)
            changed = False
            if source == "identity":
                seq = payload.get("seq") if isinstance(payload.get("seq"), int) else 0
                newer = await repo.newer_processed_types(conn, "identity", repo.subject_of("identity", payload), seq)
                changed = await apply_identity_event(conn, payload, newer_types=newer)   # không ghi đè cột đã có bản MỚI hơn
                await repo.mark_processed(conn, "identity", [payload])
            elif source == "integration":
                validate_integration_event(payload)
                await repo.mark_processed(conn, "integration", [payload])
            elif source == "canonical":
                row = to_row(payload["kind"], payload["item"])    # BadItem nếu vẫn sai
                await repo.upsert_rows(conn, payload["kind"], [row])
                changed = True
            else:
                raise ValueError("nguồn dead-letter không rõ")
            await repo.resolve_dead_letter(conn, dl["id"])
            if changed:
                await repo.bump_seal(conn)

    async def reconcile(self) -> dict:
        """So read model với `canonical/summary` (đếm + checksum) và báo chênh lệch."""
        if self.integration is None:
            raise SourceError("Integration chưa được cấu hình")
        remote = await self._call(self.integration.canonical_summary())
        async with self.db.connection() as conn:
            local = await repo.local_summary(conn)
        differences: list[dict] = []
        kinds: dict[str, dict] = {}
        for kind in KINDS:
            r_counts = (remote.get("counts") or {}).get(kind) or {}
            l_counts = local["counts"][kind]
            r_sum = (remote.get("checksum") or {}).get(kind)
            count_match = r_counts.get("total") == l_counts["total"] and r_counts.get("active") == l_counts["active"]
            checksum_match = r_sum == local["checksum"][kind]
            kinds[kind] = {"source": {"counts": r_counts, "checksum": r_sum},
                           "local": {"counts": l_counts, "checksum": local["checksum"][kind]},
                           "count_match": count_match, "checksum_match": checksum_match}
            if not count_match:
                differences.append({"kind": kind, "type": "count", "source": r_counts, "local": l_counts})
            if not checksum_match:
                differences.append({"kind": kind, "type": "checksum", "source": r_sum, "local": local["checksum"][kind]})
        r_head = remote.get("head_revision")
        result = {"ok": not differences, "head_revision": {"source": r_head, "local": local["head_revision"]},
                  "behind": isinstance(r_head, int) and r_head > local["head_revision"],
                  "kinds": kinds, "differences": differences, "checked_at": iso_utc(self.now())}
        if differences:
            logger.warning("Đối soát phát hiện chênh lệch: %s", [(d["kind"], d["type"]) for d in differences])
        async with self.db.transaction() as conn:
            await repo.put_state(conn, "reconcile", {"ok": result["ok"], "checked_at": result["checked_at"],
                                                      "differences": len(differences)})
        return result

    # ── Dead letter / định dạng ─────────────────────────────────────────────────

    async def _dead_letter(self, source: str, event: dict, error: str, attempts: int) -> None:
        async with self.db.transaction() as conn:
            await repo.put_dead_letter(conn, dl_id=event["id"], source=source, seq=int(event.get("seq") or 0),
                                       type_=str(event.get("type") or "?"), error=error, attempts=attempts, payload=event)

    async def _dead_letter_bad_events(self, source: str, page) -> None:
        """Event không có `seq` nguyên (không sắp thứ tự/đặt con trỏ được) KHÔNG được bỏ im lặng: vào dead-letter."""
        for event in self._bad_events(page):
            await self._dead_letter_malformed(source, event, "event thiếu `seq` nguyên hợp lệ")

    async def _dead_letter_malformed(self, source: str, event: dict, error: str) -> str:
        seq = event.get("seq") if isinstance(event, dict) else None
        if isinstance(seq, int) and not isinstance(seq, bool):
            dl_id = f"bad:{source}:{seq}"
        else:   # không có seq nguyên: định danh theo nội dung để các event hỏng khác nhau không đè nhau
            raw = json.dumps(event, sort_keys=True, default=str, ensure_ascii=False)
            dl_id = f"bad:{source}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"  # noqa: S324 — không phải mật mã
        async with self.db.transaction() as conn:
            await repo.put_dead_letter(conn, dl_id=dl_id, source=source, seq=seq if isinstance(seq, int) else None,
                                       type_=str((event or {}).get("type") or "?") if isinstance(event, dict) else "?",
                                       error=error, attempts=1, payload=event if isinstance(event, dict) else {})
        return "dead"

    @staticmethod
    def _events(page) -> list[dict]:
        events = page.get("events") if isinstance(page, dict) else None
        if not isinstance(events, list):
            raise SourceError("feed trả sai định dạng")
        out = []
        for e in events:
            if isinstance(e, dict) and isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool):
                out.append(e)
        return sorted(out, key=lambda e: e["seq"])

    @staticmethod
    def _bad_events(page) -> list:
        events = page.get("events") if isinstance(page, dict) else None
        if not isinstance(events, list):
            return []
        return [e for e in events if not (isinstance(e, dict) and isinstance(e.get("seq"), int)
                                          and not isinstance(e.get("seq"), bool))]

    @staticmethod
    def _cursor(page: dict, current: int) -> int:
        nxt = page.get("next_cursor")
        if isinstance(nxt, bool) or not isinstance(nxt, int):
            raise SourceError("feed thiếu next_cursor hợp lệ")
        return max(nxt, current)

    # ── Vòng lặp nền ────────────────────────────────────────────────────────────

    async def run_forever(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            result = await self.run_once()
            bad = any(isinstance(r, dict) and not r.get("ok") for r in result.values())
            failures = failures + 1 if bad else 0
            delay = self.settings.sync_interval_s
            if failures:
                delay = min(60.0, delay * (2 ** min(failures, 5))) * (1 + random.uniform(-0.2, 0.2))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


async def asyncpg_lock(conn: asyncpg.Connection) -> None:
    """Khoá advisory theo transaction: các tiến trình cùng áp dụng không giẫm lên nhau."""
    await conn.execute("SELECT pg_advisory_xact_lock($1)", APPLY_LOCK_KEY)


__all__ = ["FeedConsumer", "SourceError", "item_dead_letter_id"]
