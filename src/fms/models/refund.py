from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fms.core.database import Base
from fms.core.enums import RefundStatus, RefundType


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("booking_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[RefundType] = mapped_column(
        Enum(RefundType, name="refund_type_enum", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        Enum(RefundStatus, name="refund_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    requires_approval: Mapped[bool] = mapped_column(nullable=False, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_refund_amount_positive"),
        Index("ix_refund_booking_item", "booking_item_id"),
    )

    def __repr__(self) -> str:
        return f"<Refund(id={self.id}, type={self.type.value}, status={self.status.value})>"