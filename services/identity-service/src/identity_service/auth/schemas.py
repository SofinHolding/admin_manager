"""Body request của endpoint auth. Ràng buộc GIỮ NGUYÊN legacy để frontend cũ không bị ảnh hưởng."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ValidateKeyBody(BaseModel):
    key: str


class CheckUsernameBody(BaseModel):
    username: str


class CheckEmailBody(BaseModel):
    email: str


class IntegrationRequest(BaseModel):
    """Yêu cầu tạo Integration client kèm đăng ký (Phase 1.7). Cấm trường lạ (vd `owner_account_id`)."""

    model_config = ConfigDict(extra="forbid")
    create: bool = True
    tool_name: str = Field("", max_length=64)
    client_kind: str | None = Field(None, max_length=32)
    platforms: list[str] = Field(default_factory=list, max_length=32)
    scopes: list[str] | None = Field(None, max_length=32)


class RegisterBody(BaseModel):
    key: str
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8)
    email: EmailStr
    integration: IntegrationRequest | None = None


class LoginBody(BaseModel):
    username: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class ResendVerifyBody(BaseModel):
    email: EmailStr


class ChangePasswordBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8)


class UpdateProfileBody(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
