from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from fms.core.enums import FareType


class FareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fare_type: FareType
    price: Decimal
    refundable: bool
    change_allowed: bool
    seat_choice_allowed: bool