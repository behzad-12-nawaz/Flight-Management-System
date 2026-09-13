from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.database import get_db
from fms.core.enums import UserRole
from fms.core.exceptions import ForbiddenError, UnauthorizedError
from fms.core.security import decode_token
from fms.models.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise UnauthorizedError("Not authenticated")

    try:
        payload = decode_token(token)
    except ValueError as e:
        raise UnauthorizedError(str(e))

    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("User not found")

    if not user.is_active:
        raise ForbiddenError("User account is deactivated")

    return user


def require_role(*roles: UserRole):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise ForbiddenError(f"Required role: {', '.join(r.value for r in roles)}")
        return current_user

    return role_checker


async def get_idempotency_key(idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Optional[str]:
    return idempotency_key