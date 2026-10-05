"""Áp dụng canonical vào read model (trong transaction do consumer mở)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import asyncpg

from analytics_service.readmodels import repository as repo
from analytics_service.readmodels.canonical import KINDS, BadItem, to_row

APPLY_LOCK_KEY = 7_340_032   # pg_advisory_xact_lock: tuần tự hoá các tiến trình cùng áp dụng revision


@dataclass
class ApplyResult:
    applied: dict[str, int] = field(default_factory=dict)
    rejected: int = 0

    @property
    def total(self) -> int:
        return sum(self.applied.values())


def item_dead_letter_id(kind: str, item) -> str:
    raw = json.dumps(item, sort_keys=True, default=str, ensure_ascii=False)
    return f"canon:{kind}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"  # noqa: S324 — không phải mật mã


async def apply_canonical(conn: asyncpg.Connection, pulled: dict[str, list[dict]], *, replace: bool = False) -> ApplyResult:
    """Upsert các bản ghi canonical đã kéo. Bản ghi sai -> dead_letters RIÊNG, các bản ghi còn lại vẫn áp dụng."""
    result = ApplyResult()
    if replace:
        await repo.clear_read_model(conn)
    for kind in KINDS:
        rows = []
        for item in pulled.get(kind, []):
            try:
                rows.append(to_row(kind, item))
            except BadItem as exc:
                result.rejected += 1
                await repo.put_dead_letter(conn, dl_id=item_dead_letter_id(kind, item), source="canonical", seq=None,
                                           type_=f"canonical.{kind}", error=str(exc), attempts=1,
                                           payload={"kind": kind, "item": item})
        await repo.upsert_rows(conn, kind, rows)
        result.applied[kind] = len(rows)
    return result
