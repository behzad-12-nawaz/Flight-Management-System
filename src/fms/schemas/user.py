from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from fms.core.enums import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=100)


class UserCreateAdmin(UserCreate):
    role: UserRole = UserRole.CUSTOMER


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    loyalty_tier: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    loyalty_tier: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    role: str
    exp: int
    type: str