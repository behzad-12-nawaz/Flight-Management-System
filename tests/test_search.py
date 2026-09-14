from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient

from fms.core.enums import CabinClass, FareType, FlightStatus


class TestFlightSearch:
    async def test_search_returns_matching_scheduled_flights(
        self, client: AsyncClient, flight_factory
    ):
        departure = datetime.now(timezone.utc) + timedelta(days=10)
        fixture = await flight_factory(
            departure=departure,
            origin="JFK",
            destination="LHR",
            cabins={
                CabinClass.ECONOMY: (10, "100.00"),
                CabinClass.BUSINESS: (2, "400.00"),
            },
        )

        response = await client.get(
            "/search/flights",
            params={"origin": "JFK", "destination": "LHR", "date": departure.date().isoformat()},
        )
        assert response.status_code == 200, response.text
        results = response.json()
        assert len(results) == 1
        assert results[0]["id"] == str(fixture.flight.id)
        assert results[0]["status"] == "scheduled"

    async def test_search_is_case_insensitive(self, client: AsyncClient, sample_flight):
        response = await client.get(
            "/search/flights", params={"origin": "jfk", "destination": "lhr"}
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_cancelled_flights_are_not_returned(
        self, client: AsyncClient, flight_factory
    ):
        await flight_factory(
            flight_number="FMS200",
            status=FlightStatus.CANCELLED,
        )

        response = await client.get("/search/flights", params={"origin": "JFK"})
        assert response.status_code == 200
        assert response.json() == []

    async def test_search_date_filter(self, client: AsyncClient, flight_factory):
        today = datetime.now(timezone.utc) + timedelta(days=5)
        await flight_factory(flight_number="FMS300", departure=today)
        await flight_factory(flight_number="FMS301", departure=today + timedelta(days=1))

        response = await client.get(
            "/search/flights", params={"date": today.date().isoformat()}
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["flight_number"] == "FMS300"

    async def test_search_includes_cabins_and_fares(self, client: AsyncClient, flight_factory):
        await flight_factory(
            cabins={CabinClass.ECONOMY: (10, "100.00"), CabinClass.FIRST: (1, "900.00")}
        )

        response = await client.get("/search/flights", params={"origin": "JFK"})
        assert response.status_code == 200
        flight = response.json()[0]

        assert len(flight["seat_classes"]) == 2
        economy = next(
            seat_class
            for seat_class in flight["seat_classes"]
            if seat_class["cabin_class"] == "economy"
        )
        assert economy["available_seats"] == 10
        assert len(economy["fares"]) == 2

        fares = {fare["fare_type"]: fare for fare in economy["fares"]}
        assert fares["basic"]["refundable"] is False
        assert fares["flexible"]["refundable"] is True
        assert Decimal(fares["flexible"]["price"]) == Decimal("135.00")

    async def test_search_without_filters_returns_all_scheduled(
        self, client: AsyncClient, flight_factory
    ):
        await flight_factory(flight_number="FMS400", origin="JFK", destination="LHR")
        await flight_factory(flight_number="FMS401", origin="CDG", destination="DXB")

        response = await client.get("/search/flights")
        assert response.status_code == 200
        assert len(response.json()) == 2

    async def test_available_seats_reflect_holds(self, client: AsyncClient, auth_headers, sample_flight):
        await client.post(
            "/bookings/hold",
            headers=auth_headers,
            json={
                "items": [
                    {
                        "flight_id": str(sample_flight.flight.id),
                        "cabin_class": "economy",
                        "fare_type": "basic",
                        "passengers": [{"name": "Jane Doe"}],
                    }
                ]
            },
        )

        response = await client.get("/search/flights", params={"origin": "JFK"})
        assert response.status_code == 200
        economy = response.json()[0]["seat_classes"][0]
        assert economy["available_seats"] == 9

    async def test_fare_prices_are_plain_numbers(self, client: AsyncClient, sample_flight):
        """Currency handling is out of scope: prices are plain numeric(10,2) values."""
        response = await client.get("/search/flights", params={"origin": "JFK"})
        economy = response.json()[0]["seat_classes"][0]
        for fare in economy["fares"]:
            assert Decimal(fare["price"]) > 0
            assert fare["fare_type"] in {FareType.BASIC.value, FareType.FLEXIBLE.value}
