from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import get_current_user
from fms.core.exceptions import ValidationError
from fms.core.security import decode_token
from fms.models.user import User
from fms.schemas.user import Token, TokenRefreshRequest, UserCreate, UserLogin, UserResponse
from fms.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Customer self-registration. Admins are only created via `scripts/create_admin.py`."""
    auth_service = AuthService(db)
    return await auth_service.register(user_data.email, user_data.password, user_data.full_name)


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)
    user = await auth_service.authenticate(credentials.email, credentials.password)
    access_token, refresh_token = await auth_service.create_tokens(user)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
async def refresh_token(payload: TokenRefreshRequest, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)

    try:
        decoded = decode_token(payload.refresh_token)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if decoded.get("type") != "refresh":
        raise ValidationError("Invalid token type")

    subject = decoded.get("sub")
    if not subject:
        raise ValidationError("Invalid token payload")

    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise ValidationError("Invalid token payload") from exc

    user = await auth_service.get_user(user_id)
    if user is None or not user.is_active:
        raise ValidationError("User not found or inactive")

    new_access_token = await auth_service.refresh_access_token(payload.refresh_token)
    _, new_refresh_token = await auth_service.create_tokens(user)
    return Token(access_token=new_access_token, refresh_token=new_refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
