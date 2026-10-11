"""Native social commands are gated by the adapter capability and live geometry."""
import pytest
from test_runtime_social import invite, turn
from test_runtime_social import social_scene as social_scene

from service.lls.runtime_social import SOCIAL_EMBODIMENT_CAPABILITY


@pytest.mark.parametrize("embodied", [False, True])
async def test_setup_and_overhead_require_embodiment_capability(social_scene, embodied):
    client, runtime, session, _ = social_scene
    if embodied:
        session.request.manifest.capability_flags.append(SOCIAL_EMBODIMENT_CAPABILITY)
    response = await invite(client, session)
    assert response.status_code == 200, response.text
    setups = [c for c in session.commands.values() if c["kind"] == "social.scene"]
    assert len(setups) == int(embodied)
    if embodied:
        assert setups[0]["payload"]["participants"] == {"npc-a": "ped-0", "npc-b": "ped-1"}
        assert setups[0]["payload"]["duration_seconds"] <= 45
    await turn(client, session)
    line = next(c for c in session.commands.values() if c["kind"] == "dialogue.chunk")
    assert line["payload"].get("presentation") == ("overhead" if embodied else None)


@pytest.mark.parametrize("change", ["pair_distance", "player_distance", "vehicle", "following"])
async def test_native_scene_rejects_unavailable_geometry(social_scene, change):
    client, _, session, _ = social_scene
    session.request.manifest.capability_flags.append(SOCIAL_EMBODIMENT_CAPABILITY)
    actor = session.actors["npc-b"]
    if change == "pair_distance":
        actor["position"]["x"] = 7
    elif change == "player_distance":
        actor["distance"] = 13
    else:
        actor["in_vehicle" if change == "vehicle" else "mod_following"] = True
    response = await invite(client, session)
    assert response.status_code == 409, response.text
    assert not any(c["kind"] == "social.scene" for c in session.commands.values())


async def test_native_setup_rejection_stops_scene(social_scene):
    client, _, session, _ = social_scene
    session.request.manifest.capability_flags.append(SOCIAL_EMBODIMENT_CAPABILITY)
    assert (await invite(client, session)).status_code == 200
    setup = next(c for c in session.commands.values() if c["kind"] == "social.scene")
    result = await client.post(f"/v1/bridge/commands/{setup['id']}/ack", json={
        "session_id": session.id, "runtime_generation": 1,
        "status": "rejected", "reason": "social_native_refused",
    })
    assert result.status_code == 200, result.text
    assert session.social_scenes["social-scene"]["canceled"]


@pytest.mark.parametrize("member", [False, True])
async def test_nearby_actor_removal_only_stops_its_social_scene(social_scene, member):
    from service.lls.__main__ import fixture_event
    from service.lls.protocol import Event

    client, runtime, session, _ = social_scene
    assert (await invite(client, session)).status_code == 200
    epoch = session.epoch
    await runtime.process(session, Event.model_validate(fixture_event(
        session.id, 10, "actor.removed", {}, "npc-b" if member else "npc-c"
    )))
    assert session.social_scenes["social-scene"]["canceled"] is member
    assert session.epoch == epoch
