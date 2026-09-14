from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fms.core.enums import BookingItemStatus, BookingStatus, CabinClass, FareType, HoldStatus
from fms.schemas.refund import RefundResponse


class PassengerInfo(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class BookingHoldRequestItem(BaseModel):
    flight_id: UUID
    cabin_class: CabinClass
    fare_type: FareType
    passengers: List[PassengerInfo] = Field(min_length=1)


class BookingHoldRequest(BaseModel):
    items: List[BookingHoldRequestItem] = Field(min_length=1)


class SeatHoldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fare_id: UUID
    quantity: int
    locked_price: Decimal
    status: HoldStatus
    group_key: UUID
    expires_at: datetime


class BookingHoldResponse(BaseModel):
    holds: List[SeatHoldResponse]
    group_key: UUID
    total_price: Decimal
    expires_at: datetime


class BookingConfirmRequest(BaseModel):
    group_key: UUID
    passenger_names: List[str] = Field(min_length=1)


class BookingItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flight_id: UUID
    fare_id: UUID
    leg_number: int
    passenger_name: str
    seat_number: Optional[str]
    status: BookingItemStatus
    price_paid: Decimal


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_reference: str
    user_id: UUID
    status: BookingStatus
    total_price: Decimal
    payment_status: str
    created_at: datetime
    cancelled_at: Optional[datetime]
    items: List[BookingItemResponse] = []


class BookingCancelRequest(BaseModel):
    reason: str = Field(default="customer", max_length=100)


class BookingItemCancelRequest(BaseModel):
    reason: str = Field(default="customer", max_length=100)


class BookingItemCancellationResponse(BaseModel):
    item: BookingItemResponse
    refund: Optional[RefundResponse] = None