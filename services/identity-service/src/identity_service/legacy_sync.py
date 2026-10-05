"""Đồng bộ MỘT CHIỀU từ DB legacy (admin-server) sang `identity_db` — bước "shadow" của Phase 1.2/1.3.

    python -m identity_service.legacy_sync --source postgresql://reader:***@host/ufsync [--dry-run] [--parity]
    python -m identity_service.legacy_sync --source sqlite:///C:/path/admin-auth.db

Nguyên tắc:
  * CHỈ ĐỌC nguồn (Postgres: transaction read-only; SQLite: mở chế độ ro). Không bao giờ ghi vào legacy.
  * Giữ nguyên `id`, bcrypt hash, `used_count` để token đang dùng và mật khẩu cũ còn hiệu lực sau khi chuyển.
  * Token legacy lưu bản rõ (verify, reset, refresh) được BĂM sha256 khi nhập: link đang chờ trong email và phiên
    đang đăng nhập vẫn dùng được, nhưng identity_db không chứa token rõ.
  * Idempotent: chạy lại chỉ cập nhật phần khác biệt. Toàn bộ chạy trong MỘT transaction (hoặc không gì cả).
  * Từ chối chạy nếu identity-service đã phục vụ lưu lượng thật (có bản ghi `auth_audit`), trừ khi --force:
    sau cutover, identity là writer duy nhất và nguồn legacy không còn được ghi đè lên nó.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg

from identity_service.security import hash_token

_ROLES = {"viewer", "admin", "discord", "dash_admin"}
_KEY_STATUS = {"active", "suspended", "revoked"}
_ACCOUNT_STATUS = {"pending", "active", "suspended"}
_NUMERIC_CODE = re.compile(r"^\d{6}$")


class SyncRefused(Exception):
    pass


@dataclass
class TableReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    invite_keys: TableReport = field(default_factory=TableReport)
    accounts: TableReport = field(default_factory=TableReport)
    refresh_tokens: TableReport = field(default_factory=TableReport)
    dry_run: bool = False

    def lines(self) -> list[str]:
        out = []
        for name in ("invite_keys", "accounts", "refresh_tokens"):
            t: TableReport = getattr(self, name)
            out.append(f"{name}: +{t.inserted} ~{t.updated} ={t.unchanged} bỏ qua {len(t.skipped)}")
            out += [f"    ! {s}" for s in t.skipped[:20]]
        return out


# ── Đọc nguồn (chỉ đọc) ─────────────────────────────────────────────────────────

def _sqlite_path(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):  # sqlite:///C:/x.db -> C:/x.db
        path = path[1:]
    return path


def _read_sqlite(url: str) -> dict[str, list[dict]]:
    path = _sqlite_path(url)
    if not Path(path).exists():
        raise SyncRefused(f"Không thấy file SQLite nguồn: {path}")
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)  # chỉ đọc
    conn.row_factory = sqlite3.Row
    try:
        return {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
                for t in ("invite_keys", "accounts", "refresh_tokens")}
    finally:
        conn.close()


async def _read_postgres(url: str) -> dict[str, list[dict]]:
    conn = await asyncpg.connect(url, timeout=10)
    try:
        async with conn.transaction(readonly=True):
            return {t: [dict(r) for r in await conn.fetch(f"SELECT * FROM {t}")]
                    for t in ("invite_keys", "accounts", "refresh_tokens")}
    finally:
        await conn.close()


async def load_source(url: str) -> dict[str, list[dict]]:
    if url.startswith("sqlite"):
        return await asyncio.to_thread(_read_sqlite, url)
    if url.startswith(("postgresql://", "postgres://")):
        return await _read_postgres(url)
    raise SyncRefused("--source phải là sqlite:///... hoặc postgresql://...")


# ── Chuẩn hoá ───────────────────────────────────────────────────────────────────

def parse_ts(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _key_row(r: dict) -> dict:
    if r["role"] not in _ROLES or r["status"] not in _KEY_STATUS:
        raise ValueError(f"role/status không hợp lệ ({r['role']}/{r['status']})")
    if int(r["used_count"]) > int(r["max_uses"]) or int(r["max_uses"]) < 1:
        raise ValueError(f"used_count/max_uses không hợp lệ ({r['used_count']}/{r['max_uses']})")
    return {"key": r["key"], "role": r["role"], "label": r.get("label") or "", "max_uses": int(r["max_uses"]),
            "used_count": int(r["used_count"]), "created_at": parse_ts(r["created_at"]) or datetime.now(timezone.utc),
            "expires_at": parse_ts(r.get("expires_at")), "status": r["status"]}


def _account_row(r: dict) -> dict:
    if r["role"] not in _ROLES or r["status"] not in _ACCOUNT_STATUS:
        raise ValueError(f"role/status không hợp lệ ({r['role']}/{r['status']})")
    reset = r.get("reset_token") or ""
    # Legacy dùng chung một cột cho token reset (uuid) và mã đổi mật khẩu 6 số: tách theo hình dạng.
    is_code = bool(_NUMERIC_CODE.match(reset))
    verify = r.get("verify_token") or ""
    return {
        "id": r["id"], "username": r["username"], "email": r["email"].lower(),
        "password_hash": r["password_hash"], "role": r["role"], "invite_key": r.get("invite_key") or "",
        "email_verified": bool(r.get("email_verified")),
        "verify_token_hash": hash_token(verify) if verify else "",
        "verify_token_at": parse_ts(r.get("verify_token_at")) if verify else None,
        "reset_token_hash": hash_token(reset) if reset and not is_code else "",
        "reset_token_at": parse_ts(r.get("reset_token_at")) if reset and not is_code else None,
        "change_code_hash": hash_token(reset) if is_code else "",
        "change_code_at": parse_ts(r.get("reset_token_at")) if is_code else None,
        "created_at": parse_ts(r["created_at"]) or datetime.now(timezone.utc),
        "last_login": parse_ts(r.get("last_login")), "status": r["status"],
    }


def _refresh_row(r: dict) -> dict:
    return {"token_hash": hash_token(r["token"]), "account_id": r["account_id"],
            "created_at": parse_ts(r["created_at"]) or datetime.now(timezone.utc),
            "expires_at": parse_ts(r["expires_at"]), "revoked": bool(r.get("revoked"))}


# ── Ghi vào identity_db ─────────────────────────────────────────────────────────

_KEY_COLS = ["key", "role", "label", "max_uses", "used_count", "created_at", "expires_at", "status"]
_ACC_COLS = ["id", "username", "email", "password_hash", "role", "invite_key", "email_verified",
             "verify_token_hash", "verify_token_at", "reset_token_hash", "reset_token_at",
             "change_code_hash", "change_code_at", "created_at", "last_login", "status"]
_REF_COLS = ["token_hash", "account_id", "created_at", "expires_at", "revoked"]


async def _upsert(conn: asyncpg.Connection, table: str, pk: str, cols: list[str], row: dict) -> str:
    """Trả 'inserted' | 'updated' | 'unchanged'. Chỉ ghi khi có khác biệt thật (IS DISTINCT FROM)."""
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    non_pk = [c for c in cols if c != pk]
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in non_pk)
    distinct = (f"({', '.join(f'{table}.{c}' for c in non_pk)}) IS DISTINCT FROM "
                f"({', '.join(f'EXCLUDED.{c}' for c in non_pk)})")
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({pk}) DO UPDATE SET {sets} WHERE {distinct} RETURNING (xmax = 0) AS inserted")
    result = await conn.fetchrow(sql, *[row[c] for c in cols])
    if result is None:
        return "unchanged"
    return "inserted" if result["inserted"] else "updated"


def _count(report: TableReport, outcome: str) -> None:
    setattr(report, outcome, getattr(report, outcome) + 1)


async def sync(target_dsn: str, source_url: str, *, dry_run: bool = False, force: bool = False) -> SyncReport:
    data = await load_source(source_url)
    report = SyncReport(dry_run=dry_run)
    conn = await asyncpg.connect(target_dsn, timeout=10)
    try:
        if not force and await conn.fetchval("SELECT count(*) FROM auth_audit") > 0:
            raise SyncRefused("identity-service đã phục vụ lưu lượng thật (auth_audit không rỗng). "
                              "Sau cutover không đồng bộ ghi đè từ legacy nữa; dùng --force nếu thật sự cần.")
        tx = conn.transaction()
        await tx.start()
        try:
            for raw in data["invite_keys"]:
                try:
                    row = _key_row(raw)
                except (ValueError, KeyError) as exc:
                    report.invite_keys.skipped.append(f"key {raw.get('key')}: {exc}")
                    continue
                _count(report.invite_keys, await _upsert(conn, "invite_keys", "key", _KEY_COLS, row))

            account_ids: set[str] = set()
            for raw in data["accounts"]:
                try:
                    row = _account_row(raw)
                except (ValueError, KeyError) as exc:
                    report.accounts.skipped.append(f"account {raw.get('id')}: {exc}")
                    continue
                sp = conn.transaction()  # savepoint: một dòng xung đột không làm hỏng cả lượt
                await sp.start()
                try:
                    outcome = await _upsert(conn, "accounts", "id", _ACC_COLS, row)
                    await sp.commit()
                except asyncpg.UniqueViolationError as exc:
                    await sp.rollback()
                    report.accounts.skipped.append(
                        f"account {row['id']} ({row['username']}): trùng {exc.constraint_name} với tài khoản khác")
                    continue
                account_ids.add(row["id"])
                _count(report.accounts, outcome)

            known = account_ids | {r["id"] for r in await conn.fetch("SELECT id FROM accounts")}
            for raw in data["refresh_tokens"]:
                try:
                    row = _refresh_row(raw)
                except (ValueError, KeyError, TypeError) as exc:
                    report.refresh_tokens.skipped.append(f"refresh token của {raw.get('account_id')}: {exc}")
                    continue
                if row["account_id"] not in known:
                    report.refresh_tokens.skipped.append(
                        f"refresh token mồ côi (account {row['account_id']} không tồn tại)")
                    continue
                _count(report.refresh_tokens, await _upsert(conn, "refresh_tokens", "token_hash", _REF_COLS, row))
            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except BaseException:
            await tx.rollback()
            raise
    finally:
        await conn.close()
    return report


# ── Parity ──────────────────────────────────────────────────────────────────────

_PARITY_FIELDS = ["id", "username", "email", "password_hash", "role", "status", "email_verified", "invite_key"]


async def parity(target_dsn: str, source_url: str) -> dict:
    """So legacy với identity_db. `ok` chỉ đúng khi không có chênh lệch nào."""
    data = await load_source(source_url)
    conn = await asyncpg.connect(target_dsn, timeout=10)
    try:
        target_accounts = {r["id"]: dict(r) for r in await conn.fetch("SELECT * FROM accounts")}
        target_keys = {r["key"]: dict(r) for r in await conn.fetch("SELECT * FROM invite_keys")}
        target_refresh = {r["token_hash"] for r in await conn.fetch("SELECT token_hash FROM refresh_tokens")}
    finally:
        await conn.close()

    src_accounts = {}
    for raw in data["accounts"]:
        try:
            src_accounts[raw["id"]] = _account_row(raw)
        except (ValueError, KeyError):
            pass
    mismatched = []
    for acc_id, s in src_accounts.items():
        t = target_accounts.get(acc_id)
        if t is None:
            continue
        diffs = [f for f in _PARITY_FIELDS if s[f] != t[f]]
        if diffs:
            mismatched.append({"id": acc_id, "fields": diffs})
    src_keys = {r["key"]: r for r in data["invite_keys"]}
    key_mismatch = [k for k, r in src_keys.items()
                    if k in target_keys and (int(r["used_count"]) != target_keys[k]["used_count"]
                                             or r["status"] != target_keys[k]["status"])]
    # Token của tài khoản không còn tồn tại ở nguồn là dữ liệu mồ côi: không nhập và không tính là chênh lệch.
    src_refresh = {hash_token(r["token"]) for r in data["refresh_tokens"]
                   if r.get("token") and r.get("account_id") in src_accounts}
    orphans = sum(1 for r in data["refresh_tokens"] if r.get("account_id") not in src_accounts)
    report = {
        "accounts": {"source": len(src_accounts), "target": len(target_accounts),
                     "missing_in_target": sorted(set(src_accounts) - set(target_accounts)),
                     "only_in_target": sorted(set(target_accounts) - set(src_accounts)),
                     "mismatched": mismatched},
        "invite_keys": {"source": len(src_keys), "target": len(target_keys),
                        "missing_in_target": sorted(set(src_keys) - set(target_keys)),
                        "mismatched": key_mismatch},
        "refresh_tokens": {"source": len(src_refresh), "target": len(target_refresh),
                           "missing_in_target": len(src_refresh - target_refresh),
                           "orphans_ignored": orphans},
    }
    report["ok"] = not (report["accounts"]["missing_in_target"] or report["accounts"]["mismatched"]
                        or report["invite_keys"]["missing_in_target"] or report["invite_keys"]["mismatched"]
                        or report["refresh_tokens"]["missing_in_target"])
    return report


def main(argv: list[str] | None = None) -> int:
    from identity_service.config import ConfigError, load_settings

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="sqlite:///... hoặc postgresql://... (CHỈ ĐỌC)")
    ap.add_argument("--dry-run", action="store_true", help="chạy thử trong transaction rồi rollback")
    ap.add_argument("--parity", action="store_true", help="chỉ in báo cáo so sánh, không ghi")
    ap.add_argument("--force", action="store_true", help="bỏ qua chốt chặn 'identity đã phục vụ thật'")
    args = ap.parse_args(argv)
    try:
        settings = load_settings()
        if not settings.database_url:
            raise ConfigError("Thiếu IDENTITY_DATABASE_URL")
        if args.parity:
            result = asyncio.run(parity(settings.database_url, args.source))
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        report = asyncio.run(sync(settings.database_url, args.source, dry_run=args.dry_run, force=args.force))
    except (ConfigError, SyncRefused, OSError, asyncpg.PostgresError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    print("(CHẠY THỬ — đã rollback)" if report.dry_run else "Đã đồng bộ:")
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
