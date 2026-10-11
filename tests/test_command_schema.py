import pytest
from pydantic import ValidationError

from service.lls.actions import POLICY_VERSION
from service.lls.protocol import Command
from tests.test_bridge import client as client
from tests.test_bridge import open_ready


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("status", {"state": "ready", "message": "Ready."}),
        ("scene.cancel", {"scene_id": "scene-1"}),
        (
            "dialogue.chunk",
            {
                "text": "Hello.", "speaker": "Neighbor", "scene_id": "scene-1",
                "turn_id": "turn-1", "chunk_index": 0, "final": True,
            },
        ),
        (
            "action.execute",
            {
                "action_id": "face_player", "arguments": {}, "policy_version": POLICY_VERSION,
                "scene_id": "scene-1", "consent": True, "turn_id": "turn-1",
            },
        ),
        ("actor.pin", {"display_name": "Neighbor", "actor_kind": "spawned_persistent"}),
    ],
)
async def test_every_command_emits_and_requires_schema_version(client, kind, payload):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    command = runtime.command(runtime.sessions[sid], kind, "npc-1", payload)
    response = await client.get("/v1/bridge/commands?wait_seconds=0")
    assert response.status_code == 200
    wire = next(item for item in response.json()["commands"] if item["id"] == command["id"])
    assert wire.get("schema_version") == 1
    assert Command.model_validate(wire).schema_version == 1
    del wire["schema_version"]
    with pytest.raises(ValidationError, match="schema_version"):
        Command.model_validate(wire)
