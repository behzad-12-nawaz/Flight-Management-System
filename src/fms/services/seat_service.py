from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.enums import HoldStatus
from fms.core.exceptions import ConflictError, NotFoundError, ValidationError
from fms.core.policy import MAX_OPTIMISTIC_LOCK_RETRIES
from fms.models.booking import SeatHold
from fms.models.fare import Fare
from fms.models.flight import SeatClass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_fare(db: AsyncSession, fare_id: UUID) -> Fare:
    fare = (
        await db.execute(select(Fare).where(Fare.id == fare_id).execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if fare is None:
        raise NotFoundError("Fare", fare_id)
    return fare


async def get_seat_class(db: AsyncSession, seat_class_id: UUID) -> SeatClass:
    seat_class = (
        await db.execute(
            select(SeatClass).where(SeatClass.id == seat_class_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if seat_class is None:
        raise NotFoundError("SeatClass", seat_class_id)
    return seat_class


async def get_seat_class_for_fare(db: AsyncSession, fare_id: UUID) -> SeatClass:
    fare = await get_fare(db, fare_id)
    return await get_seat_class(db, fare.seat_class_id)


async def _read_version_and_available(db: AsyncSession, seat_class_id: UUID) -> tuple[int, int] | None:
    """Read ``(version, available_seats)`` straight from the database.

    Selecting the individual columns (instead of the ORM entity) deliberately bypasses the
    session identity map, so a retry always sees the freshest committed/visible values.
    """
    row = (
        await db.execute(
            select(SeatClass.version, SeatClass.available_seats).where(SeatClass.id == seat_class_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return int(row[0]), int(row[1])


async def adjust_available_seats(
    db: AsyncSession,
    seat_class_id: UUID,
    delta: int,
    *,
    max_retries: int = MAX_OPTIMISTIC_LOCK_RETRIES,
) -> bool:
    """Atomically add ``delta`` to ``available_seats`` using optimistic locking.

    ``delta < 0`` consumes seats and fails (returns ``False``) when there are not enough of
    them. ``delta > 0`` gives seats back. On a version conflict the read/update cycle is retried
    up to ``max_retries`` times so two simultaneous writers can never oversell the cabin.
    """
    if delta == 0:
        return True

    for _ in range(max(1, max_retries)):
        current = await _read_version_and_available(db, seat_class_id)
        if current is None:
            raise NotFoundError("SeatClass", seat_class_id)

        version, available = current
        if delta < 0 and available < -delta:
            return False

        conditions = [SeatClass.id == seat_class_id, SeatClass.version == version]
        if delta < 0:
            conditions.append(SeatClass.available_seats >= -delta)

        result = await db.execute(
            update(SeatClass)
            .where(*conditions)
            .values(available_seats=SeatClass.available_seats + delta, version=SeatClass.version + 1)
        )
        if result.rowcount == 1:
            return True

    return False


async def expire_stale_holds(db: AsyncSession, seat_class_id: UUID) -> int:
    """Lazy (check-on-read) expiry of holds whose ``expires_at`` has passed.

    This is the only expiry mechanism in the system — there is no background scheduler. It is
    called at the top of every code path that reads or mutates seat availability, so a hold that
    nobody ever looks at again is invisible to future availability checks even though its row
    still says ``active``.
    """
    now = utcnow()
    stale_holds = (
        (
            await db.execute(
                select(SeatHold)
                .join(Fare, SeatHold.fare_id == Fare.id)
                .where(
                    Fare.seat_class_id == seat_class_id,
                    SeatHold.status == HoldStatus.ACTIVE,
                    SeatHold.expires_at < now,
                )
            )
        )
        .scalars()
        .all()
    )

    for hold in stale_holds:
        await adjust_available_seats(db, seat_class_id, hold.quantity)
        hold.status = HoldStatus.EXPIRED

    if stale_holds:
        await db.flush()

    return len(stale_holds)


async def expire_all_stale_holds(db: AsyncSession) -> int:
    """Run :func:`expire_stale_holds` for every seat class that currently has stale holds."""
    now = utcnow()
    seat_class_ids = (
        (
            await db.execute(
                select(Fare.seat_class_id)
                .join(SeatHold, SeatHold.fare_id == Fare.id)
                .where(SeatHold.status == HoldStatus.ACTIVE, SeatHold.expires_at < now)
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    expired = 0
    for seat_class_id in seat_class_ids:
        expired += await expire_stale_holds(db, seat_class_id)
    return expired


async def get_available_seats(db: AsyncSession, seat_class_id: UUID) -> int:
    """Available seats for a cabin, after lazily expiring any stale holds."""
    await expire_stale_holds(db, seat_class_id)
    current = await _read_version_and_available(db, seat_class_id)
    if current is None:
        raise NotFoundError("SeatClass", seat_class_id)
    return current[1]


async def hold_seats(
    db: AsyncSession,
    *,
    fare_id: UUID,
    quantity: int,
    user_id: UUID,
    group_key: UUID | None = None,
) -> SeatHold:
    """Hold ``quantity`` seats on a fare's cabin class.

    Raises :class:`ConflictError` when the cabin does not have enough seats left (the caller is
    responsible for releasing any sibling holds already created for the same group).
    """
    if quantity <= 0:
        raise ValidationError("Hold quantity must be greater than zero")

    fare = await get_fare(db, fare_id)
    await expire_stale_holds(db, fare.seat_class_id)

    acquired = await adjust_available_seats(db, fare.seat_class_id, -quantity)
    if not acquired:
        raise ConflictError("Not enough seats available for the requested fare")

    hold = SeatHold(
        user_id=user_id,
        fare_id=fare.id,
        quantity=quantity,
        locked_price=fare.price,
        status=HoldStatus.ACTIVE,
        group_key=group_key or uuid4(),
        expires_at=utcnow() + timedelta(minutes=settings.seat_hold_minutes),
    )
    db.add(hold)
    await db.flush()
    return hold


async def release_hold(db: AsyncSession, hold_id: UUID) -> SeatHold | None:
    """Manually release a hold, returning its seats to the cabin."""
    hold = await db.get(SeatHold, hold_id)
    if hold is None:
        return None

    if hold.status != HoldStatus.ACTIVE:
        return hold

    fare = await get_fare(db, hold.fare_id)
    await adjust_available_seats(db, fare.seat_class_id, hold.quantity)
    hold.status = HoldStatus.RELEASED
    await db.flush()
    return hold


async def release_holds(db: AsyncSession, hold_ids: list[UUID]) -> None:
    for hold_id in hold_ids:
        await release_hold(db, hold_id)


async def release_holds_for_group(db: AsyncSession, group_key: UUID) -> int:
    """Release every active hold sharing a group key (all-or-nothing rollback helper)."""
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
    for hold in holds:
        await release_hold(db, hold.id)
    return len(holds)


async def convert_hold_to_booking(db: AsyncSession, hold_ids: list[UUID]) -> int:
    """Mark holds as converted. Seats were already decremented when the hold was created."""
    converted = 0
    for hold_id in hold_ids:
        hold = await db.get(SeatHold, hold_id)
        if hold is None or hold.status != HoldStatus.ACTIVE:
            continue
        hold.status = HoldStatus.CONVERTED
        converted += 1
    await db.flush()
    return converted


async def released_seats_for_fare(db: AsyncSession, fare_id: UUID, quantity: int = 1) -> None:
    """Give seats back to the cabin a cancelled booking item used to occupy."""
    fare = await get_fare(db, fare_id)
    released = await adjust_available_seats(db, fare.seat_class_id, quantity)
    if not released:
        # The cabin was already full (e.g. overbooking buffer in play); the CHECK constraint on
        # available_seats >= 0 cannot be violated by a release, so this is a defensive guard.
        raise ConflictError("Unable to release seat back to inventory")
