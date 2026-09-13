from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Enum as SQLEnum, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fms.core.database import Base
from fms.core.enums import WaitlistStatus


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flights.id", ondelete="CASCADE"),
        nullable=False,
    )
    seat_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seat_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[WaitlistStatus] = mapped_column(
        SQLEnum(WaitlistStatus, name="waitlist_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
        default=WaitlistStatus.WAITING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_waitlist_quantity_positive"),
        Index("ix_waitlist_flight_class_status", "flight_id", "seat_class_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<WaitlistEntry(id={self.id}, flight_id={self.flight_id}, status={self.status.value})>"