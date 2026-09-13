from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fms.core.database import Base
from fms.core.enums import CabinClass, FlightStatus


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    flight_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(100), nullable=False)
    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    departure_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[FlightStatus] = mapped_column(
        Enum(FlightStatus, name="flight_status_enum", create_constraint=True, validate_strings=True),
        nullable=False,
        default=FlightStatus.SCHEDULED,
    )
    total_seats: Mapped[int] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    original_departure_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_arrival_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delay_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    seat_classes: Mapped[list[SeatClass]] = relationship(
        "SeatClass",
        back_populates="flight",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("arrival_datetime > departure_datetime", name="ck_flight_arrival_after_departure"),
        CheckConstraint("total_seats > 0", name="ck_flight_total_seats_positive"),
        Index(
            "ix_flight_unique_per_day",
            "flight_number",
            "origin",
            "destination",
            text("date_trunc('day', departure_datetime AT TIME ZONE 'UTC')"),
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<Flight(id={self.id}, flight_number={self.flight_number}, status={self.status.value})>"


class SeatClass(Base):
    __tablename__ = "seat_classes"

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
    cabin_class: Mapped[CabinClass] = mapped_column(
        Enum(CabinClass, name="cabin_class_enum", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    total_seats: Mapped[int] = mapped_column(nullable=False)
    available_seats: Mapped[int] = mapped_column(nullable=False)
    overbooking_buffer: Mapped[int] = mapped_column(nullable=False, default=0)
    version: Mapped[int] = mapped_column(nullable=False, default=1)

    flight: Mapped[Flight] = relationship("Flight", back_populates="seat_classes")
    fares: Mapped[list[Fare]] = relationship(
        "Fare",
        back_populates="seat_class",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("total_seats >= 0", name="ck_seat_class_total_seats_nonneg"),
        CheckConstraint("available_seats >= 0", name="ck_seat_class_available_seats_nonneg"),
        CheckConstraint("overbooking_buffer >= 0", name="ck_seat_class_overbooking_buffer_nonneg"),
        Index("ix_seat_class_flight_cabin", "flight_id", "cabin_class", unique=True),
    )

    def __repr__(self) -> str:
        return f"<SeatClass(id={self.id}, flight_id={self.flight_id}, cabin_class={self.cabin_class.value})>"