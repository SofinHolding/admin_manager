"""Chạy migration `migrations/NNNN_ten.sql` cho identity_db. Forward-only, có khóa và checksum.

Chạy như một JOB RIÊNG, không chạy ngầm lúc service khởi động:

    python -m identity_service.migrate

- Khóa `pg_advisory_lock` để hai tiến trình không migrate cùng lúc.
- Mỗi migration chạy trong một transaction cùng với dòng ghi `schema_migrations`.
- Lưu sha256; file đã áp dụng mà bị sửa nội dung thì từ chối (migration bất biến).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_NAME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
_LOCK_KEY = 7_340_001  # khóa advisory riêng của identity-service


class MigrationError(Exception):
    pass


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in directory.glob("*.sql"))
    for p in files:
        if not _NAME.match(p.name):
            raise MigrationError(f"Tên migration không hợp lệ: {p.name} (cần NNNN_mo_ta.sql)")
    return files


def _checksum(path: Path) -> str:
    """sha256 của nội dung đã chuẩn hoá về LF: kiểu xuống dòng (CRLF/LF do git, autocrlf, Docker) KHÔNG làm migration 'bị sửa'."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _accepted_checksums(path: Path) -> set[str]:
    """Checksum chấp nhận cho file đã áp dụng: bản LF (hiện hành) và bản CRLF của cùng nội dung (hàng cũ băm trên byte thô).
    Chỉ khác kiểu xuống dòng thì chấp nhận; khác NỘI DUNG vẫn bị từ chối."""
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    return {hashlib.sha256(b).hexdigest() for b in (lf, lf.replace(b"\n", b"\r\n"))}


async def _ensure_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")


async def applied_versions(conn: asyncpg.Connection) -> dict[str, str]:
    await _ensure_table(conn)
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    return {r["version"]: r["checksum"] for r in rows}


async def run(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Áp dụng các migration còn thiếu. Trả về danh sách phiên bản vừa áp dụng."""
    conn = await asyncpg.connect(dsn, timeout=10)
    newly: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            done = await applied_versions(conn)
            for path in list_migrations(directory):
                version, checksum = path.stem, _checksum(path)
                if version in done:
                    if done[version] not in _accepted_checksums(path):
                        raise MigrationError(f"{path.name} đã áp dụng nhưng nội dung bị sửa (checksum lệch)")
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                        version, checksum)
                newly.append(version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
    finally:
        await conn.close()
    return newly


async def is_up_to_date(conn: asyncpg.Connection, directory: Path = MIGRATIONS_DIR) -> bool:
    """Dùng cho /health/ready: mọi migration đã được áp dụng."""
    try:
        done = await applied_versions(conn)
    except asyncpg.PostgresError:
        return False
    return all(p.stem in done for p in list_migrations(directory))


def main() -> int:
    from identity_service.config import ConfigError, load_settings

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Cấu hình sai: {exc}", file=sys.stderr)
        return 2
    if not settings.database_url:
        print("Thiếu IDENTITY_DATABASE_URL", file=sys.stderr)
        return 2
    try:
        newly = asyncio.run(run(settings.database_url))
    except (MigrationError, OSError, asyncpg.PostgresError) as exc:
        print(f"Migration lỗi: {exc}", file=sys.stderr)
        return 1
    print("Đã áp dụng:", ", ".join(newly) if newly else "(không có gì mới)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
