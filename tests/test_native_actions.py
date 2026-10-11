import copy
import json

import httpx
import pytest

from service.lls.actions import ActionPolicy
from service.lls.context import build_context
from service.lls.protocol import ActionProposal, ActorSnapshot, VoiceControl
from tests import test_runtime_speech as speech_fixtures
from tests.test_context import context_inputs

speech_scene = speech_fixtures.speech_scene


def environment():
    actor = {
        "_sampled_at": 100, "exists": True, "alive": True, "busy": False,
        "in_combat": False, "distance": 3, "ownership": "ambient",
        "control_policy": "ambient", "mission_entity": False, "can_move": True,
        "position": {"x": 0, "y": 0, "z": 0}, "has_firearm": True,
        "action_context": {"targets": [
            {"target_id": "player", "kind": "ped", "is_threat": True},
            {"target_id": "car", "kind": "vehicle", "is_threat": False,
             "driver_seat_free": False, "passenger_seat_free": True},
        ]},
    }
    world = dict.fromkeys(
        ("online", "mission_active", "cutscene_active", "random_event_active", "paused"), False
    )
    world["_sampled_at"] = 100
    return ActionPolicy(clock=lambda: 100), actor, world


@pytest.mark.parametrize("action,arguments", [
    ("follow_player", {}), ("wander_nearby", {"radius": 5}),
    ("go_to_safe_point", {"point": {"x": 3, "y": 0, "z": 0}}),
    ("run_to_point", {"point": {"x": 3, "y": 0, "z": 0}}),
    ("look_at", {"target_id": "player"}), ("hands_up", {}), ("cower", {}),
    ("play_verified_gesture", {"gesture_id": "wave"}),
    ("flee_from_threat", {"threat_actor_id": "player"}),
    ("enter_vehicle", {"target_id": "car", "seat": "passenger"}),
    ("fight_target", {"target_id": "player"}),
    ("shoot_at_target", {"target_id": "player"}),
])
def test_parameterized_native_actions_are_offered_and_authorized(action, arguments):
    policy, actor, world = environment()
    assert policy.eligible(actor, world, [action], True) == [action]
    proposal = ActionProposal(action_id=action, arguments=arguments)
    assert policy.check(proposal, actor, world, [action], True).allowed
    assert not policy.check(proposal, actor, world, [], True).allowed


@pytest.mark.parametrize("action,arguments,mutation,reason", [
    ("enter_vehicle", {"target_id": "car", "seat": "driver"}, {}, "seat_unavailable"),
    ("fight_target", {"target_id": "invented"}, {}, "target_unavailable"),
    ("shoot_at_target", {"target_id": "player"}, {"has_firearm": False}, "no_equipped_firearm"),
    ("look_at", {"target_id": "car"}, {}, "target_unavailable"),
    ("flee_from_threat", {"threat_actor_id": "car"}, {}, "target_unavailable"),
    ("go_to_safe_point", {"point": {"x": 16, "y": 0, "z": 0}}, {}, "point_outside_local_area"),
    ("follow_player", {}, {"can_move": False}, "actor_cannot_move"),
    ("follow_player", {}, {"distance": 16}, "actor_out_of_range"),
    ("follow_player", {}, {"_sampled_at": 94}, "stale_actor"),
])
def test_native_action_rechecks_current_state(action, arguments, mutation, reason):
    policy, actor, world = environment()
    actor.update(mutation)
    proposal = ActionProposal(action_id=action, arguments=arguments)
    assert policy.check(proposal, actor, world, [action], True).reason == reason


def test_consent_vehicle_state_and_follow_ownership_gate_selection():
    policy, actor, world = environment()
    capabilities = ["follow_player", "fight_target", "enter_vehicle", "stop_following"]
    assert policy.eligible(actor, world, capabilities, False) == []
    actor["mod_following"] = True
    assert policy.eligible(actor, world, capabilities, False) == ["stop_following"]
    actor.update(in_vehicle=True, is_driver=False, vehicle_stopped=True, can_move=False)
    vehicle_actions = ["leave_vehicle", "drive_to_point", "cruise_vehicle", "wait_here"]
    assert policy.eligible(actor, world, vehicle_actions, True) == ["leave_vehicle"]
    actor.update(is_driver=True, vehicle_stopped=False)
    assert policy.eligible(actor, world, vehicle_actions, True) == [
        "drive_to_point", "cruise_vehicle"
    ]


def test_snapshot_target_contract_is_bounded_and_voice_consent_defaults_off():
    assert VoiceControl(scene_id="s", runtime_instance_id="i").consent is False
    assert VoiceControl(scene_id="s", runtime_instance_id="i", consent=True).consent is True
    schema = ActorSnapshot.model_json_schema()
    assert schema["$defs"]["ActionContext"]["properties"]["targets"]["maxItems"] == 8


def test_context_truncation_does_not_mutate_live_target_evidence():
    values = context_inputs()
    targets = [{"target_id": str(i), "name": "x" * 128, "position": {"x": 0, "y": 1, "z": 2}}
               for i in range(8)]
    values["snapshot"]["action_context"] = {"targets": targets}
    before = copy.deepcopy(targets)
    context, _ = build_context(**values)
    assert context["context_truncated"]
    assert len(context["action_context"]["targets"]) < 8
    assert targets == before


async def test_model_receives_observed_targets_and_selects_a_real_task():
    from service.lls.outbound import OutboundGate
    from service.lls.providers import OpenAIProvider
    from service.lls.settings import Settings
    from service.lls.storage import Repository

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "action_id": "enter_vehicle", "arguments": {"target_id": "car", "seat": "passenger"}
        })}}]})

    settings = Settings(provider="local_openai", dialogue_model="local", action_model="local")
    repo = Repository(":memory:")
    try:
        provider = OpenAIProvider(
            settings, gate=OutboundGate(settings, repo), transport=httpx.MockTransport(handle)
        )
        _, actor, _ = environment()
        proposal = await provider.action(
            {"input": "Get in the car", "action_context": actor["action_context"]},
            "I'll get in.", ["enter_vehicle"],
        )
        assert proposal.action_id == "enter_vehicle"
        content = json.loads(requests[0]["messages"][1]["content"])
        assert content["action_context"]["targets"][1]["target_id"] == "car"
        assert "free seat" in content["actions"]["enter_vehicle"]
    finally:
        repo.close()


async def test_voice_action_consent_is_locked_to_capture(speech_scene):
    from service.lls.__main__ import fixture_event

    async with speech_scene() as (client, runtime, session, microphone, transcriber, *rest):
        transcriber.confidence = 0.9
        async def control(kind, consent):
            event = fixture_event(session.id, session.last_seq + 1, kind, {
                "scene_id": "voice-actions", "runtime_instance_id": "ped-1", "consent": consent,
            }, "npc-1")
            return await client.post("/v1/bridge/control", json=event)

        assert (await control("voice.ptt_started", True)).status_code == 200
        await session.voice_task
        assert session.speech_consent is True
        assert (await control("voice.ptt_stopped", False)).status_code == 409
        assert (await control("voice.ptt_stopped", True)).status_code == 200
        await session.voice_task
        if session.task:
            await session.task
        commands = [c for c in session.commands.values() if c["kind"] == "action.execute"]
        assert commands and commands[0]["payload"]["consent"] is True


async def test_new_action_crosses_authenticated_service_boundary(tmp_path):
    from service.lls.__main__ import fixture_event, fixture_role_fields, fixture_session
    from service.lls.app import create_app
    from service.lls.providers import FakeProvider
    from service.lls.settings import Settings

    class TaskProvider(FakeProvider):
        async def action(self, context, utterance, eligible):
            assert "enter_vehicle" in eligible
            target = context["action_context"]["targets"][0]["target_id"]
            return ActionProposal(action_id="enter_vehicle", arguments={
                "target_id": target, "seat": "passenger", "duration_seconds": 5,
            })

    app = create_app(Settings(database_path=str(tmp_path / "tasks.db"), provider="fake"),
                     provider=TaskProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1:8765") as client:
            request = fixture_session()
            request["capabilities"] = ["enter_vehicle"]
            opened = await client.post("/v1/bridge/sessions", json=request)
            assert opened.status_code == 200, opened.text
            session_id = opened.json()["session_id"]
            client.headers["Authorization"] = "Bearer " + opened.json()["token"]
            events = [
                fixture_event(session_id, 1, "world.snapshot", {
                    "online": False, "mission_active": False, "cutscene_active": False,
                    "random_event_active": False, "paused": False, "protagonist_id": "franklin",
                }),
                fixture_event(session_id, 2, "actor.snapshot", {
                    "runtime_handle": 42, "runtime_instance_id": "ped-1", "ownership": "ambient",
                    "control_policy": "ambient", "exists": True, "alive": True, "distance": 2,
                    "mission_entity": False, "busy": False, "position": {"x": 0, "y": 0, "z": 0},
                    "name": "Neighbor", **fixture_role_fields(),
                    "action_context": {"targets": [{
                        "target_id": "car-1", "kind": "vehicle", "name": "Car",
                        "position": {"x": 2, "y": 0, "z": 0}, "passenger_seat_free": True,
                    }]},
                }, "npc-1"),
                fixture_event(session_id, 3, "player.utterance_text", {
                    "text": "Get in the passenger seat", "scene_id": "tasks", "consent": True,
                }, "npc-1"),
            ]
            response = await client.post("/v1/bridge/events:batch", json={
                "session_id": session_id, "events": events,
            })
            assert response.status_code == 200, response.text
            session = app.state.runtime.sessions[session_id]
            await session.task
            commands = [c for c in session.commands.values() if c["kind"] == "action.execute"]
            assert len(commands) == 1
            assert commands[0]["runtime_instance_id"] == "ped-1"
            assert commands[0]["payload"]["action_id"] == "enter_vehicle"
            assert commands[0]["payload"]["arguments"]["target_id"] == "car-1"
            assert commands[0]["payload"]["consent"] is True


def test_gesture_unknown_name_rejected_by_shared_registry():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="allowlisted"):
        ActionProposal(action_id="play_verified_gesture", arguments={"gesture_id": "unknown"})
