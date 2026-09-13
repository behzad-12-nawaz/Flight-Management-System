from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fms.core.database import Base
from fms.core.enums import FareType


class Fare(Base):
    __tablename__ = "fares"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    seat_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seat_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    fare_type: Mapped[FareType] = mapped_column(
        Enum(FareType, name="fare_type_enum", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    refundable: Mapped[bool] = mapped_column(nullable=False)
    change_allowed: Mapped[bool] = mapped_column(nullable=False)
    seat_choice_allowed: Mapped[bool] = mapped_column(nullable=False)

    seat_class: Mapped[SeatClass] = relationship("SeatClass", back_populates="fares")

    __table_args__ = (
        CheckConstraint("price > 0", name="ck_fare_price_positive"),
        Index("ix_fare_seat_class_fare_type", "seat_class_id", "fare_type", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Fare(id={self.id}, seat_class_id={self.seat_class_id}, fare_type={self.fare_type.value})>"


from fms.models.flight import SeatClass