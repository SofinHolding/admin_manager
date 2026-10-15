"""Đồng bộ một chiều legacy -> identity_db. Nguồn dựng bằng DDL legacy nhúng sẵn (service không import code legacy)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import asyncpg
import bcrypt
import pytest

from identity_service import legacy_sync
from identity_service.security import hash_token

from conftest import _dsn, login

pytestmark = pytest.mark.needs_pg

LEGACY_SQLITE_DDL = """
CREATE TABLE invite_keys (key TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'viewer', label TEXT NOT NULL DEFAULT '',
  max_uses INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE accounts (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'viewer', invite_key TEXT NOT NULL,
  email_verified INTEGER NOT NULL DEFAULT 0, verify_token TEXT NOT NULL DEFAULT '',
  verify_token_at TEXT NOT NULL DEFAULT '', reset_token TEXT NOT NULL DEFAULT '',
  reset_token_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_login TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE refresh_tokens (token TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
"""

LEGACY_PG_DDL = (LEGACY_SQLITE_DDL.replace("INTEGER NOT NULL DEFAULT 0, verify_token", "boolean NOT NULL DEFAULT false, verify_token")
                 .replace("revoked INTEGER NOT NULL DEFAULT 0", "revoked boolean NOT NULL DEFAULT false")
                 .replace("TEXT", "text").replace("INTEGER", "integer"))

PW_OLD = "mat-khau-cu-123"
HASH_OLD = bcrypt.hashpw(PW_OLD.encode(), bcrypt.gensalt(rounds=4)).decode()  # cost thấp cho test nhanh
FUTURE = "2999-01-01T00:00:00+00:00"
NOW = "2026-10-05T10:00:00+00:00"

KEYS = [
    ("AAAA-1111", "admin", "Admin key", 3, 1, NOW, FUTURE, "active"),
    ("BBBB-2222", "viewer", "", 5, 5, NOW, "", "active"),
    ("CCCC-3333", "dash_admin", "Đã thu hồi", 2, 0, NOW, "", "revoked"),
]
ACCOUNTS = [  # id, username, email, role, verified, verify_token, verify_at, reset_token, reset_at, status
    ("a" * 32, "quan_tri", "quan.tri@example.com", "admin", 1, "", "", "", "", "active"),
    ("b" * 32, "dang_cho", "dang.cho@example.com", "viewer", 0, "tok-verify-dang-cho", NOW, "", "", "pending"),
    ("c" * 32, "quen_mk", "quen.mk@example.com", "viewer", 1, "", "", "reset-token-uuid-0001", NOW, "active"),
    ("d" * 32, "doi_mk", "doi.mk@example.com", "viewer", 1, "", "", "123456", NOW, "active"),
    ("e" * 32, "bi_khoa", "bi.khoa@example.com", "discord", 1, "", "", "", "", "suspended"),
]
REFRESH = [("refresh-token-quan-tri", "a" * 32, NOW, FUTURE, 0), ("refresh-da-thu-hoi", "a" * 32, NOW, FUTURE, 1),
           ("refresh-mo-coi", "z" * 32, NOW, FUTURE, 0)]


def build_sqlite(path: Path, *, extra_accounts=(), keys=KEYS, refresh=REFRESH) -> str:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SQLITE_DDL)
    conn.executemany("INSERT INTO invite_keys VALUES (?,?,?,?,?,?,?,?)", keys)
    for a in (*ACCOUNTS, *extra_accounts):
        conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (a[0], a[1], a[2], HASH_OLD, a[3], "AAAA-1111", a[4], a[5], a[6], a[7], a[8], NOW, "", a[9]))
    conn.executemany("INSERT INTO refresh_tokens VALUES (?,?,?,?,?)", refresh)
    conn.commit()
    conn.close()
    return "sqlite:///" + str(path).replace("\\", "/")


@pytest.fixture
def source(tmp_path) -> str:
    return build_sqlite(tmp_path / "legacy.db")


async def acc(db, username):
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM accounts WHERE username=$1", username)
    return dict(row) if row else None


# ── Nhập dữ liệu ────────────────────────────────────────────────────────────────

async def test_sync_imports_everything_preserving_ids_hashes_and_usage(db, source):
    r = await legacy_sync.sync(_dsn(), source)
    assert (r.invite_keys.inserted, r.accounts.inserted, r.refresh_tokens.inserted) == (3, 5, 2)
    a = await acc(db, "quan_tri")
    assert a["id"] == "a" * 32 and a["password_hash"] == HASH_OLD and a["role"] == "admin"
    assert a["email_verified"] is True and a["status"] == "active" and a["last_login"] is None
    async with db.connection() as conn:
        k = await conn.fetchrow("SELECT * FROM invite_keys WHERE key='AAAA-1111'")
        assert (k["used_count"], k["max_uses"], k["role"], k["status"]) == (1, 3, "admin", "active")
        assert k["expires_at"].year == 2999
        assert (await conn.fetchrow("SELECT expires_at FROM invite_keys WHERE key='BBBB-2222'"))["expires_at"] is None
        assert await conn.fetchval("SELECT revoked FROM refresh_tokens WHERE token_hash=$1",
                                   hash_token("refresh-da-thu-hoi")) is True


async def test_second_run_changes_nothing(db, source):
    await legacy_sync.sync(_dsn(), source)
    r = await legacy_sync.sync(_dsn(), source)
    assert (r.invite_keys.unchanged, r.accounts.unchanged, r.refresh_tokens.unchanged) == (3, 5, 2)
    assert (r.invite_keys.inserted + r.accounts.inserted + r.refresh_tokens.inserted
            + r.invite_keys.updated + r.accounts.updated + r.refresh_tokens.updated) == 0


async def _events(db):
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT type, data FROM outbox ORDER BY seq")
    return [(r["type"], json.loads(r["data"]) if isinstance(r["data"], str) else dict(r["data"])) for r in rows]


async def test_new_accounts_emit_registered_and_verified_so_other_services_can_project_them(db, source):
    await legacy_sync.sync(_dsn(), source)
    evs = await _events(db)
    registered = {d["account_id"]: d for t, d in evs if t == "account.registered"}
    verified = {d["account_id"] for t, d in evs if t == "account.verified"}
    assert set(registered) == {c * 32 for c in "abcde"}
    assert (registered["a" * 32]["username"], registered["a" * 32]["role"], registered["a" * 32]["status"]) == ("quan_tri", "admin", "active")
    assert registered["e" * 32]["status"] == "suspended" and registered["b" * 32]["status"] == "pending"
    assert verified == {c * 32 for c in "acde"}          # "dang_cho" (b) chưa xác thực email nên không có account.verified
    assert all("email" not in d and "password_hash" not in d for _, d in evs)


async def test_imported_accounts_get_the_same_default_capabilities_as_a_fresh_registration(db, source):
    await legacy_sync.sync(_dsn(), source)
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT account_id, permission FROM account_permissions")
    caps: dict[str, set[str]] = {}
    for r in rows:
        caps.setdefault(r["account_id"], set()).add(r["permission"])
    assert caps["c" * 32] == {"capability:web_access", "capability:dashboard_access", "capability:manual_channel_mgmt"}   # viewer
    assert "capability:api_write" in caps["a" * 32] and "capability:tool_integration" not in caps["a" * 32]               # admin
    assert "e" * 32 not in caps                                                              # discord: không capability mặc định
    evs = await _events(db)
    assert sum(t == "account.permission_changed" for t, _ in evs) == sum(len(v) for v in caps.values())
    await legacy_sync.sync(_dsn(), source)                                                    # chạy lại không cấp thêm
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM account_permissions") == len(rows)


async def test_rerun_and_dry_run_do_not_emit_events(db, source, tmp_path):
    r = await legacy_sync.sync(_dsn(), build_sqlite(tmp_path / "dry.db"), dry_run=True)
    assert r.accounts.inserted == 5 and await _events(db) == []         # dry-run rollback cả sự kiện
    await legacy_sync.sync(_dsn(), source)
    first = len(await _events(db))
    await legacy_sync.sync(_dsn(), source)
    assert len(await _events(db)) == first


async def test_changes_in_legacy_propagate_on_next_run(db, tmp_path, source):
    await legacy_sync.sync(_dsn(), source)
    path = Path(source.removeprefix("sqlite:///"))
    conn = sqlite3.connect(path)
    new_hash = bcrypt.hashpw(b"mat-khau-moi-999", bcrypt.gensalt(rounds=4)).decode()
    conn.execute("UPDATE accounts SET password_hash=?, status='suspended' WHERE username='quan_tri'", (new_hash,))
    conn.execute("UPDATE invite_keys SET used_count=2 WHERE key='AAAA-1111'")
    conn.commit()
    conn.close()
    r = await legacy_sync.sync(_dsn(), source)
    assert r.accounts.updated == 1 and r.invite_keys.updated == 1
    a = await acc(db, "quan_tri")
    assert a["password_hash"] == new_hash and a["status"] == "suspended"


async def test_plaintext_legacy_tokens_are_hashed_on_import(db, source):
    await legacy_sync.sync(_dsn(), source)
    waiting = await acc(db, "dang_cho")
    assert waiting["verify_token_hash"] == hash_token("tok-verify-dang-cho")
    async with db.connection() as conn:
        dump = " ".join(str(v) for r in await conn.fetch("SELECT * FROM accounts") for v in r.values())
        dump += " ".join(r["token_hash"] for r in await conn.fetch("SELECT token_hash FROM refresh_tokens"))
    for plaintext in ("tok-verify-dang-cho", "reset-token-uuid-0001", "refresh-token-quan-tri", "123456"):
        assert plaintext not in dump


async def test_numeric_legacy_reset_value_becomes_change_code_not_reset_token(db, source):
    """Legacy dùng chung cột: mã 6 số (đổi mật khẩu) phải tách khỏi token reset khi nhập."""
    await legacy_sync.sync(_dsn(), source)
    code, link = await acc(db, "doi_mk"), await acc(db, "quen_mk")
    assert code["change_code_hash"] == hash_token("123456") and code["reset_token_hash"] == ""
    assert link["reset_token_hash"] == hash_token("reset-token-uuid-0001") and link["change_code_hash"] == ""


async def test_pending_verify_link_and_old_password_and_old_session_work_via_the_api(db, client, source, clock):
    """Điều kiện chuyển không gián đoạn: link đang chờ, mật khẩu cũ và phiên đang đăng nhập còn dùng được."""
    await legacy_sync.sync(_dsn(), source)
    # FakeClock bắt đầu ở 2077; cập nhật token timestamps để các link/token chưa hết hạn (TTL 24h / 1h).
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET verify_token_at=$1 WHERE username='dang_cho'", clock())
        await conn.execute("UPDATE accounts SET reset_token_at=$1 WHERE username='quen_mk'", clock())
    # mật khẩu cũ (bcrypt do legacy tạo) đăng nhập được
    r = await login(client, "quan_tri", password=PW_OLD)
    assert r.status_code == 200 and r.json()["user"]["id"] == "a" * 32
    # link xác thực email đang nằm trong hộp thư người dùng vẫn bấm được
    v = await client.get("/v1/auth/verify-email", params={"token": "tok-verify-dang-cho"})
    assert v.status_code == 200
    assert (await acc(db, "dang_cho"))["status"] == "active"
    # refresh token cũ còn hiệu lực, token đã thu hồi thì không
    ok = await client.post("/v1/auth/refresh", json={"refresh_token": "refresh-token-quan-tri"})
    assert ok.status_code == 200
    bad = await client.post("/v1/auth/refresh", json={"refresh_token": "refresh-da-thu-hoi"})
    assert bad.status_code == 401
    # link đặt lại mật khẩu đang chờ dùng được
    rp = await client.post("/v1/auth/reset-password",
                           json={"token": "reset-token-uuid-0001", "new_password": "mat-khau-moi-999"})
    assert rp.status_code == 200
    # tài khoản bị khoá ở legacy vẫn bị khoá
    assert (await login(client, "bi_khoa", password=PW_OLD)).status_code == 403
    # key đã dùng 5/5 không đăng ký thêm được
    full = await client.post("/v1/auth/register", json={
        "key": "BBBB-2222", "username": "nguoi_moi", "password": "mat-khau-12345", "email": "moi@example.com"})
    assert full.status_code == 400


# ── Dữ liệu xấu / xung đột ──────────────────────────────────────────────────────

async def test_invalid_rows_are_skipped_with_reasons_and_the_rest_is_imported(db, tmp_path):
    keys = [*KEYS, ("DDDD-4444", "root", "", 1, 0, NOW, "", "active"), ("EEEE-5555", "viewer", "", 1, 9, NOW, "", "active")]
    extra = [("f" * 32, "role_la", "role.la@example.com", "superuser", 1, "", "", "", "", "active")]
    src = build_sqlite(tmp_path / "bad.db", extra_accounts=extra, keys=keys)
    r = await legacy_sync.sync(_dsn(), src)
    assert r.invite_keys.inserted == 3 and len(r.invite_keys.skipped) == 2
    assert r.accounts.inserted == 5 and "role_la" not in [a["username"] for a in [await acc(db, "role_la")] if a]
    assert any("superuser" in s for s in r.accounts.skipped)


async def test_username_conflict_between_different_ids_is_reported_not_fatal(db, tmp_path):
    src = build_sqlite(tmp_path / "a.db")
    await legacy_sync.sync(_dsn(), src)
    # nguồn mới: cùng username nhưng id khác (không thể xảy ra ở legacy, nhưng DB đích phải bảo vệ được)
    conn = sqlite3.connect(Path(src.removeprefix("sqlite:///")))
    conn.execute("UPDATE accounts SET id=? WHERE username='dang_cho'", ("9" * 32,))
    conn.commit()
    conn.close()
    r = await legacy_sync.sync(_dsn(), src)
    assert any("dang_cho" in s and "trùng" in s for s in r.accounts.skipped)
    assert r.accounts.unchanged == 4  # các dòng còn lại không bị ảnh hưởng, transaction không hỏng


async def test_orphan_refresh_tokens_are_skipped(db, source):
    r = await legacy_sync.sync(_dsn(), source)
    assert any("mồ côi" in s for s in r.refresh_tokens.skipped) and r.refresh_tokens.inserted == 2


# ── An toàn ─────────────────────────────────────────────────────────────────────

async def test_dry_run_rolls_back(db, source):
    r = await legacy_sync.sync(_dsn(), source, dry_run=True)
    assert r.accounts.inserted == 5
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM accounts") == 0
        assert await conn.fetchval("SELECT count(*) FROM invite_keys") == 0


async def test_refuses_after_identity_started_serving_real_traffic(db, source):
    await legacy_sync.sync(_dsn(), source)
    async with db.connection() as conn:
        await conn.execute("INSERT INTO auth_audit (account_id, event_type) VALUES ('x', 'register')")
    with pytest.raises(legacy_sync.SyncRefused, match="auth_audit"):
        await legacy_sync.sync(_dsn(), source)
    assert (await legacy_sync.sync(_dsn(), source, force=True)).accounts.unchanged == 5


async def test_source_is_never_modified(db, source):
    path = Path(source.removeprefix("sqlite:///"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    await legacy_sync.sync(_dsn(), source)
    await legacy_sync.parity(_dsn(), source)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


async def test_missing_source_file_and_bad_scheme(db, tmp_path):
    with pytest.raises(legacy_sync.SyncRefused):
        await legacy_sync.sync(_dsn(), "sqlite:///" + str(tmp_path / "khong_co.db").replace("\\", "/"))
    with pytest.raises(legacy_sync.SyncRefused):
        await legacy_sync.sync(_dsn(), "mysql://x")


# ── Parity ──────────────────────────────────────────────────────────────────────

async def test_parity_ok_after_sync_and_detects_drift(db, source):
    await legacy_sync.sync(_dsn(), source)
    ok = await legacy_sync.parity(_dsn(), source)
    assert ok["ok"] and ok["accounts"]["source"] == ok["accounts"]["target"] == 5
    assert ok["refresh_tokens"]["missing_in_target"] == 0

    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET role='admin' WHERE username='dang_cho'")
        await conn.execute("DELETE FROM accounts WHERE username='bi_khoa'")
        await conn.execute("UPDATE invite_keys SET used_count=0 WHERE key='AAAA-1111'")
    bad = await legacy_sync.parity(_dsn(), source)
    assert not bad["ok"]
    assert bad["accounts"]["missing_in_target"] == ["e" * 32]
    assert bad["accounts"]["mismatched"] == [{"id": "b" * 32, "fields": ["role"]}]
    assert bad["invite_keys"]["mismatched"] == ["AAAA-1111"]


async def test_parity_before_any_sync_reports_everything_missing(db, source):
    r = await legacy_sync.parity(_dsn(), source)
    assert not r["ok"] and len(r["accounts"]["missing_in_target"]) == 5


# ── Nguồn Postgres thật (mô phỏng DB legacy `ufsync` bằng một database tạm) ────────

import devenv  # noqa: E402

ADMIN_URL = devenv.pg_dsn(admin=True) if devenv.pg_ready() else ""


async def test_postgres_source_end_to_end_and_it_is_read_only(db):
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute("DROP DATABASE IF EXISTS legacy_sim_db")
        await admin.execute("CREATE DATABASE legacy_sim_db")
    finally:
        await admin.close()
    src_url = ADMIN_URL.rsplit("/", 1)[0] + "/legacy_sim_db"
    try:
        src = await asyncpg.connect(src_url)
        await src.execute(LEGACY_PG_DDL)
        await src.executemany("INSERT INTO invite_keys VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", KEYS)
        for a in ACCOUNTS:
            await src.execute(
                "INSERT INTO accounts VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
                a[0], a[1], a[2], HASH_OLD, a[3], "AAAA-1111", bool(a[4]), a[5], a[6], a[7], a[8], NOW, "", a[9])
        await src.executemany("INSERT INTO refresh_tokens VALUES ($1,$2,$3,$4,$5)",
                              [(t, i, c, e, bool(r)) for t, i, c, e, r in REFRESH])
        before = await src.fetchval("SELECT count(*) FROM accounts")
        await src.close()

        r = await legacy_sync.sync(_dsn(), src_url)
        assert (r.invite_keys.inserted, r.accounts.inserted, r.refresh_tokens.inserted) == (3, 5, 2)
        assert (await legacy_sync.parity(_dsn(), src_url))["ok"]

        src = await asyncpg.connect(src_url)
        assert await src.fetchval("SELECT count(*) FROM accounts") == before  # nguồn không đổi
        await src.close()
    finally:
        admin = await asyncpg.connect(ADMIN_URL)
        await admin.execute("DROP DATABASE IF EXISTS legacy_sim_db WITH (FORCE)")
        await admin.close()


# ── CLI ─────────────────────────────────────────────────────────────────────────

def test_cli_dry_run_and_parity(db, source, monkeypatch, capsys):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", _dsn())
    assert legacy_sync.main(["--source", source, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "CHẠY THỬ" in out and "accounts: +5" in out
    assert legacy_sync.main(["--source", source, "--parity"]) == 1  # chưa đồng bộ nên chưa khớp
    assert legacy_sync.main(["--source", source]) == 0
    assert legacy_sync.main(["--source", source, "--parity"]) == 0


def test_cli_fails_cleanly_without_config(monkeypatch, capsys, source):
    monkeypatch.delenv("IDENTITY_DATABASE_URL", raising=False)
    assert legacy_sync.main(["--source", source]) == 2
    assert "IDENTITY_DATABASE_URL" in capsys.readouterr().err
