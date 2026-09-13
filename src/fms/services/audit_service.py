from __future__ import annotations

from typing import Any
from uuid import UUID

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