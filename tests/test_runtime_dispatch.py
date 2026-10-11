"""Queued authorization must still hold when the bridge polls for delivery."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from service.lls.__main__ import fixture_actor_snapshot, fixture_event, fixture_session
from service.lls.actions import POLICY_VERSION
from service.lls.app import create_app
from service.lls.roles import joaat
from service.lls.settings import Settings


@pytest.fixture
async def rig(tmp_path):
    clock = SimpleNamespace(value=100.0, wall=datetime(2026, 10, 10, tzinfo=timezone.utc))
    app = create_app(
        Settings(database_path=str(tmp_path / "dispatch.db"), provider="fake"),
        runtime_options={"clock": lambda: clock.value, "utc_now": lambda: clock.wall},
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            body = fixture_session()
            body["capabilities"].append("follow_player")
            opened = await client.post("/v1/bridge/sessions", json=body)
            assert opened.status_code == 200
            client.headers["Authorization"] = "Bearer " + opened.json()["token"]
            runtime = app.state.runtime
            session = runtime.sessions[opened.json()["session_id"]]
            events = [
                fixture_event(session.id, 1, "world.snapshot", {
                    "online": False,
                    "mission_active": False,
                    "cutscene_active": False,
                    "random_event_active": False,
                    "paused": False,
                    "protagonist_id": "franklin",
                }),
                fixture_event(session.id, 2, "actor.snapshot", fixture_actor_snapshot(), "npc-1"),
            ]
            response = await client.post(
                "/v1/bridge/events:batch", json={"session_id": session.id, "events": events}
            )
            assert response.status_code == 200, response.text
            yield SimpleNamespace(
                client=client, runtime=runtime, session=session, repo=app.state.repository,
                clock=clock,
            )


def action(rig, *, action_id="face_player", consent=False):
    return rig.runtime.command(
        rig.session, "action.execute", "npc-1", {
            "action_id": action_id,
            "arguments": (
                {"max_follow_meters": 5} if action_id == "follow_player"
                else {"duration_seconds": 1}
            ),
            "policy_version": POLICY_VERSION,
            "scene_id": "dispatch-scene",
            "turn_id": "dispatch-turn",
            "consent": consent,
        },
    )


async def poll(rig):
    response = await rig.client.get(
        "/v1/bridge/commands", params={"session_id": rig.session.id, "wait_seconds": 0}
    )
    assert response.status_code == 200, response.text
    return response.json()["commands"]


@pytest.mark.parametrize("change", [
    "stored_policy", "stored_capability", "snapshot_policy", "actor_busy", "actor_stale",
    "world_stale", "mission_active", "role_facts", "ownership_facts", "actor_instance",
    "missing_actor", "policy_version", "generation", "session", "epoch", "branch",
    "protagonist", "adapter_capability", "actor_capability",
])
async def test_poll_revokes_changed_authorization_and_records_reason(rig, change):
    command = action(rig)
    session = rig.session
    actor = session.actors["npc-1"]
    if change.startswith("stored_"):
        stored = rig.repo.get_actor("fake-demo", "npc-1", session_id=session.id)
        stored["control_policy" if change == "stored_policy" else "allowed_actions"] = (
            "protected" if change == "stored_policy" else []
        )
        rig.repo.register_actor("fake-demo", stored)
    elif change == "snapshot_policy":
        actor["control_policy"] = "protected"
    elif change == "actor_busy":
        actor["busy"] = True
    elif change == "actor_stale":
        actor["_sampled_at"] -= 6
    elif change == "world_stale":
        session.world["_sampled_at"] -= 6
    elif change == "mission_active":
        session.world["mission_active"] = True
    elif change == "role_facts":
        actor["ped_facts"]["model_hash"] = joaat("s_m_y_cop_01")
    elif change == "ownership_facts":
        actor["ped_facts"]["mission_entity"] = True
    elif change == "actor_instance":
        actor["runtime_instance_id"] = "replacement-ped"
    elif change == "missing_actor":
        session.actors.pop("npc-1")
    elif change == "policy_version":
        command["payload"]["policy_version"] = "old-policy"
    elif change == "generation":
        session.request.runtime_generation += 1
    elif change == "session":
        command["session_id"] = "old-session"
    elif change == "epoch":
        session.epoch += 1
    elif change == "branch":
        command["_branch_id"] = "old-branch"
    elif change == "protagonist":
        session.world["protagonist_id"] = "michael"
    elif change == "adapter_capability":
        session.request.capabilities = []
    elif change == "actor_capability":
        actor["allowed_actions"] = []
    assert not any(c["id"] == command["id"] for c in await poll(rig))
    assert command["state"] == "canceled"
    assert command["reason"]
    assert session.action_outcomes[-1]["authority"] == "service"
    assert session.action_outcomes[-1]["native_completion_confirmed"] is False
    event_id = command["id"] + ":service:canceled"
    event = rig.repo.connection.execute(
        "SELECT data FROM events WHERE profile_id=? AND event_id=?", ("fake-demo", event_id)
    ).fetchone()
    assert json.loads(event[0])["payload"]["reason"] == command["reason"]
    assert not any(c["id"] == command["id"] for c in await poll(rig))
    assert len(session.action_outcomes) == 1


async def test_expired_command_is_not_delivered(rig):
    command = action(rig)
    rig.clock.wall += timedelta(seconds=16)
    assert not any(c["id"] == command["id"] for c in await poll(rig))
    assert command["state"] == "expired"


@pytest.mark.parametrize("consent", [False, True])
async def test_poll_rechecks_movement_consent(rig, consent):
    command = action(rig, action_id="follow_player", consent=consent)
    assert any(c["id"] == command["id"] for c in await poll(rig)) is consent
    assert command["state"] == ("queued" if consent else "canceled")


async def test_unchanged_action_can_be_polled_repeatedly(rig):
    command = action(rig)
    first = await poll(rig)
    assert any(c["id"] == command["id"] for c in first)
    assert await poll(rig) == first
    assert command["state"] == "queued"
    assert not rig.session.action_outcomes


async def test_actual_provider_action_is_rechecked_after_queueing(rig):
    event = fixture_event(rig.session.id, 3, "player.utterance_text", {
        "text": "Please look at me.", "scene_id": "foreground-scene",
    }, "npc-1")
    response = await rig.client.post(
        "/v1/bridge/events:batch", json={"session_id": rig.session.id, "events": [event]}
    )
    assert response.status_code == 200, response.text
    await rig.session.task
    command = next(c for c in rig.session.commands.values() if c["kind"] == "action.execute")
    rig.session.actors["npc-1"]["allowed_actions"] = []
    assert not any(c["id"] == command["id"] for c in await poll(rig))
    assert command["state"] == "canceled"


async def test_permissions_are_rechecked_after_outcome_persistence(rig, monkeypatch):
    expired = action(rig)
    command = action(rig)
    expired["expires_at"] = rig.clock.wall.isoformat()
    original = rig.runtime.flush_outcomes

    async def flush(session):
        await original(session)
        session.actors["npc-1"]["allowed_actions"] = []

    monkeypatch.setattr(rig.runtime, "flush_outcomes", flush)
    assert not any(c["id"] == command["id"] for c in await poll(rig))
    assert command["state"] == "canceled"
    assert command["reason"] == "capability_unavailable"


async def test_mod_owned_curated_actor_remains_eligible_with_mission_flag(rig):
    rig.repo.register_actor("fake-demo", {
        "actor_id": "curated", "control_policy": "ambient",
        "allowed_actions": ["face_player"], "display_name": "Avery",
    })
    snapshot = fixture_actor_snapshot(
        actor_kind="curated_persistent", ownership="mod_owned", mission_entity=True,
        runtime_handle=43, runtime_instance_id="curated-ped",
    )
    snapshot["ped_facts"].update(
        mission_entity=True, in_mod_registry=True,
        took_persistence=True, belongs_to_mod_script=True,
    )
    event = fixture_event(rig.session.id, 3, "actor.snapshot", snapshot, "curated")
    response = await rig.client.post(
        "/v1/bridge/events:batch", json={"session_id": rig.session.id, "events": [event]}
    )
    assert response.status_code == 200, response.text
    command = rig.runtime.command(rig.session, "action.execute", "curated", {
        "action_id": "face_player", "arguments": {}, "policy_version": POLICY_VERSION,
        "scene_id": "curated-scene", "turn_id": "curated-turn", "consent": False,
    })
    assert any(c["id"] == command["id"] for c in await poll(rig))
    assert command["state"] == "queued"
