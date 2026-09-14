from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import func, select

from fms.core.enums import CabinClass, FareType
from fms.models.booking import Booking, BookingItem, SeatHold
from fms.models.refund import Refund

from conftest import available_seats, book, booking_hold_payload, hold_booking


class TestConfirmIdempotency:
    async def test_same_key_returns_identical_response_without_double_charge(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        hold = await hold_booking(
            client, auth_headers, booking_hold_payload(sample_flight, passengers=["Jane Doe"])
        )

        first = await client.post(
            "/bookings/confirm",
            headers={**auth_headers, "Idempotency-Key": "confirm-abc-123"},
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/bookings/confirm",
            headers={**auth_headers, "Idempotency-Key": "confirm-abc-123"},
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert second.status_code == 201, second.text
        assert second.json() == first.json()

        # Exactly one booking, one item, one charge and one seat-decrement.
        bookings = (await db_session.execute(select(func.count()).select_from(Booking))).scalar_one()
        items = (await db_session.execute(select(func.count()).select_from(BookingItem))).scalar_one()
        assert bookings == 1
        assert items == 1
        assert await available_seats(db_session, sample_flight.cabin(CabinClass.ECONOMY).id) == 9

        hold_row = (
            await db_session.execute(select(SeatHold).execution_options(populate_existing=True))
        ).scalars().one()
        assert hold_row.status.value == "converted"

    async def test_without_a_key_the_hold_cannot_be_confirmed_twice(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        hold = await hold_booking(client, auth_headers, booking_hold_payload(sample_flight))

        first = await client.post(
            "/bookings/confirm",
            headers=auth_headers,
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert first.status_code == 201

        second = await client.post(
            "/bookings/confirm",
            headers=auth_headers,
            json={"group_key": hold["group_key"], "passenger_names": ["Jane Doe"]},
        )
        assert second.status_code == 409

    async def test_distinct_keys_create_distinct_bookings(
        self, client: AsyncClient, auth_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (4, "100.00")})

        for key, name in (("key-1", "Jane Doe"), ("key-2", "John Smith")):
            hold = await hold_booking(
                client, auth_headers, booking_hold_payload(fixture, passengers=[name])
            )
            response = await client.post(
                "/bookings/confirm",
                headers={**auth_headers, "Idempotency-Key": key},
                json={"group_key": hold["group_key"], "passenger_names": [name]},
            )
            assert response.status_code == 201, response.text

        bookings = (await db_session.execute(select(func.count()).select_from(Booking))).scalar_one()
        assert bookings == 2
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 2


class TestCancellationIdempotency:
    async def test_same_key_returns_identical_response_without_double_refund(
        self, client: AsyncClient, auth_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (4, "100.00")})
        booking = await book(
            client,
            auth_headers,
            fixture,
            fare_type=FareType.FLEXIBLE,
            passengers=["Jane Doe", "John Smith"],
        )
        item_id = booking["items"][0]["id"]

        headers = {**auth_headers, "Idempotency-Key": "cancel-xyz-789"}
        body = {"reason": "customer"}

        first = await client.post(
            f"/bookings/{booking['id']}/items/{item_id}/cancel", headers=headers, json=body
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/bookings/{booking['id']}/items/{item_id}/cancel", headers=headers, json=body
        )
        assert second.status_code == 200, second.text
        assert second.json() == first.json()

        refunds = (await db_session.execute(select(func.count()).select_from(Refund))).scalar_one()
        assert refunds == 1
        # 4 - 2 booked + 1 cancelled = 3
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 3

        cancelled = (
            await db_session.execute(
                select(BookingItem)
                .where(BookingItem.id == UUID(item_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert cancelled.status.value == "cancelled"

    async def test_idempotency_keys_are_scoped_per_user(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (4, "100.00")})
        booking = await book(
            client,
            auth_headers,
            fixture,
            fare_type=FareType.FLEXIBLE,
            passengers=["Jane Doe", "John Smith"],
        )
        item_id = booking["items"][0]["id"]
        shared_key = "shared-key-1"

        first = await client.post(
            f"/bookings/{booking['id']}/items/{item_id}/cancel",
            headers={**auth_headers, "Idempotency-Key": shared_key},
            json={"reason": "customer"},
        )
        assert first.status_code == 200

        # Another user's identical key must not return the first user's cached response.
        second = await client.post(
            f"/bookings/{booking['id']}/items/{item_id}/cancel",
            headers={**second_user_headers, "Idempotency-Key": shared_key},
            json={"reason": "customer"},
        )
        assert second.status_code == 403
