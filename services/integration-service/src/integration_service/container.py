"""Gói các thành phần của service để router lấy qua `request.app.state.container` (không dùng biến global)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from manager_sdk import ServiceKeyAuth, TokenVerifier

from integration_service.audit.service import AuditService
from integration_service.clients.service import ClientService
from integration_service.config import Settings
from integration_service.credentials.service import CredentialService
from integration_service.db import Database
from integration_service.guide.service import GuideService
from integration_service.installations.service import InstallationService
from integration_service.jobs.service import JobService
from integration_service.legacy_acl.service import LegacyService
from integration_service.machine_runner import MachineRunner
from integration_service.ratelimit import FixedWindowLimiter
from integration_service.resources.service import ResourceService
from integration_service.snapshots.service import SnapshotService


@dataclass
class Container:
    settings: Settings
    db: Database
    now: Callable[[], datetime]
    verifier: TokenVerifier
    service_auth: ServiceKeyAuth
    audit: AuditService
    clients: ClientService
    credentials: CredentialService
    installations: InstallationService
    resources: ResourceService
    snapshots: SnapshotService
    guide: GuideService
    runner: MachineRunner
    jobs: JobService
    legacy: LegacyService
    rate_limiter: FixedWindowLimiter
    fail_limiter: FixedWindowLimiter
    user_write_limiter: FixedWindowLimiter   # ghi của NGƯỜI DÙNG (mặt phẳng user), khoá theo account — vd channel-groups
