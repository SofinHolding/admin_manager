"""Body của API legacy — giữ NGUYÊN ràng buộc của admin-server (độ dài, pattern) để 422 trùng khớp.

Khác biệt có chủ đích duy nhất: body có khoá `owner*` bị TỪ CHỐI (legacy lặng lẽ bỏ qua). Quyền sở hữu luôn lấy từ token.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError


class LegacyBody(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _no_owner_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and any(isinstance(k, str) and k.lower().startswith("owner") for k in data):
            raise PydanticCustomError("extra_forbidden", "Extra inputs are not permitted")
        return data


class CreateSourceBody(LegacyBody):
    name: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(..., min_length=1, max_length=50)
    project_key: str = Field("", max_length=100)
    default_country: str = Field("", max_length=100)


class UpdateSourceBody(LegacyBody):
    name: str | None = Field(None, min_length=1, max_length=200)
    project_key: str | None = Field(None, max_length=100)
    default_country: str | None = Field(None, max_length=100)


class CreateResourceBody(LegacyBody):
    source_instance_id: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1, max_length=50)
    resource_type: str = Field("", max_length=50)
    input_ref: str = Field(..., min_length=1, max_length=500)
    username: str = Field("", max_length=200)
    url: str = Field("", max_length=500)
    display_name: str = Field("", max_length=200)
    country: str = Field("", max_length=100)


class ImportResourceBody(LegacyBody):
    source_instance_id: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1, max_length=50)
    resource_type: str = Field("", max_length=50)
    lines: list[str] = Field(..., min_length=1, max_length=500)


class UpdateResourceBody(LegacyBody):
    display_name: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)
    country: str | None = Field(None, max_length=100)
    resource_type: str | None = Field(None, max_length=50)


class CreateBatchBody(LegacyBody):
    platform: str = Field(..., min_length=1, max_length=50)
    name: str = Field("", max_length=200)


class AddItemsBody(LegacyBody):
    resource_ids: list[str] = Field(..., min_length=1, max_length=1000)


class UpdatePolicyBody(LegacyBody):
    web_entry_enabled: bool | None = None
    fallback_enabled: bool | None = None
    adapter_status: str | None = Field(None, pattern=r"^(none|planned|ready)$")


class SetExecutionModeBody(LegacyBody):
    execution_mode: str = Field(..., pattern=r"^(primary_tool|fallback_local|disabled)$")


class RedeemBody(LegacyBody):
    code: str = Field(..., min_length=4, max_length=32)
    tool_user_id: str = Field(..., min_length=8, max_length=128)
    device_id: str = Field("", max_length=128)
