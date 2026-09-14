from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select, text

from fms.core.enums import CabinClass, FlightStatus, HoldStatus
from fms.models.booking import SeatHold
from fms.services import seat_service

from conftest import (
    FlightFixture,
    available_seats,
    book,
    booking_hold_payload,
    confirm_booking,
    hold_booking,
)


def multi_leg_payload(*fixtures: FlightFixture) -> dict:
    return {
        "items": [
            {
                "flight_id": str(fixture.flight.id),
                "cabin_class": "economy",
                "fare_type": "basic",
                "passengers": [{"name": "Jane Doe"}],
            }
            for fixture in fixtures
        ]
    }


def _ago(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


class TestSeatHolds:
    async def test_hold_returns_locked_price_and_expiry(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=booking_hold_payload(sample_flight)
        )
        assert response.status_code == 201, response.text
        data = response.json()

        assert len(data["holds"]) == 1
        assert data["holds"][0]["quantity"] == 1
        assert data["holds"][0]["status"] == HoldStatus.ACTIVE.value
        assert Decimal(data["holds"][0]["locked_price"]) == Decimal("100.00")
        assert Decimal(data["total_price"]) == Decimal("100.00")

    async def test_hold_decrements_available_seats(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        await hold_booking(
            client, auth_headers, booking_hold_payload(sample_flight, passengers=["A", "B"])
        )
        assert await available_seats(db_session, sample_flight.cabin(CabinClass.ECONOMY).id) == 8

    async def test_last_seat_can_only_be_held_once(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, flight_factory
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})

        first = await client.post(
            "/bookings/hold", headers=auth_headers, json=booking_hold_payload(fixture)
        )
        assert first.status_code == 201

        second = await client.post(
            "/bookings/hold", headers=second_user_headers, json=booking_hold_payload(fixture)
        )
        assert second.status_code == 409
        assert "Not enough seats" in second.json()["detail"]

    async def test_group_booking_insufficient_seats_releases_everything(
        self,
        client: AsyncClient,
        auth_headers: dict,
        second_user_headers: dict,
        flight_factory,
        db_session,
    ):
        big = await flight_factory(
            flight_number="FMS500", cabins={CabinClass.ECONOMY: (2, "100.00")}
        )
        tiny = await flight_factory(
            flight_number="FMS501", destination="CDG", cabins={CabinClass.ECONOMY: (1, "100.00")}
        )

        # Take the only seat on the second leg.
        await hold_booking(client, second_user_headers, booking_hold_payload(tiny))

        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=multi_leg_payload(big, tiny)
        )
        assert response.status_code == 409

        # The first leg's hold must have been rolled back.
        assert await available_seats(db_session, big.cabin(CabinClass.ECONOMY).id) == 2
        holds = list(
            (
                await db_session.execute(
                    select(SeatHold)
                    .where(SeatHold.fare_id == big.fare(CabinClass.ECONOMY).id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert holds and all(hold.status == HoldStatus.RELEASED for hold in holds)

    async def test_multi_leg_hold_shares_one_group_key(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        leg_one = await flight_factory(
            flight_number="FMS600", cabins={CabinClass.ECONOMY: (2, "100.00")}
        )
        leg_two = await flight_factory(
            flight_number="FMS601", destination="CDG", cabins={CabinClass.ECONOMY: (2, "150.00")}
        )

        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=multi_leg_payload(leg_one, leg_two)
        )
        assert response.status_code == 201, response.text
        data = response.json()

        assert len(data["holds"]) == 2
        assert {hold["group_key"] for hold in data["holds"]} == {data["group_key"]}
        assert Decimal(data["total_price"]) == Decimal("250.00")

    async def test_hold_expiry_is_released_on_read(
        self,
        client: AsyncClient,
        auth_headers: dict,
        second_user_headers: dict,
        flight_factory,
        db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        hold = await hold_booking(client, auth_headers, booking_hold_payload(fixture))
        hold_id = hold["holds"][0]["id"]

        # Age the hold directly in the DB — there is no scheduler to expire it for us.
        await db_session.execute(
            text("UPDATE seat_holds SET expires_at = :past WHERE id = :id"),
            {"past": _ago(1), "id": hold_id},
        )

        # The next availability-reading call lazily expires the stale hold and frees the seat.
        response = await client.post(
            "/bookings/hold", headers=second_user_headers, json=booking_hold_payload(fixture)
        )
        assert response.status_code == 201, response.text

        expired_status = (
            await db_session.execute(
                select(SeatHold.status)
                .where(SeatHold.id == UUID(hold_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert expired_status == HoldStatus.EXPIRED
        # Still no double-counting: the single seat now belongs to the second user's hold.
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 0

    async def test_hold_re_reads_version_from_database(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        """A version bump outside the ORM must not break the read/retry loop."""
        seat_class = sample_flight.cabin(CabinClass.ECONOMY)
        await db_session.execute(
            text("UPDATE seat_classes SET version = version + 5 WHERE id = :id"),
            {"id": str(seat_class.id)},
        )

        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=booking_hold_payload(sample_flight)
        )
        assert response.status_code == 201, response.text

    async def test_cutoff_blocks_late_holds(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        soon = datetime.now(timezone.utc) + timedelta(minutes=20)
        fixture = await flight_factory(departure=soon, duration=timedelta(hours=2))

        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=booking_hold_payload(fixture)
        )
        assert response.status_code == 422
        assert "closed" in response.json()["detail"]

    async def test_business_cutoff_is_shorter_than_economy(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        # 45 minutes before departure: too late for economy (60 min), still fine for business (30 min)
        soon = datetime.now(timezone.utc) + timedelta(minutes=45)
        fixture = await flight_factory(
            departure=soon,
            duration=timedelta(hours=2),
            cabins={
                CabinClass.ECONOMY: (2, "100.00"),
                CabinClass.BUSINESS: (2, "400.00"),
            },
        )

        economy = await client.post(
            "/bookings/hold",
            headers=auth_headers,
            json=booking_hold_payload(fixture, cabin_class=CabinClass.ECONOMY),
        )
        assert economy.status_code == 422

        business = await client.post(
            "/bookings/hold",
            headers=auth_headers,
            json=booking_hold_payload(fixture, cabin_class=CabinClass.BUSINESS),
        )
        assert business.status_code == 201, business.text

    async def test_cannot_hold_on_non_scheduled_flight(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        fixture = await flight_factory(status=FlightStatus.DELAYED)
        response = await client.post(
            "/bookings/hold", headers=auth_headers, json=booking_hold_payload(fixture)
        )
        assert response.status_code == 409


class TestBookingConfirmation:
    async def test_hold_then_confirm_creates_booking(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        hold = await hold_booking(
            client, auth_headers, booking_hold_payload(sample_flight, passengers=["Jane Doe"])
        )
        booking = await confirm_booking(client, auth_headers, hold["group_key"], ["Jane Doe"])

        assert len(booking["booking_reference"]) == 6
        assert booking["status"] == "confirmed"
        assert booking["payment_status"] == "paid"
        assert Decimal(booking["total_price"]) == Decimal("100.00")
        assert len(booking["items"]) == 1
        assert booking["items"][0]["passenger_name"] == "Jane Doe"

        # The hold is converted, not released (the seat stays consumed).
        hold_row = (
            await db_session.execute(select(SeatHold).execution_options(populate_existing=True))
        ).scalars().one()
        assert hold_row.status == HoldStatus.CONVERTED
        assert await available_seats(db_session, sample_flight.cabin(CabinClass.ECONOMY).id) == 9

    async def test_group_booking_creates_one_item_per_passenger(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (4, "100.00")})
        booking = await book(
            client, auth_headers, fixture, passengers=["Jane Doe", "John Smith", "Amy Adams"]
        )

        assert len(booking["items"]) == 3
        assert {item["passenger_name"] for item in booking["items"]} == {
            "Jane Doe",
            "John Smith",
            "Amy Adams",
        }
        assert Decimal(booking["total_price"]) == Decimal("300.00")

    async def test_multi_leg_booking_numbers_the_legs(
        self, client: AsyncClient, auth_headers: dict, flight_factory
    ):
        leg_one = await flight_factory(
            flight_number="FMS700", cabins={CabinClass.ECONOMY: (2, "100.00")}
        )
        leg_two = await flight_factory(
            flight_number="FMS701", destination="CDG", cabins={CabinClass.ECONOMY: (2, "150.00")}
        )

        hold = await hold_booking(client, auth_headers, multi_leg_payload(leg_one, leg_two))
        booking = await confirm_booking(
            client, auth_headers, hold["group_key"], ["Jane Doe", "Jane Doe"]
        )

        assert len(booking["items"]) == 2
        assert {item["leg_number"] for item in booking["items"]} == {1, 2}

    async def test_passenger_name_count_must_match(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        hold = await hold_booking(client, auth_headers, booking_hold_payload(sample_flight))
        response = await client.post(
            "/bookings/confirm",
            headers=auth_headers,
            json={"group_key": hold["group_key"], "passenger_names": ["A", "B"]},
        )
        assert response.status_code == 422

    async def test_expired_hold_cannot_be_confirmed(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        hold = await hold_booking(client, auth_headers, booking_hold_payload(sample_flight))
        await db_session.execute(
            text("UPDATE seat_holds SET expires_at = :past"), {"past": _ago(5)}
        )

        response = await client.post(
            "/bookings/confirm",
            headers=auth_headers,
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert response.status_code == 409
        assert "expired" in response.json()["detail"].lower()

    async def test_a_hold_cannot_be_confirmed_by_another_user(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, sample_flight
    ):
        hold = await hold_booking(client, auth_headers, booking_hold_payload(sample_flight))
        response = await client.post(
            "/bookings/confirm",
            headers=second_user_headers,
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert response.status_code == 403

    async def test_unknown_group_key_is_404(self, client: AsyncClient, auth_headers: dict):
        response = await client.post(
            "/bookings/confirm",
            headers=auth_headers,
            json={
                "group_key": "11111111-1111-1111-1111-111111111111",
                "passenger_names": ["Jane Doe"],
            },
        )
        assert response.status_code == 404

    async def test_booking_detail_is_owner_only(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, sample_flight
    ):
        booking = await book(client, auth_headers, sample_flight)

        owner_view = await client.get(f"/bookings/{booking['id']}", headers=auth_headers)
        assert owner_view.status_code == 200
        assert owner_view.json()["booking_reference"] == booking["booking_reference"]

        other_view = await client.get(f"/bookings/{booking['id']}", headers=second_user_headers)
        assert other_view.status_code == 403

    async def test_admin_can_view_any_booking(
        self, client: AsyncClient, auth_headers: dict, admin_headers: dict, sample_flight
    ):
        booking = await book(client, auth_headers, sample_flight)
        response = await client.get(f"/bookings/{booking['id']}", headers=admin_headers)
        assert response.status_code == 200

    async def test_manual_release_returns_seats(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        hold = await hold_booking(
            client, auth_headers, booking_hold_payload(sample_flight, passengers=["A", "B"])
        )
        assert await available_seats(db_session, sample_flight.cabin(CabinClass.ECONOMY).id) == 8

        await seat_service.release_holds(db_session, [UUID(hold["holds"][0]["id"])])
        await db_session.flush()

        assert await available_seats(db_session, sample_flight.cabin(CabinClass.ECONOMY).id) == 10
