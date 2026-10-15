"""Hàm phát sự kiện của Integration (docs/api/internal-feed-contract-v1.md §4). Luôn gọi trong transaction nghiệp vụ.

Dữ liệu sự kiện KHÔNG chứa secret, hash hay payload thô — chỉ định danh/đếm tối thiểu.
"""

from __future__ import annotations

import asyncpg

from integration_service.feed import repository as outbox
from integration_service.security import iso


async def snapshot_committed(conn: asyncpg.Connection, snap: dict, *, counts: dict, removed: dict,
                             revision_from: int, revision_to: int) -> None:
    await outbox.append(conn, type="snapshot.committed", subject=f"snap:{snap['id']}", data={
        "snapshot_id": snap["id"], "owner_account_id": snap["owner_account_id"], "client_id": snap["client_id"],
        "platform": snap["platform"], "snapshot_type": snap["snapshot_type"],
        "observed_at": iso(snap["observed_at"]), "committed_at": iso(snap["committed_at"]),
        "report_timezone": snap["report_timezone"], "counts": counts, "removed": removed,
        "revision_from": revision_from, "revision_to": revision_to})


async def resource_changed(conn: asyncpg.Connection, *, resource_id: str, owner_account_id: str, platform: str,
                           external_id: str, state: str, revision: int) -> None:
    await outbox.append(conn, type="resource.changed", subject=f"res:{resource_id}", data={
        "resource_id": resource_id, "owner_account_id": owner_account_id, "platform": platform,
        "external_id": external_id, "state": state, "revision": revision})


async def client_provisioned(conn: asyncpg.Connection, *, client_id: str, account_id: str, status: str) -> None:
    await outbox.append(conn, type="integration.client.provisioned", subject=f"cli:{client_id}",
                        data={"client_id": client_id, "account_id": account_id, "status": status})


async def credential_revoked(conn: asyncpg.Connection, *, client_id: str, key_id: str) -> None:
    await outbox.append(conn, type="credential.revoked", subject=f"cli:{client_id}",
                        data={"client_id": client_id, "key_id": key_id})
