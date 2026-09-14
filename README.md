# Flight Management System — FastAPI Backend

A flight booking backend: flight/seat-class/fare administration, public search, seat holds with
optimistic locking, bookings, waitlists, cancellations, refunds and an audit trail.

The authoritative design document is [`plan.md`](plan.md); [`remaining.md`](remaining.md) tracks
which parts of it are implemented.

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Package/env manager | [`uv`](https://docs.astral.sh/uv/) (src layout) |
| Web framework | FastAPI + uvicorn |
| ORM / driver | SQLAlchemy 2.x (async) + asyncpg |
| Migrations | Alembic |
| DB | Postgres 15+ (Supabase or local) |
| Auth | JWT (`python-jose`) + bcrypt |
| Config | `pydantic-settings` reading `.env` |
| Tests | pytest + pytest-asyncio + httpx (ASGITransport) |

## Setup

```bash
# 1. install dependencies
uv sync

# 2. create your env file
cp .env.example .env      # then edit DATABASE_URL / JWT_SECRET_KEY / ADMIN_SEED_*

# 3. run migrations (Postgres must be reachable)
uv run alembic upgrade head

# 4. seed the first super admin from ADMIN_SEED_EMAIL / ADMIN_SEED_PASSWORD
uv run python scripts/create_admin.py

# 5. run the API
uv run uvicorn fms.main:app --reload
```

Interactive API docs are available automatically at `/docs` (Swagger UI) and `/redoc`
(ReDoc) once the server is running. A `GET /health` endpoint returns `{"status": "ok"}`.

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:password@host:5432/db` |
| `JWT_SECRET_KEY`, `JWT_ALGORITHM` | token signing |
| `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` | token lifetimes |
| `SEAT_HOLD_MINUTES` | how long a seat hold lives before it can be lazily expired |
| `CREDIT_EXPIRY_DAYS` | validity of airline-caused travel credits |
| `MAX_OPTIMISTIC_LOCK_RETRIES` | seat decrement retry budget |
| `CUTOFF_MINUTES_ECONOMY/BUSINESS/FIRST` | booking cutoffs per cabin, counted back from departure |
| `IDEMPOTENCY_TTL_SECONDS` | how long an `Idempotency-Key` response is cached |
| `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD` | used only by `scripts/create_admin.py` |

Every setting is read once through `fms.core.config.Settings`; nothing reads `os.environ` directly.

## Running the tests

The suite is **Postgres-only** (CHECK constraints, native enums and JSONB are part of the design)
and each test gets a freshly created schema.

```bash
# the DB in TEST_DATABASE_URL must already exist
createdb fms_test                                       # or: docker run -d --name fms-test-db \
                                                        #   -e POSTGRES_PASSWORD=postgres \
                                                        #   -e POSTGRES_DB=fms_test -p 5432:5432 postgres:16
uv run pytest
```

`tests/conftest.py` defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/fms_test` and
deliberately ignores the developer's `.env` (set `TEST_DATABASE_URL` to override), so a test run can
never touch your real database.

## API surface

| Method | Path | Roles |
|---|---|---|
| POST | `/auth/register` | public (always creates a `customer`) |
| POST | `/auth/login` | public |
| POST | `/auth/refresh` | valid refresh token |
| GET | `/auth/me` | authenticated |
| POST | `/admin/flights` | `ops_agent`, `super_admin` |
| PATCH | `/admin/flights/{id}` | `ops_agent`, `super_admin` |
| PATCH | `/admin/flights/{id}/delay` | `ops_agent`, `super_admin` |
| PATCH | `/admin/flights/{id}/resolve-delay` | `ops_agent`, `super_admin` |
| DELETE | `/admin/flights/{id}` | `super_admin` |
| PUT | `/admin/flights/{id}/seat-map` | `ops_agent`, `super_admin` |
| PATCH | `/admin/flights/{id}/seat-classes/{cabin_class}` | `super_admin` |
| GET | `/search/flights` | public |
| POST | `/bookings/hold` | authenticated |
| POST | `/bookings/confirm` | authenticated (optional `Idempotency-Key`) |
| GET | `/bookings/{id}` | owner or admin |
| POST | `/bookings/{id}/cancel` | owner or admin |
| POST | `/bookings/{booking_id}/items/{item_id}/cancel` | owner or admin (optional `Idempotency-Key`) |
| POST | `/refunds/{id}/approve` | `super_admin` |
| POST | `/waitlist` | authenticated |
| GET | `/admin/waitlist/{flight_id}` | `ops_agent`, `super_admin` |
| POST | `/admin/waitlist/{entry_id}/promote` | `ops_agent`, `super_admin` |
| GET | `/admin/audit-logs` | `ops_agent`, `super_admin` |

Admins are never created through `/auth/register` — only through `scripts/create_admin.py`.

## How the money- and seat-critical flows work

- **Seats are never oversold.** `seat_classes` carries a `version` column. Every hold is
  `UPDATE seat_classes SET available_seats = available_seats - :n, version = version + 1
  WHERE id = :id AND version = :read_version AND available_seats >= :n`; a lost race (rowcount 0)
  is retried up to `MAX_OPTIMISTIC_LOCK_RETRIES` times before failing with `409`. Read and write
  are separate statements, so there is no long-lived row lock.
- **Seat-hold expiry is check-on-read.** There is no scheduler: `seat_service.expire_stale_holds`
  runs at the top of every code path that reads or changes availability, so an abandoned hold frees
  its seats the next time anyone looks at that cabin (or would still count if nobody ever does —
  an accepted trade-off).
- **Group and multi-leg bookings are all-or-nothing.** All holds in one request share a
  `group_key`; if any leg cannot be held, every sibling hold is released and the caller gets `409`.
- **Idempotency** is an in-process TTL cache keyed by `(user_id, Idempotency-Key)`. It is lost on
  restart and is not shared between workers — fine for this build, replace it with Redis for
  multi-instance deployments.
- **Payment and email are mocked.** `PaymentService.charge` always succeeds;
  `NotificationService` logs the message it would have sent.
- **Delay vs. schedule edit** are separate operations. A delay snapshots the true original times
  into `original_departure_datetime` / `original_arrival_datetime` exactly once (via `COALESCE`) and
  requires a `delay_reason`; `resolve_delay` returns the flight to `scheduled` but keeps the delay
  record. A schedule edit never touches the `original_*` columns.
- **Schedule-change / delay fare override.** When the shift — measured against the *original*
  time, so repeated small delays accumulate — exceeds `SCHEDULE_CHANGE_OVERRIDE_HOURS`
  (`core/policy.py`), every confirmed `booking_items` row on the flight gets
  `schedule_change_override = true`, making an otherwise non-refundable `basic` fare refundable to
  the customer.
- **Refund branching** (per the plan's Decision #6/#8):
  - customer-initiated: refund only when the fare is refundable (or the override flag is set),
    processed immediately;
  - airline-caused (`airline_cancellation`, `airline_schedule_change`): always a row — `refund`
    when refundable, otherwise a `travel_credit` expiring in `CREDIT_EXPIRY_DAYS` — created as
    `pending_approval` and released by `POST /refunds/{id}/approve` (`super_admin`).
- **Waitlist priority** is computed at read time (never stored): loyalty tier weight descending
  (`platinum=3 … none=0`, `core/policy.py`) then `created_at` ascending. Promotion calls the exact
  same `hold_seats` path as a normal booking, so n8n calling the same endpoint later cannot race
  the API.
- **Audit trail.** Every mutating service writes an `audit_logs` row (`before_state` / `after_state`)
  through `audit_service.log_audit` in the same transaction — never from a router.

## Implementation notes / known simplifications

- `PATCH /admin/flights/{id}/seat-classes/{cabin_class}` re-validates
  `SUM(seat_classes.total_seats) == flights.total_seats`; because the endpoint resizes a single
  cabin, it keeps the invariant true by writing the new sum back to `flights.total_seats` (an
  admin can never leave a flight in a non-balancing state) and rejects any resize below the number
  of already-booked seats.
- Economy is used as the fallback cutoff if an unknown cabin class is supplied.
- Waitlists do not capture a fare preference; promotion always holds the `basic` fare
  (`flexible` is not tracked on `waitlist_entries`).
- `booking_items.seat_number` is stored but never auto-assigned; `PUT /admin/flights/{id}/seat-map`
  defines the layout in `flights.seat_map` (`{seat_number: cabin_class}`) and validates it against
  cabin capacities.
- Prices are plain `numeric(10,2)` values — no currency code, no conversion (explicitly out of
  scope).
- Range/resource limits: audit-log listing is capped at 500 rows per request.

## Project layout

```
src/fms/
├── main.py                # app factory, router mounting
├── core/                  # config, database, security, dependencies, enums, policy, exceptions
├── models/                # SQLAlchemy models (users, flights, seat_classes, fares, seat_holds,
│                          #   bookings, booking_items, waitlist_entries, refunds, audit_logs)
├── schemas/               # Pydantic v2 request/response models
├── services/              # ALL business logic (routers stay thin)
└── routers/               # HTTP layer only
alembic/versions/          # migrations
scripts/create_admin.py    # first super_admin seed
tests/                     # pytest suite
```

Routers never contain business logic or direct writes: they validate input, call one service
function and return the result. Locking, retries, refund math and audit logging all live in
`services/`.
