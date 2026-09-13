from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: Optional[UUID]
    action: str
    entity_type: str
    entity_id: UUID
    before_state: Optional[Any]
    after_state: Optional[Any]
    created_at: datetime