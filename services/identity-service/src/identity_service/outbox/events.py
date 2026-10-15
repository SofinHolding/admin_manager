"""Hàm phát sự kiện của Identity (docs/api/internal-feed-contract-v1.md §3). Luôn gọi trong transaction nghiệp vụ.

Dữ liệu sự kiện KHÔNG chứa email, hash hay token — chỉ định danh tối thiểu để consumer dựng read model.
"""

from __future__ import annotations

import asyncpg

from identity_service.outbox import repository as outbox


def _subject(account_id: str) -> str:
    return f"acc:{account_id}"


async def registered(conn: asyncpg.Connection, account: dict) -> None:
    data = {"account_id": account["id"], "username": account["username"], "role": account["role"],
            "status": account["status"]}
    await outbox.append(conn, type="account.registered", subject=_subject(account["id"]), data=data)


async def verified(conn: asyncpg.Connection, account_id: str) -> None:
    await outbox.append(conn, type="account.verified", subject=_subject(account_id), data={"account_id": account_id})


async def status_changed(conn: asyncpg.Connection, account_id: str, status: str, previous: str) -> None:
    await outbox.append(conn, type="account.status_changed", subject=_subject(account_id),
                        data={"account_id": account_id, "status": status, "previous": previous})


async def role_changed(conn: asyncpg.Connection, account_id: str, role: str, previous: str) -> None:
    await outbox.append(conn, type="account.role_changed", subject=_subject(account_id),
                        data={"account_id": account_id, "role": role, "previous": previous})


async def profile_updated(conn: asyncpg.Connection, account_id: str, username: str) -> None:
    await outbox.append(conn, type="account.profile_updated", subject=_subject(account_id),
                        data={"account_id": account_id, "username": username})


async def permission_changed(conn: asyncpg.Connection, account_id: str, permission: str, granted: bool) -> None:
    await outbox.append(conn, type="account.permission_changed", subject=_subject(account_id),
                        data={"account_id": account_id, "permission": permission, "granted": granted})


async def deleted(conn: asyncpg.Connection, account_id: str) -> None:
    await outbox.append(conn, type="account.deleted", subject=_subject(account_id), data={"account_id": account_id})


async def session_revoked(conn: asyncpg.Connection, account_id: str, sid: str | None = None) -> None:
    data = {"account_id": account_id}
    if sid:
        data["sid"] = sid
    await outbox.append(conn, type="session.revoked", subject=_subject(account_id), data=data)
