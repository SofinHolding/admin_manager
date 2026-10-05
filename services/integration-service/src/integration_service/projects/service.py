"""Project/Source: Tool khai báo, server tự upsert — không có bước Admin mapping."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from manager_contracts import ErrorCode
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from integration_service.credentials.service import MachineContext
from integration_service.db import Database
from integration_service.errors import forbidden
from integration_service.feed import repository as outbox
from integration_service.ingest.schemas import Key
from integration_service.platforms import repository as registry
from integration_service.projects import repository
from integration_service.security import new_id


class ProjectUpsert(BaseModel):
    """Body của `PUT /v1/integrations/projects/{project_key}`. Trường lạ (kể cả owner_*) bị từ chối."""
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    platform: Key | None = None
    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = None
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _small(cls, value: dict) -> dict:
        dumped = json.dumps(value)
        if len(dumped) > 8192 or "\\u0000" in dumped:
            raise ValueError("metadata quá lớn hoặc chứa NUL")
        return value


def public_project(row: dict) -> dict:
    return {"project_key": row["project_key"], "name": row["name"], "platform": row["platform"],
            "country_code": row["country_code"], "enabled": row["enabled"], "metadata": row["metadata"],
            "revision": row["revision"], "updated_at": row["updated_at"]}


class ProjectService:
    def __init__(self, db: Database, now: Callable[[], datetime]) -> None:
        self._db = db
        self._now = now

    async def upsert(self, conn: asyncpg.Connection, ctx: MachineContext, project_key: str,
                     body: ProjectUpsert) -> dict:
        if body.platform is not None and (body.platform not in ctx.allowed_platforms
                                          or body.platform not in await registry.enabled_platforms(conn)):
            raise forbidden(ErrorCode.PLATFORM_NOT_ALLOWED, "Platform không nằm trong phạm vi client được phép")
        now = self._now()
        await outbox.acquire_order_lock(conn)
        existing = await repository.get(conn, ctx.owner_account_id, project_key)
        fields = dict(platform=body.platform, name=body.name, country_code=body.country_code, enabled=body.enabled,
                      metadata=body.metadata)
        if existing and not existing["removed"] and all(existing[k] == v for k, v in fields.items()):
            return {"created": False, "changed": False, "project": public_project(existing)}
        (rev,) = await outbox.next_revisions(conn, 1)
        if existing is None:
            row = await repository.insert(conn, id=new_id("prj"), owner=ctx.owner_account_id, client_id=ctx.client_id,
                                          project_key=project_key, revision=rev, now=now, **fields)
            created = True
        else:
            row = await repository.update(conn, existing["id"], client_id=ctx.client_id, revision=rev, now=now,
                                          **fields)
            created = False
        return {"created": created, "changed": True, "project": public_project(row)}
