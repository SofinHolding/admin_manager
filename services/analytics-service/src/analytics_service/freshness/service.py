"""Tính khối `freshness` từ trạng thái đồng bộ (bảng `sync_state`) — THUẦN trên SyncInfo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from analytics_service.dates import iso_utc, parse_ts


@dataclass
class SyncInfo:
    seal: int = 0
    snapshot: dict = field(default_factory=dict)    # snapshot_id, committed_at, observed_at, revision, applied_at
    sources: dict = field(default_factory=dict)     # {integration: {ok, last_ok_at, last_attempt_at, last_error}, ...}


async def load_sync_info(conn: asyncpg.Connection) -> SyncInfo:
    """MỘT truy vấn: seal (khoá cache) + trạng thái snapshot/nguồn."""
    info = SyncInfo()
    for r in await conn.fetch("SELECT key, num, value FROM sync_state WHERE key = ANY($1::text[])",
                              ["seal", "snapshot", "sources"]):
        if r["key"] == "seal":
            info.seal = int(r["num"])
        elif r["key"] == "snapshot":
            info.snapshot = r["value"] or {}
        elif r["key"] == "sources":
            info.sources = r["value"] or {}
    return info


def freshness_block(info: SyncInfo, as_of: datetime | None, now: datetime, stale_after_s: int) -> dict:
    snap = info.snapshot
    committed = parse_ts(snap.get("committed_at")) or parse_ts(snap.get("applied_at"))
    lag = round((now - committed).total_seconds(), 1) if committed else None
    integration = info.sources.get("integration") or {}
    source_down = integration.get("ok") is False
    stale = lag is None or lag > stale_after_s or source_down
    return {"as_of": iso_utc(as_of), "committed_at": iso_utc(committed), "snapshot_id": snap.get("snapshot_id"),
            "revision": snap.get("revision"), "lag_seconds": lag, "stale": bool(stale)}
