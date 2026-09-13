from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fms.core.config import Settings, settings
from fms.core.database import Base, get_db
from fms.main import app
from fms.models import User
from fms.core.enums import UserRole, LoyaltyTier
from fms.core.security import create_access_token, hash_password


# Override settings for testing
class TestSettings(Settings):
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/fms_test"
    jwt_secret_key: str = "test-secret-key-for-testing-only"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7


test_settings = TestSettings()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        test_settings.database_url,
        echo=False,
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        await conn.begin()
        # Begin a nested transaction
        await conn.begin_nested()

        async_session = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async def do_commit():
            pass

        async def do_rollback():
            await conn.rollback()

        session = async_session()
        session.commit = do_commit
        session.rollback = do_rollback

        # Add a savepoint event listener
        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session, transaction):
            if transaction.nested and not transaction._parent.nested:
                session.expire_all()
                session.begin_nested()

        yield session

        await session.close()
        await conn.rollback()


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        full_name="Test User",
        role=UserRole.CUSTOMER,
        loyalty_tier=LoyaltyTier.NONE,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def test_admin(db_session: AsyncSession) -> User:
    admin = User(
        email="admin@test.com",
        hashed_password=hash_password("adminpassword123"),
        full_name="Test Admin",
        role=UserRole.SUPER_ADMIN,
        loyalty_tier=LoyaltyTier.PLATINUM,
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


@pytest.fixture
async def test_ops_agent(db_session: AsyncSession) -> User:
    ops = User(
        email="ops@test.com",
        hashed_password=hash_password("opspassword123"),
        full_name="Test Ops Agent",
        role=UserRole.OPS_AGENT,
        loyalty_tier=LoyaltyTier.GOLD,
    )
    db_session.add(ops)
    await db_session.flush()
    return ops


@pytest.fixture
def user_token(test_user: User) -> str:
    return create_access_token(str(test_user.id), test_user.role)


@pytest.fixture
def admin_token(test_admin: User) -> str:
    return create_access_token(str(test_admin.id), test_admin.role)


@pytest.fixture
def ops_token(test_ops_agent: User) -> str:
    return create_access_token(str(test_ops_agent.id), test_ops_agent.role)


@pytest.fixture
def auth_headers(user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def ops_headers(ops_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ops_token}"}