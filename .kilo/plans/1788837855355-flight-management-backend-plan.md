# Flight Management System — Backend Implementation Plan

## 1. Project Structure

Organize the codebase into a standard FastAPI project layout: application code lives under a `src/flight_management/` package, database migrations live under an `alembic/` directory, tests live under a top-level `tests/` directory, and an `.env.example` file documents required environment variables.

Under `src/flight_management/`:
- `main.py` — application entry point that creates the FastAPI app, conditionally enables docs, includes routers, and registers exception handlers.
- `config.py` — loads settings from environment variables using Pydantic Settings.
- `database.py` — creates the async SQLAlchemy engine and session factory, and provides a `get_db` dependency for injecting database sessions into routes.
- `middleware/auth.py` — extracts the JWT from the request, verifies it using the secret from `.env`, fetches the user profile from the database, and attaches the current user to the request state.
- `middleware/role_check.py` — provides a dependency that checks whether the current user's role is in an allowed set.
- `models/sqlalchemy/user.py` — defines the SQLAlchemy ORM model for the `profiles` table.
- `models/sqlalchemy/flight.py` — defines the SQLAlchemy ORM model for the `flights` table, including the flight status enum.
- `models/user.py` — defines Pydantic schemas for request/response validation of user data.
- `models/flight.py` — defines Pydantic schemas for request/response validation of flight data.
- `models/common.py` — defines shared schemas such as paginated responses and error response formats.
- `routers/auth.py` — endpoints for login, logout, forgot-password, and reset-password. These proxy to the Supabase Auth service.
- `routers/users.py` — CRUD endpoints for managing user accounts and roles. Restricted to super_admin.
- `routers/flights.py` — CRUD endpoints plus action endpoints for flight status transitions. Access varies by role.
- `services/auth_service.py` — helpers for interacting with Supabase Auth (login, logout, password reset).
- `services/user_service.py` — business logic for user CRUD operations using SQLAlchemy async sessions.
- `services/flight_service.py` — business logic for flight CRUD and status transitions using SQLAlchemy async sessions, including strict validation rules.
- `utils/validators.py` — reusable validation logic for flight fields (e.g., time range checks, seat constraints).
- `utils/pagination.py` — helper functions for building cursor-based pagination queries and responses.
- `exceptions.py` — custom exception classes and a handler that formats errors into the standard JSON structure.

The `alembic/` directory contains the Alembic environment configuration, a migration script template, and a `versions/` directory for storing generated migration files.

The `tests/` directory contains test fixtures in `conftest.py` (which sets up an async database session and mocks Supabase HTTP calls), plus test modules for auth, users, and flights.

## 2. Dependencies

Runtime dependencies: FastAPI with standard extras, Supabase async client, SQLAlchemy with asyncio extra, Alembic, asyncpg, PyJWT, python-jose, Pydantic, Pydantic Settings, python-multipart, httpx, pytest, pytest-asyncio, and respx.

Development dependencies: ruff for linting and mypy for type checking.

## 3. Database Schema & Migrations

Use Alembic for schema migrations and SQLAlchemy ORM for all data access from the FastAPI application.

### 3.1 Environment Variables

All secrets and configuration must live in `.env`. Never hardcode credentials. Required variables include: Supabase URL, Supabase anonymous key, Supabase service role key, a PostgreSQL async connection string pointing to the Supabase database, a JWT secret key used for verifying tokens issued by Supabase Auth, the JWT algorithm (default HS256), the API prefix (default `/api/v1`), and a flag to enable or disable interactive API documentation.

Also maintain a `.env.example` file in the repository that lists all required keys without actual secret values.

### 3.2 SQLAlchemy ORM Models

The `profiles` table is represented by a SQLAlchemy model with a UUID primary key that references the Supabase Auth `users` table, a unique email string field, a role string field constrained to the values `super_admin`, `operation_agent`, or `viewer`, and timestamp fields for record creation and update times.

The `flights` table is represented by a SQLAlchemy model with a UUID primary key, a unique indexed flight number string, origin and destination strings, departure and arrival timestamps with timezone support, an aircraft type string, total and available seat counts as integers, a price as a fixed-precision numeric field, a status field backed by a Python enum with values `scheduled`, `delayed`, `cancelled`, and `completed`, optional string fields for cancellation and delay reasons, foreign key references to the Supabase Auth `users` table for `created_by` and `updated_by`, and timestamp fields for creation and update times.

### 3.3 Alembic Setup

Initialize Alembic in the project root. Configure the Alembic environment to read the database URL from the `DATABASE_URL` environment variable, point the migration target metadata to the SQLAlchemy models defined in the project, and use async engine support so that both offline and online migration runs use the async engine and connection types.

### 3.4 Initial Migration

Generate the initial migration from the SQLAlchemy models. The resulting migration should create the `profiles` and `flights` tables with all columns, indexes on `flights.flight_number` and `profiles.email`, and foreign key constraints from `flights.created_by` and `flights.updated_by` to the Supabase Auth `users` table.

### 3.5 Supabase Auth Setup (one-time)

- Disable public sign-up in Supabase Dashboard (`Authentication → Providers → Email → Enable sign-up` should be OFF)
- Ensure Email auth provider is enabled for login/password-reset only
- Copy the Supabase Auth JWT secret from the API settings into `.env` as the JWT secret key

Note: FastAPI middleware is the sole authorization layer. No database-level row security policies are needed.

## 4. FastAPI Application Configuration

### 4.1 Tech Stack Decisions

- All endpoints use async function definitions. The SQLAlchemy database engine and the Supabase Auth client both operate asynchronously.
- SQLAlchemy 2.0 with the asyncpg driver is used for all application-level database operations.
- Alembic manages schema versioning, with the initial migration auto-generated from the ORM models.
- Swagger UI and ReDoc are enabled in development and conditionally disabled in production via an environment variable.
- Testing uses pytest with pytest-asyncio and httpx's async client for endpoint testing, with respx used to mock Supabase HTTP calls.

### 4.2 Settings

Configuration is loaded from environment variables using Pydantic Settings. Required settings include the Supabase URL, Supabase anonymous key, Supabase service role key, the async database connection string, the JWT secret key, the JWT algorithm (default HS256), the API prefix (default `/api/v1`), and a boolean flag to enable API documentation.

### 4.3 Database Engine

Create the async SQLAlchemy engine using the database URL from configuration, with connection pool pre-pinging enabled. Create an async session factory bound to that engine, configured with expired-on-commit disabled so that ORM objects remain accessible after a session commits. Provide a `get_db` dependency that opens an async session, yields it to the route handler, and commits the transaction on success or rolls back on failure.

The Alembic environment configuration must reuse the same database URL and SQLAlchemy metadata so that migrations are consistent with the application models.

### 4.4 Auth Middleware

The authentication middleware extracts the JWT from the Authorization header, verifies its signature using the JWT secret key from `.env` (not via an external JWKS endpoint), fetches the corresponding user profile from the database using an async SQLAlchemy session, and attaches the user's ID, email, and role to the request state. If the token is missing or invalid, the middleware returns a 401 response. If the user profile is not found, it returns a 403 response.

### 4.5 Role Check Dependency

Provide a reusable dependency that wraps the current user dependency and checks whether the user's role is in an allowed set. If the role is not allowed, it returns a 403 response. This allows route declarations to simply specify which roles are permitted.

### 4.6 Main App

The main application module reads the documentation enable flag from the environment. It creates the FastAPI app instance, conditionally enabling the `/docs` and `/redoc` routes only when the flag is set. It includes all routers under the configured API prefix and registers global exception handlers that format errors into the standard JSON structure.

## 5. API Endpoints

### 5.1 Permission Matrix

Every endpoint must enforce authentication and role checks. No endpoint is public except the auth password-reset helpers.

| Endpoint | super_admin | operation_agent | viewer |
|----------|:---:|:---:|:---:|
| POST /auth/login | allowed | allowed | allowed |
| POST /auth/logout | allowed | allowed | allowed |
| POST /auth/forgot-password | allowed | allowed | allowed |
| POST /auth/reset-password | allowed | allowed | allowed |
| GET /users | allowed | denied | denied |
| POST /users | allowed | denied | denied |
| GET /users/{id} | allowed | denied | denied |
| PUT /users/{id} | allowed | denied | denied |
| DELETE /users/{id} | allowed | denied | denied |
| GET /flights | allowed | allowed | allowed |
| GET /flights/{id} | allowed | allowed | allowed |
| POST /flights | allowed | allowed | denied |
| PUT /flights/{id} | allowed | allowed with restriction | denied |
| POST /flights/{id}/complete | allowed | allowed | denied |
| POST /flights/{id}/delay | allowed | denied | denied |
| POST /flights/{id}/cancel | allowed | denied | denied |

Note: operation_agent can update flights only if the new status is not `cancelled` or `delayed`.

### 5.1.1 Auth Middleware Rules

- The `get_current_user` dependency extracts the JWT, verifies its signature with the configured secret key, fetches the user profile from the database, and returns the user object. It returns a 401 response if the token is missing or invalid.
- The `require_role` dependency wraps `get_current_user` and checks whether the current user's role is in the allowed set. It returns a 403 response if the role does not match.
- Every router must use one of these dependencies on every route. No route may be left unprotected.
- Auth routes for login, forgot-password, and reset-password are public. All other routes require at least `get_current_user`.

### 5.2 Auth Router

All auth endpoints proxy to the Supabase Auth service using the async client. These endpoints are **public** — they do not require authentication and do not check user roles. Public sign-up is disabled in Supabase, so these endpoints are for existing users only. The login endpoint accepts email and password, calls the Supabase sign-in method, and returns the JWT to the client. The logout endpoint calls the Supabase sign-out method. The forgot-password endpoint accepts an email address and triggers Supabase to send a password reset email. The reset-password endpoint accepts a new password and updates the user's credentials via Supabase. These endpoints exist to support a future frontend login flow with forgot-password capability.

### 5.3 Users Router

This router is the **only** way to create new user profiles. Creating a user accepts an email address and a role, creates the user via Supabase Auth, and inserts the corresponding profile record. Listing users returns a cursor-paginated response. Retrieving a user by ID returns their profile details. Updating a user allows changing their role. Deleting a user removes their profile and their Supabase Auth account.

All endpoints in this router require the `super_admin` role.

### 5.4 Flights Router

This router provides CRUD operations for flights, plus dedicated action endpoints for status transitions. Listing flights returns a cursor-paginated response. Retrieving a flight by ID returns its details. Creating a flight accepts all flight fields, validates them strictly, and inserts a new record with the current user as the creator. Updating a flight allows modifying fields, with restrictions on status changes depending on the user's role. The complete action endpoint transitions a flight to the `completed` status. The delay action endpoint transitions a flight to the `delayed` status and requires a delay reason. The cancel action endpoint transitions a flight to the `cancelled` status and requires a cancellation reason.

Access to individual endpoints follows the permission matrix: listing and retrieving flights is available to all authenticated roles, creating and completing flights is available to super_admin and operation_agent, updating flights is available to super_admin and operation_agent with status restrictions, and delaying or canceling flights is restricted to super_admin only.

## 6. Validation Rules

All validation is enforced in the flight service layer and through Pydantic schemas. The flight number must be provided and unique across all flights, compared case-insensitively. The origin, destination, and aircraft type must be non-empty strings. The departure and arrival times must be provided, and the departure time must be strictly before the arrival time. The total seat count must be an integer greater than or equal to one. The available seat count must be an integer between zero and the total seat count inclusive. The price must be a numeric value greater than or equal to zero. The status must be one of the four allowed enum values. If the status is `cancelled`, a cancellation reason must be provided. If the status is `delayed`, a delay reason must be provided.

## 7. Cursor-Based Pagination

List endpoints use cursor-based pagination to ensure stable results even when data changes between requests. The response format includes an items array and a next cursor value. The client provides a limit parameter (default twenty, maximum one hundred) and an optional cursor parameter, which is the ID of the last item from the previous page. Results are ordered by creation timestamp descending, then by ID descending, to provide stable pagination.

## 8. Error Handling

The API returns standardized JSON error responses with a consistent structure containing an error code, a human-readable message, and optional details.

Error codes and their corresponding HTTP status codes:
- AUTH_REQUIRED — 401, returned when no authentication token is provided.
- INVALID_TOKEN — 401, returned when the token is missing, expired, or has an invalid signature.
- FORBIDDEN — 403, returned when the authenticated user's role does not permit the requested action.
- NOT_FOUND — 404, returned when a requested resource does not exist.
- VALIDATION_ERROR — 422, returned when request data fails schema or business validation.
- CONFLICT — 409, returned when a unique constraint is violated, such as attempting to create a flight with a duplicate flight number.
- INTERNAL_ERROR — 500, returned for unexpected server errors.

## 9. Supabase Setup Checklist

1. Create a new Supabase project.
2. Enable the Email authentication provider in the Supabase Dashboard, but **disable public sign-up** (allow login/password-reset only).
3. Copy the Supabase connection string from the Database settings and store it in `.env` as the database URL.
4. Copy the Supabase URL, anonymous key, and service role key into `.env`.
5. Copy the Supabase Auth JWT secret from the API settings into `.env` as the JWT secret key.
6. Run Alembic migrations against the Supabase database to create the tables.
7. Create the first super_admin user via the Supabase Dashboard, then set their role in the profiles table.
8. Verify that `.env` is listed in `.gitignore` to prevent secrets from being committed.

## 10. Implementation Steps

1. Scaffold the project directory structure, update the project configuration file with dependencies, and install dependencies.
2. Create the Supabase project, obtain the connection string, configure `.env` with all required secrets, and **disable public sign-up** in Supabase Auth settings.
3. Implement the SQLAlchemy ORM models for the profiles and flights tables.
4. Initialize Alembic, configure the environment with the async engine, and set the migration target metadata to the project's models.
5. Generate the initial migration from the SQLAlchemy models, review the generated SQL, and apply it to the database.
6. Implement the asynchronous configuration loader and database engine with session factory.
7. Implement the authentication middleware to verify JWTs locally and fetch user profiles from the database.
8. Create Pydantic schemas for flight data, user data, pagination, and error responses.
9. Implement the flight service layer with strict validation rules using SQLAlchemy async sessions.
10. Implement the flight router with CRUD and action endpoints, applying role-based access controls.
11. Implement the user service and router for super_admin user management via SQLAlchemy. This is the **only** path to create new users.
12. Implement the auth router as a proxy to the Supabase Auth async client for existing-user login and password reset only.
13. Implement global exception handlers to format all errors into the standard JSON structure.
14. Add the environment-variable-based docs toggle in the main application module.
15. Write async tests covering authentication, user CRUD, flight CRUD, and permission enforcement.
16. For every endpoint, write tests that verify: no token returns 401, wrong role returns 403, and correct role returns the expected success status.
17. Verify the setup by running migrations against the database, running the test suite, linting with ruff, and type-checking with mypy.

## 11. Risks & Mitigations

- Supabase Auth invitation flow may conflict with the temporary-password onboarding approach. Mitigation: use the Supabase Auth admin client to create users with an initial password directly.
- JWT verification latency on every request. Mitigation: verify JWTs locally using the secret key from `.env`; no external network call is needed.
- Alembic async engine misconfiguration. Mitigation: use the async migration pattern where both offline and online migration runs use the async engine and async connection.
- Flight number uniqueness across case variants. Mitigation: enforce case-insensitive uniqueness either through a database index using lowercase flight numbers or through case-insensitive checks in the service layer.
- Secrets leaked into code or logs. Mitigation: keep all secrets in `.env` only, ensure `.env` is in `.gitignore`, and never log configuration values.
- Async SQLAlchemy session not closed properly. Mitigation: use a context manager or try/finally block in the `get_db` dependency.
- Tests not covering async behavior properly. Mitigation: configure pytest-asyncio with automatic mode, and always await async service calls in tests.
- Endpoint left unprotected by a missing dependency. Mitigation: use a code-review checklist ensuring every router file imports and applies `get_current_user` or `require_role` on every route.
