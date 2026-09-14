from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.core.dependencies import require_role
from fms.core.enums import CabinClass, UserRole
from fms.models.user import User
from fms.schemas.flight import (
    FlightCreate,
    FlightDelayUpdate,
    FlightResponse,
    FlightScheduleUpdate,
    FlightSeatMapUpdate,
    SeatClassResponse,
    SeatClassUpdate,
)
from fms.services import flight_service

router = APIRouter(prefix="/admin/flights", tags=["admin-flights"])

OPS_ROLES = (UserRole.OPS_AGENT, UserRole.SUPER_ADMIN)


@router.post("", response_model=FlightResponse, status_code=status.HTTP_201_CREATED)
async def create_flight(
    payload: FlightCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    return await flight_service.create_flight(db, payload=payload, actor=actor)


@router.patch("/{flight_id}", response_model=FlightResponse)
async def edit_flight_schedule(
    flight_id: UUID,
    payload: FlightScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    return await flight_service.edit_flight_schedule(
        db, flight_id=flight_id, payload=payload, actor=actor
    )


@router.patch("/{flight_id}/delay", response_model=FlightResponse)
async def mark_flight_delayed(
    flight_id: UUID,
    payload: FlightDelayUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    return await flight_service.mark_flight_delayed(
        db, flight_id=flight_id, payload=payload, actor=actor
    )


@router.patch("/{flight_id}/resolve-delay", response_model=FlightResponse)
async def resolve_delay(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    return await flight_service.resolve_delay(db, flight_id=flight_id, actor=actor)


@router.delete("/{flight_id}", response_model=FlightResponse)
async def cancel_flight(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(UserRole.SUPER_ADMIN)),
):
    return await flight_service.cancel_flight(db, flight_id=flight_id, actor=actor)


@router.put("/{flight_id}/seat-map", response_model=FlightResponse)
async def define_seat_map(
    flight_id: UUID,
    payload: FlightSeatMapUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(*OPS_ROLES)),
):
    return await flight_service.define_seat_map(db, flight_id=flight_id, payload=payload, actor=actor)


@router.patch("/{flight_id}/seat-classes/{cabin_class}", response_model=SeatClassResponse)
async def adjust_seat_allocation(
    flight_id: UUID,
    cabin_class: CabinClass,
    payload: SeatClassUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role(UserRole.SUPER_ADMIN)),
):
    return await flight_service.adjust_seat_allocation(
        db,
        flight_id=flight_id,
        cabin_class=cabin_class,
        payload=payload,
        actor=actor,
    )
