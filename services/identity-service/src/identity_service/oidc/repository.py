"""SQL cho các bảng OIDC: oidc_clients, sso_sessions, oidc_auth_codes, oidc_refresh_tokens, oidc_session_clients,
oidc_backchannel_deliveries, oidc_cursor. Mọi bí mật chỉ lưu hash. Hàm nhận `conn` để gộp transaction."""

from __future__ import annotations

from datetime import datetime

import asyncpg

CLIENT_COLUMNS = ("client_id, name, type, secret_hash, redirect_uris, post_logout_redirect_uris, "
                  "backchannel_logout_uri, allowed_scopes, audience, first_party, status, created_at, updated_at")


def _row(r: asyncpg.Record | None) -> dict | None:
    return dict(r) if r else None


# ── clients ─────────────────────────────────────────────────────────────────────

async def client_get(conn: asyncpg.Connection, client_id: str) -> dict | None:
    return _row(await conn.fetchrow(f"SELECT {CLIENT_COLUMNS} FROM oidc_clients WHERE client_id = $1", client_id))


async def client_list(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch(f"SELECT {CLIENT_COLUMNS} FROM oidc_clients ORDER BY created_at, client_id")]


async def client_insert(conn: asyncpg.Connection, *, client_id: str, name: str, type: str, secret_hash: str,
                        redirect_uris: list[str], post_logout_redirect_uris: list[str],
                        backchannel_logout_uri: str, allowed_scopes: list[str], audience: str,
                        first_party: bool, now: datetime) -> dict:
    return dict(await conn.fetchrow(
        "INSERT INTO oidc_clients (client_id, name, type, secret_hash, redirect_uris, post_logout_redirect_uris, "
        "backchannel_logout_uri, allowed_scopes, audience, first_party, created_at, updated_at) "
        f"VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11) RETURNING {CLIENT_COLUMNS}",
        client_id, name, type, secret_hash, redirect_uris, post_logout_redirect_uris, backchannel_logout_uri,
        allowed_scopes, audience, first_party, now))


_CLIENT_UPDATABLE = {"name", "redirect_uris", "post_logout_redirect_uris", "backchannel_logout_uri",
                     "allowed_scopes", "audience", "first_party", "status", "secret_hash"}


async def client_update(conn: asyncpg.Connection, client_id: str, updates: dict, now: datetime) -> dict | None:
    items = [(k, v) for k, v in updates.items() if k in _CLIENT_UPDATABLE]
    items.append(("updated_at", now))
    sets = ", ".join(f"{k} = ${i + 2}" for i, (k, _) in enumerate(items))  # tên cột lấy từ whitelist
    return _row(await conn.fetchrow(f"UPDATE oidc_clients SET {sets} WHERE client_id = $1 RETURNING {CLIENT_COLUMNS}",
                                    client_id, *[v for _, v in items]))


async def client_delete(conn: asyncpg.Connection, client_id: str) -> bool:
    return (await conn.execute("DELETE FROM oidc_clients WHERE client_id = $1", client_id)).endswith(" 1")


# ── SSO sessions ────────────────────────────────────────────────────────────────

async def sso_create(conn: asyncpg.Connection, *, token_hash: str, sid: str, account_id: str, now: datetime,
                     expires_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO sso_sessions (token_hash, sid, account_id, auth_time, created_at, expires_at) "
        "VALUES ($1,$2,$3,$4,$4,$5)", token_hash, sid, account_id, now, expires_at)


async def sso_get_live(conn: asyncpg.Connection, token_hash: str, now: datetime) -> dict | None:
    """Phiên SSO còn sống: chưa hết hạn, `sessions` chưa bị thu hồi, tài khoản đang active."""
    return _row(await conn.fetchrow(
        "SELECT s.token_hash, s.sid, s.account_id, s.auth_time, s.expires_at FROM sso_sessions s "
        "JOIN sessions x ON x.sid = s.sid AND x.revoked_at IS NULL "
        "JOIN accounts a ON a.id = s.account_id AND a.status = 'active' "
        "WHERE s.token_hash = $1 AND s.expires_at > $2", token_hash, now))


async def sso_sid_by_hash(conn: asyncpg.Connection, token_hash: str) -> str | None:
    return await conn.fetchval("SELECT sid FROM sso_sessions WHERE token_hash = $1", token_hash)


# ── authorization codes ────────────────────────────────────────────────────────

async def code_insert(conn: asyncpg.Connection, *, code_hash: str, client_id: str, account_id: str, sid: str,
                      sso_sid: str | None, family_id: str, redirect_uri: str, code_challenge: str, nonce: str,
                      scope: str, auth_time: datetime, now: datetime, expires_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO oidc_auth_codes (code_hash, client_id, account_id, sid, sso_sid, family_id, redirect_uri, "
        "code_challenge, nonce, scope, auth_time, created_at, expires_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
        code_hash, client_id, account_id, sid, sso_sid, family_id, redirect_uri, code_challenge, nonce, scope,
        auth_time, now, expires_at)


async def code_lock(conn: asyncpg.Connection, code_hash: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM oidc_auth_codes WHERE code_hash = $1 FOR UPDATE", code_hash))


async def code_mark_used(conn: asyncpg.Connection, code_hash: str, now: datetime) -> None:
    await conn.execute("UPDATE oidc_auth_codes SET used_at = $2 WHERE code_hash = $1 AND used_at IS NULL",
                       code_hash, now)


# ── refresh tokens (family) ────────────────────────────────────────────────────

async def refresh_insert(conn: asyncpg.Connection, *, token_hash: str, family_id: str, client_id: str,
                         account_id: str, sid: str, scope: str, auth_time: datetime, now: datetime,
                         expires_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO oidc_refresh_tokens (token_hash, family_id, client_id, account_id, sid, scope, auth_time, "
        "created_at, expires_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        token_hash, family_id, client_id, account_id, sid, scope, auth_time, now, expires_at)


async def refresh_lock(conn: asyncpg.Connection, token_hash: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM oidc_refresh_tokens WHERE token_hash = $1 FOR UPDATE", token_hash))


async def refresh_get(conn: asyncpg.Connection, token_hash: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM oidc_refresh_tokens WHERE token_hash = $1", token_hash))


async def refresh_mark_used(conn: asyncpg.Connection, token_hash: str, now: datetime) -> None:
    await conn.execute("UPDATE oidc_refresh_tokens SET used_at = $2 WHERE token_hash = $1", token_hash, now)


async def refresh_revoke_family(conn: asyncpg.Connection, family_id: str, now: datetime) -> None:
    await conn.execute("UPDATE oidc_refresh_tokens SET revoked_at = $2 WHERE family_id = $1 AND revoked_at IS NULL",
                       family_id, now)


async def refresh_revoke_sid(conn: asyncpg.Connection, sid: str, now: datetime) -> None:
    await conn.execute("UPDATE oidc_refresh_tokens SET revoked_at = $2 WHERE sid = $1 AND revoked_at IS NULL",
                       sid, now)


# ── client ↔ session (căn cứ back-channel logout) ──────────────────────────────

async def session_client_add(conn: asyncpg.Connection, *, sid: str, client_id: str, account_id: str,
                             sso_sid: str | None, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO oidc_session_clients (sid, client_id, account_id, sso_sid, created_at) "
        "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (sid, client_id) DO NOTHING", sid, client_id, account_id, sso_sid, now)


async def sids_under_sso(conn: asyncpg.Connection, sso_sid: str) -> list[str]:
    return [r["sid"] for r in await conn.fetch("SELECT DISTINCT sid FROM oidc_session_clients WHERE sso_sid = $1",
                                               sso_sid)]


async def sids_of_client(conn: asyncpg.Connection, client_id: str) -> list[str]:
    return [r["sid"] for r in await conn.fetch("SELECT sid FROM oidc_session_clients WHERE client_id = $1", client_id)]


# ── back-channel: con trỏ, hàng đợi ────────────────────────────────────────────

BACKCHANNEL_CURSOR = "backchannel"


async def cursor_get(conn: asyncpg.Connection, name: str = BACKCHANNEL_CURSOR) -> int:
    await conn.execute("INSERT INTO oidc_cursor (name, seq) VALUES ($1, 0) ON CONFLICT DO NOTHING", name)
    return await conn.fetchval("SELECT seq FROM oidc_cursor WHERE name = $1 FOR UPDATE", name)


async def cursor_set(conn: asyncpg.Connection, seq: int, name: str = BACKCHANNEL_CURSOR) -> None:
    await conn.execute("UPDATE oidc_cursor SET seq = $2 WHERE name = $1", name, seq)


async def targets_for_session_revoked(conn: asyncpg.Connection, account_id: str, sid: str | None) -> list[dict]:
    """Các (client, sid) cần báo; chỉ client đang bật và có `backchannel_logout_uri`."""
    base = ("SELECT sc.sid, sc.client_id, sc.account_id FROM oidc_session_clients sc "
            "JOIN oidc_clients c ON c.client_id = sc.client_id AND c.status = 'active' "
            "AND c.backchannel_logout_uri <> '' WHERE ")
    if sid:
        rows = await conn.fetch(base + "sc.sid = $1", sid)
    else:
        rows = await conn.fetch(base + "sc.account_id = $1", account_id)
    return [dict(r) for r in rows]


async def delivery_enqueue(conn: asyncpg.Connection, *, event_id: str, client_id: str, account_id: str, sid: str,
                           now: datetime) -> None:
    await conn.execute(
        "INSERT INTO oidc_backchannel_deliveries (event_id, client_id, account_id, sid, next_attempt_at, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$5) ON CONFLICT (client_id, sid) DO NOTHING", event_id, client_id, account_id, sid, now)


async def deliveries_lease(conn: asyncpg.Connection, *, now: datetime, lease_until: datetime,
                           limit: int) -> list[dict]:
    """Nhận các lần giao đến hạn (SKIP LOCKED: nhiều instance không giao trùng); gia hạn `next_attempt_at` làm lease."""
    rows = await conn.fetch(
        "UPDATE oidc_backchannel_deliveries d SET next_attempt_at = $2 WHERE d.id IN ("
        "SELECT id FROM oidc_backchannel_deliveries WHERE status = 'pending' AND next_attempt_at <= $1 "
        "ORDER BY next_attempt_at, id LIMIT $3 FOR UPDATE SKIP LOCKED) RETURNING d.*", now, lease_until, limit)
    return [dict(r) for r in rows]


async def delivery_done(conn: asyncpg.Connection, delivery_id: int, now: datetime) -> None:
    await conn.execute("UPDATE oidc_backchannel_deliveries SET status='delivered', delivered_at=$2, "
                       "attempts = attempts + 1, last_error = '' WHERE id = $1", delivery_id, now)


async def delivery_failed(conn: asyncpg.Connection, delivery_id: int, *, error: str, next_attempt_at: datetime,
                          dead: bool) -> None:
    await conn.execute(
        "UPDATE oidc_backchannel_deliveries SET attempts = attempts + 1, last_error = $2, next_attempt_at = $3, "
        "status = CASE WHEN $4 THEN 'dead' ELSE 'pending' END WHERE id = $1",
        delivery_id, error[:500], next_attempt_at, dead)
