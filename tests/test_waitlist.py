from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select, text

from fms.core.enums import CabinClass, FlightStatus, LoyaltyTier, UserRole, WaitlistStatus
from fms.core.security import create_access_token, hash_password
from fms.models.booking import SeatHold
from fms.models.user import User
from fms.models.waitlist import WaitlistEntry
from fms.services import seat_service

from conftest import FlightFixture, available_seats, booking_hold_payload, hold_booking


async def join_waitlist(
    client: AsyncClient, headers: dict, fixture: FlightFixture, cabin: CabinClass = CabinClass.ECONOMY
) -> dict:
    response = await client.post(
        "/waitlist",
        headers=headers,
        json={
            "flight_id": str(fixture.flight.id),
            "cabin_class": cabin.value,
            "quantity": 1,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def make_user(db_session, email: str, tier: LoyaltyTier) -> dict[str, str]:
    """Create a customer directly in the DB and return their auth headers."""
    user = User(
        email=email,
        hashed_password=hash_password("password123"),
        full_name=email.split("@")[0].title(),
        role=UserRole.CUSTOMER,
        loyalty_tier=tier,
    )
    db_session.add(user)
    await db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(str(user.id), user.role)}"}


async def set_created_at(db_session, entry_id: str, when: datetime) -> None:
    await db_session.execute(
        text("UPDATE waitlist_entries SET created_at = :when WHERE id = :id"),
        {"when": when, "id": entry_id},
    )


class TestJoinWaitlist:
    async def test_cannot_join_when_seats_are_available(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        response = await client.post(
            "/waitlist",
            headers=auth_headers,
            json={
                "flight_id": str(sample_flight.flight.id),
                "cabin_class": "economy",
                "quantity": 1,
            },
        )
        assert response.status_code == 409
        assert "book normally" in response.json()["detail"]

    async def test_join_when_cabin_is_full(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, flight_factory
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        await hold_booking(client, auth_headers, booking_hold_payload(fixture))

        entry = await join_waitlist(client, second_user_headers, fixture)
        assert entry["quantity"] == 1
        assert entry["status"] == WaitlistStatus.WAITING.value

    async def test_join_is_rejected_for_a_cancelled_flight(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        fixture = await flight_factory(
            status=FlightStatus.CANCELLED, cabins={CabinClass.ECONOMY: (1, "100.00")}
        )
        response = await client.post(
            "/waitlist",
            headers=auth_headers,
            json={
                "flight_id": str(fixture.flight.id),
                "cabin_class": "economy",
                "quantity": 1,
            },
        )
        assert response.status_code == 409

    async def test_unknown_flight_is_404(self, client: AsyncClient, auth_headers: dict):
        response = await client.post(
            "/waitlist",
            headers=auth_headers,
            json={
                "flight_id": "11111111-1111-1111-1111-111111111111",
                "cabin_class": "economy",
                "quantity": 1,
            },
        )
        assert response.status_code == 404


class TestWaitlistPriority:
    async def test_sorted_by_loyalty_tier_then_first_come(
        self,
        client: AsyncClient,
        auth_headers: dict,
        admin_headers: dict,
        flight_factory,
        db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        await hold_booking(client, auth_headers, booking_hold_payload(fixture))

        silver = await make_user(db_session, "silver@example.com", LoyaltyTier.SILVER)
        none = await make_user(db_session, "none@example.com", LoyaltyTier.NONE)
        platinum = await make_user(db_session, "platinum@example.com", LoyaltyTier.PLATINUM)

        # Join order: silver, then none, then platinum.
        for headers in (silver, none, platinum):
            await join_waitlist(client, headers, fixture)

        listing = await client.get(f"/admin/waitlist/{fixture.flight.id}", headers=admin_headers)
        assert listing.status_code == 200, listing.text

        tiers = [entry["user_loyalty_tier"] for entry in listing.json()]
        assert tiers == ["platinum", "silver", "none"]

    async def test_first_come_first_served_within_a_tier(
        self, client: AsyncClient, auth_headers: dict, admin_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        await hold_booking(client, auth_headers, booking_hold_payload(fixture))

        first = await make_user(db_session, "gold-1@example.com", LoyaltyTier.GOLD)
        second = await make_user(db_session, "gold-2@example.com", LoyaltyTier.GOLD)

        first_entry = await join_waitlist(client, first, fixture)
        second_entry = await join_waitlist(client, second, fixture)

        # Pin arrival order explicitly (both rows share a transaction timestamp otherwise).
        now = datetime.now(timezone.utc)
        await set_created_at(db_session, first_entry["id"], now)
        await set_created_at(db_session, second_entry["id"], now + timedelta(seconds=5))

        listing = await client.get(f"/admin/waitlist/{fixture.flight.id}", headers=admin_headers)
        emails = [entry["user_email"] for entry in listing.json()]
        assert emails == ["gold-1@example.com", "gold-2@example.com"]

    async def test_only_waiting_entries_are_listed(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, admin_headers: dict,
        flight_factory, db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        await hold_booking(client, auth_headers, booking_hold_payload(fixture))
        await join_waitlist(client, second_user_headers, fixture)

        entry = (
            await db_session.execute(
                select(WaitlistEntry).execution_options(populate_existing=True)
            )
        ).scalar_one()
        entry.status = WaitlistStatus.CANCELLED
        await db_session.flush()

        listing = await client.get(f"/admin/waitlist/{fixture.flight.id}", headers=admin_headers)
        assert listing.status_code == 200
        assert listing.json() == []


class TestWaitlistPromotion:
    async def test_promote_creates_a_hold_and_marks_entry_promoted(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, admin_headers: dict,
        flight_factory, db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        hold = await hold_booking(client, auth_headers, booking_hold_payload(fixture))
        entry = await join_waitlist(client, second_user_headers, fixture)

        # The first passenger abandons their hold, freeing the only seat.
        await seat_service.release_holds(db_session, [UUID(hold["holds"][0]["id"])])
        await db_session.flush()

        response = await client.post(f"/admin/waitlist/{entry['id']}/promote", headers=admin_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == WaitlistStatus.PROMOTED.value
        assert data["hold_id"] is not None

        promoted_entry = (
            await db_session.execute(
                select(WaitlistEntry)
                .where(WaitlistEntry.id == UUID(entry["id"]))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert promoted_entry.status == WaitlistStatus.PROMOTED

        new_hold = (
            await db_session.execute(
                select(SeatHold)
                .where(SeatHold.id == UUID(data["hold_id"]))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert new_hold.status.value == "active"
        assert str(new_hold.user_id) == entry["user_id"]
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 0

    async def test_promote_fails_when_seats_are_gone(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, admin_headers: dict,
        flight_factory, db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        await hold_booking(client, auth_headers, booking_hold_payload(fixture))
        entry = await join_waitlist(client, second_user_headers, fixture)

        response = await client.post(f"/admin/waitlist/{entry['id']}/promote", headers=admin_headers)
        assert response.status_code == 409

        still_waiting = (
            await db_session.execute(
                select(WaitlistEntry.status)
                .where(WaitlistEntry.id == UUID(entry["id"]))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert still_waiting == WaitlistStatus.WAITING

    async def test_promoting_a_promoted_entry_conflicts(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, admin_headers: dict,
        flight_factory, db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        hold = await hold_booking(client, auth_headers, booking_hold_payload(fixture))
        entry = await join_waitlist(client, second_user_headers, fixture)

        await seat_service.release_holds(db_session, [UUID(hold["holds"][0]["id"])])
        await db_session.flush()

        first = await client.post(f"/admin/waitlist/{entry['id']}/promote", headers=admin_headers)
        assert first.status_code == 200

        second = await client.post(f"/admin/waitlist/{entry['id']}/promote", headers=admin_headers)
        assert second.status_code == 409

    async def test_customer_cannot_view_or_promote(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        listing = await client.get(
            f"/admin/waitlist/{sample_flight.flight.id}", headers=auth_headers
        )
        assert listing.status_code == 403

        promote = await client.post(
            "/admin/waitlist/11111111-1111-1111-1111-111111111111/promote", headers=auth_headers
        )
        assert promote.status_code == 403

    async def test_unknown_entry_is_404(self, client: AsyncClient, admin_headers: dict):
        response = await client.post(
            "/admin/waitlist/11111111-1111-1111-1111-111111111111/promote",
            headers=admin_headers,
        )
        assert response.status_code == 404
