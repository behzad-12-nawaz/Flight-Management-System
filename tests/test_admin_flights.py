from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select, text

from fms.core.enums import CabinClass, FareType
from fms.models.fare import Fare
from fms.models.flight import Flight, SeatClass
from fms.models.refund import Refund

from conftest import book, items_for_flight


def flight_payload(**overrides) -> dict:
    departure = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "flight_number": "FMS100",
        "origin": "JFK",
        "destination": "LHR",
        "departure_datetime": departure.isoformat(),
        "arrival_datetime": (departure + timedelta(hours=7)).isoformat(),
        "total_seats": 12,
        "seat_classes": [
            {"cabin_class": "economy", "total_seats": 10, "base_price": "100.00"},
            {"cabin_class": "business", "total_seats": 2, "base_price": "250.00"},
        ],
    }
    payload.update(overrides)
    return payload


class TestCreateFlight:
    async def test_create_flight_with_seat_classes(
        self, client: AsyncClient, admin_headers: dict
    ):
        response = await client.post("/admin/flights", headers=admin_headers, json=flight_payload())
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "scheduled"
        assert data["total_seats"] == 12
        assert data["version"] == 1

    async def test_auto_created_fares(
        self, client: AsyncClient, admin_headers: dict, db_session
    ):
        response = await client.post("/admin/flights", headers=admin_headers, json=flight_payload())
        assert response.status_code == 201
        flight_id = response.json()["id"]

        fares = list(
            (
                await db_session.execute(
                    select(Fare)
                    .join(SeatClass, Fare.seat_class_id == SeatClass.id)
                    .where(SeatClass.flight_id == flight_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(fares) == 4  # 2 cabins x (basic, flexible)

        by_seat_class: dict = {}
        for fare in fares:
            by_seat_class.setdefault(fare.seat_class_id, {})[fare.fare_type] = fare

        assert len(by_seat_class) == 2
        for pair in by_seat_class.values():
            basic = pair[FareType.BASIC]
            flexible = pair[FareType.FLEXIBLE]
            assert basic.refundable is False
            assert basic.change_allowed is False
            assert basic.seat_choice_allowed is False
            assert flexible.refundable is True
            assert flexible.price == (basic.price * Decimal("1.35")).quantize(Decimal("0.01"))

    async def test_seat_totals_must_sum_to_capacity(self, client: AsyncClient, admin_headers: dict):
        response = await client.post(
            "/admin/flights", headers=admin_headers, json=flight_payload(total_seats=11)
        )
        assert response.status_code == 422

    async def test_zero_or_negative_seat_counts_rejected(
        self, client: AsyncClient, admin_headers: dict
    ):
        payload = flight_payload(
            total_seats=10,
            seat_classes=[{"cabin_class": "economy", "total_seats": 0, "base_price": "100.00"}],
        )
        response = await client.post("/admin/flights", headers=admin_headers, json=payload)
        assert response.status_code == 422

    async def test_duplicate_flight_same_day_is_rejected(
        self, client: AsyncClient, admin_headers: dict, sample_flight
    ):
        payload = flight_payload(
            flight_number=sample_flight.flight.flight_number,
            origin=sample_flight.flight.origin,
            destination=sample_flight.flight.destination,
            departure_datetime=sample_flight.flight.departure_datetime.isoformat(),
            arrival_datetime=sample_flight.flight.arrival_datetime.isoformat(),
        )
        response = await client.post("/admin/flights", headers=admin_headers, json=payload)
        assert response.status_code == 409

    async def test_arrival_must_be_after_departure(self, client: AsyncClient, admin_headers: dict):
        departure = datetime.now(timezone.utc) + timedelta(days=7)
        payload = flight_payload(
            departure_datetime=departure.isoformat(),
            arrival_datetime=(departure - timedelta(hours=1)).isoformat(),
        )
        response = await client.post("/admin/flights", headers=admin_headers, json=payload)
        assert response.status_code == 422

    async def test_customer_cannot_create_flight(self, client: AsyncClient, auth_headers: dict):
        response = await client.post("/admin/flights", headers=auth_headers, json=flight_payload())
        assert response.status_code == 403

    async def test_ops_agent_can_create_flight(self, client: AsyncClient, ops_headers: dict):
        response = await client.post("/admin/flights", headers=ops_headers, json=flight_payload())
        assert response.status_code == 201


class TestScheduleEdit:
    async def test_edit_schedule_moves_both_times(
        self, client: AsyncClient, admin_headers: dict, sample_flight
    ):
        flight = sample_flight.flight
        new_departure = flight.departure_datetime + timedelta(hours=1)

        response = await client.patch(
            f"/admin/flights/{flight.id}",
            headers=admin_headers,
            json={
                "departure_datetime": new_departure.isoformat(),
                "arrival_datetime": (new_departure + timedelta(hours=7)).isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["version"] == 2
        assert data["original_departure_datetime"] is None

    async def test_small_change_does_not_trigger_fare_override(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        await book(client, auth_headers, sample_flight)
        flight = sample_flight.flight
        new_departure = flight.departure_datetime + timedelta(hours=1)

        response = await client.patch(
            f"/admin/flights/{flight.id}",
            headers=admin_headers,
            json={
                "departure_datetime": new_departure.isoformat(),
                "arrival_datetime": (new_departure + timedelta(hours=7)).isoformat(),
            },
        )
        assert response.status_code == 200

        items = await items_for_flight(db_session, flight.id)
        assert items[0].schedule_change_override is False

    async def test_large_change_triggers_fare_override(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        await book(client, auth_headers, sample_flight)
        flight = sample_flight.flight
        new_departure = flight.departure_datetime + timedelta(hours=5)

        response = await client.patch(
            f"/admin/flights/{flight.id}",
            headers=admin_headers,
            json={
                "departure_datetime": new_departure.isoformat(),
                "arrival_datetime": (new_departure + timedelta(hours=7)).isoformat(),
            },
        )
        assert response.status_code == 200

        items = await items_for_flight(db_session, flight.id)
        assert items[0].schedule_change_override is True

    async def test_schedule_edit_optimistic_lock_conflict(
        self, client: AsyncClient, admin_headers: dict, sample_flight, db_session
    ):
        flight = sample_flight.flight
        # Bump the stored version behind the ORM's back so the service's version check fails.
        await db_session.execute(
            text("UPDATE flights SET version = version + 1 WHERE id = :id"), {"id": str(flight.id)}
        )

        new_departure = flight.departure_datetime + timedelta(hours=1)
        response = await client.patch(
            f"/admin/flights/{flight.id}",
            headers=admin_headers,
            json={
                "departure_datetime": new_departure.isoformat(),
                "arrival_datetime": (new_departure + timedelta(hours=7)).isoformat(),
            },
        )
        assert response.status_code == 409


class TestDelay:
    async def test_first_delay_snapshots_original_times(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        flight = sample_flight.flight
        original_departure = flight.departure_datetime
        original_arrival = flight.arrival_datetime

        response = await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (original_departure + timedelta(hours=2)).isoformat(),
                "new_arrival": (original_arrival + timedelta(hours=2)).isoformat(),
                "delay_reason": "weather",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "delayed"
        assert data["delay_reason"] == "weather"
        assert datetime.fromisoformat(data["original_departure_datetime"]) == original_departure
        assert datetime.fromisoformat(data["original_arrival_datetime"]) == original_arrival

    async def test_second_delay_does_not_overwrite_original(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        flight = sample_flight.flight
        original_departure = flight.departure_datetime
        original_arrival = flight.arrival_datetime

        first = await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (original_departure + timedelta(hours=1)).isoformat(),
                "new_arrival": (original_arrival + timedelta(hours=1)).isoformat(),
                "delay_reason": "weather",
            },
        )
        assert first.status_code == 200

        second = await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (original_departure + timedelta(hours=2)).isoformat(),
                "new_arrival": (original_arrival + timedelta(hours=2)).isoformat(),
                "delay_reason": "crew",
            },
        )
        assert second.status_code == 200, second.text
        data = second.json()
        assert data["delay_reason"] == "crew"
        assert datetime.fromisoformat(data["original_departure_datetime"]) == original_departure

    async def test_cumulative_delay_across_updates_triggers_override(
        self, client: AsyncClient, ops_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        await book(client, auth_headers, sample_flight)
        flight = sample_flight.flight
        original_departure = flight.departure_datetime
        original_arrival = flight.arrival_datetime

        for hours in (2, 4):
            response = await client.patch(
                f"/admin/flights/{flight.id}/delay",
                headers=ops_headers,
                json={
                    "new_departure": (original_departure + timedelta(hours=hours)).isoformat(),
                    "new_arrival": (original_arrival + timedelta(hours=hours)).isoformat(),
                    "delay_reason": "weather",
                },
            )
            assert response.status_code == 200, response.text

        items = await items_for_flight(db_session, flight.id)
        assert items[0].schedule_change_override is True

    async def test_delay_under_threshold_keeps_override_unset(
        self, client: AsyncClient, ops_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        await book(client, auth_headers, sample_flight)
        flight = sample_flight.flight

        response = await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (flight.departure_datetime + timedelta(hours=2)).isoformat(),
                "new_arrival": (flight.arrival_datetime + timedelta(hours=2)).isoformat(),
                "delay_reason": "weather",
            },
        )
        assert response.status_code == 200

        items = await items_for_flight(db_session, flight.id)
        assert items[0].schedule_change_override is False

    async def test_delay_requires_a_reason(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        flight = sample_flight.flight
        response = await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (flight.departure_datetime + timedelta(hours=1)).isoformat(),
                "new_arrival": (flight.arrival_datetime + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 422


class TestResolveDelay:
    async def test_resolve_requires_delayed_status(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        response = await client.patch(
            f"/admin/flights/{sample_flight.flight.id}/resolve-delay", headers=ops_headers
        )
        assert response.status_code == 422

    async def test_resolve_keeps_delay_record(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        flight = sample_flight.flight
        await client.patch(
            f"/admin/flights/{flight.id}/delay",
            headers=ops_headers,
            json={
                "new_departure": (flight.departure_datetime + timedelta(hours=4)).isoformat(),
                "new_arrival": (flight.arrival_datetime + timedelta(hours=4)).isoformat(),
                "delay_reason": "technical",
            },
        )

        response = await client.patch(
            f"/admin/flights/{flight.id}/resolve-delay", headers=ops_headers
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "scheduled"
        assert data["delay_reason"] == "technical"
        assert data["original_departure_datetime"] is not None


class TestFlightCancellation:
    async def test_ops_agent_cannot_cancel_flight(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        response = await client.delete(
            f"/admin/flights/{sample_flight.flight.id}", headers=ops_headers
        )
        assert response.status_code == 403

    async def test_airline_cancellation_compensates_basic_fare_with_credit(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        booking = await book(
            client, auth_headers, sample_flight, fare_type=FareType.BASIC
        )

        response = await client.delete(
            f"/admin/flights/{sample_flight.flight.id}", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"

        refunds = list((await db_session.execute(select(Refund))).scalars().all())
        assert len(refunds) == 1
        refund = refunds[0]
        assert refund.type.value == "travel_credit"
        assert refund.status.value == "pending_approval"
        assert refund.requires_approval is True
        assert refund.expires_at is not None
        assert str(refund.booking_item_id) == booking["items"][0]["id"]

    async def test_airline_cancellation_refunds_flexible_fare(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, sample_flight, db_session
    ):
        await book(client, auth_headers, sample_flight, fare_type=FareType.FLEXIBLE)

        response = await client.delete(
            f"/admin/flights/{sample_flight.flight.id}", headers=admin_headers
        )
        assert response.status_code == 200

        refund = (await db_session.execute(select(Refund))).scalars().one()
        assert refund.type.value == "refund"
        assert refund.status.value == "pending_approval"
        assert refund.expires_at is None

    async def test_cannot_cancel_twice(
        self, client: AsyncClient, admin_headers: dict, sample_flight
    ):
        flight_id = sample_flight.flight.id
        first = await client.delete(f"/admin/flights/{flight_id}", headers=admin_headers)
        assert first.status_code == 200
        second = await client.delete(f"/admin/flights/{flight_id}", headers=admin_headers)
        assert second.status_code == 409


class TestSeatAllocation:
    async def test_shrink_below_booked_count_is_rejected(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, flight_factory
    ):
        fixture = await flight_factory(cabins={CabinClass.ECONOMY: (2, "100.00")})
        await book(
            client, auth_headers, fixture, passengers=["Jane Doe", "John Smith"]
        )

        response = await client.patch(
            f"/admin/flights/{fixture.flight.id}/seat-classes/economy",
            headers=admin_headers,
            json={"total_seats": 1},
        )
        assert response.status_code == 422

    async def test_grow_cabin_keeps_invariant_and_updates_flight_total(
        self, client: AsyncClient, admin_headers: dict, sample_flight, db_session
    ):
        flight_id = sample_flight.flight.id

        response = await client.patch(
            f"/admin/flights/{flight_id}/seat-classes/economy",
            headers=admin_headers,
            json={"total_seats": 15},
        )
        assert response.status_code == 200, response.text
        assert response.json()["total_seats"] == 15
        assert response.json()["available_seats"] == 15

        flight_total = (
            await db_session.execute(
                select(Flight.total_seats)
                .where(Flight.id == flight_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert flight_total == 15

    async def test_only_super_admin_can_adjust_allocation(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        response = await client.patch(
            f"/admin/flights/{sample_flight.flight.id}/seat-classes/economy",
            headers=ops_headers,
            json={"total_seats": 12},
        )
        assert response.status_code == 403

    async def test_overbooking_buffer_can_be_raised(
        self, client: AsyncClient, admin_headers: dict, sample_flight
    ):
        response = await client.patch(
            f"/admin/flights/{sample_flight.flight.id}/seat-classes/economy",
            headers=admin_headers,
            json={"overbooking_buffer": 2},
        )
        assert response.status_code == 200
        assert response.json()["overbooking_buffer"] == 2


class TestSeatMap:
    async def test_define_seat_map_stores_layout(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        flight_id = sample_flight.flight.id
        response = await client.put(
            f"/admin/flights/{flight_id}/seat-map",
            headers=ops_headers,
            json={
                "seat_map": [
                    {"seat_number": "1A", "cabin_class": "economy"},
                    {"seat_number": "1B", "cabin_class": "economy"},
                ]
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["seat_map"] == {"1A": "economy", "1B": "economy"}

    async def test_duplicate_seat_numbers_rejected(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        response = await client.put(
            f"/admin/flights/{sample_flight.flight.id}/seat-map",
            headers=ops_headers,
            json={
                "seat_map": [
                    {"seat_number": "1A", "cabin_class": "economy"},
                    {"seat_number": "1A", "cabin_class": "economy"},
                ]
            },
        )
        assert response.status_code == 422

    async def test_seat_map_cannot_exceed_cabin_capacity(
        self, client: AsyncClient, ops_headers: dict, sample_flight
    ):
        response = await client.put(
            f"/admin/flights/{sample_flight.flight.id}/seat-map",
            headers=ops_headers,
            json={
                "seat_map": [
                    {"seat_number": f"1{chr(65 + i)}", "cabin_class": "economy"} for i in range(11)
                ]
            },
        )
        assert response.status_code == 422
