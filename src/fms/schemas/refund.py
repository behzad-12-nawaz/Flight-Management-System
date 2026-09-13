from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from fms.core.enums import RefundStatus, RefundType


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_item_id: UUID
    type: RefundType
    amount: Decimal
    status: RefundStatus
    requires_approval: bool
    approved_by: Optional[UUID]
    expires_at: Optional[datetime]
    created_at: datetime


class RefundApproveRequest(BaseModel):
    pass