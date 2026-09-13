from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fms.core import fms_exception_handler
from fms.core.database import engine
from fms.core.exceptions import FMSException
from fms.routers import auth


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

app.include_router(auth.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}