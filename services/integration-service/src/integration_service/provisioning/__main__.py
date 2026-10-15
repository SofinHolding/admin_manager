"""Lệnh vận hành: `python -m integration_service.provisioning replay [--id evt_...]` — áp lại event trong dead_letters."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from integration_service.clients.service import ClientService
from integration_service.config import ConfigError, load_settings
from integration_service.credentials.service import CredentialService
from integration_service.crypto import RevealCrypto
from integration_service.db import Database
from integration_service.provisioning.consumer import ProvisioningConsumer
from integration_service.ratelimit import FixedWindowLimiter


async def _replay(event_id: str | None) -> dict:
    settings = load_settings()
    if not settings.database_url:
        raise ConfigError("Thiếu INTEGRATION_DATABASE_URL")
    db = Database(settings.database_url)
    try:
        now = lambda: datetime.now(timezone.utc)  # noqa: E731
        clients = ClientService(db, settings, now)
        reveal = RevealCrypto(settings.reveal_key) if settings.reveal_key else None
        credentials = CredentialService(db, settings, now, clients, FixedWindowLimiter(600), FixedWindowLimiter(30),
                                        reveal_crypto=reveal)
        consumer = ProvisioningConsumer(db, settings, clients, credentials, feed=None, now=now)  # type: ignore[arg-type]
        return await consumer.replay_dead_letters(event_id)
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integration_service.provisioning")
    sub = parser.add_subparsers(dest="cmd", required=True)
    replay = sub.add_parser("replay", help="áp lại dead-letter")
    replay.add_argument("--id", dest="event_id", default=None)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_replay(args.event_id))
    except ConfigError as exc:
        print(f"Cấu hình sai: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
