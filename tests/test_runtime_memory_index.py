"""Real foreground/social consumers with only model HTTP replaced by fixtures."""

import asyncio
import json

import httpx
import pytest
from test_bridge import client as client
from test_bridge import open_ready, utterance
from test_social import scene as scene

from service.lls.embeddings import EmbeddingConfig
from service.lls.gateway_config import ChatterBudget, DailyBudget, GatewayConfig
from service.lls.memory_index import MemoryIndex
from service.lls.providers import create_provider
from service.lls.settings import Settings


class EmbeddingAndDialogueHTTP:
    def __init__(self):
        self.contexts = []
        self.embedding_inputs = []

    def __call__(self, request):
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            self.embedding_inputs.extend(payload["input"])
            vectors = []
            for text in payload["input"]:
                related = (
                    "motorbike" in text
                    or "two-wheeler" in text
                    or text == "conversation acquaintances social"
                )
                vectors.append([1, 0] if related else [0, 1])
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": vector}
                        for index, vector in enumerate(vectors)
                    ],
                    "usage": {"prompt_tokens": 20, "total_tokens": 20},
                },
            )
        if not payload.get("stream"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "null"}}]})
        context = json.loads(payload["messages"][1]["content"])
        self.contexts.append(context)
        text = (
            '{"decision":"accept"}'
            if context.get("social", {}).get("phase") == "invitation"
            else "I remember what you said."
        )
        wire = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})
        return httpx.Response(200, content=(wire + "\ndata: [DONE]\n").encode())


def configuration(**values):
    return Settings(
        provider="local_openai",
        dialogue_model="fixture-dialogue",
        embedding=EmbeddingConfig(provider="local_openai", model="fixture-embedding"),
        ai_gateway=GatewayConfig(
            foreground=DailyBudget(max_requests=100),
            background=DailyBudget(max_requests=100),
        ),
        **values,
    )


def memories(repo, profile, branch, actor, stamp, *, session_id=None):
    result = []
    for event_id, text, importance in (
        ("semantic-wanted", "I heard Franklin describe a loose motorbike bell.", 0),
        ("lexical-distractor", "I looked at the crowded avenue.", 1),
    ):
        repo.record_event(
            profile,
            branch,
            {
                "event_id": event_id,
                "game_datetime": stamp,
                "protagonist_id": "franklin",
                "event_type": "fixture",
                "observer_ids": [actor],
                "provenance": "heard",
                "confidence": 0.8,
                "payload": {"text": text},
            },
        )
        result.append(
            repo.add_memory(
                profile,
                branch,
                actor,
                "franklin",
                stamp,
                text,
                [event_id],
                "heard",
                importance=importance,
                session_id=session_id,
            )
        )
    return result


@pytest.mark.parametrize("erase_after_fetch", [False, True])
async def test_foreground_runtime_routes_semantic_memory_into_context(
    client,
    monkeypatch,
    erase_after_fetch,
):
    runtime = client.app.state.runtime
    boundary = EmbeddingAndDialogueHTTP()
    transport = httpx.MockTransport(boundary)

    def factory(settings, repository, **options):
        return create_provider(settings, repository, transport=transport, **options)

    runtime.provider_factory = factory
    await runtime.configure_provider(configuration(database_path=runtime.repo.path))
    runtime.memory_index.provider.transport = transport
    sid, _ = await open_ready(client)
    session = runtime.sessions[sid]
    wanted, distractor = memories(
        runtime.repo,
        session.request.profile_id,
        session.branch_id,
        "npc-1",
        session.game_datetime,
        session_id=sid,
    )
    query = "What needed attention on his two-wheeler?"
    lexical = runtime.repo.retrieve_memories(
        session.request.profile_id,
        session.branch_id,
        "npc-1",
        "franklin",
        session.game_datetime,
        query,
        session_id=sid,
    )
    assert lexical[0]["memory_id"] == distractor["memory_id"]
    if erase_after_fetch:
        original = runtime.repo.get_relationship

        def delete_after_fetch(*args, **kwargs):
            runtime.repo.delete_event(session.request.profile_id, "semantic-wanted")
            return original(*args, **kwargs)

        monkeypatch.setattr(runtime.repo, "get_relationship", delete_after_fetch)
    response, _ = await utterance(client, sid, text=query)
    assert response.status_code == 200
    await asyncio.wait_for(session.task, 3)
    context = boundary.contexts[0]
    identifiers = [memory["memory_id"] for memory in context["memories"]]
    if erase_after_fetch:
        assert wanted["memory_id"] not in identifiers
        assert all("motorbike" not in memory["text"] for memory in context["memories"])
    else:
        assert identifiers[0] == wanted["memory_id"]
    assert wanted["text"] in boundary.embedding_inputs
    requests = runtime.repo.connection.execute(
        "SELECT budget_class FROM ai_requests WHERE model='fixture-embedding'",
    ).fetchall()
    assert len(requests) == 2 and {row[0] for row in requests} == {"foreground"}


@pytest.mark.parametrize("erase_after_fetch", [False, True])
async def test_social_engine_routes_semantic_memory_through_chatter_budget(
    scene,
    monkeypatch,
    erase_after_fetch,
):
    boundary = EmbeddingAndDialogueHTTP()
    transport = httpx.MockTransport(boundary)
    settings = configuration(release=False)
    settings.ai_gateway.chatter = ChatterBudget(max_requests=100)
    await scene.provider.aclose()
    provider = create_provider(settings, scene.repo, transport=transport)
    scene.engine.provider = provider
    scene.engine.memory_index = MemoryIndex(
        scene.repo, settings, gate=provider.gate, transport=transport
    )
    wanted, distractor = memories(
        scene.repo,
        "profile",
        scene.branch,
        "avery",
        scene.env.game_datetime.isoformat(),
    )
    lexical = scene.repo.retrieve_memories(
        "profile",
        scene.branch,
        "avery",
        "franklin",
        scene.env.game_datetime.isoformat(),
        "conversation acquaintances social",
    )
    assert lexical[0]["memory_id"] == distractor["memory_id"]
    if erase_after_fetch:
        original = scene.engine.store.context

        def delete_after_fetch(*args, **kwargs):
            scene.repo.delete_event("profile", "semantic-wanted")
            return original(*args, **kwargs)

        monkeypatch.setattr(scene.engine.store, "context", delete_after_fetch)
    try:
        await scene.engine.invite(scene.start, lambda: scene.env)
        context = next(
            context for context in boundary.contexts if context["actor"]["actor_id"] == "avery"
        )
        identifiers = [memory["memory_id"] for memory in context["memories"]]
        if erase_after_fetch:
            assert wanted["memory_id"] not in identifiers
            assert all("motorbike" not in memory["text"] for memory in context["memories"])
        else:
            assert identifiers[0] == wanted["memory_id"]
        assert wanted["text"] in boundary.embedding_inputs
        requests = scene.repo.connection.execute(
            "SELECT budget_class FROM ai_requests WHERE model='fixture-embedding'",
        ).fetchall()
        assert len(requests) == 2 and {row[0] for row in requests} == {"chatter"}
        for context in boundary.contexts:
            if context["actor"]["actor_id"] != "avery":
                assert context["memories"] == []
    finally:
        await provider.aclose()
