import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from service.lls.__main__ import (
    fixture_event,
    fixture_ped_facts,
    fixture_role_fields,
    fixture_session,
)
from service.lls.app import create_app
from service.lls.protocol import ActionProposal, Event
from service.lls.providers import FakeProvider
from service.lls.roles import joaat
from service.lls.runtime import now
from service.lls.settings import Settings
from service.lls.storage import Repository


@pytest.fixture
async def client(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "test.db"), provider="fake"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            client.app = app
            yield client


async def open_ready(client, *, capabilities=None, capability_flags=None, actor_overrides=None):
    body = fixture_session()
    if capabilities is not None:
        body["capabilities"] = capabilities
    if capability_flags is not None:
        body["manifest"]["capability_flags"] = capability_flags
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 200, response.text
    opened = response.json()
    client.headers["Authorization"] = "Bearer " + opened["token"]
    sid = opened["session_id"]
    world = fixture_event(
        sid,
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
        sid,
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
            **fixture_role_fields(),
        },
        "npc-1",
    )
    actor["payload"].update(actor_overrides or {})
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [world, actor]}
    )
    assert response.status_code == 200, response.text
    return sid, actor


async def utterance(client, sid, text="Please look at me.", seq=3):
    event = fixture_event(
        sid, seq, "player.utterance_text", {"text": text, "scene_id": f"scene-{seq}"}, "npc-1"
    )
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [event]}
    )
    return response, event


class ContextProvider(FakeProvider):
    def __init__(self):
        self.contexts = []

    async def dialogue(self, context):
        self.contexts.append(context)
        yield "I heard you."


@pytest.mark.parametrize(
    ("capability_flags", "live_integration", "live_policy", "accepted"),
    [
        (["service:test-shop"], "test-shop", "service_limited", True),
        ([], "test-shop", "service_limited", False),
        (["service:test-shop"], "other-shop", "service_limited", False),
        (["service:test-shop"], "test-shop", "protected", False),
    ],
)
async def test_service_conversation_requires_authored_integration_without_actions(
    client, capability_flags, live_integration, live_policy, accepted
):
    client.app.state.repository.register_actor(
        "fake-demo",
        {
            "actor_id": "npc-1",
            "actor_kind": "anchored_service",
            "display_name": "Known Clerk",
            "control_policy": "service_limited",
            "service_integration_id": "test-shop",
            "allowed_actions": ["face_player", "wait_here"],
        },
    )
    provider = ContextProvider()
    client.app.state.runtime.provider = provider
    sid, _ = await open_ready(
        client,
        capability_flags=capability_flags,
        actor_overrides={
            "actor_kind": "anchored_service",
            "ownership": "game_script_owned",
            "mission_entity": True,
            "control_policy": live_policy,
            "service_integration_id": live_integration,
        },
    )
    response, _ = await utterance(client, sid)
    assert response.status_code == (200 if accepted else 409)
    session = client.app.state.runtime.sessions[sid]
    if accepted:
        await session.task
        assert provider.contexts[0]["eligible_actions"] == {}
        assert any(c["kind"] == "dialogue.chunk" for c in session.commands.values())
    else:
        assert provider.contexts == []
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())


async def test_context_includes_only_scoped_knowledge_relationships_and_text_free_trace(client):
    repo = client.app.state.repository
    repo.register_actor(
        "fake-demo",
        {
            "actor_id": "npc-1",
            "display_name": "Avery",
            "group_ids": ["local-shop"],
            "region_id": "authored-home-is-not-current-location",
            "allowed_actions": ["face_player"],
        },
    )
    repo.register_actor("fake-demo", {"actor_id": "npc-other", "display_name": "Other"})
    sid, _ = await open_ready(client, actor_overrides={"area_id": "current-area"})
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    provider = ContextProvider()
    runtime.provider = provider

    def add_knowledge(identifier, scope, scope_id=None, owner=None, protagonist="franklin"):
        event = {
            "event_id": identifier,
            "game_datetime": session.game_datetime,
            "protagonist_id": protagonist,
            "event_type": "conversation_completed",
            "payload": {"visibility_scope": scope, "scope_id": scope_id},
            "observer_ids": [owner or "npc-1"],
            "provenance": "directly_observed",
            "confidence": 1,
        }
        repo.record_event("fake-demo", session.branch_id, event)
        return repo.add_knowledge(
            "fake-demo",
            session.branch_id,
            session.game_datetime,
            "private-text-" + identifier,
            [identifier],
            "directly_observed",
            scope=scope,
            scope_id=scope_id,
            owner_id=owner,
            protagonist_id=protagonist,
        )

    visible = [
        add_knowledge("own", "actor", owner="npc-1"),
        add_knowledge("group", "group", "local-shop"),
        add_knowledge("region", "region", "current-area"),
        add_knowledge("public", "public"),
    ]
    hidden = [
        add_knowledge("other-owner", "actor", owner="npc-other"),
        add_knowledge("other-group", "group", "other-shop"),
        add_knowledge("home-region", "region", "authored-home-is-not-current-location"),
        add_knowledge("other-protagonist", "actor", owner="npc-1", protagonist="michael"),
    ]
    repo.apply_relationship_event("fake-demo", session.branch_id, "npc-1", "franklin", "own")
    repo.apply_relationship_event(
        "fake-demo", session.branch_id, "npc-1", "michael", "other-protagonist"
    )
    response, _ = await utterance(client, sid, "A private player sentence.")
    assert response.status_code == 200
    await session.task
    context = provider.contexts[0]
    assert {k["card_id"] for k in context["knowledge"]} == {k["card_id"] for k in visible}
    assert not {k["card_id"] for k in hidden} & {k["card_id"] for k in context["knowledge"]}
    assert context["relationship"]["familiarity"] == 0.02
    trace = next(t for t in session.trace if "knowledge_ids" in t)
    assert set(trace["knowledge_ids"]) == {k["card_id"] for k in visible}
    assert trace["relationship_event_ids"] == ["own"]
    assert "private-text-" not in json.dumps(list(session.trace))
    assert "A private player sentence." not in json.dumps(list(session.trace))


@pytest.mark.parametrize("status", ["rejected", "expired", "started"])
async def test_action_outcome_reaches_next_turn_without_claiming_completion(client, status):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    await utterance(client, sid)
    await session.task
    action = next(c for c in session.commands.values() if c["kind"] == "action.execute")
    if status == "expired":
        action["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
        await client.get("/v1/bridge/commands?wait_seconds=0")
    else:
        ack = {
            "session_id": sid,
            "runtime_generation": 1,
            "status": status,
            "reason": "The actor could not finish.",
        }
        result = await client.post(f"/v1/bridge/commands/{action['id']}/ack", json=ack)
        assert result.status_code == 200
    own = session.action_outcomes[-1]
    session.action_outcomes.extend(
        [
            {**own, "event_id": "foreign-actor", "actor_id": "other-actor"},
            {**own, "event_id": "foreign-protagonist", "protagonist_id": "michael"},
            {**own, "event_id": "foreign-session", "session_id": "other-session"},
        ]
    )
    for command in session.commands.values():
        if command["kind"] == "dialogue.chunk":
            result = await client.post(
                f"/v1/bridge/commands/{command['id']}/ack",
                json={"session_id": sid, "runtime_generation": 1, "status": "canceled"},
            )
            assert result.status_code == 200
    provider = ContextProvider()
    runtime.provider = provider
    response, _ = await utterance(client, sid, "What happened?", seq=4)
    assert response.status_code == 200, response.text
    await session.task
    outcomes = provider.contexts[0]["action_outcomes"]
    suffix = ":service:expired" if status == "expired" else ":" + status
    assert [o["event_id"] for o in outcomes] == [action["id"] + suffix]
    assert outcomes[0]["status"] == status
    assert all(o["status"] != "completed" for o in outcomes)
    memories = provider.contexts[0]["memories"]
    if status != "expired":
        outcome_memory = next(m for m in memories if action["id"] in m["source_event_ids"][0])
        assert "This does not confirm completion." in outcome_memory["text"]


async def test_dialogue_budget_closes_provider_stream_before_action_selection(client):
    class ManySentences(FakeProvider):
        def __init__(self):
            self.closed = False
            self.checked = False

        async def dialogue(self, context):
            try:
                yield "One. " * 40
            finally:
                self.closed = True

        async def action(self, context, utterance, eligible):
            assert self.closed
            self.checked = True
            return None

    sid, _ = await open_ready(client)
    provider = ManySentences()
    client.app.state.runtime.provider = provider
    await utterance(client, sid)
    await client.app.state.runtime.sessions[sid].task
    assert provider.closed
    assert provider.checked


def register_persistent_neighbor(client):
    client.app.state.repository.register_actor(
        "fake-demo",
        {"actor_id": "npc-1", "display_name": "Avery", "allowed_actions": ["face_player"]},
    )
    client.app.state.repository.register_actor(
        "fake-demo", {"actor_id": "other-npc", "display_name": "Other"}
    )


def relationship(client, session, *, actor="npc-1", protagonist="franklin", branch=None):
    return client.app.state.repository.get_relationship(
        "fake-demo",
        branch or session.branch_id,
        actor,
        protagonist,
        session.game_datetime,
        session_id=session.id,
    )


async def delivery_ack(client, session, command, *, status="completed", text=None):
    body = {"session_id": session.id, "runtime_generation": 1, "status": status}
    if command["kind"] == "dialogue.chunk" and status == "completed":
        body["observed"] = {
            "delivery": "subtitle",
            "text": command["payload"]["text"] if text is None else text,
        }
    response = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=body)
    assert response.status_code == 200, response.text
    return response


async def test_full_turn_delivery_changes_relationship_once_with_persistent_scope(client):
    register_persistent_neighbor(client)
    sid, _ = await open_ready(client)
    session = client.app.state.runtime.sessions[sid]
    await utterance(client, sid, "Hello, Avery.")
    await session.task
    chunks = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    assert len(chunks) >= 2
    final = next(c for c in chunks if c["payload"]["final"])
    nonempty = [c for c in chunks if c["payload"]["text"]]
    assert relationship(client, session)["familiarity"] == 0
    # Network acknowledgments can arrive out of order. The marker alone proves nothing.
    await delivery_ack(client, session, final)
    assert relationship(client, session)["familiarity"] == 0
    for command in nonempty[:-1]:
        await delivery_ack(client, session, command)
        assert relationship(client, session)["familiarity"] == 0
    await delivery_ack(client, session, nonempty[-1])
    assert relationship(client, session)["familiarity"] == 0.02
    source_ids = relationship(client, session)["source_event_ids"]
    assert len(source_ids) == 1
    assert source_ids[0].endswith(":conversation_completed")
    await delivery_ack(client, session, nonempty[-1])
    await delivery_ack(client, session, final)
    assert relationship(client, session)["familiarity"] == 0.02
    assert relationship(client, session, protagonist="michael")["familiarity"] == 0
    assert relationship(client, session, actor="other-npc")["familiarity"] == 0
    repo = client.app.state.repository
    history = repo.relationship_history(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        session.game_datetime,
    )
    assert len(history) == 1
    assert history[0]["reason"] == "conversation_completed"
    # A rollback preserves the earlier branch but cannot inherit a future conversation.
    fork = repo.open_timeline(
        "fake-demo", "2013-09-17T11:59:00", parent_branch_id=session.branch_id
    )
    assert relationship(client, session, branch=fork)["familiarity"] == 0
    assert relationship(client, session)["familiarity"] == 0.02
    await client.app.state.runtime.close_session(session)
    with Repository(client.app.state.runtime.settings.database_path) as reopened:
        assert (
            reopened.get_relationship(
                "fake-demo",
                session.branch_id,
                "npc-1",
                "franklin",
                session.game_datetime,
            )["familiarity"]
            == 0.02
        )


async def test_completion_projection_retry_keeps_original_event_time(client, monkeypatch):
    register_persistent_neighbor(client)
    sid, _ = await open_ready(client)
    session = client.app.state.runtime.sessions[sid]
    repo = client.app.state.repository
    await utterance(client, sid, "Hello, Avery.")
    await session.task
    chunks = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    final = next(c for c in chunks if c["payload"]["final"])
    for command in chunks:
        if not command["payload"]["final"]:
            await delivery_ack(client, session, command)
    apply = repo.apply_relationship_event
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("injected projection failure after event persistence")
        return apply(*args, **kwargs)

    monkeypatch.setattr(repo, "apply_relationship_event", fail_once)
    ack = {"session_id": sid, "runtime_generation": 1, "status": "completed"}
    response = await client.post(f"/v1/bridge/commands/{final['id']}/ack", json=ack)
    assert response.status_code == 409
    assert calls == 1
    assert relationship(client, session)["familiarity"] == 0
    turn_id = final["payload"]["turn_id"]
    completion_id = turn_id + ":conversation_completed"
    original = repo.connection.execute(
        "SELECT data,game_datetime FROM events WHERE profile_id=? AND event_id=?",
        ("fake-demo", completion_id),
    ).fetchone()
    assert original is not None
    original_data, original_time = original["data"], original["game_datetime"]
    assert session.delivery_turns[turn_id]["completion_game_datetime"] == session.game_datetime
    # A normal world snapshot advances the game clock before the adapter retries its ACK.
    world = fixture_event(
        sid,
        4,
        "world.snapshot",
        {k: v for k, v in session.world.items() if not k.startswith("_")},
    )
    world["game_datetime"] = "2013-09-17T12:01:00"
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [world]}
    )
    assert response.status_code == 200
    await delivery_ack(client, session, final)
    assert calls == 2
    assert relationship(client, session)["familiarity"] == 0.02
    await delivery_ack(client, session, final)
    assert calls == 2
    assert relationship(client, session)["familiarity"] == 0.02
    stored = repo.connection.execute(
        "SELECT data,game_datetime FROM events WHERE profile_id=? AND event_id=?",
        ("fake-demo", completion_id),
    ).fetchone()
    assert stored["data"] == original_data
    assert stored["game_datetime"] == original_time
    history = repo.relationship_history(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        session.game_datetime,
    )
    assert len(history) == 1
    assert history[0]["game_datetime"] == original_time


@pytest.mark.parametrize(
    "delivery",
    [
        "partial",
        "canceled",
        "rejected",
        "expired",
        "disabled",
        "authored",
        "no_final",
        "action_only",
    ],
)
async def test_incomplete_or_nonconversation_delivery_has_no_relationship_delta(client, delivery):
    register_persistent_neighbor(client)
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    if delivery == "authored":
        command = runtime.command(
            session,
            "dialogue.chunk",
            "npc-1",
            {
                "text": "An automatic greeting.",
                "speaker": "Avery",
                "scene_id": "auto-scene",
                "turn_id": "automatic-turn",
                "chunk_index": 0,
                "final": True,
            },
        )
        command["_autonomous"] = True
        await delivery_ack(client, session, command)
    elif delivery == "action_only":
        command = runtime.command(
            session,
            "action.execute",
            "npc-1",
            {
                "action_id": "face_player",
                "arguments": {},
                "policy_version": "1",
                "scene_id": "action-scene",
                "turn_id": "action-turn",
                "consent": False,
            },
        )
        await delivery_ack(client, session, command, status="started")
        await delivery_ack(client, session, command)
    else:
        if delivery == "disabled":
            from service.lls.providers import DisabledProvider

            runtime.provider = DisabledProvider()
        await utterance(client, sid, "Hello, Avery.")
        await session.task
        chunks = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
        nonempty = next(c for c in chunks if c["payload"]["text"])
        if delivery == "canceled":
            cancel = fixture_event(sid, 4, "scene.cancel", {})
            assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
        for command in chunks:
            if delivery == "no_final" and command["payload"]["final"]:
                continue
            if command is nonempty and delivery == "partial":
                await delivery_ack(client, session, command, text=command["payload"]["text"][:4])
            elif command is nonempty and delivery == "rejected":
                await delivery_ack(client, session, command, status="rejected")
            else:
                if command is nonempty and delivery == "expired":
                    command["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
                await delivery_ack(client, session, command)
    result = relationship(client, session)
    assert result["familiarity"] == 0
    assert result["affinity"] == 0
    assert result["trust"] == 0
    assert result["source_event_ids"] == []


@pytest.mark.asyncio
async def test_full_turn_and_repeat_safe_ack(client):
    sid, _ = await open_ready(client)
    response, event = await utterance(client, sid)
    assert response.status_code == 200, response.text
    await client.app.state.runtime.sessions[sid].task
    poll = await client.get("/v1/bridge/commands?wait_seconds=0")
    commands = poll.json()["commands"]
    assert any(c["kind"] == "dialogue.chunk" for c in commands)
    action = next(c for c in commands if c["kind"] == "action.execute")
    assert action["payload"]["action_id"] == "face_player"
    replay = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [event]}
    )
    assert replay.json()["results"][0]["duplicate"]
    ack = {"session_id": sid, "runtime_generation": 1, "status": "started"}
    assert (await client.post(f"/v1/bridge/commands/{action['id']}/ack", json=ack)).json()[
        "status"
    ] == "started"
    ack["status"] = "completed"
    assert not (await client.post(f"/v1/bridge/commands/{action['id']}/ack", json=ack)).json()[
        "duplicate"
    ]
    assert (await client.post(f"/v1/bridge/commands/{action['id']}/ack", json=ack)).json()[
        "duplicate"
    ]


@pytest.mark.asyncio
async def test_auth_schema_host_origin_and_body_limits(client):
    assert (await client.get("/v1/diagnostics")).status_code == 401
    assert (
        await client.get("/health", headers={"Origin": "https://hostile.example"})
    ).status_code == 403
    assert (await client.get("/health", headers={"Host": "hostile.example"})).status_code == 403
    assert (
        await client.post("/v1/bridge/sessions", content=b"x" * (256 * 1024 + 1))
    ).status_code == 413
    body = fixture_session()
    body["manifest"]["story_mode"] = False
    assert (await client.post("/v1/bridge/sessions", json=body)).status_code == 422
    body = fixture_session()
    body["shell"] = "echo never"
    assert (await client.post("/v1/bridge/sessions", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_stale_generation_duplicate_collision_protagonist(client):
    sid, actor = await open_ready(client)
    body = {"session_id": sid, "events": [actor]}
    actor["payload"]["name"] = "Another person"
    assert (await client.post("/v1/bridge/events:batch", json=body)).status_code == 409
    actor["event_id"] = "new-event"
    actor["seq"] = 3
    actor["runtime_generation"] = 0
    assert (await client.post("/v1/bridge/events:batch", json=body)).status_code == 409
    actor["runtime_generation"] = 1
    actor["protagonist_id"] = "michael"
    assert (await client.post("/v1/bridge/events:batch", json=body)).status_code == 409


@pytest.mark.asyncio
async def test_cancellation_priority_invalidates_late_batch(client):
    sid, _ = await open_ready(client)
    cancel = fixture_event(sid, 5, "scene.cancel", {})
    assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
    response, _ = await utterance(client, sid, seq=4)
    assert response.status_code == 409
    poll = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()
    assert [c["kind"] for c in poll["commands"]] == ["scene.cancel"]


@pytest.mark.asyncio
async def test_cancel_running_provider_has_no_late_actions(client):
    class Slow(FakeProvider):
        async def dialogue(self, context):
            yield "One sentence. "
            await asyncio.sleep(1)
            yield "Too late."

    sid, _ = await open_ready(client)
    client.app.state.runtime.provider = Slow()
    response, _ = await utterance(client, sid)
    assert response.status_code == 200
    await asyncio.sleep(0.01)
    cancel = fixture_event(sid, 4, "scene.cancel", {})
    assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
    await client.app.state.runtime.sessions[sid].task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    assert all(c["kind"] == "scene.cancel" for c in commands)


@pytest.mark.asyncio
async def test_protected_actor_and_no_capability(client):
    sid, actor = await open_ready(client, capabilities=[])
    response, _ = await utterance(client, sid)
    assert response.status_code == 200
    await client.app.state.runtime.sessions[sid].task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    assert not any(c["kind"] == "action.execute" for c in commands)
    actor["event_id"] = "protect"
    actor["seq"] = 4
    actor["payload"]["ownership"] = "game_script_owned"
    actor["payload"]["control_policy"] = "protected"
    actor["payload"]["mission_entity"] = True
    assert (
        await client.post("/v1/bridge/events:batch", json={"session_id": sid, "events": [actor]})
    ).status_code == 200
    assert (await utterance(client, sid, seq=5))[0].status_code == 409


@pytest.mark.asyncio
async def test_close_purges_ephemeral_and_rejects_token(client):
    sid, _ = await open_ready(client)
    response = await client.post(f"/v1/bridge/sessions/{sid}/close", json={"runtime_generation": 1})
    assert response.status_code == 200
    assert client.app.state.repository.get_actor("fake-demo", "npc-1") is None
    assert (await client.get("/v1/diagnostics")).status_code == 410


@pytest.mark.asyncio
async def test_disabled_default_no_model_or_action(tmp_path):
    settings = Settings(database_path=str(tmp_path / "disabled.db"))
    assert settings.provider == "disabled"
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 8)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            client.app = app
            sid, _ = await open_ready(client)
            await utterance(client, sid)
            await app.state.runtime.sessions[sid].task
            commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
            assert not any(c["kind"] == "action.execute" for c in commands)
            assert any("AI is off" in c["payload"].get("text", "") for c in commands)


def test_no_arbitrary_action_or_arguments():
    for payload in [
        {"action_id": "native", "arguments": {}},
        {"action_id": "face_player", "arguments": {"shell": "anything"}},
        {"action_id": "face_player", "arguments": {"point": {"x": 1, "y": 2, "z": 3}}},
    ]:
        with pytest.raises(ValueError):
            ActionProposal.model_validate(payload)
    event = fixture_event("s", 1, "scene.cancel", {"execute": "never"})
    with pytest.raises(ValueError):
        Event.model_validate(event)


def test_cloud_and_lan_require_explicit_configuration():
    with pytest.raises(ValueError):
        Settings(provider="openai")
    with pytest.raises(ValueError):
        Settings(host="0.0.0.0")
    with pytest.raises(ValueError):
        Settings(dev_lan=True)
    Settings(
        host="100.64.0.2",
        release=False,
        dev_lan=True,
        bootstrap_token="test-only",
        allow_sources=["100.64.0.3"],
    )


@pytest.mark.asyncio
async def test_authored_policy_cannot_be_widened_by_snapshot(client):
    sid, actor = await open_ready(client)
    repo = client.app.state.repository
    repo.register_actor(
        "fake-demo",
        {
            "actor_id": "curated",
            "actor_kind": "curated_persistent",
            "display_name": "Curated",
            "persona": {},
            "control_policy": "ambient",
            "allowed_actions": [],
        },
    )
    actor.update(actor_id="curated", seq=3, event_id="curated-snapshot")
    actor["payload"].update(
        actor_kind="curated_persistent",
        ownership="mod_owned",
        mission_entity=True,
        runtime_handle=43,
        runtime_instance_id="curated-1",
    )
    assert (
        await client.post("/v1/bridge/events:batch", json={"session_id": sid, "events": [actor]})
    ).status_code == 200
    event = fixture_event(
        sid, 4, "player.utterance_text", {"text": "Look at me", "scene_id": "unique"}, "curated"
    )
    assert (
        await client.post("/v1/bridge/events:batch", json={"session_id": sid, "events": [event]})
    ).status_code == 200
    await client.app.state.runtime.sessions[sid].task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    assert not any(c["kind"] == "action.execute" for c in commands)
    assert all(c["runtime_instance_id"] == "curated-1" for c in commands)


@pytest.mark.asyncio
async def test_unattested_world_fact_cannot_become_witness_memory(client):
    sid, _ = await open_ready(client)
    observation = fixture_event(
        sid,
        3,
        "world.observed",
        {
            "text": "Hidden event",
            "observer_ids": ["npc-1"],
            "provenance": "directly_observed",
            "confidence": 1,
        },
    )
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [observation]}
    )
    assert response.status_code == 422
    observation["payload"]["provenance"] = "unverified"
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [observation]}
    )
    assert response.status_code == 200
    session = client.app.state.runtime.sessions[sid]
    assert (
        client.app.state.repository.retrieve_memories(
            "fake-demo",
            session.branch_id,
            "npc-1",
            "franklin",
            "2013-09-17T12:00:00",
            "Hidden",
            session_id=sid,
        )
        == []
    )


@pytest.mark.asyncio
async def test_delivery_memory_requires_actual_ack(client):
    sid, _ = await open_ready(client)
    await utterance(client, sid)
    session = client.app.state.runtime.sessions[sid]
    await session.task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    command = next(c for c in commands if c["kind"] == "dialogue.chunk" and c["payload"]["text"])
    ack = {"session_id": sid, "runtime_generation": 1, "status": "completed"}
    assert (
        await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    ).status_code == 422
    ack["observed"] = {"delivery": "subtitle", "text": "Forged words"}
    assert (
        await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    ).status_code == 422
    prefix = command["payload"]["text"][:8]
    ack["observed"]["text"] = prefix
    assert (
        await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    ).status_code == 200
    memories = client.app.state.repository.retrieve_memories(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        "2013-09-17T12:00:00",
        prefix,
        session_id=sid,
    )
    spoken = [m["text"] for m in memories if m["text"].startswith("I said:")]
    assert spoken == ["I said: " + prefix]


@pytest.mark.asyncio
async def test_snapshot_expiry_and_instance_reuse_cancel_commands(client):
    sid, actor = await open_ready(client)
    await utterance(client, sid)
    session = client.app.state.runtime.sessions[sid]
    await session.task
    actor.update(seq=4, event_id="new-instance")
    actor["payload"]["runtime_instance_id"] = "ped-2"
    assert (
        await client.post("/v1/bridge/events:batch", json={"session_id": sid, "events": [actor]})
    ).status_code == 409
    assert (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"] == []
    session.world["_sampled_at"] -= 10
    assert (await utterance(client, sid, seq=5))[0].status_code == 409


@pytest.mark.asyncio
async def test_sentence_stream_strips_tag_and_bounds_generation():
    from service.lls.runtime import sentence_chunks

    class Tokens(FakeProvider):
        async def dialogue(self, context):
            for token in ["[ha", "ppy] Hello", ". ", "Next sentence", ".", "x" * 1000]:
                yield token

    chunks = [chunk async for chunk in sentence_chunks(Tokens(), {}, 100)]
    assert chunks[0] == "Hello. "
    assert "[happy]" not in "".join(chunks)
    assert len("".join(chunks)) <= 100


@pytest.mark.asyncio
async def test_nonnative_source_and_dev_lan_bootstrap(tmp_path):
    app = create_app(
        Settings(
            database_path=str(tmp_path / "lan.db"),
            host="100.64.0.2",
            release=False,
            dev_lan=True,
            bootstrap_token="local-test",
            allow_sources=["100.64.0.3"],
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("100.64.0.4", 12)),
            base_url="http://100.64.0.2:8765",
        ) as client:
            assert (await client.get("/health")).status_code == 403
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("100.64.0.3", 12)),
            base_url="http://100.64.0.2:8765",
        ) as client:
            assert (
                await client.post("/v1/bridge/sessions", json=fixture_session())
            ).status_code == 401
            assert (
                await client.post(
                    "/v1/bridge/sessions",
                    json=fixture_session(),
                    headers={"Authorization": "Bearer local-test"},
                )
            ).status_code == 200


@pytest.mark.asyncio
async def test_inactive_session_expires_and_purges_ephemeral(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.sessions[sid].last_activity -= 31
    await runtime.expire_sessions()
    assert runtime.sessions[sid].closed
    assert runtime.sessions[sid].actors == {}
    assert client.app.state.repository.get_actor("fake-demo", "npc-1") is None
    assert (await client.get("/v1/diagnostics")).status_code == 410


@pytest.mark.asyncio
async def test_delayed_interruption_ack_records_only_delivered_prefix(client):
    sid, _ = await open_ready(client)
    await utterance(client, sid)
    session = client.app.state.runtime.sessions[sid]
    await session.task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    command = next(c for c in commands if c["kind"] == "dialogue.chunk" and c["payload"]["text"])
    cancel = fixture_event(sid, 4, "scene.cancel", {})
    assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
    prefix = command["payload"]["text"][:6]
    ack = {
        "session_id": sid,
        "runtime_generation": 1,
        "status": "canceled",
        "observed": {"delivery": "subtitle", "text": prefix},
    }
    result = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
    assert result.status_code == 200
    assert result.json()["status"] == "canceled"
    memories = client.app.state.repository.retrieve_memories(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        "2013-09-17T12:00:00",
        prefix,
        session_id=sid,
    )
    assert [m["text"] for m in memories if m["text"].startswith("I said:")] == ["I said: " + prefix]


@pytest.mark.asyncio
async def test_cancel_first_rendered_chunk_never_remembers_unshown_chunks(client):
    sid, _ = await open_ready(client)
    await utterance(client, sid)
    session = client.app.state.runtime.sessions[sid]
    await session.task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    chunks = [c for c in commands if c["kind"] == "dialogue.chunk"]
    assert len(chunks) >= 3
    cancel = fixture_event(sid, 4, "scene.cancel", {})
    assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
    for index, chunk in enumerate(chunks):
        ack = {"session_id": sid, "runtime_generation": 1, "status": "canceled"}
        if index == 0:
            ack["observed"] = {"delivery": "subtitle", "text": chunk["payload"]["text"]}
        assert (
            await client.post(f"/v1/bridge/commands/{chunk['id']}/ack", json=ack)
        ).status_code == 200
    memories = client.app.state.repository.retrieve_memories(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        "2013-09-17T12:00:00",
        "",
        session_id=sid,
    )
    assert [m["text"] for m in memories if m["text"].startswith("I said:")] == [
        "I said: " + chunks[0]["payload"]["text"].strip()
    ]


@pytest.mark.asyncio
async def test_dialogue_window_bound_is_separate_from_action_ttl(client):
    from datetime import datetime, timezone

    class ManySentences(FakeProvider):
        async def dialogue(self, context):
            for _ in range(100):
                yield "A small sentence. "

    sid, _ = await open_ready(client)
    client.app.state.runtime.provider = ManySentences()
    await utterance(client, sid)
    await client.app.state.runtime.sessions[sid].task
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    chunks = [c for c in commands if c["kind"] == "dialogue.chunk" and c["payload"]["text"]]
    assert len(chunks) <= 20
    assert sum(max(3, min(15, len(c["payload"]["text"]) / 12)) for c in chunks) <= 100
    for command in commands:
        remaining = (
            datetime.fromisoformat(command["expires_at"]) - datetime.now(timezone.utc)
        ).total_seconds()
        assert remaining <= (120 if command["kind"] == "dialogue.chunk" else 15)
        if command["kind"] == "dialogue.chunk":
            assert remaining > 100


@pytest.mark.asyncio
async def test_cancelled_database_write_finishes_before_erasure(client, monkeypatch):
    import threading

    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    repo = client.app.state.repository
    source = {
        "event_id": "blocked-source",
        "game_datetime": "2013-09-17T12:00:00",
        "protagonist_id": "franklin",
        "event_type": "dialogue.delivered",
        "payload": {},
        "provenance": "heard",
        "observer_ids": ["npc-1"],
        "confidence": 1,
    }
    repo.record_event("fake-demo", session.branch_id, source)
    entered, release = threading.Event(), threading.Event()
    original = repo.add_memory
    order = []

    def delayed_write(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=3):
            raise RuntimeError("test did not release database operation")
        result = original(*args, **kwargs)
        order.append("write")
        return result

    monkeypatch.setattr(repo, "add_memory", delayed_write)
    write = asyncio.create_task(
        runtime.db(
            "add_memory",
            "fake-demo",
            session.branch_id,
            "npc-1",
            "franklin",
            "2013-09-17T12:00:00",
            "Must stay erased",
            ["blocked-source"],
            "heard",
            session_id=sid,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        async def erase_after_cancel():
            await asyncio.gather(write, return_exceptions=True)
            await runtime.db("reset_actor", "fake-demo", "npc-1")
            order.append("erase")

        erase = asyncio.create_task(erase_after_cancel())
        await asyncio.sleep(0)
        assert not erase.done()
        release.set()
        await erase
        assert order == ["write", "erase"]
        assert (
            repo.retrieve_memories(
                "fake-demo",
                session.branch_id,
                "npc-1",
                "franklin",
                "2013-09-17T12:00:00",
                "Must stay erased",
                session_id=sid,
            )
            == []
        )
    finally:
        release.set()
        await asyncio.gather(write, return_exceptions=True)


COP_FACTS = fixture_ped_facts("s_m_y_cop_01", ped_type=6, relationship_group_hash=joaat("COP"))
ROLE_SNAPSHOTS = {
    "police": {"role_class": "police", "ped_facts": COP_FACTS},
    "gang": {
        "role_class": "gang",
        "ped_facts": fixture_ped_facts(
            "g_m_y_ballaeast_01", ped_type=15, relationship_group_hash=joaat("AMBIENT_GANG_BALLAS")
        ),
    },
    "animal": {
        "role_class": "animal",
        "ped_facts": fixture_ped_facts("a_c_chop", ped_type=28, human=False, animal_model=True),
    },
    "missing_facts": {"ped_facts": None},
    "civilian_label_cop_facts": {"ped_facts": COP_FACTS},
    "ambient_claims_mod_owned": {"ownership": "mod_owned"},
}


@pytest.mark.parametrize("case", list(ROLE_SNAPSHOTS))
async def test_snapshot_role_cannot_widen_policy(client, case):
    sid, _ = await open_ready(client, actor_overrides=ROLE_SNAPSHOTS[case])
    session = client.app.state.runtime.sessions[sid]
    stored = session.actors["npc-1"]
    assert stored["control_policy"] == "protected"
    assert stored["role_class"] == {
        "police": "police",
        "gang": "gang",
        "animal": "animal",
        "missing_facts": "unknown",
        "civilian_label_cop_facts": "police",
        "ambient_claims_mod_owned": "civilian",
    }[case]
    response, _ = await utterance(client, sid)
    assert response.status_code == 409
    assert session.task is None
    assert not session.commands
    assert (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"] == []


async def test_civilian_evidence_stays_ambient(client):
    sid, _ = await open_ready(client)
    session = client.app.state.runtime.sessions[sid]
    stored = session.actors["npc-1"]
    assert (stored["control_policy"], stored["role_class"], stored["role_reason"]) == (
        "ambient",
        "civilian",
        "civilian_evidence",
    )
    response, _ = await utterance(client, sid)
    assert response.status_code == 200
    await session.task
    kinds = [c["kind"] for c in session.commands.values()]
    assert "dialogue.chunk" in kinds and "action.execute" in kinds


async def test_role_change_mid_scene_cancels_the_scene(client):
    class Slow(FakeProvider):
        async def dialogue(self, context):
            yield "One sentence. "
            await asyncio.sleep(1)
            yield "Too late."

    sid, actor = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    runtime.provider = Slow()
    assert (await utterance(client, sid))[0].status_code == 200
    await asyncio.sleep(0.01)
    actor.update(seq=4, event_id="cuffed-snapshot")
    actor["payload"]["ped_facts"] = fixture_ped_facts(cuffed=True)
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": [actor]}
    )
    assert response.status_code == 200
    await session.task
    stored = session.actors["npc-1"]
    assert (stored["role_class"], stored["role_reason"], stored["control_policy"]) == (
        "prisoner",
        "cuffed",
        "protected",
    )
    chunks = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    assert chunks and all(c["state"] == "canceled" for c in chunks)
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())
    assert (await utterance(client, sid, seq=5))[0].status_code == 409
