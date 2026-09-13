from __future__ import annotations

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/fms",
        alias="DATABASE_URL",
    )
    jwt_secret_key: str = Field(default="change-me-to-a-random-64-char-string", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    seat_hold_minutes: int = Field(default=15, alias="SEAT_HOLD_MINUTES")
    price_hold_minutes: int = Field(default=15, alias="PRICE_HOLD_MINUTES")
    credit_expiry_days: int = Field(default=365, alias="CREDIT_EXPIRY_DAYS")
    max_optimistic_lock_retries: int = Field(default=3, alias="MAX_OPTIMISTIC_LOCK_RETRIES")

    cutoff_minutes_economy: int = Field(default=60, alias="CUTOFF_MINUTES_ECONOMY")
    cutoff_minutes_business: int = Field(default=30, alias="CUTOFF_MINUTES_BUSINESS")
    cutoff_minutes_first: int = Field(default=15, alias="CUTOFF_MINUTES_FIRST")

    idempotency_ttl_seconds: int = Field(default=86400, alias="IDEMPOTENCY_TTL_SECONDS")

    admin_seed_email: str = Field(default="admin@example.com", alias="ADMIN_SEED_EMAIL")
    admin_seed_password: str = Field(default="change-me", alias="ADMIN_SEED_PASSWORD")


settings = Settings()