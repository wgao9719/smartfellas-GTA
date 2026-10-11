import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from service.lls.__main__ import fixture_event, fixture_session
from service.lls.app import create_app
from service.lls.settings import Settings
from service.lls.social import FakeSocialProvider
from service.lls.social_bridge import DEVELOPMENT_SOCIAL_CAPABILITY
from service.lls.social_models import SocialStart


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
async def social_scene(tmp_path):
    clock = Clock()
    app = create_app(
        Settings(
            database_path=str(tmp_path / "social.db"),
            provider="fake", release=False, dev_social=True,
        ),
        runtime_options={
            "clock": clock.monotonic, "utc_now": clock.utc_now, "id_factory": clock.identity,
        },
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 11)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            request = fixture_session()
            request["manifest"]["capability_flags"].append(DEVELOPMENT_SOCIAL_CAPABILITY)
            opened = (await client.post("/v1/bridge/sessions", json=request)).json()
            client.headers["Authorization"] = "Bearer " + opened["token"]
            runtime = app.state.runtime
            session = runtime.sessions[opened["session_id"]]
            events = [fixture_event(session.id, 1, "world.snapshot", {
                "online": False, "mission_active": False, "cutscene_active": False,
                "random_event_active": False, "paused": False, "protagonist_id": "franklin",
            })]
            for i, actor_id in enumerate(("npc-a", "npc-b", "npc-c")):
                runtime.repo.register_actor(session.request.profile_id, {
                    "actor_id": actor_id, "actor_kind": "curated_persistent",
                    "display_name": actor_id, "control_policy": "ambient", "allowed_actions": [],
                    "persona": {"social": {
                        "accept_invitations": True, "opinion": f"{actor_id} prefers quiet streets."
                    }},
                })
                events.append(fixture_event(session.id, i + 2, "actor.snapshot", {
                    "runtime_handle": 42 + i, "runtime_instance_id": f"ped-{i}",
                    "ownership": "ambient", "control_policy": "ambient", "exists": True,
                    "alive": True, "distance": 2 + i, "mission_entity": False, "busy": False,
                    "position": {"x": float(i), "y": 0.0, "z": 0.0}, "name": actor_id,
                    "actor_kind": "curated_persistent", "area_id": "block",
                    "interior_id": "street",
                }, actor_id))
            response = await client.post(
                "/v1/bridge/events:batch", json={"session_id": session.id, "events": events}
            )
            assert response.status_code == 200, response.text
            yield client, runtime, session, clock


def envelope(session, **fields):
    return {
        "session_id": session.id, "runtime_generation": 1, "protagonist_id": "franklin",
        "scene_id": "social-scene", "game_datetime": session.game_datetime, **fields,
    }


async def invite(client, session):
    return await client.post("/v1/bridge/social/invite", json=envelope(
        session, initiator_id="npc-a", participants={"npc-a": "ped-0", "npc-b": "ped-1"}
    ))


async def turn(client, session):
    response = await client.post(
        "/v1/bridge/social/turn", json=envelope(session, actor_id="npc-a")
    )
    assert response.status_code == 200, response.text
    return response.json()


async def acknowledge(client, session, command):
    response = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json={
        "session_id": session.id, "runtime_generation": 1, "status": "completed",
        "observed": {"delivery": "simulated", "text": command["payload"]["text"]},
    })
    assert response.status_code == 200, response.text


def listener(session, clock, **fields):
    actor = session.actors["npc-b"]
    return {
        "actor_id": "npc-b", "runtime_instance_id": "ped-1",
        "sampled_at": clock.wall.isoformat(), "position": actor["position"],
        "area_id": "block", "interior_id": "street", "exists": True, "alive": True,
        "can_hear": True, "confidence": 0.9, **fields,
    }


def heard_memories(runtime, session, actor_id):
    rows = runtime.repo.retrieve_memories(
        session.request.profile_id, session.branch_id, actor_id, "franklin",
        session.game_datetime, "quiet streets", session_id=session.id,
    )
    return [row for row in rows if row["provenance"] == "reported"]


def test_release_refuses_development_social():
    with pytest.raises(ValidationError, match="release builds refuse development social"):
        Settings(dev_social=True)


@pytest.mark.parametrize("gate", ["setting", "capability"])
async def test_social_requires_explicit_development_gate(social_scene, gate):
    client, runtime, session, clock = social_scene
    if gate == "setting":
        runtime.settings.dev_social = False
    else:
        session.request.manifest.capability_flags.remove(DEVELOPMENT_SOCIAL_CAPABILITY)
    assert (await invite(client, session)).status_code == 403
    assert not runtime.social.scenes_for_session(session.id)


async def test_simulated_social_delivery_requires_separate_listener_evidence(social_scene):
    client, runtime, session, clock = social_scene
    response = await invite(client, session)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    reply = await turn(client, session)
    command = session.commands[reply["command_id"]]
    assert not heard_memories(runtime, session, "npc-b")
    delivery = envelope(session, command_id=command["id"], witnesses=[listener(session, clock)])
    assert (await client.post("/v1/bridge/social/delivery", json=delivery)).status_code == 409
    await acknowledge(client, session, command)
    assert not heard_memories(runtime, session, "npc-b")
    response = await client.post("/v1/bridge/social/delivery", json=delivery)
    assert response.status_code == 200, response.text
    heard = heard_memories(runtime, session, "npc-b")
    assert len(heard) == 1
    assert heard[0]["provenance"] == "reported"
    assert command["payload"]["text"] in heard[0]["text"]
    assert not heard_memories(runtime, session, "npc-c")
    repeat = await client.post("/v1/bridge/social/delivery", json=delivery)
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["duplicate"]
    assert len(heard_memories(runtime, session, "npc-b")) == 1
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())


@pytest.mark.parametrize("change", ["unknown_hearing", "binding", "stale"])
async def test_social_rejects_unproved_listener_knowledge(social_scene, change):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    reply = await turn(client, session)
    command = session.commands[reply["command_id"]]
    await acknowledge(client, session, command)
    witness = listener(session, clock)
    if change == "unknown_hearing":
        witness["can_hear"] = None
    elif change == "binding":
        witness["runtime_instance_id"] = "rebound-ped"
    else:
        witness["sampled_at"] = (clock.wall - timedelta(seconds=3)).isoformat()
    response = await client.post("/v1/bridge/social/delivery", json=envelope(
        session, command_id=command["id"], witnesses=[witness]
    ))
    assert response.status_code == (200 if change == "unknown_hearing" else 409), response.text
    assert not heard_memories(runtime, session, "npc-b")


async def test_foreground_input_cancels_and_joins_social_generation(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    started, cleaned = asyncio.Event(), asyncio.Event()

    class Blocked(FakeSocialProvider):
        async def dialogue(self, context):
            if context.get("social", {}).get("phase") == "turn":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            async for value in super().dialogue(context):
                yield value

    runtime.provider.primary = Blocked()
    request = asyncio.create_task(client.post(
        "/v1/bridge/social/turn", json=envelope(session, actor_id="npc-a")
    ))
    await asyncio.wait_for(started.wait(), 1)
    event = fixture_event(session.id, session.last_seq + 1, "player.utterance_text", {
        "text": "Hello", "scene_id": "player-scene",
    }, "npc-a")
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )
    assert response.status_code == 200, response.text
    assert cleaned.is_set()
    assert (await request).status_code == 409
    await session.task
    assert not any(c.get("_social") for c in session.commands.values())


async def test_erasure_joins_social_write_before_clearing_history(social_scene, monkeypatch):
    client, runtime, session, clock = social_scene
    entered, release = threading.Event(), threading.Event()
    original = runtime.social.store.decision

    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(runtime.social.store, "decision", blocked_write)
    request = asyncio.create_task(invite(client, session))
    assert await asyncio.to_thread(entered.wait, 1)

    async def erase():
        async with session.lock:
            runtime.cancel(session)
            await runtime.stop_session_work(session, erase=True)
            await runtime.db("reset_actor", session.request.profile_id, "npc-b")

    erasing = asyncio.create_task(erase())
    await asyncio.sleep(0)
    assert not erasing.done()
    release.set()
    await erasing
    assert (await request).status_code == 409
    assert not runtime.social.scenes_for_session(session.id)
    assert not session.social_scenes
    assert not heard_memories(runtime, session, "npc-b")


async def test_social_scene_identity_cannot_cross_authenticated_sessions(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    request = fixture_session()
    request["profile_id"] = "other-profile"
    request["manifest"]["capability_flags"].append(DEVELOPMENT_SOCIAL_CAPABILITY)
    opened = (await client.post("/v1/bridge/sessions", json=request)).json()
    other = runtime.sessions[opened["session_id"]]
    response = await client.post(
        "/v1/bridge/social/invite",
        headers={"Authorization": "Bearer " + opened["token"]},
        json=envelope(other, initiator_id="npc-a", participants={
            "npc-a": "ped-0", "npc-b": "ped-1",
        }),
    )
    assert response.status_code == 409
    assert not other.social_scenes
    assert runtime.social.get_scene("social-scene")["session_id"] == session.id


async def test_social_snapshot_departure_cancels_pending_delivery(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    reply = await turn(client, session)
    command = session.commands[reply["command_id"]]
    actor = {
        k: v for k, v in session.actors["npc-b"].items()
        if not k.startswith("_") and k != "allowed_actions"
    }
    actor["area_id"] = "other-block"
    event = fixture_event(session.id, session.last_seq + 1, "actor.snapshot", actor, "npc-b")
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )
    assert response.status_code == 200, response.text
    assert command["state"] == "canceled"
    assert runtime.social.get_scene("social-scene")["status"] == "canceled"


async def test_recipient_withdrawal_cancels_queued_social_subtitle(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    reply = await turn(client, session)
    actor = runtime.repo.get_actor(session.request.profile_id, "npc-b")
    actor["persona"]["social"]["withdraw"] = True
    runtime.repo.register_actor(session.request.profile_id, actor)
    response = await client.post(
        "/v1/bridge/social/review", json=envelope(session, actor_id="npc-b")
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "withdrawn"
    assert session.commands[reply["command_id"]]["state"] == "canceled"


async def test_social_turn_cap_closes_runtime_scene_after_confirmed_delivery(social_scene):
    client, runtime, session, clock = social_scene
    runtime.social.config.max_turns = 2
    assert (await invite(client, session)).status_code == 200
    for index, speaker in enumerate(("npc-a", "npc-b")):
        response = await client.post(
            "/v1/bridge/social/turn", json=envelope(session, actor_id=speaker)
        )
        assert response.status_code == 200, response.text
        command = session.commands[response.json()["command_id"]]
        await acknowledge(client, session, command)
        listener_id = "npc-b" if speaker == "npc-a" else "npc-a"
        state = session.actors[listener_id]
        witness = listener(session, clock, actor_id=listener_id,
                           runtime_instance_id=state["runtime_instance_id"],
                           position=state["position"])
        response = await client.post("/v1/bridge/social/delivery", json=envelope(
            session, command_id=command["id"], witnesses=[witness]
        ))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == ("active" if index == 0 else "completed")
    assert session.social_scenes["social-scene"]["canceled"]
    assert runtime.social.get_scene("social-scene")["status"] == "completed"
    response = await client.post(
        "/v1/bridge/social/turn", json=envelope(session, actor_id="npc-a")
    )
    assert response.status_code == 409


def prior_hearing(runtime, session, number):
    """npc-b confirmed hearing npc-a in an earlier scene, through the social store."""
    stamp = datetime.fromisoformat(session.game_datetime)
    start = SocialStart(
        scene_id=f"prior-{number}", profile_id=session.request.profile_id,
        branch_id=session.branch_id, session_id=session.id, protagonist_id="franklin",
        runtime_generation=1, initiator_id="npc-a",
        participant_bindings={"npc-a": "ped-0", "npc-b": "ped-1"}, area_id="block",
        interior_id="street", game_datetime=stamp, development_attestation=True,
    )
    return runtime.social.store.delivered(
        start,
        SimpleNamespace(
            actor_id="npc-a", utterance_id=f"prior-{number}", retell_event_id=None,
            carrier_event_ids=(),
        ),
        SimpleNamespace(event_id=f"prior-{number}:delivered", text="The pier is quiet.",
                        sampled_at=stamp),
        [SimpleNamespace(actor_id="npc-b", confidence=0.9)], stamp,
    )["npc-b"]["event_id"]


async def topic_turn(client, session):
    response = await client.post("/v1/bridge/social/invite", json=envelope(
        session, initiator_id="npc-b", participants={"npc-b": "ped-1", "npc-a": "ped-0"}
    ))
    assert response.status_code == 200, response.text
    return await client.post("/v1/bridge/social/turn", json=envelope(session, actor_id="npc-b"))


@pytest.mark.parametrize("copy", ["runtime", "social"])
async def test_deleting_either_speech_copy_removes_both(social_scene, copy):
    client, runtime, session, clock = social_scene
    profile = session.request.profile_id
    assert (await invite(client, session)).status_code == 200
    command = session.commands[(await turn(client, session))["command_id"]]
    await acknowledge(client, session, command)
    response = await client.post("/v1/bridge/social/delivery", json=envelope(
        session, command_id=command["id"], witnesses=[listener(session, clock)]
    ))
    assert response.status_code == 200, response.text
    spoken = runtime.social.store.event_id(session.id, command["id"] + ":delivered", "npc-a")
    copies = {"runtime": command["id"] + ":memory", "social": spoken + ":memory"}
    assert all(runtime.repo.get_memory(profile, memory_id) for memory_id in copies.values())
    hearing = runtime.repo.list_memories(profile, "npc-b")
    assert sum(row["provenance"] == "reported" for row in hearing) == 1
    assert runtime.repo.delete_memory(profile, copies[copy])
    assert not any(runtime.repo.get_memory(profile, memory_id) for memory_id in copies.values())
    assert runtime.repo.list_memories(profile, "npc-b") == hearing


async def test_unheard_topic_line_forgotten_with_source(social_scene):
    client, runtime, session, clock = social_scene
    profile = session.request.profile_id
    sources = [prior_hearing(runtime, session, number) for number in (1, 2)]
    response = await topic_turn(client, session)
    assert response.status_code == 200, response.text
    command = session.commands[response.json()["command_id"]]
    assert command["payload"]["text"].startswith("npc-a, you said: The pier is quiet.")
    await acknowledge(client, session, command)  # Hearing evidence never arrives.
    assert runtime.repo.get_memory(profile, command["id"] + ":memory") is not None
    for source in sources:
        assert runtime.repo.delete_memory(profile, source + ":memory")
    assert runtime.repo.get_memory(profile, command["id"] + ":memory") is None
    assert not any("pier" in row["text"] for row in runtime.repo.list_memories(profile, "npc-b"))
    spoken = [row for row in runtime.repo.list_memories(profile, "npc-a") if "pier" in row["text"]]
    assert len(spoken) == 2


async def test_turn_provenance_failure_cancels_command(social_scene):
    client, runtime, session, clock = social_scene
    profile = session.request.profile_id
    sources = [prior_hearing(runtime, session, number) for number in (1, 2)]

    class Forgetting(FakeSocialProvider):
        async def dialogue(self, context):
            if context["social"]["phase"] == "turn":
                # Erasure lands after the prompt is built and before the command exists.
                for source in sources:
                    runtime.repo.delete_memory(profile, source + ":memory")
            async for part in super().dialogue(context):
                yield part

    runtime.provider.primary = Forgetting()
    response = await topic_turn(client, session)
    assert response.status_code == 409
    assert "forgotten words" in response.text
    [command] = [value for value in session.commands.values() if value.get("_social")]
    assert command["state"] == "canceled"
    assert command["payload"]["text"] == ""
    assert session.social_scenes["social-scene"]["canceled"]
    assert runtime.social.get_scene("social-scene")["status"] == "canceled"
    assert any(
        value["kind"] == "scene.cancel" and value["payload"]["scene_id"] == "social-scene"
        for value in session.commands.values()
    )
    assert not runtime.repo.connection.execute(
        "SELECT 1 FROM memory_derivations WHERE actor_id='npc-b'"
    ).fetchall()
