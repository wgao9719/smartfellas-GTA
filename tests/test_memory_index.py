"""Semantic plumbing, scope, deletion, and fixed recall checks with synthetic vectors.

Only the embedding HTTP boundary is fake. These tests do not measure any real
embedding model's semantic quality or platform-specific native performance.
"""

import asyncio
import json
from pathlib import Path
from threading import Event

import httpx
import pytest

from service.lls.embeddings import EmbeddingConfig
from service.lls.gateway_config import DailyBudget, GatewayConfig
from service.lls.memory_index import MemoryIndex
from service.lls.outbound import OutboundGate
from service.lls.settings import Settings
from service.lls.storage import Repository

T0 = "2025-01-01T09:00:00Z"
T1 = "2025-01-01T10:00:00Z"
T2 = "2025-01-01T11:00:00Z"
LATER = "2025-01-11T13:00:00Z"
REGRESSION = json.loads((Path(__file__).parents[1] / "data/memory_regression.json").read_text())
TOPICS = [
    ("bicycle", "bell", "two-wheeler"),
    ("canopy", "overhead shelter"),
    ("coffee", "decaffeinated", "caffeine-free drink"),
    ("sewing", "knot", "needlework"),
    ("garden", "flower bed"),
    ("book", "title", "reading material"),
    ("painting", "blue", "artwork"),
    ("bus", "stop", "public transit"),
    ("music", "quiet", "recording", "sound preference"),
    ("walking", "route", "stroll"),
]
PARAPHRASES = [
    "What needed attention on his two-wheeler?",
    "Was overhead shelter just a proposal?",
    "Who preferred a caffeine-free drink?",
    "What did I explain about needlework?",
    "Did he show interest in a flower bed?",
    "Did I lend him reading material?",
    "Which artwork design did he like?",
    "Did I track his public transit travel?",
    "What was his sound preference?",
    "Did we take a stroll together?",
]


class FixtureEmbeddingHTTP:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        payload = json.loads(request.content)
        self.requests.append(payload)
        vectors = []
        for text in payload["input"]:
            lower = text.lower()
            vector = [float(any(term in lower for term in terms)) for terms in TOPICS]
            vector.append(float(not any(vector)))
            vectors.append(vector)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": vector} for index, vector in enumerate(vectors)
                ],
                "usage": {"prompt_tokens": 20, "total_tokens": 20},
            },
        )


def settings(**changes):
    return Settings(
        embedding=EmbeddingConfig(provider="local_openai", model="fixture-embedding", **changes),
        ai_gateway=GatewayConfig(
            foreground=DailyBudget(max_requests=1000),
            background=DailyBudget(max_requests=1000),
        ),
    )


def index_for(repo, wire=None, configuration=None):
    configuration = configuration or settings()
    gate = OutboundGate(configuration, repo)
    wire = wire or FixtureEmbeddingHTTP()
    return (
        MemoryIndex(repo, configuration, gate=gate, transport=httpx.MockTransport(wire)),
        gate,
        wire,
    )


def seed(
    repo,
    branch,
    *,
    identifier="event-one",
    actor="avery",
    protagonist="franklin",
    text="Franklin told me about his bicycle.",
    at=T1,
    profile="save-a",
    **options,
):
    repo.record_event(
        profile,
        branch,
        {
            "event_id": identifier,
            "game_datetime": at,
            "protagonist_id": protagonist,
            "event_type": "fixture",
            "observer_ids": [actor],
            "provenance": "heard",
            "confidence": 0.8,
            "payload": {"text": text},
        },
    )
    return repo.add_memory(
        profile,
        branch,
        actor,
        protagonist,
        at,
        text,
        [identifier],
        "heard",
        **options,
    )


def scope(branch, *, actor="avery", protagonist="franklin", at=LATER, profile="save-a"):
    return profile, branch, actor, protagonist, at


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "semantic.sqlite3") as repository:
        repository.register_actor("save-a", {"id": "avery"})
        repository.register_actor("save-a", {"id": "bea"})
        yield repository


async def test_disabled_mode_exactly_preserves_lexical_results_without_gate(repo):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    expected = repo.retrieve_memories(*scope(branch), "bicycle")
    index = MemoryIndex(repo, Settings(), gate=None)
    assert await index.retrieve_memories(*scope(branch), "bicycle") == expected
    assert index.provider is None


@pytest.mark.parametrize("mode", ["semantic", "lexical_fallback"])
async def test_fixed_fifty_questions_after_reopen_and_ten_days(tmp_path, mode):
    location = tmp_path / "fixed-evaluation.sqlite3"
    assert len(REGRESSION["questions"]) == 50
    with Repository(location) as repo:
        repo.register_actor("save-a", {"id": "avery"})
        repo.register_actor("save-a", {"id": "bea"})
        branch = repo.open_timeline("save-a", T0)
        for source in REGRESSION["events"]:
            repo.record_event("save-a", branch, source)
            repo.add_memory(
                "save-a",
                branch,
                "avery",
                "franklin",
                source["game_datetime"],
                source["payload"]["text"],
                [source["event_id"]],
                source["provenance"],
                confidence=source["confidence"],
            )
        index, gate, wire = index_for(repo)
        report = await index.rebuild(*scope(branch))
        assert report == {"cleared": 0, "indexed": 10, "reason": None}
        await gate.aclose()
    with Repository(location) as reopened:
        index, gate, wire = index_for(reopened)
        if mode == "lexical_fallback":
            # A genuinely unavailable HTTP provider must activate the fallback.
            def unavailable(_):
                raise httpx.ConnectError("synthetic provider unavailable")

            index.provider.transport = httpx.MockTransport(unavailable)
        for question in REGRESSION["questions"]:
            found = await index.retrieve_memories(
                *scope(branch, actor=question["actor_id"], protagonist=question["protagonist_id"]),
                question["query"],
                limit=1,
            )
            expected = question["expected_top_source_event_ids"]
            if not expected:
                assert found == [], question["id"]
            else:
                assert found[0]["source_event_ids"] == expected, question["id"]
                assert found[0]["retrieval_method"] == (
                    "semantic" if mode == "semantic" else "lexical"
                )
                if question["required_qualification"]:
                    assert found[0]["text"] == question["required_qualification"]
        if mode == "semantic":
            # Reopening preserved all document vectors. Only query text was sent.
            assert len(wire.requests) == 30
            assert all(len(request["input"]) == 1 for request in wire.requests)
        await gate.aclose()


async def test_ten_paraphrases_exercise_semantic_ranking(repo):
    branch = repo.open_timeline("save-a", T0)
    for source in REGRESSION["events"]:
        seed(
            repo,
            branch,
            identifier=source["event_id"],
            text=source["payload"]["text"],
            at=source["game_datetime"],
        )
    index, gate, wire = index_for(repo)
    for question, source in zip(PARAPHRASES, REGRESSION["events"], strict=True):
        result = await index.retrieve_memories(*scope(branch), question, limit=1)
        assert result[0]["source_event_ids"] == [source["event_id"]]
        assert result[0]["retrieval_method"] == "semantic"
    await gate.aclose()


async def test_scope_filters_apply_before_outbound_and_after_indexing(repo):
    branch = repo.open_timeline("save-a", T0)
    wanted = seed(repo, branch)
    seed(repo, branch, identifier="other-actor", actor="bea", text="Secret bicycle for Bea.")
    seed(repo, branch, identifier="other-player", protagonist="michael", text="Michael bicycle.")
    seed(repo, branch, identifier="future", at=T2, text="Future bicycle.")
    seed(repo, branch, identifier="expired", text="Expired bicycle.", expires_at=T2)
    index, gate, wire = index_for(repo)
    result = await index.retrieve_memories(*scope(branch), "bicycle")
    assert {row["source_event_ids"][0] for row in result} == {"event-one", "future"}
    sent = " ".join(text for request in wire.requests for text in request["input"])
    assert all(text not in sent for text in ("Secret", "Michael", "Expired"))
    earlier = await index.retrieve_memories(*scope(branch, at=T1), "bicycle")
    assert {row["source_event_ids"][0] for row in earlier} == {"event-one", "expired"}
    child = repo.open_timeline("save-a", T1, parent_branch_id=branch)
    seed(repo, child, identifier="child-only", text="Child bicycle.", at=T2)
    child_rows = await index.retrieve_memories(*scope(child), "bicycle")
    assert {row["source_event_ids"][0] for row in child_rows} == {"event-one", "child-only"}
    assert wanted["memory_id"] in {row["memory_id"] for row in child_rows}
    await gate.aclose()


@pytest.mark.parametrize("phase", ["query", "documents"])
async def test_delete_source_while_provider_waits_cannot_restore_vector(repo, phase):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    entered, release = asyncio.Event(), asyncio.Event()
    wire = FixtureEmbeddingHTTP()
    calls = []

    async def held(request):
        calls.append(json.loads(request.content)["input"])
        if len(calls) == (1 if phase == "query" else 2):
            entered.set()
            await release.wait()
        return wire(request)

    index, gate, _ = index_for(repo, held)
    task = asyncio.create_task(index.retrieve_memories(*scope(branch), "bicycle"))
    await asyncio.wait_for(entered.wait(), 2)
    assert repo.delete_event("save-a", "event-one")
    release.set()
    assert await task == []
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0
    assert len(calls) == (1 if phase == "query" else 2)
    await gate.aclose()


async def test_changed_memory_during_embedding_uses_current_text_without_stale_vector(repo):
    branch = repo.open_timeline("save-a", T0)
    saved = seed(repo, branch)
    wire = FixtureEmbeddingHTTP()
    count = 0

    def edit_on_document(request):
        nonlocal count
        count += 1
        if count == 2:
            repo.update_memory("save-a", saved["memory_id"], text="Corrected garden statement.")
        return wire(request)

    index, gate, _ = index_for(repo, edit_on_document)
    result = await index.retrieve_memories(*scope(branch), "bicycle")
    assert result[0]["text"] == "Corrected garden statement."
    assert result[0]["retrieval_method"] == "lexical"
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0
    await gate.aclose()


async def test_edit_delete_rebuild_and_model_change_invalidate_cached_vectors(repo):
    branch = repo.open_timeline("save-a", T0)
    saved = seed(repo, branch)
    index, gate, wire = index_for(repo)
    await index.retrieve_memories(*scope(branch), "bicycle")
    assert len(wire.requests) == 2
    repo.update_memory("save-a", saved["memory_id"], text="I discussed the garden.")
    await index.retrieve_memories(*scope(branch), "garden")
    assert len(wire.requests) == 4
    vector_row = repo.connection.execute("SELECT * FROM memory_vectors").fetchone()
    assert "garden" not in bytes(vector_row["vector"]).decode()
    first_key = vector_row["model"]
    changed = settings()
    changed.embedding.model = "different-fixture-model"
    await gate.aclose()
    index, gate, wire = index_for(repo, configuration=changed)
    await index.retrieve_memories(*scope(branch), "garden")
    assert len(wire.requests) == 2
    assert repo.connection.execute("SELECT model FROM memory_vectors").fetchone()[0] != first_key
    assert await index.rebuild(*scope(branch)) == {"cleared": 1, "indexed": 1, "reason": None}
    assert repo.delete_memory("save-a", saved["memory_id"])
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0
    assert await index.retrieve_memories(*scope(branch), "garden") == []
    await gate.aclose()


async def test_corrupt_cached_vector_is_replaced_and_malformed_provider_falls_back(repo):
    branch = repo.open_timeline("save-a", T0)
    saved = seed(repo, branch)
    index, gate, wire = index_for(repo)
    await index.retrieve_memories(*scope(branch), "bicycle")
    repo.connection.execute("UPDATE memory_vectors SET vector=?", (b"{invalid",))
    repo.connection.commit()
    assert (await index.retrieve_memories(*scope(branch), "bicycle"))[0][
        "retrieval_method"
    ] == "semantic"
    assert len(wire.requests) == 4
    index.provider.transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0]}]}),
    )
    result = await index.retrieve_memories(*scope(branch), "bicycle")
    assert result[0]["memory_id"] == saved["memory_id"]
    assert result[0]["retrieval_method"] == "lexical"
    assert index.last_status["reason"] == "ProviderResponseError"
    await gate.aclose()


async def test_invalid_canonical_source_never_reaches_provider(repo):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    raw = json.loads(repo.connection.execute("SELECT data FROM events").fetchone()[0])
    raw["observer_ids"] = ["bea"]
    repo.connection.execute("UPDATE events SET data=?", (json.dumps(raw),))
    repo.connection.commit()
    index, gate, wire = index_for(repo)
    assert await index.retrieve_memories(*scope(branch), "bicycle") == []
    assert wire.requests == []
    await gate.aclose()


async def test_revalidate_rejects_deleted_changed_and_wrong_scope_results(repo):
    branch = repo.open_timeline("save-a", T0)
    first = seed(repo, branch)
    second = seed(repo, branch, identifier="second", text="A garden memory.")
    index, gate, _ = index_for(repo)
    rows = await index.retrieve_memories(*scope(branch), "bicycle")
    assert index.revalidate_memories(*scope(branch), "bicycle", memories=rows) == rows
    assert index.revalidate_memories(*scope(branch, actor="bea"), memories=rows) == []
    repo.update_memory("save-a", first["memory_id"], text="A correction.")
    repo.delete_memory("save-a", second["memory_id"])
    assert index.revalidate_memories(*scope(branch), memories=rows) == []
    await gate.aclose()


async def test_index_work_and_returned_token_cost_are_bounded(repo):
    branch = repo.open_timeline("save-a", T0)
    for number in range(20):
        seed(repo, branch, identifier=f"event-{number}", text=f"Bicycle memory {number}.")
    index, gate, wire = index_for(repo, configuration=settings(max_index_per_query=3, batch_size=2))
    rows = await index.retrieve_memories(*scope(branch), "bicycle", token_budget=160)
    assert sum(row["token_cost"] for row in rows) <= 160
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 3
    assert [len(request["input"]) for request in wire.requests] == [1, 2, 1]
    await gate.aclose()


async def test_deletion_during_cached_lookup_prevents_document_transmission(repo, monkeypatch):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    index, gate, wire = index_for(repo)
    original = index._cached

    def delete_during_lookup(*args):
        cached = original(*args)
        repo.delete_event("save-a", "event-one")
        return cached

    monkeypatch.setattr(index, "_cached", delete_during_lookup)
    assert await index.retrieve_memories(*scope(branch), "bicycle") == []
    assert [request["input"] for request in wire.requests] == [["bicycle"]]
    await gate.aclose()


@pytest.mark.parametrize("phase", ["query", "documents"])
async def test_deletion_while_waiting_for_gateway_rejects_before_http(repo, monkeypatch, phase):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    index, gate, wire = index_for(repo)
    entered = asyncio.Event()
    original = index.provider.embed
    calls = 0

    async def hold_admission(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (1 if phase == "query" else 2):
            await gate.lock.acquire()
            entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(index.provider, "embed", hold_admission)
    task = asyncio.create_task(index.retrieve_memories(*scope(branch), "bicycle"))
    await asyncio.wait_for(entered.wait(), 2)
    repo.delete_event("save-a", "event-one")
    gate.lock.release()
    assert await task == []
    assert len(wire.requests) == (0 if phase == "query" else 1)
    assert not gate.active
    await gate.aclose()


@pytest.mark.parametrize("stage", ["disabled", "snapshot", "cached", "rank"])
async def test_cancellation_joins_database_read_before_return(repo, monkeypatch, stage):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    if stage == "disabled":
        index, gate = MemoryIndex(repo, Settings(), gate=None), None
        target, name = repo, "retrieve_memories"
    else:
        index, gate, _ = index_for(repo)
        target, name = index, "_" + stage
    original = getattr(target, name)
    entered, release, finished = Event(), Event(), Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3), "test did not release its database reader"
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(target, name, blocked)
    task = asyncio.create_task(index.retrieve_memories(*scope(branch), "bicycle"))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert repo.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    if gate is not None:
        await gate.aclose()


async def test_profile_and_ephemeral_session_isolation_and_erasure(repo):
    branch = repo.open_timeline("save-a", T0)
    seed(repo, branch)
    repo.register_actor("save-b", {"id": "avery"})
    other_branch = repo.open_timeline("save-b", T0)
    seed(repo, other_branch, profile="save-b", text="Other profile bicycle secret.")
    repo.start_session("save-a", "temporary", T1)
    repo.register_actor(
        "save-a",
        {
            "id": "ambient",
            "actor_kind": "ambient_ephemeral",
            "session_id": "temporary",
        },
    )
    seed(
        repo,
        branch,
        actor="ambient",
        identifier="ambient",
        session_id="temporary",
        text="An ephemeral bicycle memory.",
    )
    index, gate, wire = index_for(repo)
    found = await index.retrieve_memories(*scope(branch), "bicycle")
    assert [memory["source_event_ids"] for memory in found] == [["event-one"]]
    assert "Other profile" not in str(wire.requests)
    assert await index.retrieve_memories(*scope(branch, actor="ambient"), "bicycle") == []
    assert (
        await index.retrieve_memories(
            *scope(branch, actor="ambient"),
            "bicycle",
            session_id="wrong",
        )
        == []
    )
    found = await index.retrieve_memories(
        *scope(branch, actor="ambient"),
        "bicycle",
        session_id="temporary",
    )
    assert found[0]["retrieval_method"] == "semantic"
    repo.end_session("save-a", "temporary")
    assert (
        index.revalidate_memories(
            *scope(branch, actor="ambient"),
            memories=found,
            session_id="temporary",
        )
        == []
    )
    assert (
        repo.connection.execute(
            "SELECT COUNT(*) FROM memory_vectors WHERE memory_id=?",
            (found[0]["memory_id"],),
        ).fetchone()[0]
        == 0
    )
    await gate.aclose()


async def test_recreated_memory_identifier_does_not_accept_old_inflight_vector(repo):
    branch = repo.open_timeline("save-a", T0)
    saved = seed(repo, branch)
    wire = FixtureEmbeddingHTTP()
    calls = 0

    def recreate_on_document(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            repo.delete_memory("save-a", saved["memory_id"])
            # Erased raw sources cannot be reused. A separate observation can still
            # reuse a caller-supplied memory ID and must reject the old in-flight vector.
            repo.record_event("save-a", branch, {
                "event_id": "new-observation", "game_datetime": T1,
                "protagonist_id": "franklin", "provenance": "heard",
                "observer_ids": ["avery"], "payload": {"text": saved["text"]},
            })
            recreated = repo.add_memory(
                "save-a",
                branch,
                "avery",
                "franklin",
                T1,
                saved["text"],
                ["new-observation"],
                "heard",
                memory_id=saved["memory_id"],
            )
            assert recreated["created_at"] != saved["created_at"]
        return wire(request)

    index, gate, _ = index_for(repo, recreate_on_document)
    result = await index.retrieve_memories(*scope(branch), "bicycle")
    assert result[0]["retrieval_method"] == "lexical"
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0
    await gate.aclose()


async def test_partial_rebuild_reports_successful_rows_before_provider_failure(repo):
    branch = repo.open_timeline("save-a", T0)
    for number in range(10):
        seed(repo, branch, identifier=f"event-{number}", text=f"Bicycle memory {number}.")
    wire = FixtureEmbeddingHTTP()

    def fail_second_batch(request):
        if wire.requests:
            raise httpx.ConnectError("synthetic provider unavailable")
        return wire(request)

    index, gate, _ = index_for(repo, fail_second_batch)
    assert await index.rebuild(*scope(branch)) == {
        "cleared": 0,
        "indexed": 8,
        "reason": "ConnectError",
    }
    assert repo.connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 8
    await gate.aclose()
