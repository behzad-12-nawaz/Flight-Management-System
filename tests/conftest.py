from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fms.core.config import Settings
from fms.core.database import Base, get_db
from fms.core.enums import CabinClass, FareType, FlightStatus, LoyaltyTier, UserRole
from fms.core.policy import FLEXIBLE_FARE_MULTIPLIER
from fms.core.security import create_access_token, hash_password
from fms.main import app
from fms.models.fare import Fare
from fms.models.flight import Flight, SeatClass
from fms.models.user import User

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/fms_test",
)


class TestSettings(Settings):
    # Never read the developer's real .env file while testing.
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


test_settings = TestSettings(
    DATABASE_URL=TEST_DATABASE_URL,
    JWT_SECRET_KEY="test-secret-key-for-testing-only",
)


@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator:
    """A real Postgres engine with a schema created from scratch for every test."""
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


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    user = User(
        email="second@example.com",
        hashed_password=hash_password("testpassword123"),
        full_name="Second User",
        role=UserRole.CUSTOMER,
        loyalty_tier=LoyaltyTier.SILVER,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
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
def second_user_token(second_user: User) -> str:
    return create_access_token(str(second_user.id), second_user.role)


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
def second_user_headers(second_user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {second_user_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def ops_headers(ops_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ops_token}"}


# --------------------------------------------------------------------------------------
# Flight fixtures
# --------------------------------------------------------------------------------------


@dataclass
class FlightFixture:
    flight: Flight
    cabins: dict[CabinClass, SeatClass] = field(default_factory=dict)
    fares: dict[tuple[CabinClass, FareType], Fare] = field(default_factory=dict)

    def cabin(self, cabin_class: CabinClass) -> SeatClass:
        return self.cabins[cabin_class]

    def fare(self, cabin_class: CabinClass, fare_type: FareType = FareType.BASIC) -> Fare:
        return self.fares[(cabin_class, fare_type)]


FlightFactory = Callable[..., Awaitable[FlightFixture]]

DEFAULT_CABINS: dict[CabinClass, tuple[int, str]] = {
    CabinClass.ECONOMY: (10, "100.00"),
}


@pytest_asyncio.fixture
async def flight_factory(db_session: AsyncSession, test_admin: User) -> FlightFactory:
    """Create a scheduled flight (with auto-generated basic/flexible fares) straight in the DB."""

    async def _create(
        *,
        flight_number: str = "FMS100",
        origin: str = "JFK",
        destination: str = "LHR",
        departure: datetime | None = None,
        duration: timedelta = timedelta(hours=7),
        cabins: dict[CabinClass, tuple[int, str]] | None = None,
        status: FlightStatus = FlightStatus.SCHEDULED,
        seat_map: dict[str, str] | None = None,
    ) -> FlightFixture:
        departure = departure or (datetime.now(timezone.utc) + timedelta(days=7))
        cabin_spec = cabins or dict(DEFAULT_CABINS)

        flight = Flight(
            flight_number=flight_number,
            origin=origin,
            destination=destination,
            departure_datetime=departure,
            arrival_datetime=departure + duration,
            status=status,
            total_seats=sum(count for count, _ in cabin_spec.values()),
            created_by=test_admin.id,
            seat_map=seat_map,
        )
        db_session.add(flight)
        await db_session.flush()

        fixture = FlightFixture(flight=flight)

        for cabin_class, (count, price) in cabin_spec.items():
            seat_class = SeatClass(
                flight_id=flight.id,
                cabin_class=cabin_class,
                total_seats=count,
                available_seats=count,
                overbooking_buffer=0,
            )
            db_session.add(seat_class)
            await db_session.flush()

            basic_price = Decimal(price)
            basic = Fare(
                seat_class_id=seat_class.id,
                fare_type=FareType.BASIC,
                price=basic_price,
                refundable=False,
                change_allowed=False,
                seat_choice_allowed=False,
            )
            flexible = Fare(
                seat_class_id=seat_class.id,
                fare_type=FareType.FLEXIBLE,
                price=(basic_price * Decimal(str(FLEXIBLE_FARE_MULTIPLIER))).quantize(Decimal("0.01")),
                refundable=True,
                change_allowed=True,
                seat_choice_allowed=True,
            )
            db_session.add_all([basic, flexible])
            await db_session.flush()

            fixture.cabins[cabin_class] = seat_class
            fixture.fares[(cabin_class, FareType.BASIC)] = basic
            fixture.fares[(cabin_class, FareType.FLEXIBLE)] = flexible

        return fixture

    return _create


@pytest_asyncio.fixture
async def sample_flight(flight_factory: FlightFactory) -> FlightFixture:
    return await flight_factory()


def booking_hold_payload(
    fixture: FlightFixture,
    *,
    cabin_class: CabinClass = CabinClass.ECONOMY,
    fare_type: FareType = FareType.BASIC,
    passengers: list[str] | None = None,
) -> dict:
    return {
        "items": [
            {
                "flight_id": str(fixture.flight.id),
                "cabin_class": cabin_class.value,
                "fare_type": fare_type.value,
                "passengers": [{"name": name} for name in (passengers or ["Jane Doe"])],
            }
        ]
    }


async def hold_booking(client: AsyncClient, headers: dict, payload: dict) -> dict:
    response = await client.post("/bookings/hold", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def confirm_booking(
    client: AsyncClient,
    headers: dict,
    group_key: str,
    passenger_names: list[str],
    *,
    idempotency_key: str | None = None,
) -> dict:
    extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    response = await client.post(
        "/bookings/confirm",
        headers={**headers, **extra_headers},
        json={"group_key": group_key, "passenger_names": passenger_names},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def book(
    client: AsyncClient,
    headers: dict,
    fixture: FlightFixture,
    *,
    cabin: CabinClass = CabinClass.ECONOMY,
    fare_type: FareType = FareType.BASIC,
    passengers: list[str] | None = None,
) -> dict:
    """Hold + confirm a booking in one go and return the confirmed booking payload."""
    names = passengers or ["Jane Doe"]
    hold = await hold_booking(
        client,
        headers,
        booking_hold_payload(fixture, cabin_class=cabin, fare_type=fare_type, passengers=names),
    )
    return await confirm_booking(client, headers, hold["group_key"], names)


async def items_for_flight(db_session: AsyncSession, flight_id) -> list:
    from fms.models.booking import BookingItem

    return list(
        (
            await db_session.execute(
                select(BookingItem)
                .where(BookingItem.flight_id == flight_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def available_seats(db_session: AsyncSession, seat_class_id) -> int:
    from fms.models.flight import SeatClass

    return int(
        (
            await db_session.execute(
                select(SeatClass.available_seats)
                .where(SeatClass.id == seat_class_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
    )


__all__ = [
    "FlightFixture",
    "FlightFactory",
    "available_seats",
    "book",
    "booking_hold_payload",
    "confirm_booking",
    "hold_booking",
    "items_for_flight",
]
