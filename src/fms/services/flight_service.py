from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fms.core.enums import BookingItemStatus, FareType, FlightStatus
from fms.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    ValidationError,
)
from fms.core.policy import FLEXIBLE_FARE_MULTIPLIER, SCHEDULE_CHANGE_OVERRIDE_HOURS
from fms.models.booking import Booking, BookingItem
from fms.models.fare import Fare
from fms.models.flight import Flight, SeatClass
from fms.models.user import User
from fms.schemas.flight import (
    FlightCreate,
    FlightDelayUpdate,
    FlightScheduleUpdate,
    FlightSeatMapUpdate,
    SeatClassUpdate,
)
from fms.services import cancellation_service
from fms.services.audit_service import log_audit
from fms.services.notification_service import NotificationService

notification_service = NotificationService()


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _flexible_price(basic_price: Decimal) -> Decimal:
    return _quantize(basic_price * Decimal(str(FLEXIBLE_FARE_MULTIPLIER)))


def _serialize_flight(flight: Flight) -> dict:
    return {
        "flight_number": flight.flight_number,
        "origin": flight.origin,
        "destination": flight.destination,
        "departure_datetime": flight.departure_datetime.isoformat(),
        "arrival_datetime": flight.arrival_datetime.isoformat(),
        "status": flight.status.value,
        "total_seats": flight.total_seats,
        "version": flight.version,
    }


async def get_flight(db: AsyncSession, flight_id) -> Flight:
    flight = await db.get(Flight, flight_id)
    if flight is None:
        raise NotFoundError("Flight", flight_id)
    return flight


async def get_flight_detail(db: AsyncSession, flight_id) -> Flight:
    flight = (
        await db.execute(
            select(Flight)
            .where(Flight.id == flight_id)
            .options(selectinload(Flight.seat_classes).selectinload(SeatClass.fares))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if flight is None:
        raise NotFoundError("Flight", flight_id)
    return flight


async def _find_duplicate(
    db: AsyncSession,
    flight_number: str,
    origin: str,
    destination: str,
    departure_datetime,
) -> bool:
    day = func.date_trunc("day", departure_datetime)
    existing = (
        await db.execute(
            select(Flight.id).where(
                Flight.flight_number == flight_number,
                Flight.origin == origin,
                Flight.destination == destination,
                func.date_trunc("day", Flight.departure_datetime) == day,
            )
        )
    ).scalar_one_or_none()
    return existing is not None


async def create_flight(db: AsyncSession, *, payload: FlightCreate, actor: User) -> Flight:
    """Create a flight with its seat classes and the auto-generated basic/flexible fares."""
    seat_total = sum(seat_class.total_seats for seat_class in payload.seat_classes)
    if seat_total != payload.total_seats:
        raise ValidationError(
            f"Seat class totals ({seat_total}) must sum to the flight capacity "
            f"({payload.total_seats})"
        )

    if payload.arrival_datetime <= payload.departure_datetime:
        raise ValidationError("arrival_datetime must be after departure_datetime")

    cabins = [seat_class.cabin_class for seat_class in payload.seat_classes]
    if len(set(cabins)) != len(cabins):
        raise ValidationError("Duplicate cabin classes are not allowed on the same flight")

    if await _find_duplicate(
        db, payload.flight_number, payload.origin, payload.destination, payload.departure_datetime
    ):
        raise ConflictError(
            "A flight with the same number, origin, destination and departure date already exists"
        )

    flight = Flight(
        flight_number=payload.flight_number,
        origin=payload.origin,
        destination=payload.destination,
        departure_datetime=payload.departure_datetime,
        arrival_datetime=payload.arrival_datetime,
        status=FlightStatus.SCHEDULED,
        total_seats=payload.total_seats,
        created_by=actor.id,
    )
    db.add(flight)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "A flight with the same number, origin, destination and departure date already exists"
        ) from exc

    for seat_class_payload in payload.seat_classes:
        seat_class = SeatClass(
            flight_id=flight.id,
            cabin_class=seat_class_payload.cabin_class,
            total_seats=seat_class_payload.total_seats,
            available_seats=seat_class_payload.total_seats,
            overbooking_buffer=seat_class_payload.overbooking_buffer,
        )
        db.add(seat_class)
        await db.flush()

        basic_price = seat_class_payload.base_price
        db.add_all(
            [
                Fare(
                    seat_class_id=seat_class.id,
                    fare_type=FareType.BASIC,
                    price=basic_price,
                    refundable=False,
                    change_allowed=False,
                    seat_choice_allowed=False,
                ),
                Fare(
                    seat_class_id=seat_class.id,
                    fare_type=FareType.FLEXIBLE,
                    price=_flexible_price(basic_price),
                    refundable=True,
                    change_allowed=True,
                    seat_choice_allowed=True,
                ),
            ]
        )

    await db.flush()
    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.create",
        entity_type="flight",
        entity_id=flight.id,
        before_state=None,
        after_state=_serialize_flight(flight),
    )
    return flight


async def apply_schedule_change_override(db: AsyncSession, flight_id, *, enabled: bool = True) -> int:
    """Flag every confirmed booking item on a flight as eligible for a free change/refund."""
    result = await db.execute(
        update(BookingItem)
        .where(
            BookingItem.flight_id == flight_id,
            BookingItem.status == BookingItemStatus.CONFIRMED,
        )
        .values(schedule_change_override=enabled)
    )
    return result.rowcount or 0


async def _optimistic_update_flight(db: AsyncSession, flight: Flight, **values) -> None:
    """``UPDATE flights ... WHERE id = :id AND version = :expected`` with a 409 on conflict."""
    expected_version = flight.version
    result = await db.execute(
        update(Flight)
        .where(Flight.id == flight.id, Flight.version == expected_version)
        .values(version=Flight.version + 1, **values)
    )
    if result.rowcount != 1:
        raise OptimisticLockError("Flight was modified concurrently, please re-fetch and retry")
    await db.flush()
    await db.refresh(flight)


async def edit_flight_schedule(
    db: AsyncSession, *, flight_id, payload: FlightScheduleUpdate, actor: User
) -> Flight:
    """Permanently replan a flight (data-entry fix / route retiming). Does not touch original_*."""
    flight = await get_flight(db, flight_id)

    if flight.status == FlightStatus.CANCELLED:
        raise ConflictError("A cancelled flight's schedule cannot be edited")

    new_departure = payload.departure_datetime or flight.departure_datetime
    new_arrival = payload.arrival_datetime or flight.arrival_datetime

    if new_arrival <= new_departure:
        raise ValidationError("arrival_datetime must be after departure_datetime")

    old_departure = flight.departure_datetime
    before_state = _serialize_flight(flight)

    await _optimistic_update_flight(
        db,
        flight,
        departure_datetime=new_departure,
        arrival_datetime=new_arrival,
    )

    shift = abs(new_departure - old_departure)
    overridden = shift > timedelta(hours=SCHEDULE_CHANGE_OVERRIDE_HOURS)
    affected = 0
    if overridden:
        affected = await apply_schedule_change_override(db, flight.id)

    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.schedule_change",
        entity_type="flight",
        entity_id=flight.id,
        before_state=before_state,
        after_state={
            **_serialize_flight(flight),
            "schedule_change_override_applied": overridden,
            "affected_booking_items": affected,
        },
    )
    return flight


async def mark_flight_delayed(
    db: AsyncSession, *, flight_id, payload: FlightDelayUpdate, actor: User
) -> Flight:
    """Operational disruption: keep the intended schedule, record the disruption and new times."""
    flight = await get_flight(db, flight_id)

    if flight.status == FlightStatus.CANCELLED:
        raise ConflictError("A cancelled flight cannot be marked as delayed")

    if payload.new_arrival <= payload.new_departure:
        raise ValidationError("new_arrival must be after new_departure")

    old_departure = flight.departure_datetime
    old_arrival = flight.arrival_datetime
    original_departure = flight.original_departure_datetime or old_departure
    before_state = {**_serialize_flight(flight), "delay_reason": flight.delay_reason}

    await _optimistic_update_flight(
        db,
        flight,
        status=FlightStatus.DELAYED,
        departure_datetime=payload.new_departure,
        arrival_datetime=payload.new_arrival,
        delay_reason=payload.delay_reason,
        # Snapshot the true original times exactly once (COALESCE keeps the first values).
        original_departure_datetime=func.coalesce(Flight.original_departure_datetime, old_departure),
        original_arrival_datetime=func.coalesce(Flight.original_arrival_datetime, old_arrival),
    )

    cumulative_delay = abs(payload.new_departure - original_departure)
    overridden = cumulative_delay > timedelta(hours=SCHEDULE_CHANGE_OVERRIDE_HOURS)
    affected = 0
    if overridden:
        affected = await apply_schedule_change_override(db, flight.id)

    await _notify_delay(db, flight, old_departure, payload)

    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.delay",
        entity_type="flight",
        entity_id=flight.id,
        before_state=before_state,
        after_state={
            **_serialize_flight(flight),
            "delay_reason": flight.delay_reason,
            "original_departure_datetime": flight.original_departure_datetime.isoformat()
            if flight.original_departure_datetime
            else None,
            "schedule_change_override_applied": overridden,
            "affected_booking_items": affected,
        },
    )
    return flight


async def _notify_delay(db: AsyncSession, flight: Flight, old_departure, payload: FlightDelayUpdate) -> None:
    rows = await db.execute(
        select(BookingItem.passenger_name, User.email)
        .join(Booking, BookingItem.booking_id == Booking.id)
        .join(User, Booking.user_id == User.id)
        .where(
            BookingItem.flight_id == flight.id,
            BookingItem.status == BookingItemStatus.CONFIRMED,
        )
    )
    for _passenger_name, email in rows.all():
        await notification_service.send_delay_notice(
            user_email=email,
            flight_number=flight.flight_number,
            old_departure=old_departure,
            new_departure=payload.new_departure,
            delay_reason=payload.delay_reason,
        )


async def resolve_delay(db: AsyncSession, *, flight_id, actor: User) -> Flight:
    """Return a delayed flight to ``scheduled`` — keeps delay_reason and original_* as a record."""
    flight = await get_flight(db, flight_id)

    if flight.status != FlightStatus.DELAYED:
        raise ValidationError("Flight is not currently delayed")

    before_state = _serialize_flight(flight)
    await _optimistic_update_flight(db, flight, status=FlightStatus.SCHEDULED)

    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.delay_resolved",
        entity_type="flight",
        entity_id=flight.id,
        before_state=before_state,
        after_state=_serialize_flight(flight),
    )
    return flight


async def cancel_flight(db: AsyncSession, *, flight_id, actor: User) -> Flight:
    """Cancel a whole flight and compensate every affected booking item (Decision #6)."""
    flight = await get_flight(db, flight_id)

    if flight.status == FlightStatus.CANCELLED:
        raise ConflictError("Flight is already cancelled")

    before_state = _serialize_flight(flight)
    await _optimistic_update_flight(db, flight, status=FlightStatus.CANCELLED)

    items = (
        (
            await db.execute(
                select(BookingItem).where(
                    BookingItem.flight_id == flight.id,
                    BookingItem.status == BookingItemStatus.CONFIRMED,
                )
            )
        )
        .scalars()
        .all()
    )

    for item in items:
        await cancellation_service.cancel_booking_item(
            db,
            item_id=item.id,
            actor=actor,
            reason=cancellation_service.AIRLINE_CANCELLATION_REASON,
        )

    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.cancel",
        entity_type="flight",
        entity_id=flight.id,
        before_state=before_state,
        after_state={**_serialize_flight(flight), "compensated_booking_items": len(items)},
    )
    return flight


async def adjust_seat_allocation(
    db: AsyncSession,
    *,
    flight_id,
    cabin_class,
    payload: SeatClassUpdate,
    actor: User,
) -> SeatClass:
    """Resize a cabin's allocation, keeping ``SUM(seat_classes.total_seats) == flights.total_seats``."""
    flight = await get_flight(db, flight_id)

    seat_class = (
        await db.execute(
            select(SeatClass)
            .where(SeatClass.flight_id == flight.id, SeatClass.cabin_class == cabin_class)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if seat_class is None:
        raise NotFoundError("SeatClass", f"{flight_id}/{cabin_class}")

    new_total = payload.total_seats if payload.total_seats is not None else seat_class.total_seats
    booked = seat_class.total_seats - seat_class.available_seats
    if new_total < booked:
        raise ValidationError(
            f"Cannot shrink {seat_class.cabin_class.value} below the {booked} seat(s) already booked"
        )

    delta = new_total - seat_class.total_seats
    before_state = {
        "cabin_class": seat_class.cabin_class.value,
        "total_seats": seat_class.total_seats,
        "available_seats": seat_class.available_seats,
        "overbooking_buffer": seat_class.overbooking_buffer,
        "flight_total_seats": flight.total_seats,
    }

    update_values = {
        "total_seats": new_total,
        "available_seats": SeatClass.available_seats + delta,
        "version": SeatClass.version + 1,
    }
    if payload.overbooking_buffer is not None:
        update_values["overbooking_buffer"] = payload.overbooking_buffer

    result = await db.execute(
        update(SeatClass)
        .where(SeatClass.id == seat_class.id, SeatClass.version == seat_class.version)
        .values(**update_values)
    )
    if result.rowcount != 1:
        raise OptimisticLockError("Seat class was modified concurrently, please re-fetch and retry")
    await db.flush()

    # Re-validate the flight-level invariant and keep the denormalised flight total in sync.
    new_flight_total = (
        await db.execute(
            select(func.coalesce(func.sum(SeatClass.total_seats), 0)).where(
                SeatClass.flight_id == flight.id
            )
        )
    ).scalar_one()

    if new_flight_total != flight.total_seats:
        await db.execute(
            update(Flight).where(Flight.id == flight.id).values(total_seats=new_flight_total)
        )
        await db.flush()

    flight_total = (
        await db.execute(select(func.coalesce(func.sum(SeatClass.total_seats), 0)).where(SeatClass.flight_id == flight.id))
    ).scalar_one()
    actual_flight_total = (
        await db.execute(select(Flight.total_seats).where(Flight.id == flight.id))
    ).scalar_one()
    if flight_total != actual_flight_total:
        raise ValidationError(
            f"Seat allocation no longer balances: seat classes sum to {flight_total} but the "
            f"flight capacity is {actual_flight_total}"
        )

    await db.refresh(seat_class)
    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.seat_class_adjust",
        entity_type="seat_class",
        entity_id=seat_class.id,
        before_state=before_state,
        after_state={
            "cabin_class": seat_class.cabin_class.value,
            "total_seats": seat_class.total_seats,
            "available_seats": seat_class.available_seats,
            "overbooking_buffer": seat_class.overbooking_buffer,
            "flight_total_seats": actual_flight_total,
        },
    )
    return seat_class


async def define_seat_map(
    db: AsyncSession, *, flight_id, payload: FlightSeatMapUpdate, actor: User
) -> Flight:
    """Store the seat layout as ``{seat_number: cabin_class}``."""
    flight = await get_flight_detail(db, flight_id)

    seat_numbers = [item.seat_number for item in payload.seat_map]
    if len(set(seat_numbers)) != len(seat_numbers):
        raise ValidationError("Seat numbers must be unique within a seat map")

    available_cabins = {seat_class.cabin_class for seat_class in flight.seat_classes}
    seat_map: dict[str, str] = {}
    for item in payload.seat_map:
        if item.cabin_class not in available_cabins:
            raise ValidationError(
                f"Cabin class {item.cabin_class.value} is not configured on this flight"
            )
        seat_map[item.seat_number] = item.cabin_class.value

    capacities = {
        seat_class.cabin_class.value: seat_class.total_seats for seat_class in flight.seat_classes
    }
    for cabin in capacities:
        seats_in_cabin = sum(1 for value in seat_map.values() if value == cabin)
        if seats_in_cabin > capacities[cabin]:
            raise ValidationError(
                f"Seat map defines {seats_in_cabin} {cabin} seats but the cabin holds "
                f"{capacities[cabin]}"
            )

    before_state = {"seat_map": flight.seat_map}
    flight.seat_map = seat_map
    flight.version = flight.version + 1
    await db.flush()
    # `updated_at` is server-generated (onupdate), so it must be re-read before serialization.
    await db.refresh(flight)

    await log_audit(
        db,
        actor_id=actor.id,
        action="flight.seat_map",
        entity_type="flight",
        entity_id=flight.id,
        before_state=before_state,
        after_state={"seat_map": seat_map},
    )
    return flight
