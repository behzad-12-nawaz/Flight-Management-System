from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fms.core.database import Base
from fms.core.enums import BookingItemStatus, BookingStatus, HoldStatus


class SeatHold(Base):
    __tablename__ = "seat_holds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fare_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fares.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    locked_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[HoldStatus] = mapped_column(
        Enum(HoldStatus, name="hold_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
        default=HoldStatus.ACTIVE,
    )
    group_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_seat_hold_quantity_positive"),
    )

    def __repr__(self) -> str:
        return f"<SeatHold(id={self.id}, user_id={self.user_id}, status={self.status.value})>"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
        default=BookingStatus.CONFIRMED,
    )
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="paid")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[BookingItem]] = relationship(
        "BookingItem",
        back_populates="booking",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Booking(id={self.id}, ref={self.booking_reference}, status={self.status.value})>"


class BookingItem(Base):
    __tablename__ = "booking_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )
    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flights.id", ondelete="CASCADE"),
        nullable=False,
    )
    fare_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fares.id", ondelete="CASCADE"),
        nullable=False,
    )
    leg_number: Mapped[int] = mapped_column(nullable=False, default=1)
    passenger_name: Mapped[str] = mapped_column(String(255), nullable=False)
    seat_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[BookingItemStatus] = mapped_column(
        Enum(BookingItemStatus, name="booking_item_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
        default=BookingItemStatus.CONFIRMED,
    )
    price_paid: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    schedule_change_override: Mapped[bool] = mapped_column(nullable=False, default=False)

    booking: Mapped[Booking] = relationship("Booking", back_populates="items")

    def __repr__(self) -> str:
        return f"<BookingItem(id={self.id}, booking_id={self.booking_id}, passenger={self.passenger_name})>"