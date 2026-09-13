from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fms.core.enums import CabinClass, WaitlistStatus


class WaitlistJoinRequest(BaseModel):
    flight_id: UUID
    cabin_class: CabinClass
    quantity: int = Field(gt=0)


class WaitlistEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flight_id: UUID
    seat_class_id: UUID
    user_id: UUID
    quantity: int
    status: WaitlistStatus
    created_at: datetime
    user_email: Optional[str] = None
    user_loyalty_tier: Optional[str] = None
    user_full_name: Optional[str] = None


class WaitlistPromoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    status: WaitlistStatus
    hold_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    message: str