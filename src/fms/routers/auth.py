from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.database import get_db
from fms.core.dependencies import get_current_user
from fms.core.exceptions import ValidationError
from fms.core.security import decode_token
from fms.models.user import User
from fms.schemas.user import Token, TokenRefreshRequest, UserCreate, UserLogin, UserResponse
from fms.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.access_token_expire_minutes * 60,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=settings.refresh_token_expire_days * 86400,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key="access_token",
        domain=settings.cookie_domain,
        path="/",
    )
    response.delete_cookie(
        key="refresh_token",
        domain=settings.cookie_domain,
        path="/",
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Customer self-registration. Admins are only created via `scripts/create_admin.py`."""
    auth_service = AuthService(db)
    return await auth_service.register(user_data.email, user_data.password, user_data.full_name)


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, response: Response, db: AsyncSession = Depends(get_db)):
    auth_service = AuthService(db)
    user = await auth_service.authenticate(credentials.email, credentials.password)
    access_token, refresh_token = await auth_service.create_tokens(user)
    _set_auth_cookies(response, access_token, refresh_token)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    response: Response,
    payload: Optional[TokenRefreshRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    raw_refresh_token = payload.refresh_token if (payload and payload.refresh_token) else request.cookies.get("refresh_token")
    if not raw_refresh_token:
        raise ValidationError("Refresh token missing from request body and cookies")

    auth_service = AuthService(db)

    try:
        decoded = decode_token(raw_refresh_token)
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

    new_access_token = await auth_service.refresh_access_token(raw_refresh_token)
    _, new_refresh_token = await auth_service.create_tokens(user)
    _set_auth_cookies(response, new_access_token, new_refresh_token)
    return Token(access_token=new_access_token, refresh_token=new_refresh_token)


@router.post("/logout")
async def logout(response: Response):
    _clear_auth_cookies(response)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
