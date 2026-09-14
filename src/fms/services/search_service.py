from __future__ import annotations

from datetime import date as date_type, datetime

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fms.core.enums import FlightStatus
from fms.models.flight import Flight, SeatClass


async def search_flights(
    db: AsyncSession,
    *,
    origin: str | None = None,
    destination: str | None = None,
    date: date_type | datetime | None = None,
) -> list[Flight]:
    """Search bookable (``scheduled``) flights, with their cabins and fares eagerly loaded."""
    statement = (
        select(Flight)
        .where(Flight.status == FlightStatus.SCHEDULED)
        .options(selectinload(Flight.seat_classes).selectinload(SeatClass.fares))
        .order_by(Flight.departure_datetime)
    )

    if origin:
        statement = statement.where(func.lower(Flight.origin) == origin.strip().lower())
    if destination:
        statement = statement.where(func.lower(Flight.destination) == destination.strip().lower())
    if date is not None:
        target_day = date.date() if isinstance(date, datetime) else date
        statement = statement.where(cast(Flight.departure_datetime, Date) == target_day)

    return list((await db.execute(statement)).scalars().all())
