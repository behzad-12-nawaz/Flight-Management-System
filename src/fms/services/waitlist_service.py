from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.enums import FareType, FlightStatus, LoyaltyTier, WaitlistStatus
from fms.core.exceptions import ConflictError, NotFoundError
from fms.core.policy import TIER_WEIGHT
from fms.models.fare import Fare
from fms.models.flight import Flight, SeatClass
from fms.models.user import User
from fms.models.waitlist import WaitlistEntry
from fms.schemas.waitlist import WaitlistJoinRequest
from fms.services import seat_service
from fms.services.audit_service import log_audit
from fms.services.booking_service import get_seat_class_for_flight
from fms.services.notification_service import NotificationService

notification_service = NotificationService()


def _tier_weight(tier: LoyaltyTier | str) -> int:
    value = tier.value if isinstance(tier, LoyaltyTier) else str(tier)
    return TIER_WEIGHT.get(value, 0)


async def join_waitlist(db: AsyncSession, *, user: User, payload: WaitlistJoinRequest) -> WaitlistEntry:
    """Only allowed when the cabin cannot satisfy the request — otherwise book normally."""
    flight = await db.get(Flight, payload.flight_id)
    if flight is None:
        raise NotFoundError("Flight", payload.flight_id)

    if flight.status not in (FlightStatus.SCHEDULED, FlightStatus.DELAYED):
        raise ConflictError(f"Cannot join a waitlist for a {flight.status.value} flight")

    seat_class = await get_seat_class_for_flight(db, payload.flight_id, payload.cabin_class)
    available = await seat_service.get_available_seats(db, seat_class.id)

    if available >= payload.quantity:
        raise ConflictError(
            f"{available} seat(s) are still available in {payload.cabin_class.value}; "
            "please book normally instead of joining the waitlist"
        )

    entry = WaitlistEntry(
        flight_id=flight.id,
        seat_class_id=seat_class.id,
        user_id=user.id,
        quantity=payload.quantity,
        status=WaitlistStatus.WAITING,
    )
    db.add(entry)
    await db.flush()

    await log_audit(
        db,
        actor_id=user.id,
        action="waitlist.join",
        entity_type="waitlist_entry",
        entity_id=entry.id,
        before_state=None,
        after_state={
            "flight_id": str(flight.id),
            "cabin_class": seat_class.cabin_class.value,
            "quantity": entry.quantity,
            "status": entry.status.value,
        },
    )
    return entry


async def list_waitlist(db: AsyncSession, *, flight_id: UUID) -> list[dict]:
    """Waiting entries for a flight, priority-sorted in Python (tier weight desc, then FCFS)."""
    rows = (
        await db.execute(
            select(WaitlistEntry, User)
            .join(User, WaitlistEntry.user_id == User.id)
            .where(
                WaitlistEntry.flight_id == flight_id,
                WaitlistEntry.status == WaitlistStatus.WAITING,
            )
        )
    ).all()

    ordered = sorted(
        rows,
        key=lambda row: (-_tier_weight(row[1].loyalty_tier), row[0].created_at),
    )

    return [
        {
            "id": str(entry.id),
            "flight_id": str(entry.flight_id),
            "seat_class_id": str(entry.seat_class_id),
            "user_id": str(entry.user_id),
            "quantity": entry.quantity,
            "status": entry.status.value,
            "created_at": entry.created_at,
            "user_email": user.email,
            "user_loyalty_tier": user.loyalty_tier.value,
            "user_full_name": user.full_name,
        }
        for entry, user in ordered
    ]


async def promote_entry(db: AsyncSession, *, entry_id: UUID, actor: User) -> dict:
    """Reuse the normal optimistic-locking hold path to promote a waiting entry."""
    entry = await db.get(WaitlistEntry, entry_id)
    if entry is None:
        raise NotFoundError("WaitlistEntry", entry_id)

    if entry.status != WaitlistStatus.WAITING:
        raise ConflictError(f"Waitlist entry is not waiting (status: {entry.status.value})")

    seat_class = (
        await db.execute(select(SeatClass).where(SeatClass.id == entry.seat_class_id))
    ).scalar_one_or_none()
    if seat_class is None:
        raise NotFoundError("SeatClass", entry.seat_class_id)

    fare = (
        await db.execute(
            select(Fare).where(
                Fare.seat_class_id == seat_class.id,
                Fare.fare_type == FareType.BASIC,
            )
        )
    ).scalar_one_or_none()
    if fare is None:
        raise NotFoundError("Fare", f"{seat_class.id}/{FareType.BASIC.value}")

    try:
        hold = await seat_service.hold_seats(
            db,
            fare_id=fare.id,
            quantity=entry.quantity,
            user_id=entry.user_id,
        )
    except ConflictError as exc:
        raise ConflictError(
            "Seats are no longer available for this waitlist entry; it stays queued"
        ) from exc

    entry.status = WaitlistStatus.PROMOTED
    await db.flush()

    user = (await db.execute(select(User).where(User.id == entry.user_id))).scalar_one_or_none()
    flight = (await db.execute(select(Flight).where(Flight.id == entry.flight_id))).scalar_one_or_none()
    if user is not None and flight is not None:
        await notification_service.send_waitlist_promotion(
            user_email=user.email,
            flight_number=flight.flight_number,
            cabin_class=seat_class.cabin_class.value,
            hold_expires_at=hold.expires_at,
        )

    await log_audit(
        db,
        actor_id=actor.id,
        action="waitlist.promote",
        entity_type="waitlist_entry",
        entity_id=entry.id,
        before_state={"status": WaitlistStatus.WAITING.value},
        after_state={
            "status": entry.status.value,
            "hold_id": str(hold.id),
            "expires_at": hold.expires_at.isoformat(),
        },
    )

    return {
        "entry_id": str(entry.id),
        "status": entry.status.value,
        "hold_id": str(hold.id),
        "expires_at": hold.expires_at,
        "message": "Waitlist entry promoted; the passenger has a seat hold to confirm",
    }
