"""HTTP integration coverage for DemoOwner application wiring."""

import asyncio

import pytest
from fastapi import Request
from httpx2 import ASGITransport, AsyncClient, Response

from backend import create_app
from backend.config import ApplicationSettings, AuthMode
from backend.security import DemoOwnerAccessPolicy, DemoOwnerIdentityResolver

pytestmark = pytest.mark.integration


def test_http_request_resolves_owner_without_authorization_header_or_idp() -> None:
    settings = ApplicationSettings(
        auth={"mode": AuthMode.DEMO_OWNER, "demo_owner_id": "http-test-owner"}
    )
    application = create_app(settings)

    @application.get("/_test/principal")
    async def principal(request: Request) -> dict[str, str]:
        context = await request.app.state.identity_resolver.resolve(request)
        return {
            "principal_id": context.principal_id,
            "principal_type": context.principal_type,
        }

    async def get_principal() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/_test/principal")

    response = asyncio.run(get_principal())

    assert response.status_code == 200
    assert response.json() == {
        "principal_id": "http-test-owner",
        "principal_type": "user",
    }
    assert isinstance(application.state.identity_resolver, DemoOwnerIdentityResolver)
    assert isinstance(
        application.state.workspace_access_policy,
        DemoOwnerAccessPolicy,
    )


def test_unimplemented_auth_mode_does_not_fall_back_to_demo_owner() -> None:
    settings = ApplicationSettings(auth={"mode": AuthMode.OIDC})

    with pytest.raises(RuntimeError, match="oidc has no configured adapters"):
        create_app(settings)
