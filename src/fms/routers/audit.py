from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import require_role
from fms.core.enums import UserRole
from fms.models.user import User
from fms.schemas.audit import AuditLogResponse
from fms.services.audit_service import list_audit_logs

router = APIRouter(prefix="/admin", tags=["audit"])


@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def get_audit_logs(
    action: Optional[str] = Query(default=None, description="Exact action name, e.g. flight.create"),
    entity_type: Optional[str] = Query(default=None, description="e.g. flight, booking, refund"),
    entity_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_role(UserRole.OPS_AGENT, UserRole.SUPER_ADMIN)),
):
    """Newest-first audit trail (read-only view over every mutating operation)."""
    return await list_audit_logs(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
        offset=offset,
    )
