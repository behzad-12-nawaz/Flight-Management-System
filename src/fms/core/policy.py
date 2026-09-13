from __future__ import annotations

from fms.core.enums import CabinClass

FLEXIBLE_FARE_MULTIPLIER = 1.35
SCHEDULE_CHANGE_OVERRIDE_HOURS = 3
MAX_OPTIMISTIC_LOCK_RETRIES = 3

CUTOFF_MINUTES: dict[CabinClass, int] = {
    CabinClass.ECONOMY: 60,
    CabinClass.BUSINESS: 30,
    CabinClass.FIRST: 15,
}

TIER_WEIGHT = {
    "platinum": 3,
    "gold": 2,
    "silver": 1,
    "none": 0,
}

AUTO_APPROVED_ACTIONS = {
    "customer_cancellation_refundable",
    "customer_cancellation_flexible",
}

MANUAL_APPROVAL_ACTIONS = {
    "airline_cancellation_refund",
    "airline_cancellation_credit",
    "airline_schedule_change_refund",
    "airline_schedule_change_credit",
}