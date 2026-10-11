import httpx
import pytest

from service.lls.__main__ import fixture_session
from service.lls.app import create_app
from service.lls.protocol import SessionResponse
from service.lls.settings import Settings


@pytest.fixture
async def client(tmp_path, request):
    settings = Settings(
        database_path=str(tmp_path / "test.db"), provider="fake", **getattr(request, "param", {})
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            client.app = app
            yield client


@pytest.mark.parametrize("version", [0, 2])
async def test_session_major_mismatch_is_actionable_before_opening_state(client, version):
    body = fixture_session()
    body["schema_version"] = version
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 426
    detail = response.json()["detail"]
    assert f"schema version {version}" in detail
    assert "requires version 1" in detail
    assert "adapter and service together" in detail
    assert client.app.state.runtime.sessions == {}
    assert client.app.state.repository.list_profiles() == []


@pytest.mark.parametrize("version", ["2", None, [], 2.5])
async def test_malformed_schema_version_remains_a_validation_error(client, version):
    body = fixture_session()
    body["schema_version"] = version
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 422
    assert client.app.state.runtime.sessions == {}


async def test_session_negotiates_optional_actions_and_features(client):
    body = fixture_session()
    body["capabilities"] = ["wait_here", "future_action", "face_player", "wait_here"]
    body["manifest"]["capability_flags"] = [
        "typed_input", "future_feature", "actor_pin", "development:perception:v1", "actor_pin"
    ]
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 200, response.text
    opened = response.json()
    assert opened["capabilities"] == ["wait_here", "face_player"]
    assert opened["capability_flags"] == ["typed_input", "actor_pin"]
    SessionResponse.model_validate(opened)
    session = client.app.state.runtime.sessions[opened["session_id"]]
    assert session.request.capabilities == opened["capabilities"]
    assert session.request.manifest.capability_flags == opened["capability_flags"]


async def test_session_never_enables_unoffered_features(client):
    body = fixture_session()
    body["capabilities"] = []
    body["manifest"]["capability_flags"] = []
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["capabilities"] == []
    assert response.json()["capability_flags"] == []


@pytest.mark.parametrize("client", [{"release": False, "dev_perception": True}], indirect=True)
async def test_session_negotiates_enabled_development_feature(client):
    body = fixture_session()
    body["manifest"]["capability_flags"] = [
        "development:perception:v1", "development:social:v1", "development:perception:v2"
    ]
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["capability_flags"] == ["development:perception:v1"]


async def test_session_negotiates_only_authored_service_integrations(client):
    client.app.state.repository.register_actor(
        "fake-demo",
        {
            "actor_id": "shop-clerk",
            "actor_kind": "anchored_service",
            "display_name": "Known Clerk",
            "control_policy": "service_limited",
            "service_integration_id": "test-shop",
            "allowed_actions": [],
        },
    )
    body = fixture_session()
    body["manifest"]["capability_flags"] = ["service:test-shop", "service:unknown-shop"]
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["capability_flags"] == ["service:test-shop"]
