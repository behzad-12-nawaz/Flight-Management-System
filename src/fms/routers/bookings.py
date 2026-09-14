from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import get_current_user, get_idempotency_key
from fms.models.user import User
from fms.schemas.booking import (
    BookingCancelRequest,
    BookingConfirmRequest,
    BookingHoldRequest,
    BookingHoldResponse,
    BookingResponse,
)
from fms.services import booking_service, cancellation_service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("/hold", response_model=BookingHoldResponse, status_code=status.HTTP_201_CREATED)
async def hold_booking(
    payload: BookingHoldRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hold seats for a single, multi-leg or group booking (all-or-nothing)."""
    return await booking_service.hold_booking(db, user=current_user, payload=payload)


@router.post("/confirm", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def confirm_booking(
    payload: BookingConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Pay (mock) and confirm a hold group. Sending the same `Idempotency-Key` twice is a no-op."""
    return await booking_service.confirm_booking(
        db, user=current_user, payload=payload, idempotency_key=idempotency_key
    )


@router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Booking detail for the owner (or any admin)."""
    return await booking_service.get_booking_detail(db, booking_id=booking_id, user=current_user)


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: UUID,
    payload: BookingCancelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel every active item on a booking, refunding each item per its fare rules."""
    booking = await cancellation_service.get_booking(db, booking_id)
    cancellation_service.assert_owner_or_admin(current_user, booking)

    await cancellation_service.cancel_full_booking(
        db,
        booking_id=booking_id,
        actor=current_user,
        reason=payload.reason,
    )
    return await booking_service.get_booking_detail(db, booking_id=booking_id, user=current_user)
