from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.enums import UserRole
from fms.core.exceptions import ConflictError, ValidationError
from fms.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from fms.models.user import User


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, email: str, password: str, full_name: str) -> User:
        result = await self.db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            raise ConflictError("Email already registered")

        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=UserRole.CUSTOMER,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def authenticate(self, email: str, password: str) -> User:
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            raise ValidationError("User not found")

        if not verify_password(password, user.hashed_password):
            raise ValidationError("Invalid password")

        if not user.is_active:
            raise ValidationError("Account is deactivated")

        return user

    async def create_tokens(self, user: User) -> tuple[str, str]:
        access_token = create_access_token(str(user.id), user.role)
        refresh_token = create_refresh_token(str(user.id))
        return access_token, refresh_token

    async def refresh_access_token(self, refresh_token: str) -> str:
        from fms.core.security import decode_token

        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise ValidationError("Invalid token type")

        user_id = payload.get("sub")
        if not user_id:
            raise ValidationError("Invalid token")

        result = await self.db.execute(select(User).where(User.id == UUID(user_id)))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise ValidationError("User not found or inactive")

        return create_access_token(str(user.id), user.role)

    async def get_user(self, user_id: UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()