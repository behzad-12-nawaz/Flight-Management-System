from __future__ import annotations

from datetime import date as date_type
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from fms.core.database import get_db
from fms.schemas.flight import FlightSearchResult
from fms.services import search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/flights", response_model=List[FlightSearchResult])
async def search_flights(
    origin: Optional[str] = Query(default=None, description="Departure airport/city, case-insensitive"),
    destination: Optional[str] = Query(default=None, description="Arrival airport/city, case-insensitive"),
    date: Optional[date_type] = Query(default=None, description="Departure date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """Public search over `scheduled` flights, returning each cabin class with its fares.

    Prices are plain numeric values (no currency code) — currency handling is out of scope.
    """
    return await search_service.search_flights(
        db, origin=origin, destination=destination, date=date
    )
