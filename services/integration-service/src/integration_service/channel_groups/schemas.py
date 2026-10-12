"""Schema Pydantic của channel_groups (nhóm quốc gia + kênh, mặt phẳng USER)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class ChannelGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "example": {"name": "Vietnam", "country_code": "VN"}})
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    country_code: Annotated[str, StringConstraints(max_length=10)] = ""


class ChannelGroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "example": {"name": "Vietnam (Nam)"}})
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    country_code: Annotated[str, StringConstraints(max_length=10)] | None = None


class ChannelGroupOut(BaseModel):
    id: str
    owner_account_id: str
    name: str
    country_code: str
    sort_order: int
    created_at: datetime
