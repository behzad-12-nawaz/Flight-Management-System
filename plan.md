# Flight Management System — FastAPI Backend Build Plan

**Scope note:** This plan covers ONLY the FastAPI-owned and "Both" (shared design) items from the
original feature list. All `[n8n]`-only items (scheduled reminders, price-drop alerts, RAG/fraud
jobs, Pinecone ingestion, waitlist auto-promotion cron, ops reporting) are explicitly OUT OF SCOPE
for this build. Where a "Both" item needs an n8n counterpart later, this plan implements the
FastAPI-side contract (DB constraints, row versioning, audit trail) so n8n can be bolted on without
schema changes.

This document is self-contained. Follow it top to bottom, in order. Do not skip the "Design
Decisions & Assumptions" section — every non-obvious business rule that was NOT explicitly
specified by the user is decided there and must be implemented exactly as written.

---

## 1. Tech Stack (final — do not substitute)

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Package/env manager | `uv` (uv's default `src/` layout) |
| Web framework | FastAPI |
| ASGI server | `uvicorn[standard]` |
| ORM | SQLAlchemy 2.x, **async** engine |
| DB driver | `asyncpg` |
| Migrations | Alembic |
| Database | Supabase Postgres (any Postgres 15+ works for local dev) |
| Auth | JWT — `python-jose` (encode/decode) + `passlib[bcrypt]` (password hashing) |
| Config | `pydantic-settings` reading a `.env` file |
| Validation | Pydantic v2 schemas for every request/response |
| Testing | `pytest` + `pytest-asyncio` + `httpx.AsyncClient` (ASGITransport) |
| Payments | Mocked — a `PaymentService` that always returns `success` instantly, no external call |
| Email | Stubbed — a `NotificationService` that logs the would-be email content, no real send |
| Idempotency store | In-process Python dict (module-level, not persisted, lost on restart) |
| Containerization | None — run locally with `uv run uvicorn ...` |
| Concurrency control on seats | Optimistic locking via an integer `version` column + bounded retry |
| Seat-hold expiry | Check-on-read (lazy expiry) — no background scheduler/APScheduler |

---

## 2. Environment Variables (`.env` / `.env.example`)

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/fms
JWT_SECRET_KEY=change-me-to-a-random-64-char-string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

SEAT_HOLD_MINUTES=15
PRICE_HOLD_MINUTES=15
CREDIT_EXPIRY_DAYS=365
MAX_OPTIMISTIC_LOCK_RETRIES=3

CUTOFF_MINUTES_ECONOMY=60
CUTOFF_MINUTES_BUSINESS=30
CUTOFF_MINUTES_FIRST=15

IDEMPOTENCY_TTL_SECONDS=86400

ADMIN_SEED_EMAIL=admin@example.com
ADMIN_SEED_PASSWORD=change-me
```

All of these are read once at startup into a `Settings` object (`core/config.py`) via
`pydantic-settings`. Nothing in the codebase reads `os.environ` directly — always go through
`Settings`.

---

## 3. Folder Structure (uv `src` layout)

```
flight-management-system/
├── pyproject.toml
├── uv.lock
├── .env.example
├── .env                      # gitignored
├── README.md
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── scripts/
│   └── create_admin.py       # seed script — creates the first super_admin
├── src/
│   └── fms/
│       ├── __init__.py
│       ├── main.py                    # FastAPI app factory, router mounting, exception handlers
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py              # Settings (pydantic-settings)
│       │   ├── database.py            # async engine, session factory, get_db dependency
│       │   ├── security.py            # password hashing, JWT encode/decode
│       │   ├── dependencies.py        # get_current_user, require_role(...), idempotency dep
│       │   ├── exceptions.py          # custom exception classes + handlers
│       │   ├── enums.py               # all shared Python Enums (UserRole, FareType, etc.)
│       │   └── policy.py              # AUTO_APPROVED_ACTIONS / MANUAL_APPROVAL_ACTIONS constants
│       ├── models/                    # SQLAlchemy ORM models, one file per table group
│       │   ├── __init__.py            # imports every model so Alembic autodiscovers them
│       │   ├── user.py
│       │   ├── flight.py              # Flight, SeatClass
│       │   ├── fare.py                # Fare
│       │   ├── booking.py             # Booking, BookingItem, SeatHold
│       │   ├── waitlist.py            # WaitlistEntry
│       │   ├── refund.py              # Refund
│       │   └── audit.py               # AuditLog
│       ├── schemas/                   # Pydantic request/response models, mirrors models/
│       │   ├── __init__.py
│       │   ├── user.py
│       │   ├── flight.py
│       │   ├── fare.py
│       │   ├── booking.py
│       │   ├── waitlist.py
│       │   ├── refund.py
│       │   └── audit.py
│       ├── services/                  # ALL business logic lives here, not in routers
│       │   ├── __init__.py
│       │   ├── auth_service.py
│       │   ├── flight_service.py
│       │   ├── search_service.py
│       │   ├── seat_service.py        # optimistic-locking decrement/release primitives
│       │   ├── booking_service.py     # hold -> confirm flow, group booking, multi-leg
│       │   ├── cancellation_service.py
│       │   ├── waitlist_service.py
│       │   ├── audit_service.py
│       │   ├── payment_service.py     # mock, always succeeds
│       │   ├── notification_service.py # stub, logs instead of sending
│       │   └── idempotency_service.py # in-memory TTL cache
│       └── routers/                   # thin HTTP layer — parse request, call service, return
│           ├── __init__.py
│           ├── auth.py
│           ├── admin_flights.py
│           ├── search.py
│           ├── bookings.py
│           ├── cancellations.py
│           ├── waitlist.py
│           ├── refunds.py
│           └── audit.py
└── tests/
    ├── conftest.py                    # test DB fixture, async client fixture, auth fixtures
    ├── test_auth.py
    ├── test_admin_flights.py
    ├── test_search.py
    ├── test_seat_holds_and_booking.py
    ├── test_cancellations.py
    ├── test_waitlist.py
    └── test_idempotency.py
```

**Rule for the implementing LLM:** routers must never contain business logic or direct DB writes.
A router function does exactly three things: (1) validate input via the Pydantic schema FastAPI
already parsed, (2) call one service function, (3) return the schema-serialized result. All
"what happens" logic (locking, retries, refund math, audit logging) lives in `services/`.

---

## 4. Enums (`core/enums.py`)

```python
class UserRole(str, Enum):
    CUSTOMER = "customer"
    OPS_AGENT = "ops_agent"
    SUPER_ADMIN = "super_admin"

class LoyaltyTier(str, Enum):
    NONE = "none"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"

class CabinClass(str, Enum):
    ECONOMY = "economy"
    BUSINESS = "business"
    FIRST = "first"

class FareType(str, Enum):
    BASIC = "basic"
    FLEXIBLE = "flexible"

class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"

class HoldStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CONVERTED = "converted"     # turned into a confirmed booking
    RELEASED = "released"       # manually released / failed booking rollback

class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    PARTIALLY_CANCELLED = "partially_cancelled"
    CANCELLED = "cancelled"

class BookingItemStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"

class WaitlistStatus(str, Enum):
    WAITING = "waiting"
    PROMOTED = "promoted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class RefundType(str, Enum):
    REFUND = "refund"
    TRAVEL_CREDIT = "travel_credit"

class RefundStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    PROCESSED = "processed"
    EXPIRED = "expired"
    REJECTED = "rejected"
```

---

## 5. Database Schema

Implement every table below as a SQLAlchemy model, then generate the Alembic migration from it
(`alembic revision --autogenerate`). Add the listed CHECK constraints by hand in the migration —
`autogenerate` will not create them for you.

### 5.1 `users`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `gen_random_uuid()` |
| email | text | unique, not null |
| hashed_password | text | not null |
| full_name | text | not null |
| role | enum `UserRole` | not null, default `customer` |
| loyalty_tier | enum `LoyaltyTier` | not null, default `none` |
| is_active | boolean | not null, default `true` |
| created_at | timestamptz | not null, default `now()` |
| updated_at | timestamptz | not null, default `now()`, on update `now()` |

### 5.2 `flights`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| flight_number | text | not null |
| origin | text | not null |
| destination | text | not null |
| departure_datetime | timestamptz | not null |
| arrival_datetime | timestamptz | not null, CHECK `arrival_datetime > departure_datetime` |
| status | enum `FlightStatus` | not null, default `scheduled` |
| total_seats | integer | not null, CHECK `total_seats > 0` |
| version | integer | not null, default `1` (optimistic lock for schedule/status/delay edits) |
| original_departure_datetime | timestamptz | nullable — set the first time a flight is delayed, so the true original schedule is never lost even across multiple delay updates |
| original_arrival_datetime | timestamptz | nullable — same purpose, for arrival |
| delay_reason | text | nullable — free-text reason (e.g. `"weather"`, `"technical"`, `"crew"`), required by the API whenever `status` is set to `delayed` |
| created_by | UUID | FK -> users.id |
| created_at | timestamptz | not null, default `now()` |
| updated_at | timestamptz | not null, default `now()` |

Unique partial index for duplicate-flight detection: unique on
`(flight_number, origin, destination, date_trunc('day', departure_datetime))`.

### 5.3 `seat_classes`
One row per `(flight, cabin_class)`.
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| flight_id | UUID | FK -> flights.id, not null |
| cabin_class | enum `CabinClass` | not null |
| total_seats | integer | not null, CHECK `total_seats >= 0` |
| available_seats | integer | not null, CHECK `available_seats >= 0` |
| overbooking_buffer | integer | not null, default `0`, CHECK `overbooking_buffer >= 0` |
| version | integer | not null, default `1` (optimistic lock for seat decrement/release) |
| unique | — | unique `(flight_id, cabin_class)` |

Invariant enforced in application code at flight-creation time: `SUM(seat_classes.total_seats)
== flights.total_seats`.

### 5.4 `fares`
One row per `(seat_class, fare_type)` — this is what defines price + change/refund policy.
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| seat_class_id | UUID | FK -> seat_classes.id, not null |
| fare_type | enum `FareType` | not null |
| price | numeric(10,2) | not null, CHECK `price > 0` |
| refundable | boolean | not null |
| change_allowed | boolean | not null |
| seat_choice_allowed | boolean | not null |
| unique | — | unique `(seat_class_id, fare_type)` |

Seed rule when a flight/seat_class is created: auto-create two `fares` rows per seat_class —
`basic` (`refundable=false, change_allowed=false, seat_choice_allowed=false`) and `flexible`
(`refundable=true, change_allowed=true, seat_choice_allowed=true`), with `flexible.price` set to
`basic.price * 1.35` (config-free simple multiplier, documented as an assumption below).

### 5.5 `seat_holds`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK -> users.id, not null |
| fare_id | UUID | FK -> fares.id, not null |
| quantity | integer | not null, CHECK `quantity > 0` |
| locked_price | numeric(10,2) | not null (price snapshotted at hold time) |
| status | enum `HoldStatus` | not null, default `active` |
| group_key | UUID | not null — links multiple `seat_holds` rows created together (multi-leg / group booking) so they expire/release/convert atomically |
| expires_at | timestamptz | not null |
| created_at | timestamptz | not null, default `now()` |

### 5.6 `bookings`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| booking_reference | text | unique, not null (e.g. 6-char alphanumeric) |
| user_id | UUID | FK -> users.id, not null |
| status | enum `BookingStatus` | not null, default `confirmed` |
| total_price | numeric(10,2) | not null |
| payment_status | text | not null, default `paid` (mock always succeeds) |
| created_at | timestamptz | not null, default `now()` |
| cancelled_at | timestamptz | nullable |

### 5.7 `booking_items`
One row per passenger-per-leg. A single-leg booking with 3 passengers = 3 rows. A 2-leg booking
for 1 passenger = 2 rows sharing the same `booking_id`.
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| booking_id | UUID | FK -> bookings.id, not null |
| flight_id | UUID | FK -> flights.id, not null |
| fare_id | UUID | FK -> fares.id, not null |
| leg_number | integer | not null, default `1` |
| passenger_name | text | not null |
| seat_number | text | nullable (only assignable if `fare.seat_choice_allowed`) |
| status | enum `BookingItemStatus` | not null, default `confirmed` |
| price_paid | numeric(10,2) | not null |

### 5.8 `waitlist_entries`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| flight_id | UUID | FK -> flights.id, not null |
| seat_class_id | UUID | FK -> seat_classes.id, not null |
| user_id | UUID | FK -> users.id, not null |
| quantity | integer | not null, CHECK `quantity > 0` |
| status | enum `WaitlistStatus` | not null, default `waiting` |
| created_at | timestamptz | not null, default `now()` (used as FCFS tiebreak) |

`priority_score` is NOT a stored column — it is computed at query time (see §7.6) so priority
always reflects the user's *current* loyalty tier.

### 5.9 `refunds`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| booking_item_id | UUID | FK -> booking_items.id, not null |
| type | enum `RefundType` | not null |
| amount | numeric(10,2) | not null |
| status | enum `RefundStatus` | not null |
| requires_approval | boolean | not null, default `false` |
| approved_by | UUID | FK -> users.id, nullable |
| expires_at | timestamptz | nullable (only set for `travel_credit`) |
| created_at | timestamptz | not null, default `now()` |

### 5.10 `audit_logs`
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| actor_id | UUID | FK -> users.id, nullable (nullable = system action) |
| action | text | not null (e.g. `"flight.create"`, `"booking.cancel"`) |
| entity_type | text | not null |
| entity_id | UUID | not null |
| before_state | JSONB | nullable |
| after_state | JSONB | nullable |
| created_at | timestamptz | not null, default `now()` |

---

## 6. Design Decisions & Assumptions (explicitly flagged — not specified by the user)

These are business-rule gaps in the original feature list that had to be resolved to make the
system buildable. Implement them exactly as written; they are not guesses, they are the spec.

1. **Group booking (N seats, some unavailable):** ALL-OR-NOTHING. If the full requested quantity
   cannot be held across all seat classes/legs in the request, the entire hold request fails with
   `409 Conflict` and nothing is held. No partial holds.
2. **Multi-leg itinerary:** Also ALL-OR-NOTHING, for the same reason and via the same code path
   (`group_key` on `seat_holds`). If any leg's fare becomes unavailable between the initial search
   and the hold request, the whole multi-leg hold fails.
3. **Waitlist priority:** combined rule — primary sort key is `loyalty_tier` weight
   (`platinum=3, gold=2, silver=1, none=0`, descending), secondary sort key is `created_at`
   ascending (first-come-first-served within the same tier). No fare-class weighting.
4. **Flexible fare price:** `flexible.price = basic.price * 1.35`. This multiplier is a constant
   in `core/policy.py`, not hardcoded inline, so it's trivial to change later.
5. **Schedule-change fare override:** if an admin edits a flight's `departure_datetime` by more
   than 3 hours (constant `SCHEDULE_CHANGE_OVERRIDE_HOURS` in `core/policy.py`), every existing
   `basic` fare booking on that flight is treated as `change_allowed=true` / `refundable=true`
   for cancellations made within 14 days of the change — implemented as a flag
   (`schedule_change_override=true`) stored on the affected `booking_items` rows, not a schema
   change to `fares` (fares stay generic; the override is booking-specific).
6. **Flight cancellation by admin:** for every affected `booking_item`, create a `refund` row
   whose `type` is `travel_credit` if the fare was non-refundable, or `refund` if refundable —
   this is airline-caused, so even `basic` (non-refundable) fares get a `travel_credit` rather
   than nothing. `travel_credit.expires_at = now() + CREDIT_EXPIRY_DAYS`.
7. **Overbooking:** hard no-oversell by default — every `seat_classes.overbooking_buffer` starts
   at `0` when a flight is created. Admins can raise it per class later via the seat-allocation
   edit endpoint; this is what makes the "explicit overbooking policy per class" configurable
   without a separate table.
8. **Approval gate:** `refunds.requires_approval = true` automatically when `type=refund AND
   amount > 0` for a schedule-change or flight-cancellation compensation case (i.e. airline-caused,
   not customer-initiated); customer-initiated cancellations under normal fare rules are
   auto-approved. The exact lists live in `core/policy.py` as `AUTO_APPROVED_ACTIONS` /
   `MANUAL_APPROVAL_ACTIONS` string constants and are checked by name in
   `cancellation_service.py`.
9. **Admin role split:** `ops_agent` can create flights, edit schedule, view audit logs. Only
   `super_admin` can cancel a flight, shrink/adjust seat allocations after bookings exist, approve
   pending refunds, and create other admin accounts.
10. **Waitlist promotion:** the actual scheduled promotion job is `n8n`'s job and is NOT built
    here. This backend exposes `POST /admin/waitlist/{entry_id}/promote` so it can be triggered
    manually (by an admin, or later by n8n calling this same endpoint) — it reuses the identical
    optimistic-locking seat-decrement path as a normal booking, so there is no separate "n8n vs
    FastAPI" race: both would call the same versioned UPDATE.
11. **Delay vs. schedule edit — they are two different operations, not the same endpoint:**
    `edit_flight_schedule` (§7.3) is for an admin *correcting or replanning* a flight (e.g. fixing
    a data-entry mistake, permanently moving a route to a new time). `mark_flight_delayed` (new,
    §7.3) is for an *operational disruption to an otherwise-correct schedule* — the flight still
    intends to run at its original time, but currently won't. They are kept distinct because they
    mean different things to a passenger and to the fare-override rule:
    - A delay ALWAYS records the very first pre-delay `departure_datetime`/`arrival_datetime`
      into `original_departure_datetime`/`original_arrival_datetime` (only set once — a second
      delay on an already-delayed flight does not overwrite the true original). A schedule edit
      does not touch these columns at all.
    - A delay requires a `delay_reason`; a schedule edit does not.
    - Both delay and schedule-change reuse the exact same fare-override trigger from Decision #5
      (`SCHEDULE_CHANGE_OVERRIDE_HOURS` compared against the *original* time, not the
      previous-delay time, so repeated small delays that add up past the threshold still correctly
      trigger the override).
    - A delayed flight can be resolved back to `scheduled` (see `resolve_delay` in §7.3) once the
      disruption is over and the current `departure_datetime`/`arrival_datetime` are confirmed
      final — this does NOT restore the original times; those stay recorded for audit purposes.

---

## 7. Business Logic — Detailed Per-Service Spec

### 7.1 `security.py` / `auth_service.py`
- `hash_password(plain) -> str` via `passlib.CryptContext(schemes=["bcrypt"])`.
- `verify_password(plain, hashed) -> bool`.
- `create_access_token(user_id, role) -> str`, expiry = `ACCESS_TOKEN_EXPIRE_MINUTES`.
- `create_refresh_token(user_id) -> str`, expiry = `REFRESH_TOKEN_EXPIRE_DAYS`.
- `decode_token(token) -> dict`, raises `401` on expiry/invalid signature.
- `POST /auth/register`: customer self-registration only (`role` is always forced to `customer`
  server-side, ignore any role field in the request body).
- `POST /auth/login`: verify password, return `{access_token, refresh_token, token_type: "bearer"}`.
- `POST /auth/refresh`: exchange a valid refresh token for a new access token.
- `GET /auth/me`: return current user from the JWT.
- Admins are NEVER created via `/auth/register`. Only via `scripts/create_admin.py`.

### 7.2 `core/dependencies.py`
- `get_current_user(token: str = Depends(oauth2_scheme), db) -> User`: decode JWT, load user,
  `401` if not found/inactive.
- `require_role(*roles: UserRole)`: returns a dependency that `403`s if
  `current_user.role not in roles`.
- `get_idempotency_key(idempotency_key: str | None = Header(default=None))`.

### 7.3 `flight_service.py` (admin & flight management)
- `create_flight(payload, actor)`:
  1. Validate `sum(seat_class.total_seats for seat_class in payload.seat_classes) ==
     payload.total_seats`. `422` if not.
  2. Validate every seat count is a positive integer (Pydantic `conint(gt=0)` handles this at the
     schema level already — reject at parse time, not in the service).
  3. Check duplicate: query for an existing flight with same `flight_number`, `origin`,
     `destination`, same calendar day of `departure_datetime`. `409` if found.
  4. Insert `flights` row, then one `seat_classes` row per class with `available_seats =
     total_seats`, then auto-create `basic`/`flexible` `fares` rows per §5.4.
  5. Write an `audit_logs` row (`action="flight.create"`).
- `edit_flight_schedule(flight_id, payload, actor)`:
  1. Load flight with `SELECT ... FOR UPDATE` is NOT needed here (schedule edits are rare/admin
     serialized) — use the `version` column: `UPDATE flights SET ..., version = version + 1
     WHERE id = :id AND version = :expected_version`. `409` if `rowcount == 0` (someone else
     edited concurrently) — the caller should re-fetch and retry once at the router level.
  2. If `abs(new_departure - old_departure) > SCHEDULE_CHANGE_OVERRIDE_HOURS`, apply the
     `schedule_change_override` flag to every confirmed `booking_item` on this flight (see
     Decision #5).
  3. Audit log (`action="flight.schedule_change"`, before/after state = old vs new datetimes).
- `mark_flight_delayed(flight_id, new_departure, new_arrival, delay_reason, actor)` —
  `ops_agent` or `super_admin`:
  1. Load the flight with its current `version`.
  2. If `flights.status != delayed` (i.e. this is the *first* delay applied to this flight), copy
     the current `departure_datetime`/`arrival_datetime` into `original_departure_datetime`/
     `original_arrival_datetime` before overwriting them. If the flight is already `delayed`
     (a second/third delay update), leave `original_*` untouched — only the display departure/
     arrival and `delay_reason` change.
  3. `UPDATE flights SET status = 'delayed', departure_datetime = :new_departure,
     arrival_datetime = :new_arrival, delay_reason = :reason, original_departure_datetime =
     COALESCE(original_departure_datetime, :old_departure), original_arrival_datetime =
     COALESCE(original_arrival_datetime, :old_arrival), version = version + 1 WHERE id = :id AND
     version = :expected_version`. `409` and ask the caller to retry once if `rowcount == 0`.
  4. Compute the cumulative delay against the *original* time (not the previous delayed time):
     `delay_hours = (new_departure - original_departure_datetime_or_old_departure_if_first_delay)
     in hours`. If `delay_hours > SCHEDULE_CHANGE_OVERRIDE_HOURS`, apply the
     `schedule_change_override` flag to every confirmed `booking_item` on this flight — this is
     the same flag and threshold from Decision #5, reused here so a passenger gets the same
     free-change/refund protection whether the disruption was framed as a "schedule change" or a
     "delay".
  5. Call `notification_service.send_delay_notice(...)` (stub/log only) for every affected
     confirmed `booking_item`'s passenger.
  6. Audit log (`action="flight.delay"`, before/after state includes `delay_reason` and both old
     and new departure/arrival times).
- `resolve_delay(flight_id, actor)` — `ops_agent` or `super_admin`:
  1. Requires `flights.status == delayed`, else `422` ("flight is not currently delayed").
  2. `UPDATE flights SET status = 'scheduled', version = version + 1 WHERE id = :id AND version =
     :expected_version` — `409`/retry-once on conflict, same pattern as above.
  3. Does NOT clear `delay_reason` or `original_departure_datetime`/`original_arrival_datetime` —
     those remain on the row as a permanent record that this flight was once delayed, for audit
     and reporting purposes.
  4. Audit log (`action="flight.delay_resolved"`).
- `cancel_flight(flight_id, actor)` — `super_admin` only:
  1. Set `flights.status = cancelled`.
  2. For every `booking_item` with `status=confirmed` on this flight: call
     `cancellation_service.cancel_item(item, reason="airline_cancellation")` (see §7.5, applies
     Decision #6).
  3. Audit log.
- `adjust_seat_allocation(seat_class_id, new_total, actor)` — `super_admin` only:
  1. Compute `booked = seat_class.total_seats - seat_class.available_seats`.
  2. `422` if `new_total < booked` (cannot shrink below already-booked count).
  3. `UPDATE seat_classes SET total_seats = :new_total, available_seats = available_seats +
     (:new_total - old_total), version = version + 1 WHERE id = :id AND version = :expected`.
  4. Re-validate `SUM(seat_classes.total_seats) == flights.total_seats` after the change; `422`
     and roll back if it no longer balances (admin must adjust another class too — return a clear
     error naming the mismatch).
  5. Audit log.
- `define_seat_map(flight_id, payload, actor)`: stores a JSON layout (`{seat_number: cabin_class}`)
  — simplest correct implementation is a `seat_map` JSONB column added to `flights` (add this
  column to the schema in §5.2 if the implementing LLM wants seat-number-level assignment;
  otherwise `booking_items.seat_number` stays free-text and uniqueness per flight is enforced at
  the service layer by checking no other confirmed `booking_item` on the same `flight_id` already
  has that `seat_number`).

### 7.4 `search_service.py`
- `GET /search/flights?origin&destination&date`: join `flights` + `seat_classes` + `fares`, filter
  `status=scheduled`, `origin`, `destination`, `date_trunc('day', departure_datetime) = :date`.
  Return per flight: flight info + list of `{cabin_class, available_seats, fares: [{fare_type,
  price, refundable, change_allowed, seat_choice_allowed}]}`.
- Currency/locale handling: explicitly OUT OF SCOPE — all prices are plain `numeric(10,2)`, no
  currency code stored, no conversion. Document this in the OpenAPI description of `price` fields.

### 7.5 `seat_service.py` (the atomic core)
- `hold_seats(fare_id, quantity, user_id, group_key) -> SeatHold`:
  1. `SELECT` the `seat_classes` row for this fare's `seat_class_id` (no `FOR UPDATE` — we use
     optimistic locking, not pessimistic).
  2. `allowed = seat_class.total_seats + seat_class.overbooking_buffer`. Compute currently-held
     active (non-expired) holds for this seat_class (lazy-expire any stale ones first — see
     `expire_stale_holds` below) and confirmed bookings to get a true `available_seats`.
  3. If `available_seats < quantity`: return failure (caller aggregates all leg failures and
     aborts the whole group per Decision #1/#2).
  4. `UPDATE seat_classes SET available_seats = available_seats - :quantity, version = version + 1
     WHERE id = :id AND version = :read_version AND available_seats >= :quantity`. If
     `rowcount == 0`, retry from step 1 up to `MAX_OPTIMISTIC_LOCK_RETRIES` times, then fail.
  5. Insert `seat_holds` row (`status=active`, `expires_at = now() + SEAT_HOLD_MINUTES`,
     `locked_price` = current fare price, same `group_key` as sibling legs in this request).
- `expire_stale_holds(seat_class_id)` (check-on-read, called at the top of every function that
  reads seat availability):
  1. `SELECT` all `seat_holds` with `status=active AND expires_at < now()` for this seat_class.
  2. For each, release its seats back (`available_seats += quantity, version += 1`, same
     optimistic-retry pattern) and set `status = expired`.
  3. This is the ONLY expiry mechanism — there is no cron/APScheduler. A hold that nobody ever
     reads again (e.g. abandoned checkout with zero subsequent traffic) stays "active" in the row
     but is functionally invisible because every future availability check re-runs this function
     first. This is an accepted tradeoff, not a bug — document it in the README.
- `release_hold(hold_id)`: manual release (e.g. user cancels checkout) — same seat-restoration
  logic, `status = released`.
- `convert_hold_to_booking(hold_ids: list[UUID])`: called by `booking_service.confirm_booking`
  after payment mock succeeds — sets `status = converted` on all holds sharing the `group_key`,
  does NOT touch `available_seats` again (it was already decremented at hold time).

### 7.6 `booking_service.py`
- `POST /bookings/hold` (`hold_booking`):
  1. Enforce cutoff: for each requested leg, reject with `422` if
     `now() > flight.departure_datetime - CUTOFF_MINUTES_<CABIN_CLASS>`.
  2. Generate one `group_key = uuid4()` for the whole request.
  3. For every (leg, passenger) combination, call `seat_service.hold_seats(...)`. If ANY call
     fails, immediately call `seat_service.release_hold(...)` on every hold already created in
     this request (rollback), and return `409` (Decision #1/#2, all-or-nothing).
  4. Return the list of created `seat_holds` + `expires_at` + total locked price.
- `POST /bookings/confirm` (`confirm_booking`):
  1. Requires `Idempotency-Key` header — check `idempotency_service` first; if a cached response
     exists for this `(user_id, key)`, return it unchanged without re-running anything.
  2. Load all `seat_holds` for `group_key`, verify all `status=active` and none `expires_at <
     now()` (re-run `expire_stale_holds` first — if any expired, `409 "hold expired, please
     search again"`).
  3. Call `payment_service.charge(amount=sum(locked_price*quantity))` — mock, always returns
     success synchronously.
  4. Create `bookings` row (`booking_reference` = random 6-char alphanumeric, unique-checked),
     one `booking_items` row per hold (respecting `quantity` — one row per seat/passenger; the
     request body must supply passenger names matching total quantity).
  5. Call `seat_service.convert_hold_to_booking(...)`.
  6. Call `notification_service.send_booking_confirmation(...)` (stub/log only).
  7. Audit log (`action="booking.confirm"`).
  8. Store the full response in `idempotency_service` before returning.
- Group booking / multi-leg are the same code path: the request schema for `hold_booking` accepts
  a list of `{flight_id, cabin_class, fare_type, passengers: [names]}` items — one item per leg —
  and quantity is `len(passengers)` per item.

### 7.7 `cancellation_service.py`
- `cancel_booking_item(item_id, actor, reason)`:
  1. Load `booking_items` + its `fares` row.
  2. Release the seat: `seat_classes.available_seats += 1, version += 1` (same optimistic path as
     §7.5 — release, don't hold).
  3. Set `booking_items.status = cancelled`.
  4. Determine refund per fare + reason (Decision #6 for airline-caused reasons):
     - `reason="customer"`: `refundable=true` -> `refund` (full `price_paid`, auto-approved,
       `status=processed` immediately since payment is mocked); `refundable=false` -> no refund
       row at all (non-refundable, customer-initiated).
     - `reason="airline_cancellation"` or `"airline_schedule_change"`: always create a `refund`
       row — `type=refund` if `fare.refundable` else `type=travel_credit`
       (`expires_at = now() + CREDIT_EXPIRY_DAYS`); `requires_approval=true` per Decision #8,
       `status=pending_approval` until a `super_admin` calls `POST /refunds/{id}/approve`.
  5. Recompute parent `bookings.status`: `cancelled` if all items cancelled, else
     `partially_cancelled`.
  6. Audit log.
- `cancel_full_booking(booking_id, actor)`: loops `cancel_booking_item` for every item, reason
  `"customer"` — this is what `POST /bookings/{id}/cancel` calls. Proportional refund falls out
  naturally because each item's refund is computed independently from its own `price_paid`.
- `POST /refunds/{id}/approve` (`super_admin` only): `status: pending_approval -> processed`.

### 7.8 `waitlist_service.py`
- `POST /waitlist` (`join_waitlist`): only allowed when `seat_service` reports
  `available_seats < requested_quantity` for the target seat_class (otherwise tell the caller to
  book normally instead). Insert `waitlist_entries`.
- `GET /admin/waitlist/{flight_id}` (`list_waitlist`): fetch all `status=waiting` entries for a
  flight, sort in Python (not SQL) by the combined priority rule (Decision #3):
  `sorted(entries, key=lambda e: (-TIER_WEIGHT[e.user.loyalty_tier], e.created_at))`.
- `POST /admin/waitlist/{entry_id}/promote` (`super_admin`, `ops_agent`):
  1. Call `seat_service.hold_seats(...)` for the entry's `seat_class_id` at `entry.quantity`
     (basic fare by default, since waitlist doesn't capture fare preference in this design —
     document this as a known simplification).
  2. If it succeeds: mark `waitlist_entries.status = promoted`, return the new hold to the caller
     (the user still has to `confirm_booking` within `SEAT_HOLD_MINUTES`, same as a normal flow).
  3. If it fails (seats gone again): leave entry as `waiting`, return `409`.

### 7.9 `idempotency_service.py`
```python
# module-level, process-lifetime only, per Decision: in-memory, not persisted
_store: dict[tuple[str, str], tuple[float, dict]] = {}  # (user_id, key) -> (expires_at, response)

def get(user_id: str, key: str) -> dict | None: ...
def set(user_id: str, key: str, response: dict, ttl_seconds: int) -> None: ...
```
Called only by endpoints that accept an `Idempotency-Key` header (`confirm_booking`,
`cancel_booking_item`, `promote` — any endpoint that mutates money/seats and could be retried by a
flaky client). GET endpoints never use this.

### 7.10 `audit_service.py`
`log(db, actor_id, action, entity_type, entity_id, before, after)` — a single function called
inline (same DB transaction, same commit) by every other service after a mutating operation.
Never call this from a router directly.

---

## 8. API Endpoint Summary

| Method | Path | Roles | Purpose |
|---|---|---|---|
| POST | `/auth/register` | public | customer signup |
| POST | `/auth/login` | public | get access+refresh tokens |
| POST | `/auth/refresh` | public (valid refresh token) | rotate access token |
| GET | `/auth/me` | any authenticated | current user info |
| POST | `/admin/flights` | ops_agent, super_admin | create flight + seat classes + fares |
| PATCH | `/admin/flights/{id}` | ops_agent, super_admin | edit schedule (permanent replan) |
| PATCH | `/admin/flights/{id}/delay` | ops_agent, super_admin | mark flight delayed with new times + reason |
| PATCH | `/admin/flights/{id}/resolve-delay` | ops_agent, super_admin | clear delayed status back to scheduled |
| DELETE | `/admin/flights/{id}` | super_admin | cancel flight entirely |
| PUT | `/admin/flights/{id}/seat-map` | ops_agent, super_admin | define seat layout |
| PATCH | `/admin/flights/{id}/seat-classes/{class}` | super_admin | adjust seat allocation |
| GET | `/search/flights` | public | search available flights/fares |
| POST | `/bookings/hold` | customer | hold seats (single/multi-leg/group) |
| POST | `/bookings/confirm` | customer | pay (mock) + confirm booking |
| GET | `/bookings/{id}` | owner or admin | booking detail |
| POST | `/bookings/{id}/cancel` | owner or admin | cancel full booking |
| POST | `/bookings/{booking_id}/items/{item_id}/cancel` | owner or admin | partial cancellation |
| POST | `/refunds/{id}/approve` | super_admin | approve a pending-approval refund |
| POST | `/waitlist` | customer | join waitlist |
| GET | `/admin/waitlist/{flight_id}` | ops_agent, super_admin | view priority-sorted waitlist |
| POST | `/admin/waitlist/{entry_id}/promote` | ops_agent, super_admin | manually promote (also the hook n8n will call later) |
| GET | `/admin/audit-logs` | ops_agent, super_admin | view audit trail |

---

## 9. Testing Plan

Write tests domain-by-domain, matching the build order in §10. Use a real Postgres test database
(not SQLite — Postgres-specific features like `CHECK` constraints and enums are part of the
spec) with a dedicated `fms_test` DB, torn down/recreated per test session via a `conftest.py`
fixture that runs Alembic migrations once, and wraps each test in a transaction that's rolled
back afterward.

Minimum required test cases (do not skip these — they cover the edge cases the original feature
list called out explicitly):
- Seat totals must sum to capacity; reject if not.
- Reject negative/zero/non-integer seat counts.
- Duplicate flight number same day/route is rejected.
- Cannot shrink seat allocation below already-booked count.
- Two concurrent `hold_seats` calls for the last seat — exactly one succeeds (simulate with
  `asyncio.gather` and assert one `409`).
- Idempotency: calling `confirm_booking` twice with the same key returns the identical response
  and does not double-charge or double-decrement seats.
- Group booking where only some seats are available fails entirely, and any already-created holds
  in that request are released (available_seats returns to its original value).
- Hold expiry: create a hold with a past `expires_at` directly in the test DB, then call any
  availability-reading endpoint, and assert the seat is available again (proves check-on-read
  expiry works with no scheduler).
- Cancellation refund branching: basic fare (customer-cancelled) gets no refund; flexible fare
  gets full refund; airline-cancelled basic fare gets a travel credit, not nothing.
- Partial cancellation on a 3-passenger booking refunds only the cancelled passenger's share.
- Waitlist ordering: platinum joined later beats silver joined earlier.
- Role enforcement: `ops_agent` gets `403` on flight cancellation and seat-allocation shrink;
  `super_admin` succeeds.
- Delay: marking a flight delayed for the first time correctly stores
  `original_departure_datetime`/`original_arrival_datetime`; a second delay on the same flight
  does not overwrite those original values.
- Delay fare override: a cumulative delay past `SCHEDULE_CHANGE_OVERRIDE_HOURS` (even if applied
  across two separate smaller delay updates) sets `schedule_change_override=true` on confirmed
  booking items; a delay under the threshold does not.
- `resolve_delay` fails with `422` on a flight that isn't currently `delayed`, and succeeds
  (returns `status=scheduled`) without clearing `delay_reason`/`original_departure_datetime` on
  one that is.

---

## 10. Build Order (milestones — build and test in this sequence)

1. **Scaffold**: `uv init`, add dependencies, `core/config.py`, `core/database.py`, empty
   `main.py` that boots and returns `{"status": "ok"}` on `GET /health`.
2. **Alembic wired up**, `users` model + migration, `core/security.py`, `auth_service.py`,
   `routers/auth.py`, `core/dependencies.py` (role guards). Write `tests/test_auth.py`.
3. `scripts/create_admin.py` (seed a `super_admin` from `ADMIN_SEED_EMAIL`/`ADMIN_SEED_PASSWORD`).
4. `flights`, `seat_classes`, `fares` models + migration, `flight_service.py` (including
   `mark_flight_delayed`/`resolve_delay`), `routers/admin_flights.py`, `audit_service.py` wired
   in. Write `tests/test_admin_flights.py` (include the delay test cases from §9).
5. `search_service.py`, `routers/search.py`. Write `tests/test_search.py`.
6. `seat_holds`, `bookings`, `booking_items` models + migration, `seat_service.py` (the
   optimistic-locking core — build and test this in isolation before wiring it to HTTP),
   `payment_service.py` (mock), `idempotency_service.py`, `booking_service.py`,
   `routers/bookings.py`. Write `tests/test_seat_holds_and_booking.py` and
   `tests/test_idempotency.py` — these are the highest-risk files, do not rush them.
7. `refunds` model + migration, `cancellation_service.py`, `routers/cancellations.py`,
   `routers/refunds.py`. Write `tests/test_cancellations.py`.
8. `waitlist_entries` model + migration, `waitlist_service.py`, `routers/waitlist.py`. Write
   `tests/test_waitlist.py`.
9. `notification_service.py` (stub), wire it into booking confirmation and cancellation.
10. `core/policy.py` constants (auto vs manual approval action lists, schedule-change override
    hours, flexible-fare multiplier) — sweep back through steps 4–8 and replace any inline
    "magic numbers" with references to this file.
11. Final pass: `README.md` with setup/run instructions, confirm `.env.example` is complete, run
    the full test suite, confirm every endpoint in §8 exists and is role-guarded correctly.

---

## 11. Run Instructions (put these in README.md too)

```bash
# install deps
uv sync

# start Postgres locally (or point DATABASE_URL at Supabase)
# run migrations
uv run alembic upgrade head

# seed the first admin
uv run python scripts/create_admin.py

# run the API
uv run uvicorn fms.main:app --reload

# run tests
uv run pytest
```

Interactive API docs are available automatically at `/docs` (Swagger UI) and `/redoc` once the
server is running — no extra work needed, this is FastAPI's default.
