from __future__ import annotations

from fms.routers.admin_flights import router as admin_flights_router
from fms.routers.audit import router as audit_router
from fms.routers.auth import router as auth_router
from fms.routers.bookings import router as bookings_router
from fms.routers.cancellations import router as cancellations_router
from fms.routers.refunds import router as refunds_router
from fms.routers.search import router as search_router
from fms.routers.waitlist import router as waitlist_router

__all__ = [
    "auth_router",
    "admin_flights_router",
    "search_router",
    "bookings_router",
    "cancellations_router",
    "refunds_router",
    "waitlist_router",
    "audit_router",
]
