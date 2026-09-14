from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.config import settings
from fms.core.database import get_db
from fms.core.dependencies import get_current_user, get_idempotency_key
from fms.models.user import User
from fms.schemas.booking import (
    BookingItemCancelRequest,
    BookingItemCancellationResponse,
)
from fms.services import cancellation_service, idempotency_service

router = APIRouter(tags=["cancellations"])


@router.post(
    "/bookings/{booking_id}/items/{item_id}/cancel",
    response_model=BookingItemCancellationResponse,
)
async def cancel_booking_item(
    booking_id: UUID,
    item_id: UUID,
    payload: BookingItemCancelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Cancel a single passenger-per-leg item and branch the refund by fare/initiator rules."""
    if idempotency_key:
        cached = idempotency_service.get(str(current_user.id), idempotency_key)
        if cached is not None:
            return cached

    booking = await cancellation_service.get_booking(db, booking_id)
    cancellation_service.assert_owner_or_admin(current_user, booking)

    item, refund = await cancellation_service.cancel_booking_item(
        db, item_id=item_id, actor=current_user, reason=payload.reason
    )
    await db.flush()

    response = BookingItemCancellationResponse(item=item, refund=refund).model_dump(mode="json")

    if idempotency_key:
        idempotency_service.set(
            str(current_user.id), idempotency_key, response, settings.idempotency_ttl_seconds
        )
    return response
