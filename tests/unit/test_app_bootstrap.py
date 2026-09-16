import asyncio

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient, Response

from backend import create_app
from backend.config import PostgreSQLSettings

pytestmark = pytest.mark.unit


def test_create_app_returns_fastapi_application() -> None:
    assert isinstance(create_app(), FastAPI)


def test_liveness_does_not_require_external_services() -> None:
    async def get_liveness() -> Response:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health/live")

    response = asyncio.run(get_liveness())

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.parametrize(
    ("database_ready", "expected_status", "expected_body"),
    [
        (True, 200, {"status": "ready"}),
        (False, 503, {"status": "unready"}),
    ],
)
def test_readiness_reflects_postgres_connectivity(
    monkeypatch: pytest.MonkeyPatch,
    database_ready: bool,
    expected_status: int,
    expected_body: dict[str, str],
) -> None:
    async def postgres_probe(_settings: PostgreSQLSettings) -> bool:
        return database_ready

    monkeypatch.setattr("backend.app.postgres_is_ready", postgres_probe)

    async def get_readiness() -> Response:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health/ready")

    response = asyncio.run(get_readiness())

    assert response.status_code == expected_status
    assert response.json() == expected_body
