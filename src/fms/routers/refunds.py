from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import require_role
from fms.core.enums import UserRole
from fms.models.user import User
from fms.schemas.refund import RefundResponse
from fms.services import cancellation_service

router = APIRouter(prefix="/refunds", tags=["refunds"])


@router.post("/{refund_id}/approve", response_model=RefundResponse)
async def approve_refund(
    refund_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(UserRole.SUPER_ADMIN)),
):
    """Approve an airline-caused refund/travel credit that is awaiting manual approval."""
    return await cancellation_service.approve_refund(db, refund_id=refund_id, actor=actor)
