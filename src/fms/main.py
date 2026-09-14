from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fms.core import fms_exception_handler
from fms.core.database import engine
from fms.core.exceptions import FMSException
from fms.routers import (
    admin_flights_router,
    audit_router,
    auth_router,
    bookings_router,
    cancellations_router,
    refunds_router,
    search_router,
    waitlist_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="Flight Management System",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(FMSException, cast(Any, fms_exception_handler))

app.include_router(auth_router)
app.include_router(admin_flights_router)
app.include_router(search_router)
app.include_router(bookings_router)
app.include_router(cancellations_router)
app.include_router(refunds_router)
app.include_router(waitlist_router)
app.include_router(audit_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
