from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class NotificationService:
    async def send_booking_confirmation(
        self,
        user_email: str,
        booking_reference: str,
        total_price: Decimal,
        flight_details: list[dict[str, Any]],
    ) -> None:
        logger.info(
            "EMAIL: Booking confirmation sent to %s for booking %s (total: %s)",
            user_email,
            booking_reference,
            total_price,
        )
        logger.info("Flight details: %s", flight_details)

    async def send_cancellation_notice(
        self,
        user_email: str,
        booking_reference: str,
        refund_amount: Decimal,
        refund_type: str,
    ) -> None:
        logger.info(
            "EMAIL: Cancellation notice sent to %s for booking %s (refund: %s, type: %s)",
            user_email,
            booking_reference,
            refund_amount,
            refund_type,
        )

    async def send_delay_notice(
        self,
        user_email: str,
        flight_number: str,
        old_departure: Any,
        new_departure: Any,
        delay_reason: str,
    ) -> None:
        logger.info(
            "EMAIL: Delay notice sent to %s for flight %s (old: %s, new: %s, reason: %s)",
            user_email,
            flight_number,
            old_departure,
            new_departure,
            delay_reason,
        )

    async def send_waitlist_promotion(
        self,
        user_email: str,
        flight_number: str,
        cabin_class: str,
        hold_expires_at: Any,
    ) -> None:
        logger.info(
            "EMAIL: Waitlist promotion sent to %s for flight %s (%s) - hold expires at %s",
            user_email,
            flight_number,
            cabin_class,
            hold_expires_at,
        )