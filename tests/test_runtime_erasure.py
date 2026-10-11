"""Foreground replies keep identifier-only links to the input and recalled evidence."""

import json

import pytest
from test_bridge import client as client
from test_bridge import open_ready, utterance

from service.lls.protocol import Ack
from service.lls.providers import FakeProvider


class ParaphraseProvider(FakeProvider):
    def __init__(self):
        self.context = None

    async def dialogue(self, context):
        self.context = context
        yield "I remember our violet password."


@pytest.mark.parametrize("source_kind", ["input", "recalled_memory"])
async def test_forgetting_source_removes_foreground_paraphrase(client, source_kind):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    repo = runtime.repo
    session = runtime.sessions[sid]
    provider = ParaphraseProvider()
    runtime.provider = provider
    if source_kind == "recalled_memory":
        repo.record_event(
            "fake-demo", session.branch_id,
            {
                "event_id": "remembered-conversation",
                "event_type": "player.utterance_text",
                "game_datetime": session.game_datetime,
                "protagonist_id": session.request.protagonist_id,
                "observer_ids": ["npc-1"],
                "provenance": "heard",
                "payload": {"text": "The lavender password belongs to Franklin."},
            },
        )
        source = repo.add_memory(
            "fake-demo", session.branch_id, "npc-1", session.request.protagonist_id,
            session.game_datetime, "The lavender password belongs to Franklin.",
            ["remembered-conversation"], "heard", session_id=sid,
        )
    response, input_event = await utterance(client, sid, text="What password do you remember?")
    assert response.status_code == 200
    await session.task
    if source_kind == "input":
        source = next(
            memory for memory in repo.export_profile("fake-demo")["memories"]
            if input_event["event_id"] in memory["source_event_ids"]
        )
    else:
        assert [memory["memory_id"] for memory in provider.context["memories"]] == [
            source["memory_id"]
        ]
    command = next(
        command for command in session.commands.values()
        if command["kind"] == "dialogue.chunk" and command["payload"]["text"]
    )
    await runtime.acknowledge(
        session, command["id"],
        Ack(
            session_id=sid, runtime_generation=1, status="completed",
            observed={"delivery": "simulated", "text": command["payload"]["text"]},
        ),
    )
    derived_id = command["id"] + ":memory"
    assert repo.get_memory("fake-demo", derived_id) is not None
    assert repo.delete_memory("fake-demo", source["memory_id"])
    assert repo.get_memory("fake-demo", derived_id) is None
    assert "violet password" not in json.dumps(repo.export_profile("fake-demo"))


async def test_foreground_derivations_use_only_memories_in_final_bounded_context(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    repo = runtime.repo
    session = runtime.sessions[sid]
    runtime.settings = runtime.settings.model_copy(
        update={"context_byte_budget": 2048, "memory_token_budget": 2048}
    )
    provider = ParaphraseProvider()
    runtime.provider = provider
    sources = []
    for index in range(2):
        event_id = f"password-{index}"
        repo.record_event(
            "fake-demo", session.branch_id,
            {
                "event_id": event_id,
                "event_type": "player.utterance_text",
                "game_datetime": session.game_datetime,
                "protagonist_id": session.request.protagonist_id,
                "observer_ids": ["npc-1"],
                "provenance": "heard",
                "payload": {"text": event_id},
            },
        )
        sources.append(repo.add_memory(
            "fake-demo", session.branch_id, "npc-1", session.request.protagonist_id,
            session.game_datetime, f"Password {index}. " + "A private detail. " * 40,
            [event_id], "heard", importance=1 - index * 0.1, session_id=sid,
        ))
    assert len(repo.retrieve_memories(
        "fake-demo", session.branch_id, "npc-1", session.request.protagonist_id,
        session.game_datetime, "password", token_budget=2048, session_id=sid,
    )) == 2
    response, _ = await utterance(client, sid, text="What password do you remember?")
    assert response.status_code == 200
    await session.task
    retained = {memory["memory_id"] for memory in provider.context["memories"]}
    assert len(retained) == 1
    assert provider.context["context_truncated"]
    command = next(
        command for command in session.commands.values()
        if command["kind"] == "dialogue.chunk" and command["payload"]["text"]
    )
    await runtime.acknowledge(
        session, command["id"],
        Ack(
            session_id=sid, runtime_generation=1, status="completed",
            observed={"delivery": "simulated", "text": command["payload"]["text"]},
        ),
    )
    derived_id = command["id"] + ":memory"
    omitted = next(memory for memory in sources if memory["memory_id"] not in retained)
    assert repo.delete_memory("fake-demo", omitted["memory_id"])
    assert repo.get_memory("fake-demo", derived_id) is not None
    assert repo.delete_memory("fake-demo", next(iter(retained)))
    assert repo.get_memory("fake-demo", derived_id) is None
