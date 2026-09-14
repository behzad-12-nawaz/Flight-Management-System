from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fms.core.config import settings
from fms.core.enums import BookingItemStatus, BookingStatus, FareType, FlightStatus, HoldStatus
from fms.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from fms.core.policy import CUTOFF_MINUTES
from fms.models.booking import Booking, BookingItem, SeatHold
from fms.models.fare import Fare
from fms.models.flight import Flight, SeatClass
from fms.models.user import User
from fms.schemas.booking import (
    BookingConfirmRequest,
    BookingHoldRequest,
    BookingHoldResponse,
)
from fms.services import idempotency_service, seat_service
from fms.services.audit_service import log_audit
from fms.services.notification_service import NotificationService
from fms.services.payment_service import PaymentService
from fms.services.seat_service import utcnow

notification_service = NotificationService()
payment_service = PaymentService()

REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 6


def _generate_reference() -> str:
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))


async def get_flight(db: AsyncSession, flight_id: UUID) -> Flight:
    flight = await db.get(Flight, flight_id)
    if flight is None:
        raise NotFoundError("Flight", flight_id)
    return flight


async def get_seat_class_for_flight(db: AsyncSession, flight_id: UUID, cabin: str) -> SeatClass:
    seat_class = (
        await db.execute(
            select(SeatClass).where(SeatClass.flight_id == flight_id, SeatClass.cabin_class == cabin)
        )
    ).scalar_one_or_none()
    if seat_class is None:
        raise NotFoundError("SeatClass", f"{flight_id}/{cabin}")
    return seat_class


async def get_fare_for_seat_class(db: AsyncSession, seat_class_id: UUID, fare_type: FareType) -> Fare:
    fare = (
        await db.execute(
            select(Fare).where(Fare.seat_class_id == seat_class_id, Fare.fare_type == fare_type)
        )
    ).scalar_one_or_none()
    if fare is None:
        raise NotFoundError("Fare", f"{seat_class_id}/{fare_type.value}")
    return fare


async def _enforce_cutoff(flight: Flight, cabin) -> None:
    cutoff_minutes = CUTOFF_MINUTES.get(cabin, settings.cutoff_minutes_economy)
    if utcnow() > flight.departure_datetime - timedelta(minutes=cutoff_minutes):
        raise ValidationError(
            f"Booking for flight {flight.flight_number} is closed: it departs in less than "
            f"{cutoff_minutes} minutes"
        )


async def hold_booking(db: AsyncSession, *, user: User, payload: BookingHoldRequest) -> dict:
    """Hold every requested seat as one all-or-nothing group (multi-leg / group bookings).

    One ``group_key`` is shared by all ``seat_holds`` rows created here. If any leg cannot be
    held, everything already held for this request is released before the error propagates.
    """
    group_key = uuid4()
    created_holds: list[SeatHold] = []

    try:
        for item in payload.items:
            flight = await get_flight(db, item.flight_id)
            if flight.status != FlightStatus.SCHEDULED:
                raise ConflictError(
                    f"Flight {flight.flight_number} is not open for booking "
                    f"(status: {flight.status.value})"
                )

            seat_class = await get_seat_class_for_flight(db, item.flight_id, item.cabin_class)
            await _enforce_cutoff(flight, item.cabin_class)
            fare = await get_fare_for_seat_class(db, seat_class.id, item.fare_type)

            hold = await seat_service.hold_seats(
                db,
                fare_id=fare.id,
                quantity=len(item.passengers),
                user_id=user.id,
                group_key=group_key,
            )
            created_holds.append(hold)
    except Exception:
        await seat_service.release_holds(db, [hold.id for hold in created_holds])
        await db.commit()
        raise

    total_price = sum(
        (hold.locked_price * hold.quantity for hold in created_holds), Decimal("0")
    )
    expires_at = min(hold.expires_at for hold in created_holds)

    await log_audit(
        db,
        actor_id=user.id,
        action="booking.hold",
        entity_type="seat_hold",
        entity_id=group_key,
        before_state=None,
        after_state={
            "group_key": str(group_key),
            "hold_ids": [str(hold.id) for hold in created_holds],
            "total_price": str(total_price),
            "expires_at": expires_at.isoformat(),
        },
    )
    await db.flush()

    response = BookingHoldResponse(
        holds=[hold for hold in created_holds],
        group_key=group_key,
        total_price=total_price,
        expires_at=expires_at,
    )
    return response.model_dump(mode="json")


async def _unique_reference(db: AsyncSession) -> str:
    for _ in range(10):
        reference = _generate_reference()
        exists = (
            await db.execute(select(Booking.id).where(Booking.booking_reference == reference))
        ).scalar_one_or_none()
        if exists is None:
            return reference
    raise ConflictError("Could not generate a unique booking reference, please retry")


async def confirm_booking(
    db: AsyncSession,
    *,
    user: User,
    payload: BookingConfirmRequest,
    idempotency_key: str | None = None,
) -> dict:
    """Charge (mock) for a held group and turn the holds into a confirmed booking."""
    if idempotency_key:
        cached = idempotency_service.get(str(user.id), idempotency_key)
        if cached is not None:
            return cached

    holds = (
        (
            await db.execute(
                select(SeatHold)
                .where(SeatHold.group_key == payload.group_key)
                .order_by(SeatHold.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not holds:
        raise NotFoundError("Seat hold group", str(payload.group_key))

    if any(hold.user_id != user.id for hold in holds):
        raise ForbiddenError("These seat holds belong to another user")

    fare_cache: dict[UUID, tuple[Fare, SeatClass]] = {}
    for hold in holds:
        fare = await seat_service.get_fare(db, hold.fare_id)
        seat_class = await seat_service.get_seat_class(db, fare.seat_class_id)
        fare_cache[hold.fare_id] = (fare, seat_class)

    for _, seat_class in fare_cache.values():
        await seat_service.expire_stale_holds(db, seat_class.id)

    now = utcnow()
    for hold in holds:
        if hold.status != HoldStatus.ACTIVE or hold.expires_at < now:
            raise ConflictError("Your seat hold has expired, please search again")

    total_quantity = sum(hold.quantity for hold in holds)
    if len(payload.passenger_names) != total_quantity:
        raise ValidationError(
            f"Expected {total_quantity} passenger name(s) for this hold group, "
            f"got {len(payload.passenger_names)}"
        )

    total_price = sum((hold.locked_price * hold.quantity for hold in holds), Decimal("0"))

    payment = await payment_service.charge(
        total_price, description=f"Flight booking for hold group {payload.group_key}"
    )
    if not payment.success:
        raise ConflictError("Payment could not be processed")

    booking = Booking(
        booking_reference=await _unique_reference(db),
        user_id=user.id,
        status=BookingStatus.CONFIRMED,
        total_price=total_price,
        payment_status="paid",
    )
    db.add(booking)
    await db.flush()

    names = iter(payload.passenger_names)
    items: list[BookingItem] = []
    leg_by_flight: dict[UUID, int] = {}

    for hold in holds:
        fare, seat_class = fare_cache[hold.fare_id]
        if seat_class.flight_id not in leg_by_flight:
            leg_by_flight[seat_class.flight_id] = len(leg_by_flight) + 1
        leg_number = leg_by_flight[seat_class.flight_id]

        for _ in range(hold.quantity):
            item = BookingItem(
                booking_id=booking.id,
                flight_id=seat_class.flight_id,
                fare_id=fare.id,
                leg_number=leg_number,
                passenger_name=next(names),
                seat_number=None,
                status=BookingItemStatus.CONFIRMED,
                price_paid=hold.locked_price,
            )
            db.add(item)
            items.append(item)

    await db.flush()
    await seat_service.convert_hold_to_booking(db, [hold.id for hold in holds])
    await db.refresh(booking)

    await notification_service.send_booking_confirmation(
        user_email=user.email,
        booking_reference=booking.booking_reference,
        total_price=total_price,
        flight_details=[
            {
                "flight_id": str(item.flight_id),
                "leg_number": item.leg_number,
                "passenger_name": item.passenger_name,
            }
            for item in items
        ],
    )

    await log_audit(
        db,
        actor_id=user.id,
        action="booking.confirm",
        entity_type="booking",
        entity_id=booking.id,
        before_state={"group_key": str(payload.group_key)},
        after_state={
            "booking_reference": booking.booking_reference,
            "status": booking.status.value,
            "total_price": str(booking.total_price),
            "item_count": len(items),
        },
    )
    await db.flush()

    response = _serialize_booking(booking, items)
    if idempotency_key:
        idempotency_service.set(
            str(user.id), idempotency_key, response, settings.idempotency_ttl_seconds
        )
    return response


def _serialize_booking(booking: Booking, items: list[BookingItem]) -> dict:
    item_payloads = sorted(items, key=lambda item: (item.leg_number, item.passenger_name))
    return {
        "id": str(booking.id),
        "booking_reference": booking.booking_reference,
        "user_id": str(booking.user_id),
        "status": booking.status.value,
        "total_price": str(booking.total_price),
        "payment_status": booking.payment_status,
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
        "cancelled_at": booking.cancelled_at.isoformat() if booking.cancelled_at else None,
        "items": [
            {
                "id": str(item.id),
                "flight_id": str(item.flight_id),
                "fare_id": str(item.fare_id),
                "leg_number": item.leg_number,
                "passenger_name": item.passenger_name,
                "seat_number": item.seat_number,
                "status": item.status.value,
                "price_paid": str(item.price_paid),
            }
            for item in item_payloads
        ],
    }


async def get_booking_detail(db: AsyncSession, *, booking_id: UUID, user: User) -> Booking:
    booking = (
        await db.execute(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(selectinload(Booking.items))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if booking is None:
        raise NotFoundError("Booking", booking_id)

    from fms.services.cancellation_service import assert_owner_or_admin

    assert_owner_or_admin(user, booking)
    return booking


async def release_group(db: AsyncSession, *, group_key: UUID, user: User) -> int:
    """Abandon a checkout: release every active hold in the group."""
    holds = (
        (
            await db.execute(
                select(SeatHold).where(
                    SeatHold.group_key == group_key, SeatHold.status == HoldStatus.ACTIVE
                )
            )
        )
        .scalars()
        .all()
    )
    if not holds:
        raise NotFoundError("Seat hold group", str(group_key))
    if any(hold.user_id != user.id for hold in holds):
        raise ForbiddenError("These seat holds belong to another user")

    released = 0
    for hold in holds:
        hold = await seat_service.release_hold(db, hold.id)
        if hold is not None:
            released += 1
    return released


__all__ = [
    "hold_booking",
    "confirm_booking",
    "get_booking_detail",
    "release_group",
    "get_flight",
    "get_seat_class_for_flight",
    "get_fare_for_seat_class",
]
