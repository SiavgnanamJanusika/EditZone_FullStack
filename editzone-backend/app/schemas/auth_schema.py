import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal, Optional
from app.core.validators import is_valid_nic, is_valid_phone


class ValidatedModel(BaseModel):
    pass


def validate_strong_password(value: str) -> str:
    if not 8 <= len(value) <= 128:
        raise ValueError("Password must be between 8 and 128 characters")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain at least one letter and one number")
    return value


class ChooseRoleRequest(ValidatedModel):
    role: Literal["user", "editor"]


class RegisterAccountRequest(ValidatedModel):
    """Step 1: create the login credentials (post choose-role)."""
    username: str = Field(min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(max_length=128)
    nic: str
    role: Literal["user", "editor"]

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        value = v.strip()
        if len(value) < 2:
            raise ValueError("Username must be at least 2 characters")
        return value

    @field_validator("nic")
    @classmethod
    def validate_nic(cls, v):
        if not is_valid_nic(v):
            raise ValueError("Invalid Sri Lankan NIC number")
        return v.strip().upper()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        return validate_strong_password(v)


class LoginRequest(ValidatedModel):
    email: EmailStr
    password: str
    role: Optional[Literal["user", "editor"]] = None
    captcha_token: Optional[str] = Field(default=None, max_length=4096)


class GoogleLoginRequest(ValidatedModel):
    credential: str = Field(min_length=1, max_length=8192)
    role: Literal["user", "editor"]


class CompleteProfileRequest(ValidatedModel):
    """Step 2: complete registration details after first login."""
    username: str = Field(min_length=2, max_length=50)
    nic: str
    district: Literal[
        "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya", "Galle", "Matara",
        "Hambantota", "Jaffna", "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu", "Batticaloa",
        "Ampara", "Trincomalee", "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa",
        "Badulla", "Monaragala", "Ratnapura", "Kegalle",
    ]
    gender: Literal["Male", "Female"]
    phone: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        value = v.strip()
        if len(value) < 2:
            raise ValueError("Username must be at least 2 characters")
        return value

    @field_validator("nic")
    @classmethod
    def validate_nic(cls, v):
        if not is_valid_nic(v):
            raise ValueError("Invalid Sri Lankan NIC number")
        return v.strip().upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if not is_valid_phone(v):
            raise ValueError("Invalid Sri Lankan phone number")
        return v.strip()


class ForgotPasswordRequest(ValidatedModel):
    email: EmailStr
    captcha_token: Optional[str] = Field(default=None, max_length=4096)


class ResetPasswordRequest(ValidatedModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(max_length=128)
    captcha_token: Optional[str] = Field(default=None, max_length=4096)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v):
        return validate_strong_password(v)


class VerifyOtpRequest(ValidatedModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    captcha_token: Optional[str] = Field(default=None, max_length=4096)


class TokenResponse(BaseModel):
    message: str
    role: str
    registration_complete: bool
    email_verified: bool
    user_id: str
    profile_completion_required: bool
    user: dict
