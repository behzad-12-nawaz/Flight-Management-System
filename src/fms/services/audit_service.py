from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fms.models.audit import AuditLog


async def log_audit(
    db: AsyncSession,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    audit_log = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
    )
    db.add(audit_log)


async def list_audit_logs(
    db: AsyncSession,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """Read the audit trail, newest first, with optional filters."""
    statement = select(AuditLog).order_by(AuditLog.created_at.desc())

    if action:
        statement = statement.where(AuditLog.action == action)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditLog.entity_id == entity_id)

    statement = statement.limit(limit).offset(offset)
    return list((await db.execute(statement)).scalars().all())