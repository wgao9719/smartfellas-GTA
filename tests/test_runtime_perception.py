import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from service.lls.__main__ import fixture_event, fixture_session
from service.lls.app import create_app
from service.lls.display import DISPLAY_MAX_CHARS, is_display_safe
from service.lls.perception_bridge import DEVELOPMENT_PERCEPTION_CAPABILITY
from service.lls.providers import DisabledProvider
from service.lls.settings import Settings
from service.lls.triggers import TriggerEngine, TriggerPack


class Clock:
    def __init__(self):
        self.value = 100.0
        self.wall = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        self.sequence = 0

    def monotonic(self):
        return self.value

    def utc_now(self):
        return self.wall

    def identity(self, prefix):
        self.sequence += 1
        return f"{prefix}_{self.sequence}"


@pytest.fixture
async def scene(tmp_path):
    clock = Clock()
    app = create_app(
        Settings(
            database_path=str(tmp_path / "perception.db"),
            provider="fake",
            release=False,
            dev_perception=True,
        ),
        runtime_options={
            "clock": clock.monotonic,
            "utc_now": clock.utc_now,
            "id_factory": clock.identity,
        },
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 11)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            body = fixture_session()
            body["manifest"]["capability_flags"].append(DEVELOPMENT_PERCEPTION_CAPABILITY)
            opened = (await client.post("/v1/bridge/sessions", json=body)).json()
            client.headers["Authorization"] = "Bearer " + opened["token"]
            session = app.state.runtime.sessions[opened["session_id"]]
            world = fixture_event(
                session.id,
                1,
                "world.snapshot",
                {
                    "online": False,
                    "mission_active": False,
                    "cutscene_active": False,
                    "random_event_active": False,
                    "paused": False,
                    "protagonist_id": "franklin",
                },
            )
            actor = fixture_event(
                session.id,
                2,
                "actor.snapshot",
                {
                    "runtime_handle": 42,
                    "runtime_instance_id": "ped-1",
                    "ownership": "ambient",
                    "control_policy": "ambient",
                    "exists": True,
                    "alive": True,
                    "distance": 2,
                    "mission_entity": False,
                    "busy": False,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "name": "Neighbor",
                    "area_id": "block",
                    "interior_id": "street",
                },
                "npc-1",
            )
            response = await client.post(
                "/v1/bridge/events:batch", json={"session_id": session.id, "events": [world, actor]}
            )
            assert response.status_code == 200, response.text
            yield client, app.state.runtime, session, clock


def batch(session, clock, **overrides):
    result = {
        "session_id": session.id,
        "runtime_generation": 1,
        "protagonist_id": "franklin",
        "seq": session.last_seq + 1,
        "game_datetime": session.game_datetime,
        "scene_id": "perception-scene",
        "incident": {
            "event_id": "incident-1",
            "event_type": "world.weather_changed",
            "sampled_at": clock.wall.isoformat(),
            "position": {"x": 0, "y": 0, "z": 0},
            "area_id": "block",
            "interior_id": "street",
            "text": "Rain began near the shop.",
            "modality": "visual",
            "confidence": 0.9,
        },
        "witnesses": [
            {
                "actor_id": "npc-1",
                "runtime_instance_id": "ped-1",
                "sampled_at": clock.wall.isoformat(),
                "position": {"x": 0, "y": 0, "z": 0},
                "area_id": "block",
                "interior_id": "street",
                "exists": True,
                "alive": True,
                "line_of_sight": True,
                "within_view": True,
                "can_hear": True,
                "confidence": 0.9,
            }
        ],
    }
    result.update(overrides)
    return result


def memories(runtime, session, actor="npc-1", protagonist="franklin"):
    return runtime.repo.retrieve_memories(
        session.request.profile_id,
        session.branch_id,
        actor,
        protagonist,
        session.game_datetime,
        "rain",
        session_id=session.id,
    )


async def add_second_actor(client, session):
    actor = copy.deepcopy(session.actors["npc-1"])
    actor = {k: v for k, v in actor.items() if not k.startswith("_") and k != "allowed_actions"}
    actor.update(runtime_handle=43, runtime_instance_id="ped-2", name="Other")
    event = fixture_event(session.id, session.last_seq + 1, "actor.snapshot", actor, "npc-2")
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )
    assert response.status_code == 200, response.text


def engine(clock, *effects):
    return TriggerEngine(
        TriggerPack.model_validate(
            {
                "pack_id": "test-reactions",
                "rules": [
                    {
                        "id": f"rule-{i}",
                        "match": {"event_types": ["world.weather_changed", "actor.spoke"]},
                        "probability": 1.0,
                        "cooldown_seconds": 0.0,
                        "effect": effect,
                    }
                    for i, effect in enumerate(effects)
                ],
            }
        ),
        clock=clock.monotonic,
        random_value=lambda: 0.0,
    )


def test_development_perception_is_disabled_by_default_and_forbidden_in_release():
    assert not Settings().dev_perception
    with pytest.raises(ValidationError, match="release builds refuse development perception"):
        Settings(dev_perception=True)


@pytest.mark.parametrize("gate", ["settings", "capability"])
async def test_network_perception_requires_both_development_gates(scene, gate):
    client, runtime, session, clock = scene
    if gate == "settings":
        runtime.settings.dev_perception = False
    else:
        session.request.manifest.capability_flags.clear()
    response = await client.post("/v1/bridge/perception", json=batch(session, clock))
    assert response.status_code == 403
    assert not memories(runtime, session)


async def test_perception_persists_only_actual_witness_and_deduplicates_incident(scene):
    client, runtime, session, clock = scene
    await add_second_actor(client, session)
    body = batch(session, clock)
    response = await client.post("/v1/bridge/perception", json=body)
    assert response.status_code == 200, response.text
    assert len(response.json()["accepted_event_ids"]) == 1
    assert not response.json()["duplicate"]
    recalled = memories(runtime, session)
    assert len(recalled) == 1
    assert recalled[0]["provenance"] == "directly_observed"
    assert recalled[0]["text"] == "I observed: Rain began near the shop."
    assert not memories(runtime, session, actor="npc-2")
    assert not memories(runtime, session, protagonist="michael")
    repeated = await client.post("/v1/bridge/perception", json=body)
    assert repeated.json()["duplicate"]
    assert len(memories(runtime, session)) == 1
    body["incident"]["text"] = "Different source claim."
    assert (await client.post("/v1/bridge/perception", json=body)).status_code == 409
    assert "Rain began" not in json.dumps(list(session.trace))
    assert runtime.trigger_engine is None
    assert not session.commands


@pytest.mark.parametrize(
    "fault",
    [
        "future_incident",
        "stale_incident",
        "future_game",
        "stale_game",
        "stale_witness",
        "future_witness",
        "binding",
        "position",
        "area",
        "interior",
        "life",
        "protagonist",
        "generation",
        "sequence",
        "unknown_witness",
        "unknown_source",
        "world",
    ],
)
async def test_perception_rejects_unbound_or_stale_attestations(scene, fault):
    client, runtime, session, clock = scene
    body = batch(session, clock)
    witness = body["witnesses"][0]
    if fault in {"future_incident", "stale_incident"}:
        body["incident"]["sampled_at"] = (
            clock.wall + timedelta(seconds=1 if fault == "future_incident" else -3)
        ).isoformat()
    elif fault in {"future_game", "stale_game"}:
        body["game_datetime"] = (
            datetime.fromisoformat(session.game_datetime)
            + timedelta(seconds=1 if fault == "future_game" else -1)
        ).isoformat()
    elif fault in {"stale_witness", "future_witness"}:
        witness["sampled_at"] = (
            clock.wall + timedelta(seconds=-3 if fault == "stale_witness" else 1)
        ).isoformat()
    elif fault == "binding":
        witness["runtime_instance_id"] = "replacement-ped"
    elif fault == "position":
        witness["position"]["x"] = 100
    elif fault == "area":
        witness["area_id"] = "other-area"
    elif fault == "interior":
        witness["interior_id"] = "other-interior"
    elif fault == "life":
        witness["alive"] = False
    elif fault == "protagonist":
        body["protagonist_id"] = "michael"
    elif fault == "generation":
        body["runtime_generation"] = 2
    elif fault == "sequence":
        body["seq"] = 2
    elif fault == "unknown_witness":
        witness["actor_id"] = "not-loaded"
    elif fault == "unknown_source":
        body["incident"]["source_actor_id"] = "not-loaded"
    else:
        session.world["mission_active"] = True
    response = await client.post("/v1/bridge/perception", json=body)
    assert response.status_code == 409, response.text
    assert not memories(runtime, session)
    assert not session.commands


async def test_unknown_visibility_and_other_incident_locations_do_not_create_memories(scene):
    client, runtime, session, clock = scene
    body = batch(session, clock)
    body["witnesses"][0]["line_of_sight"] = None
    result = await client.post("/v1/bridge/perception", json=body)
    assert result.status_code == 200
    assert result.json()["accepted_event_ids"] == []
    body = batch(session, clock)
    body["incident"]["event_id"] = "incident-2"
    body["incident"]["interior_id"] = "inside-shop"
    result = await client.post("/v1/bridge/perception", json=body)
    assert result.json()["accepted_event_ids"] == []
    assert not memories(runtime, session)


async def test_trigger_effects_keep_private_scope_and_reauthorize_actions(scene):
    client, runtime, session, clock = scene
    runtime.trigger_engine = engine(
        clock,
        {"type": "knowledge_write"},
        {"type": "speak_to_player", "template": "It started raining."},
        {"type": "suggested_action", "action": {"action_id": "follow_player", "arguments": {}}},
    )
    body = batch(session, clock)
    result = await client.post("/v1/bridge/perception", json=body)
    assert result.status_code == 200, result.text
    knowledge = runtime.repo.retrieve_knowledge(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        session.game_datetime,
        session_id=session.id,
    )
    assert len(knowledge) == 1
    assert knowledge[0]["owner_id"] == "npc-1"
    assert knowledge[0]["scope"] == "actor"
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())
    speech = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    assert len(speech) == 1
    assert speech[0]["_autonomous"]
    assert "It started raining." not in [m["text"] for m in memories(runtime, session)]
    diagnostic = (await client.get("/v1/diagnostics")).json()
    assert diagnostic["triggers"]["native_perception_verified"] is False
    assert diagnostic["perception"]["native_verified"] is False


@pytest.mark.parametrize(
    ("template", "evidence", "expected"),
    [
        (
            "Look: {evidence} ~r~Wow~s~<br>",
            "Rain <b>began</b>~n~near the shop.",
            "Look: Rain began near the shop. Wow",
        ),
        ("{evidence}", "word " * 199 + "end.", ("word " * 100).strip()),
    ],
)
async def test_trigger_remarks_are_display_safe_and_bounded(scene, template, evidence, expected):
    client, runtime, session, clock = scene
    runtime.trigger_engine = engine(clock, {"type": "speak_to_player", "template": template})
    body = batch(session, clock)
    body["incident"]["text"] = evidence
    assert (await client.post("/v1/bridge/perception", json=body)).status_code == 200
    remark = next(c for c in session.commands.values() if c.get("_autonomous"))["payload"]
    assert remark["text"] == expected and is_display_safe(expected)
    assert len(expected) <= DISPLAY_MAX_CHARS


async def test_foreground_speech_suppresses_automatic_remark(scene):
    client, runtime, session, clock = scene
    runtime.trigger_engine = engine(
        clock, {"type": "speak_to_player", "template": "Automatic remark."}
    )
    event = fixture_event(
        session.id,
        3,
        "player.utterance_text",
        {"text": "Hello.", "scene_id": "player-scene"},
        "npc-1",
    )
    result = await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )
    assert result.status_code == 200
    await session.task
    result = await client.post("/v1/bridge/perception", json=batch(session, clock))
    assert result.status_code == 200
    assert not any(c.get("_autonomous") for c in session.commands.values())
    assert runtime.trigger_engine.diagnostics()["counters"]["suppressed"] == 1


async def test_player_turn_cancels_automatic_remark_before_generation(scene):
    client, runtime, session, clock = scene
    runtime.trigger_engine = engine(
        clock, {"type": "speak_to_player", "template": "Automatic remark."}
    )
    assert (
        await client.post("/v1/bridge/perception", json=batch(session, clock))
    ).status_code == 200
    remark = next(c for c in session.commands.values() if c.get("_autonomous"))
    event = fixture_event(
        session.id,
        4,
        "player.utterance_text",
        {"text": "Hello.", "scene_id": "player-scene"},
        "npc-1",
    )
    result = await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )
    assert result.status_code == 200, result.text
    await session.task
    assert remark["state"] == "canceled"
    assert any(c["kind"] == "scene.cancel" for c in session.commands.values())
    assert any(
        c["kind"] == "dialogue.chunk" and not c.get("_autonomous")
        for c in session.commands.values()
    )


async def test_report_requires_actual_delivered_quote_and_stays_reported(scene):
    client, runtime, session, clock = scene
    await add_second_actor(client, session)
    runtime.trigger_engine = engine(
        clock,
        {"type": "knowledge_write"},
        {"type": "speak_to_player", "template": "Forwarding rumor."},
    )
    event = fixture_event(
        session.id,
        4,
        "player.utterance_text",
        {"text": "Tell me about rain.", "scene_id": "player-scene"},
        "npc-1",
    )
    assert (
        await client.post(
            "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
        )
    ).status_code == 200
    await session.task
    command = next(
        c
        for c in session.commands.values()
        if c["kind"] == "dialogue.chunk" and c["payload"]["text"]
    )
    body = batch(session, clock)
    body["incident"].update(
        event_id=command["id"] + ":delivered",
        event_type="actor.spoke",
        modality="report",
        source_actor_id="npc-1",
        reported_event_id=event["event_id"],
        text=command["payload"]["text"],
    )
    body["witnesses"][0].update(actor_id="npc-2", runtime_instance_id="ped-2")
    assert (await client.post("/v1/bridge/perception", json=body)).status_code == 409
    ack = {
        "session_id": session.id,
        "runtime_generation": 1,
        "status": "completed",
        "observed": {"delivery": "subtitle", "text": command["payload"]["text"]},
    }
    assert (
        await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    ).status_code == 200
    altered = copy.deepcopy(body)
    altered["incident"]["text"] = "A quote nobody delivered."
    assert (await client.post("/v1/bridge/perception", json=altered)).status_code == 409
    result = await client.post("/v1/bridge/perception", json=body)
    assert result.status_code == 200, result.text
    recalled = memories(runtime, session, actor="npc-2")
    assert len(recalled) == 1
    assert recalled[0]["provenance"] == "reported"
    assert recalled[0]["text"].startswith("I heard a report from npc-1:")
    knowledge = runtime.repo.retrieve_knowledge(
        "fake-demo",
        session.branch_id,
        "npc-2",
        "franklin",
        session.game_datetime,
        session_id=session.id,
    )
    assert knowledge[0]["provenance"] == "reported"
    assert not any(c.get("_autonomous") for c in session.commands.values())


async def test_clock_injection_controls_authorization_and_expiry(scene):
    client, runtime, session, clock = scene
    assert session.id == "ses_1"
    assert session.last_activity == 100
    command = runtime.command(session, "status", None, {"state": "test", "message": "Test"})
    assert command["id"] == "cmd_2"
    assert datetime.fromisoformat(command["expires_at"]) == clock.wall + timedelta(seconds=15)
    clock.wall += timedelta(seconds=15)
    runtime.expire_commands(session)
    assert command["state"] == "expired"
    clock.value += 6
    assert not runtime.conversation_policy.check(session.actors["npc-1"], session.world).allowed
    clock.value += 25
    await runtime.expire_sessions()
    assert session.closed


async def test_authored_trigger_delivery_persists_with_model_provider_disabled(scene):
    client, runtime, session, clock = scene
    runtime.provider = DisabledProvider()
    runtime.trigger_engine = engine(
        clock, {"type": "speak_to_player", "template": "It started raining."}
    )
    result = await client.post("/v1/bridge/perception", json=batch(session, clock))
    assert result.status_code == 200
    command = next(c for c in session.commands.values() if c.get("_autonomous"))
    ack = {
        "session_id": session.id,
        "runtime_generation": 1,
        "status": "completed",
        "observed": {"delivery": "subtitle", "text": command["payload"]["text"]},
    }
    response = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    assert response.status_code == 200
    assert any(m["text"] == "I said: It started raining." for m in memories(runtime, session))
    assert command["id"] + ":delivered" in session.delivered_utterances


async def test_delayed_delivery_receipt_does_not_refresh_report_eligibility(scene):
    client, runtime, session, clock = scene
    command = runtime.command(
        session,
        "dialogue.chunk",
        "npc-1",
        {
            "text": "It started raining.",
            "speaker": "Neighbor",
            "scene_id": "report-scene",
            "turn_id": "report-turn",
            "chunk_index": 0,
            "final": True,
        },
    )
    created_at = clock.wall
    clock.wall += timedelta(seconds=3)
    ack = {
        "session_id": session.id,
        "runtime_generation": 1,
        "status": "completed",
        "observed": {"delivery": "subtitle", "text": command["payload"]["text"]},
    }
    assert (
        await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    ).status_code == 200
    receipt = session.delivered_utterances[command["id"] + ":delivered"]
    assert receipt.sampled_at == created_at
    body = batch(session, clock)
    body["incident"].update(
        event_id=receipt.event_id,
        event_type="actor.spoke",
        modality="report",
        source_actor_id="npc-1",
        reported_event_id="claimed-topic",
        text=receipt.text,
    )
    assert (await client.post("/v1/bridge/perception", json=body)).status_code == 409
