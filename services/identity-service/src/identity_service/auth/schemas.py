"""Body request của endpoint auth. Ràng buộc GIỮ NGUYÊN legacy để frontend cũ không bị ảnh hưởng."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from identity_service.security import password_too_long


def _max_72_bytes(value: str) -> str:
    """bcrypt chỉ xét 72 byte đầu: từ chối rõ ràng (422) thay vì để hash lỗi. Thông điệp KHÔNG chứa giá trị nhập."""
    if password_too_long(value):
        raise ValueError("Mật khẩu quá dài (tối đa 72 byte; ký tự có dấu chiếm nhiều byte)")
    return value


NewPassword = Annotated[str, Field(min_length=8), AfterValidator(_max_72_bytes)]


class ValidateKeyBody(BaseModel):
    key: str


class CheckUsernameBody(BaseModel):
    username: str


class CheckEmailBody(BaseModel):
    email: str


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: NewPassword
    email: EmailStr


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
    new_password: NewPassword


class ResendVerifyBody(BaseModel):
    email: EmailStr


class ChangePasswordBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: NewPassword


class UpdateProfileBody(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
