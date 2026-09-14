from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select

from fms.core.enums import CabinClass, FareType
from fms.models.booking import Booking, BookingItem
from fms.models.refund import Refund
from fms.models.user import User
from fms.services import cancellation_service

from conftest import available_seats, book, booking_hold_payload


async def cancel_item(
    client: AsyncClient,
    headers: dict,
    booking_id: str,
    item_id: str,
    reason: str = "customer",
    idempotency_key: str | None = None,
) -> dict:
    extra = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    response = await client.post(
        f"/bookings/{booking_id}/items/{item_id}/cancel",
        headers={**headers, **extra},
        json={"reason": reason},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestCustomerCancellation:
    async def test_flexible_fare_gets_a_full_processed_refund(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.FLEXIBLE)

        response = await client.post(
            f"/bookings/{booking['id']}/cancel", headers=auth_headers, json={"reason": "customer"}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "cancelled"
        assert data["cancelled_at"] is not None
        assert data["items"][0]["status"] == "cancelled"

        refund = (await db_session.execute(select(Refund))).scalars().one()
        assert refund.type.value == "refund"
        assert refund.status.value == "processed"
        assert refund.requires_approval is False
        assert refund.expires_at is None
        assert refund.amount == Decimal("135.00")

    async def test_non_refundable_basic_fare_gets_nothing(
        self, client: AsyncClient, auth_headers: dict, sample_flight, db_session
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.BASIC)

        response = await client.post(
            f"/bookings/{booking['id']}/cancel", headers=auth_headers, json={"reason": "customer"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

        refunds = (await db_session.execute(select(Refund))).scalars().all()
        assert refunds == []

    async def test_cancellation_releases_the_seat(
        self,
        client: AsyncClient,
        auth_headers: dict,
        second_user_headers: dict,
        flight_factory,
        db_session,
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (1, "100.00")})
        booking = await book(client, auth_headers, fixture, fare_type=FareType.FLEXIBLE)

        await client.post(
            f"/bookings/{booking['id']}/cancel", headers=auth_headers, json={"reason": "customer"}
        )
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 1

        response = await client.post(
            "/bookings/hold", headers=second_user_headers, json=booking_hold_payload(fixture)
        )
        assert response.status_code == 201, response.text

    async def test_cannot_cancel_the_same_item_twice(
        self, client: AsyncClient, auth_headers: dict, sample_flight
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.FLEXIBLE)
        item_id = booking["items"][0]["id"]

        await cancel_item(client, auth_headers, booking["id"], item_id)

        response = await client.post(
            f"/bookings/{booking['id']}/items/{item_id}/cancel",
            headers=auth_headers,
            json={"reason": "customer"},
        )
        assert response.status_code == 409

    async def test_customer_cannot_cancel_someone_elses_booking(
        self, client: AsyncClient, auth_headers: dict, second_user_headers: dict, sample_flight
    ):
        booking = await book(client, auth_headers, sample_flight)
        response = await client.post(
            f"/bookings/{booking['id']}/cancel",
            headers=second_user_headers,
            json={"reason": "customer"},
        )
        assert response.status_code == 403


class TestPartialCancellation:
    async def test_only_the_cancelled_passenger_is_refunded(
        self, client: AsyncClient, auth_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (5, "100.00")})
        booking = await book(
            client,
            auth_headers,
            fixture,
            fare_type=FareType.FLEXIBLE,
            passengers=["Jane Doe", "John Smith", "Amy Adams"],
        )
        assert Decimal(booking["total_price"]) == Decimal("405.00")
        middle_item = booking["items"][1]

        result = await cancel_item(client, auth_headers, booking["id"], middle_item["id"])
        assert result["item"]["status"] == "cancelled"
        assert result["refund"]["amount"] == "135.00"

        refunds = (await db_session.execute(select(Refund))).scalars().all()
        assert len(refunds) == 1
        assert str(refunds[0].booking_item_id) == middle_item["id"]

        booking_row = (
            await db_session.execute(
                select(Booking).where(Booking.id == UUID(booking["id"])).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert booking_row.status.value == "partially_cancelled"
        assert booking_row.cancelled_at is None

        items = (
            await db_session.execute(
                select(BookingItem)
                .where(BookingItem.booking_id == UUID(booking["id"]))
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        assert sum(1 for item in items if item.status.value == "confirmed") == 2

        # Exactly one seat came back to inventory.
        assert await available_seats(db_session, fixture.cabin(CabinClass.ECONOMY).id) == 3

    async def test_cancelling_every_item_marks_the_booking_cancelled(
        self, client: AsyncClient, auth_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (5, "100.00")})
        booking = await book(
            client,
            auth_headers,
            fixture,
            fare_type=FareType.FLEXIBLE,
            passengers=["Jane Doe", "John Smith"],
        )

        for item in booking["items"]:
            await cancel_item(client, auth_headers, booking["id"], item["id"])

        booking_row = (
            await db_session.execute(
                select(Booking).where(Booking.id == UUID(booking["id"])).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert booking_row.status.value == "cancelled"
        assert booking_row.cancelled_at is not None

    async def test_partial_cancel_then_full_cancel(
        self, client: AsyncClient, auth_headers: dict, flight_factory, db_session
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (5, "100.00")})
        booking = await book(
            client,
            auth_headers,
            fixture,
            fare_type=FareType.FLEXIBLE,
            passengers=["Jane Doe", "John Smith", "Amy Adams"],
        )

        await cancel_item(client, auth_headers, booking["id"], booking["items"][0]["id"])
        response = await client.post(
            f"/bookings/{booking['id']}/cancel", headers=auth_headers, json={"reason": "customer"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"

        refunds = (await db_session.execute(select(Refund))).scalars().all()
        assert len(refunds) == 3


class TestAirlineCausedCompensation:
    async def test_airline_schedule_change_creates_pending_credit_for_basic_fare(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_admin: User,
        sample_flight,
        db_session,
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.BASIC)
        item_id = UUID(booking["items"][0]["id"])

        item, refund = await cancellation_service.cancel_booking_item(
            db_session,
            item_id=item_id,
            actor=test_admin,
            reason="airline_schedule_change",
        )
        await db_session.flush()

        assert item.status.value == "cancelled"
        assert refund is not None
        assert refund.type.value == "travel_credit"
        assert refund.status.value == "pending_approval"
        assert refund.requires_approval is True
        assert refund.expires_at is not None

    async def test_approval_workflow_moves_refund_to_processed(
        self,
        client: AsyncClient,
        auth_headers: dict,
        admin_headers: dict,
        ops_headers: dict,
        test_admin: User,
        sample_flight,
        db_session,
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.BASIC)

        # Airline-caused cancellations always produce a compensation row, gated on approval.
        _, refund = await cancellation_service.cancel_booking_item(
            db_session,
            item_id=UUID(booking["items"][0]["id"]),
            actor=test_admin,
            reason="airline_cancellation",
        )
        await db_session.flush()

        assert refund is not None
        assert refund.status.value == "pending_approval"
        refund_id = str(refund.id)

        # Only a super admin may approve.
        forbidden = await client.post(f"/refunds/{refund_id}/approve", headers=ops_headers)
        assert forbidden.status_code == 403

        response = await client.post(f"/refunds/{refund_id}/approve", headers=admin_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "processed"
        assert data["approved_by"] is not None

        again = await client.post(f"/refunds/{refund_id}/approve", headers=admin_headers)
        assert again.status_code == 409

    async def test_auto_approved_customer_refund_cannot_be_re_approved(
        self, client: AsyncClient, auth_headers: dict, admin_headers: dict, sample_flight, db_session
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.FLEXIBLE)
        await client.post(
            f"/bookings/{booking['id']}/cancel", headers=auth_headers, json={"reason": "customer"}
        )

        refund = (await db_session.execute(select(Refund))).scalars().one()
        assert refund.status.value == "processed"

        response = await client.post(f"/refunds/{refund.id}/approve", headers=admin_headers)
        assert response.status_code == 409

    async def test_schedule_override_makes_basic_fare_refundable_to_the_customer(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        booking = await book(client, auth_headers, sample_flight, fare_type=FareType.BASIC)

        # A large schedule change flags the booking item (Decision #5)...
        shifted = sample_flight.flight.departure_datetime + timedelta(hours=5)
        await client.patch(
            f"/admin/flights/{sample_flight.flight.id}",
            headers=admin_headers,
            json={
                "departure_datetime": shifted.isoformat(),
                "arrival_datetime": (shifted + timedelta(hours=7)).isoformat(),
            },
        )

        # ...so a customer cancellation now refunds this otherwise non-refundable fare.
        await cancel_item(client, auth_headers, booking["id"], booking["items"][0]["id"])

        refund = (await db_session.execute(select(Refund))).scalars().one()
        assert refund.type.value == "refund"
        assert refund.status.value == "processed"
        assert refund.amount == Decimal("100.00")

    async def test_unknown_hold_id_for_cancellation_is_404(
        self, client: AsyncClient, auth_headers: dict
    ):
        response = await client.post(
            "/bookings/11111111-1111-1111-1111-111111111111/items/"
            "22222222-2222-2222-2222-222222222222/cancel",
            headers=auth_headers,
            json={"reason": "customer"},
        )
        assert response.status_code == 404
