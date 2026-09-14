from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import get_current_user, require_role
from fms.core.enums import UserRole
from fms.models.user import User
from fms.schemas.waitlist import (
    WaitlistEntryResponse,
    WaitlistJoinRequest,
    WaitlistPromoteResponse,
)
from fms.services import waitlist_service

router = APIRouter(tags=["waitlist"])

OPS_ROLES = (UserRole.OPS_AGENT, UserRole.SUPER_ADMIN)


@router.post("/waitlist", response_model=WaitlistEntryResponse, status_code=status.HTTP_201_CREATED)
async def join_waitlist(
    payload: WaitlistJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Join the waitlist — only valid when the cabin has fewer seats than requested."""
    return await waitlist_service.join_waitlist(db, user=current_user, payload=payload)


@router.get("/admin/waitlist/{flight_id}", response_model=List[WaitlistEntryResponse])
async def list_waitlist(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_role(*OPS_ROLES)),
):
    """Waiting entries for a flight, priority-sorted (loyalty tier desc, then first-come)."""
    return await waitlist_service.list_waitlist(db, flight_id=flight_id)


@router.post("/admin/waitlist/{entry_id}/promote", response_model=WaitlistPromoteResponse)
async def promote_waitlist_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    """Manually promote a waiting entry; the passenger still has to confirm inside the hold window."""
    return await waitlist_service.promote_entry(db, entry_id=entry_id, actor=actor)
