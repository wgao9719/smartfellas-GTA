"""Recent direct exchanges survive lexical misses without crossing conversation scopes."""

import json

import pytest
from test_bridge import client as client
from test_bridge import open_ready, utterance
from test_context import context_inputs

from service.lls.__main__ import fixture_event, fixture_session
from service.lls.context import build_context, dialogue_context_bytes
from service.lls.protocol import Ack
from service.lls.providers import FakeProvider
from service.lls.storage import Repository

INTRODUCTION = "Hi, I'm Franklin. I fix cars for a living."


class ConversationProvider(FakeProvider):
    def __init__(self):
        self.contexts = []

    async def dialogue(self, context):
        self.contexts.append(context)
        yield "My name is Avery."


async def complete_reply(runtime, session):
    await session.task
    for command in list(session.commands.values()):
        if command["kind"] == "dialogue.chunk" and command["state"] == "queued":
            await runtime.acknowledge(
                session, command["id"],
                Ack(
                    session_id=session.id, runtime_generation=1, status="completed",
                    observed={"delivery": "simulated", "text": command["payload"]["text"]},
                ),
            )


async def test_runtime_name_question_keeps_previous_exchange_without_embeddings(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    runtime.settings = runtime.settings.model_copy(update={"memory_token_budget": 128})
    provider = ConversationProvider()
    runtime.provider = provider
    response, _ = await utterance(client, sid, INTRODUCTION)
    assert response.status_code == 200
    await complete_reply(runtime, session)
    response, _ = await utterance(client, sid, "What's my name?", seq=4)
    assert response.status_code == 200
    await session.task
    assert INTRODUCTION in json.dumps(provider.contexts[-1])
    history = provider.contexts[-1]["recent_conversation"]
    assert [line["role"] for line in history] == ["player", "npc"]
    assert [line["provenance"] for line in history] == ["heard", "directly_observed"]
    assert history[0]["text"] == "The player said: " + INTRODUCTION
    assert history[1]["text"] == "I said: My name is Avery."
    assert provider.contexts[-1]["memories"] == []


async def test_recent_context_sources_erase_later_reply_paraphrases(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    runtime.settings = runtime.settings.model_copy(update={"memory_token_budget": 64})
    provider = ConversationProvider()
    runtime.provider = provider
    _, first = await utterance(client, sid, INTRODUCTION)
    await complete_reply(runtime, session)
    await utterance(client, sid, "What's my name?", seq=4)
    await complete_reply(runtime, session)
    history = provider.contexts[-1]["recent_conversation"]
    assert history[0]["source_event_ids"] == [first["event_id"]]
    replies = [
        command["id"] + ":memory" for command in session.commands.values()
        if command["kind"] == "dialogue.chunk" and command["payload"]["text"]
    ]
    assert len(replies) == 2
    assert runtime.repo.delete_memory("fake-demo", history[0]["memory_id"])
    assert all(runtime.repo.get_memory("fake-demo", identifier) is None for identifier in replies)
    await utterance(client, sid, "What did I tell you?", seq=5)
    await session.task
    assert INTRODUCTION not in json.dumps(provider.contexts[-1])


@pytest.mark.parametrize("scope", ["actor", "protagonist"])
async def test_runtime_recent_history_isolates_target_and_protagonist(client, scope):
    runtime = client.app.state.runtime
    runtime.repo.register_actor("fake-demo", {"id": "npc-1", "display_name": "Avery"})
    sid, actor = await open_ready(client)
    session = runtime.sessions[sid]
    provider = ConversationProvider()
    runtime.provider = provider
    await utterance(client, sid, INTRODUCTION)
    await complete_reply(runtime, session)
    assert len(provider.contexts) == 1
    assert runtime.repo.recent_conversation(
        "fake-demo", session.branch_id, "npc-1", "franklin", session.game_datetime,
        session_id=sid,
    )
    protagonist, actor_id = "franklin", "npc-2"
    events = []
    if scope == "protagonist":
        protagonist, actor_id = "michael", "npc-1"
        request = {**fixture_session(), "protagonist_id": protagonist, "runtime_generation": 2}
        response = await client.post("/v1/bridge/sessions", json=request)
        assert response.status_code == 200, response.text
        opened = response.json()
        sid = opened["session_id"]
        client.headers["Authorization"] = "Bearer " + opened["token"]
        events.append(fixture_event(sid, 4, "world.snapshot", {
            **{key: value for key, value in session.world.items() if not key.startswith("_")},
            "protagonist_id": protagonist,
        }))
        session = runtime.sessions[sid]
    events.append(fixture_event(sid, 5, "actor.snapshot", {
        **actor["payload"], "runtime_handle": 43, "runtime_instance_id": "ped-2",
    }, actor_id))
    events.append(fixture_event(sid, 6, "player.utterance_text", {
        "text": "What's my name?", "scene_id": "scene-6",
    }, actor_id))
    for event in events:
        event["protagonist_id"] = protagonist
        event["runtime_generation"] = session.request.runtime_generation
    response = await client.post(
        "/v1/bridge/events:batch", json={"session_id": sid, "events": events},
    )
    assert response.status_code == 200, response.text
    await session.task
    assert provider.contexts, list(session.trace)
    assert provider.contexts[-1]["actor"]["actor_id"] == actor_id
    assert provider.contexts[-1]["protagonist_id"] == protagonist
    assert provider.contexts[-1]["recent_conversation"] == []
    assert INTRODUCTION not in json.dumps(provider.contexts[-1])


@pytest.fixture
def history_repo():
    with Repository() as repo:
        repo.register_actor("profile", {"id": "avery"})
        repo.register_actor("profile", {"id": "bea"})
        branch = repo.open_timeline("profile", "2013-09-17T12:00:00Z")
        yield repo, branch


def exchange(repo, branch, identifier, *, actor="avery", protagonist="franklin", session="s1"):
    stamp = "2013-09-17T12:00:00Z"
    result = []
    for event_id, kind, text, provenance, payload in [
        (identifier, "player.utterance_text", f"The player said: {identifier}", "heard", {}),
        (identifier + "-reply", "dialogue.delivered", f"I said: Reply to {identifier}",
         "directly_observed", {"input_event_id": identifier, "chunk_index": 0}),
    ]:
        repo.record_event("profile", branch, {
            "event_id": event_id, "event_type": kind, "session_id": session,
            "actor_id": actor, "game_datetime": stamp, "protagonist_id": protagonist,
            "observer_ids": [actor], "provenance": provenance,
            "payload": {"text": text, **payload},
        })
        result.append(repo.add_memory(
            "profile", branch, actor, protagonist, stamp, text, [event_id], provenance,
        ))
    return result


def recent(repo, branch, **values):
    arguments = {
        "actor_id": "avery", "protagonist_id": "franklin",
        "game_datetime": "2013-09-17T12:00:00Z", "session_id": "s1",
        **values,
    }
    return repo.recent_conversation("profile", branch, **arguments)


@pytest.mark.parametrize("scope", ["actor", "protagonist", "session", "branch", "time"])
def test_recent_conversation_does_not_cross_scopes(history_repo, scope):
    repo, branch = history_repo
    exchange(repo, branch, "private-introduction")
    assert len(recent(repo, branch)) == 2
    options = {
        "actor": {"actor_id": "bea"}, "protagonist": {"protagonist_id": "michael"},
        "session": {"session_id": "s2"},
        "time": {"game_datetime": "2013-09-17T11:59:00Z"},
    }
    if scope == "branch":
        branch = repo.open_timeline("profile", "2013-09-17T11:59:00Z")
    assert recent(repo, branch, **options.get(scope, {})) == []


@pytest.mark.parametrize("operation", ["memory", "event", "actor", "retention", "ttl"])
def test_recent_conversation_respects_erasure_and_retention(history_repo, operation):
    repo, branch = history_repo
    memory, reply = exchange(repo, branch, "forgotten-introduction")
    repo.link_memory_derivations(
        "profile", "avery", [("forgotten-introduction", "forgotten-introduction-reply")],
    )
    if operation == "memory":
        repo.delete_memory("profile", memory["memory_id"])
    elif operation == "event":
        repo.delete_event("profile", "forgotten-introduction")
    elif operation == "actor":
        repo.reset_actor("profile", "avery")
    elif operation == "retention":
        assert repo.expire_raw_events("2100-01-01T00:00:00Z") == 2
        assert repo.get_memory("profile", memory["memory_id"]) is not None
    else:
        with repo.connection:
            repo.connection.execute(
                "UPDATE memories SET expires_at=game_datetime WHERE memory_id IN (?,?)",
                (memory["memory_id"], reply["memory_id"]),
            )
    assert recent(repo, branch) == []


def test_window_counts_distinct_exchanges_in_ingestion_order_at_equal_game_time(history_repo):
    repo, branch = history_repo
    for identifier in ["z", "y", "x", "w", "v"]:
        memory, reply = exchange(repo, branch, identifier)
        for source in [memory, reply]:
            repo.add_memory(
                "profile", branch, "avery", "franklin", source["game_datetime"],
                "Duplicate summary: " + source["text"], source["source_event_ids"],
                source["provenance"],
            )
    history = recent(repo, branch)
    assert [line["exchange_id"] for line in history] == ["y", "y", "x", "x", "w", "w", "v", "v"]
    assert all("Duplicate" not in line["text"] for line in history)
    assert [line["exchange_id"] for line in recent(repo, branch, exclude_event_id="v")] == [
        "z", "z", "y", "y", "x", "x", "w", "w",
    ]


def test_recent_conversation_budget_deduplication_and_provenance(history_repo):
    repo, branch = history_repo
    for number in range(4):
        exchange(repo, branch, f"turn-{number}")
    history = recent(repo, branch)
    for item in history:
        item["text"] += " \u754c" * 1000
    values = context_inputs()
    values.update(recent_conversation=history, memories=history, byte_budget=3500)
    context, trace = build_context(**values)
    assert len(json.dumps(context, ensure_ascii=False).encode("utf-8")) <= 3500
    assert dialogue_context_bytes(context) <= 3500
    assert context["context_truncated"]
    assert context["memories"] == []
    assert context["recent_conversation"]
    assert {item["exchange_id"] for item in context["recent_conversation"]} == {"turn-3"}
    assert {item["role"] for item in context["recent_conversation"]} == {"player", "npc"}
    assert all(item["source_event_ids"] for item in context["recent_conversation"])
    assert trace["memory_ids"] == [item["memory_id"] for item in context["recent_conversation"]]
    assert "The player said" not in json.dumps(trace)


def test_minimum_byte_budget_preserves_introduction_before_multiple_npc_chunks(history_repo):
    repo, branch = history_repo
    exchange(repo, branch, "introduction")
    history = recent(repo, branch)
    history[0]["text"] = "The player said: " + INTRODUCTION
    history.extend([
        {**history[1], "memory_id": f"extra-reply-{index}", "text": "I said: Ordinary chatter."}
        for index in range(8)
    ])
    values = context_inputs()
    values.update(recent_conversation=history, input_text="What's my name?", byte_budget=2048)
    context, trace = build_context(**values)
    assert len(json.dumps(context, ensure_ascii=False).encode("utf-8")) <= 2048
    assert dialogue_context_bytes(context) <= 2048
    assert context["context_truncated"]
    assert context["recent_conversation"][0]["role"] == "player"
    assert INTRODUCTION in context["recent_conversation"][0]["text"]
    assert trace["recent_conversation_memory_ids"][0] == history[0]["memory_id"]


def test_old_exchanges_are_evicted_before_clipping_current_question(history_repo):
    repo, branch = history_repo
    exchange(repo, branch, "older")
    exchange(repo, branch, "latest")
    history = recent(repo, branch)
    history[0]["text"] += "Old topic. " * 1000
    values = context_inputs()
    question = "What do you remember about our conversation? " * 6
    values.update(recent_conversation=history, input_text=question, byte_budget=3500)
    context, _ = build_context(**values)
    assert context["input"] == question
    assert {item["exchange_id"] for item in context["recent_conversation"]} == {"latest"}


@pytest.mark.parametrize("delivered", [False, True])
async def test_only_acknowledged_npc_prefix_enters_recent_window(client, delivered):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    runtime.provider = ConversationProvider()
    await utterance(client, sid, INTRODUCTION)
    await session.task
    command = next(
        command for command in session.commands.values()
        if command["kind"] == "dialogue.chunk" and command["payload"]["text"]
    )
    runtime.cancel(session)
    if delivered:
        await runtime.acknowledge(
            session, command["id"],
            Ack(
                session_id=sid, runtime_generation=1, status="canceled",
                observed={"delivery": "simulated", "text": "My name"},
            ),
        )
    history = runtime.repo.recent_conversation(
        "fake-demo", session.branch_id, "npc-1", "franklin", session.game_datetime,
        session_id=sid,
    )
    assert [line["role"] for line in history] == (["player", "npc"] if delivered else ["player"])
    if delivered:
        assert history[-1]["text"] == "I said: My name"
