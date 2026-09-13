from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.dependencies import get_current_user
from fms.core.database import get_db
from fms.core.security import decode_token
from fms.models.user import User
from fms.schemas.user import Token, UserCreate, UserResponse
from fms.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)
    user = await auth_service.register(user_data.email, user_data.password, user_data.full_name)
    return user


@router.post("/login", response_model=Token)
async def login(credentials: UserCreate, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)
    user = await auth_service.authenticate(credentials.email, credentials.password)
    access_token, refresh_token = await auth_service.create_tokens(user)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        from fms.core.exceptions import ValidationError
        raise ValidationError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        from fms.core.exceptions import ValidationError
        raise ValidationError("Invalid token")

    user = await auth_service.get_user(user_id)
    if not user or not user.is_active:
        from fms.core.exceptions import ValidationError
        raise ValidationError("User not found or inactive")

    new_access_token = await auth_service.refresh_access_token(refresh_token)
    new_refresh_token = (await auth_service.create_tokens(user))[1]
    return Token(access_token=new_access_token, refresh_token=new_refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user