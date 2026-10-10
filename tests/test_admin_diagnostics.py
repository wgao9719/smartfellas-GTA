import json

from test_admin import client as client
from test_admin import open_profile


async def test_admin_diagnostics_exposes_scoped_state_without_runtime_secrets(client):
    session = await open_profile(client)
    session.world = {"paused": False, "_private": "hidden-world-value"}
    session.actors["fixture-actor"] = {
        "name": "Fixture Person",
        "alive": True,
        "_secret": "hidden-actor-value",
    }
    session.commands["fixture-command"] = {
        "id": "fixture-command",
        "kind": "dialogue.chunk",
        "state": "queued",
        "payload": {"text": "Visible fixture reply."},
        "_hidden": "hidden-command-value",
    }
    session.trace.append(
        {
            "memory_ids": ["fixture-memory"],
            "context_bytes": 120,
            "api_key": "must-not-appear",
            "accidental_snippet": "hidden-trace-value",
        }
    )
    response = await client.get(f"/v1/admin/sessions/{session.id}/diagnostics")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["actors"][0]["actor_id"] == "fixture-actor"
    assert data["commands"][0]["payload"]["text"] == "Visible fixture reply."
    assert data["trace"][0]["memory_ids"] == ["fixture-memory"]
    assert data["game_verified"] is False
    serialized = json.dumps(data)
    for secret in (
        session.token,
        "hidden-world-value",
        "hidden-actor-value",
        "hidden-command-value",
        "hidden-trace-value",
        "must-not-appear",
    ):
        assert secret not in serialized
    # Synthetic commands need an ordinary scene payload during session teardown.
    session.commands.clear()
    session.actors.clear()


async def test_bridge_identity_cannot_open_administrator_diagnostics(client):
    session = await open_profile(client)
    response = await client.get(
        f"/v1/admin/sessions/{session.id}/diagnostics",
        headers={"Authorization": "Bearer " + session.token},
    )
    assert response.status_code == 401
    assert (await client.get("/v1/admin/sessions/missing/diagnostics")).status_code == 404
