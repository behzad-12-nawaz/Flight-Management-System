from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    CUSTOMER = "customer"
    OPS_AGENT = "ops_agent"
    SUPER_ADMIN = "super_admin"


class LoyaltyTier(str, Enum):
    NONE = "none"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class CabinClass(str, Enum):
    ECONOMY = "economy"
    BUSINESS = "business"
    FIRST = "first"


class FareType(str, Enum):
    BASIC = "basic"
    FLEXIBLE = "flexible"


class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class HoldStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CONVERTED = "converted"
    RELEASED = "released"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    PARTIALLY_CANCELLED = "partially_cancelled"
    CANCELLED = "cancelled"


class BookingItemStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class WaitlistStatus(str, Enum):
    WAITING = "waiting"
    PROMOTED = "promoted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RefundType(str, Enum):
    REFUND = "refund"
    TRAVEL_CREDIT = "travel_credit"


class RefundStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    PROCESSED = "processed"
    EXPIRED = "expired"
    REJECTED = "rejected"