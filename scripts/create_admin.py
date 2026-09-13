#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.database import Base, async_session_factory, engine
from fms.core.enums import UserRole
from fms.core.security import hash_password
from fms.models.user import User


async def create_admin() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.email == settings.admin_seed_email))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Admin user {settings.admin_seed_email} already exists")
            return

        admin = User(
            email=settings.admin_seed_email,
            hashed_password=hash_password(settings.admin_seed_password),
            full_name="System Administrator",
            role=UserRole.SUPER_ADMIN,
        )
        db.add(admin)
        await db.commit()
        print(f"Created admin user: {settings.admin_seed_email}")


if __name__ == "__main__":
    asyncio.run(create_admin())