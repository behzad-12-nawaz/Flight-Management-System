from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestAuth:
    async def test_register_success(self, client: AsyncClient):
        response = await client.post(
            "/auth/register",
            json={"email": "newuser@example.com", "password": "password123", "full_name": "New User"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["full_name"] == "New User"
        assert data["role"] == "customer"
        assert "id" in data

    async def test_register_duplicate_email(self, client: AsyncClient, test_user):
        response = await client.post(
            "/auth/register",
            json={"email": test_user.email, "password": "password123", "full_name": "Another User"},
        )
        assert response.status_code == 409

    async def test_login_success(self, client: AsyncClient, test_user):
        response = await client.post(
            "/auth/login",
            json={"email": test_user.email, "password": "testpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_invalid_password(self, client: AsyncClient, test_user):
        response = await client.post(
            "/auth/login",
            json={"email": test_user.email, "password": "wrongpassword"},
        )
        assert response.status_code == 422

    async def test_login_nonexistent_user(self, client: AsyncClient):
        response = await client.post(
            "/auth/login",
            json={"email": "nonexistent@example.com", "password": "password123"},
        )
        assert response.status_code == 422

    async def test_refresh_token_success(self, client: AsyncClient, test_user):
        # First login to get refresh token
        login_response = await client.post(
            "/auth/login",
            json={"email": test_user.email, "password": "testpassword123"},
        )
        refresh_token = login_response.json()["refresh_token"]

        # Use refresh token to get new access token
        response = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_token_invalid(self, client: AsyncClient):
        response = await client.post("/auth/refresh", json={"refresh_token": "invalid.token.here"})
        assert response.status_code == 422

    async def test_me_endpoint(self, client: AsyncClient, auth_headers, test_user):
        response = await client.get("/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["full_name"] == test_user.full_name

    async def test_me_unauthorized(self, client: AsyncClient):
        response = await client.get("/auth/me")
        assert response.status_code == 401