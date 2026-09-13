from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fms.core.enums import CabinClass, FareType, FlightStatus


class SeatClassBase(BaseModel):
    cabin_class: CabinClass
    total_seats: int = Field(gt=0)
    overbooking_buffer: int = Field(default=0, ge=0)


class SeatClassCreate(SeatClassBase):
    pass


class SeatClassUpdate(BaseModel):
    total_seats: Optional[int] = Field(default=None, gt=0)
    overbooking_buffer: Optional[int] = Field(default=None, ge=0)


class SeatClassResponse(SeatClassBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    available_seats: int
    version: int


class FareBase(BaseModel):
    fare_type: FareType
    price: Decimal = Field(gt=0)
    refundable: bool
    change_allowed: bool
    seat_choice_allowed: bool


class FareResponse(FareBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


class FlightBase(BaseModel):
    flight_number: str = Field(min_length=1, max_length=50)
    origin: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)
    departure_datetime: datetime
    arrival_datetime: datetime
    seat_classes: List[SeatClassCreate] = Field(min_length=1)


class FlightCreate(FlightBase):
    pass


class FlightScheduleUpdate(BaseModel):
    departure_datetime: Optional[datetime] = None
    arrival_datetime: Optional[datetime] = None


class FlightDelayUpdate(BaseModel):
    new_departure: datetime
    new_arrival: datetime
    delay_reason: str = Field(min_length=1, max_length=500)


class SeatMapItem(BaseModel):
    seat_number: str
    cabin_class: CabinClass


class FlightSeatMapUpdate(BaseModel):
    seat_map: List[SeatMapItem] = Field(min_length=1)


class FlightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flight_number: str
    origin: str
    destination: str
    departure_datetime: datetime
    arrival_datetime: datetime
    status: FlightStatus
    total_seats: int
    version: int
    original_departure_datetime: Optional[datetime]
    original_arrival_datetime: Optional[datetime]
    delay_reason: Optional[str]
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class FlightSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flight_number: str
    origin: str
    destination: str
    departure_datetime: datetime
    arrival_datetime: datetime
    status: FlightStatus
    seat_classes: List[SeatClassResponse]