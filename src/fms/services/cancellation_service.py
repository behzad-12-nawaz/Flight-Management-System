from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.enums import BookingItemStatus, BookingStatus, RefundStatus, RefundType
from fms.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from fms.core.policy import MANUAL_APPROVAL_ACTIONS
from fms.models.booking import Booking, BookingItem
from fms.models.fare import Fare
from fms.models.refund import Refund
from fms.models.user import User
from fms.services import seat_service
from fms.services.audit_service import log_audit
from fms.services.notification_service import NotificationService
from fms.services.seat_service import utcnow

notification_service = NotificationService()

CUSTOMER_REASON = "customer"
AIRLINE_CANCELLATION_REASON = "airline_cancellation"
AIRLINE_SCHEDULE_CHANGE_REASON = "airline_schedule_change"


async def get_booking_item(db: AsyncSession, item_id: UUID) -> BookingItem:
    item = await db.get(BookingItem, item_id)
    if item is None:
        raise NotFoundError("BookingItem", item_id)
    return item


async def get_booking(db: AsyncSession, booking_id: UUID) -> Booking:
    booking = await db.get(Booking, booking_id)
    if booking is None:
        raise NotFoundError("Booking", booking_id)
    return booking


def assert_owner_or_admin(user: User, booking: Booking) -> None:
    from fms.core.enums import UserRole

    if booking.user_id == user.id:
        return
    if user.role in (UserRole.OPS_AGENT, UserRole.SUPER_ADMIN):
        return
    raise ForbiddenError("You do not have access to this booking")


def _refund_action_key(reason: str, refund_type: RefundType) -> str:
    suffix = "credit" if refund_type == RefundType.TRAVEL_CREDIT else "refund"
    return f"{reason}_{suffix}"


async def create_refund_for_item(
    db: AsyncSession,
    *,
    item: BookingItem,
    fare: Fare,
    reason: str,
) -> Refund | None:
    """Create the refund/travel-credit row for a cancelled item, or ``None`` when nothing is due.

    - Customer-initiated: money back only when the fare is refundable (or a schedule-change
      override made it effectively refundable). Non-refundable fares get nothing.
    - Airline-caused: always creates a row — a refund when the fare was refundable, otherwise a
      travel credit. These are gated behind a super-admin approval per Decision #8.
    """
    now = utcnow()

    if reason == CUSTOMER_REASON:
        effectively_refundable = fare.refundable or item.schedule_change_override
        if not effectively_refundable:
            return None
        action_key = "customer_cancellation_refundable"
        requires_approval = action_key in MANUAL_APPROVAL_ACTIONS
        refund = Refund(
            booking_item_id=item.id,
            type=RefundType.REFUND,
            amount=item.price_paid,
            status=RefundStatus.PENDING_APPROVAL if requires_approval else RefundStatus.PROCESSED,
            requires_approval=requires_approval,
            expires_at=None,
        )
        db.add(refund)
        return refund

    if reason not in (AIRLINE_CANCELLATION_REASON, AIRLINE_SCHEDULE_CHANGE_REASON):
        raise ValidationError(f"Unsupported cancellation reason: {reason}")

    refund_type = RefundType.REFUND if fare.refundable else RefundType.TRAVEL_CREDIT
    action_key = _refund_action_key(reason, refund_type)
    requires_approval = action_key in MANUAL_APPROVAL_ACTIONS
    refund = Refund(
        booking_item_id=item.id,
        type=refund_type,
        amount=item.price_paid,
        status=RefundStatus.PENDING_APPROVAL if requires_approval else RefundStatus.PROCESSED,
        requires_approval=requires_approval,
        expires_at=(
            now + timedelta(days=settings.credit_expiry_days)
            if refund_type == RefundType.TRAVEL_CREDIT
            else None
        ),
    )
    db.add(refund)
    return refund


async def recompute_booking_status(db: AsyncSession, booking: Booking) -> BookingStatus:
    """Derive the parent booking status from its items: cancelled / partially_cancelled / confirmed."""
    await db.flush()
    items = (
        (await db.execute(select(BookingItem).where(BookingItem.booking_id == booking.id)))
        .scalars()
        .all()
    )

    if items and all(item.status == BookingItemStatus.CANCELLED for item in items):
        booking.status = BookingStatus.CANCELLED
        if booking.cancelled_at is None:
            booking.cancelled_at = utcnow()
    elif any(item.status == BookingItemStatus.CANCELLED for item in items):
        booking.status = BookingStatus.PARTIALLY_CANCELLED
    else:
        booking.status = BookingStatus.CONFIRMED
        booking.cancelled_at = None

    await db.flush()
    return booking.status


async def cancel_booking_item(
    db: AsyncSession,
    *,
    item_id: UUID,
    actor: User | None,
    reason: str = CUSTOMER_REASON,
) -> tuple[BookingItem, Refund | None]:
    """Cancel a single passenger-per-leg item: release its seat, cancel it, and branch the refund."""
    item = await get_booking_item(db, item_id)
    booking = await get_booking(db, item.booking_id)

    if item.status == BookingItemStatus.CANCELLED:
        raise ConflictError("This booking item is already cancelled")

    fare = (
        await db.execute(
            select(Fare).where(Fare.id == item.fare_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if fare is None:
        raise NotFoundError("Fare", item.fare_id)

    await seat_service.released_seats_for_fare(db, item.fare_id, 1)

    before_state = {
        "status": item.status.value,
        "booking_status": booking.status.value,
        "reason": reason,
    }

    item.status = BookingItemStatus.CANCELLED
    refund = await create_refund_for_item(db, item=item, fare=fare, reason=reason)
    await db.flush()

    new_status = await recompute_booking_status(db, booking)

    if refund is not None and actor is not None:
        await notification_service.send_cancellation_notice(
            user_email=(await _user_email(db, booking.user_id)) or "",
            booking_reference=booking.booking_reference,
            refund_amount=refund.amount,
            refund_type=refund.type.value,
        )

    await log_audit(
        db,
        actor_id=actor.id if actor else None,
        action="booking.cancel_item",
        entity_type="booking_item",
        entity_id=item.id,
        before_state=before_state,
        after_state={
            "status": item.status.value,
            "booking_status": new_status.value,
            "refund_type": refund.type.value if refund else None,
            "refund_amount": str(refund.amount) if refund else None,
            "refund_status": refund.status.value if refund else None,
        },
    )

    return item, refund


async def cancel_full_booking(
    db: AsyncSession,
    *,
    booking_id: UUID,
    actor: User | None,
    reason: str = CUSTOMER_REASON,
) -> Booking:
    """Cancel every still-confirmed item on a booking (what ``POST /bookings/{id}/cancel`` calls)."""
    booking = await get_booking(db, booking_id)

    items = (
        (
            await db.execute(
                select(BookingItem).where(
                    BookingItem.booking_id == booking.id,
                    BookingItem.status == BookingItemStatus.CONFIRMED,
                )
            )
        )
        .scalars()
        .all()
    )

    if not items:
        raise ConflictError("This booking has no active items to cancel")

    for item in items:
        await cancel_booking_item(db, item_id=item.id, actor=actor, reason=reason)

    await recompute_booking_status(db, booking)

    await log_audit(
        db,
        actor_id=actor.id if actor else None,
        action="booking.cancel",
        entity_type="booking",
        entity_id=booking.id,
        before_state={"status": BookingStatus.CONFIRMED.value},
        after_state={"status": booking.status.value, "cancelled_items": len(items)},
    )

    return booking


async def approve_refund(db: AsyncSession, *, refund_id: UUID, actor: User) -> Refund:
    """Move an airline-caused refund/travel credit from ``pending_approval`` to ``processed``."""
    refund = await db.get(Refund, refund_id)
    if refund is None:
        raise NotFoundError("Refund", refund_id)

    if refund.status != RefundStatus.PENDING_APPROVAL:
        raise ConflictError(f"Refund is not awaiting approval (current status: {refund.status.value})")

    before_state = {"status": refund.status.value, "approved_by": None}
    refund.status = RefundStatus.PROCESSED
    refund.approved_by = actor.id
    await db.flush()

    await log_audit(
        db,
        actor_id=actor.id,
        action="refund.approve",
        entity_type="refund",
        entity_id=refund.id,
        before_state=before_state,
        after_state={"status": refund.status.value, "approved_by": str(actor.id)},
    )
    return refund


async def _user_email(db: AsyncSession, user_id: UUID) -> str | None:
    return (await db.execute(select(User.email).where(User.id == user_id))).scalar_one_or_none()
