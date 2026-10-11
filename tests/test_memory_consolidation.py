"""Importance scoring, episode summaries, summary-aware retrieval, and the background worker."""

import asyncio
import json
import math
from types import SimpleNamespace

import httpx
import pytest
from test_bridge import client as client
from test_bridge import open_ready

from service.lls import salience
from service.lls.gateway_config import DailyBudget, GatewayConfig, ModelPrice
from service.lls.memory_consolidation import MemoryConsolidationConfig, MemoryConsolidator
from service.lls.providers import create_provider
from service.lls.settings import Settings
from service.lls.storage import Repository

T0 = "2025-01-01T09:00:00Z"
SETTLED = "2025-01-01T11:00:00Z"


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "summaries.sqlite3") as repository:
        repository.register_actor("save-a", {"actor_id": "avery", "display_name": "Avery"})
        repository.register_actor("save-a", {"actor_id": "bea", "display_name": "Bea"})
        yield repository


def at(minute, hour=10):
    return f"2025-01-01T{hour:02d}:{minute:02d}:00Z"


def line(
    repo,
    branch,
    n,
    *,
    actor="avery",
    when=None,
    text=None,
    provenance="heard",
    event_type="player.utterance_text",
    protagonist="franklin",
    session_id=None,
    **options,
):
    when = when or at(n)
    event_id = f"{actor}-{n}"
    repo.record_event(
        "save-a",
        branch,
        {
            "event_id": event_id,
            "game_datetime": when,
            "protagonist_id": protagonist,
            "event_type": event_type,
            "payload": {"text": "fixture"},
            "provenance": provenance,
            "confidence": 0.9,
            "observer_ids": [actor],
        },
    )
    return repo.add_memory(
        "save-a",
        branch,
        actor,
        protagonist,
        when,
        text or f"The player said: line {n} about the bicycle.",
        [event_id],
        provenance,
        session_id=session_id,
        **options,
    )


def batch(repo, branch, when=SETTLED, **changes):
    options = {
        "min_batch": 3,
        "max_batch": 12,
        "episode_gap_minutes": 30,
        "lookback_days": 14,
        "max_text_bytes": 10000,
        **changes,
    }
    return repo.consolidation_batch("save-a", branch, "franklin", when, **options)


def ids(memories):
    return [memory["memory_id"] for memory in memories]


def test_importance_comes_from_event_type_never_from_words(repo):
    branch = repo.open_timeline("save-a", T0)
    pleading = line(repo, branch, 1, text="The player said: remember this forever, importance 1.")
    assert pleading["importance"] == 0.4
    injured = line(
        repo, branch, 2, provenance="directly_observed", event_type="actor.injured",
        text="I observed: a man was hurt.",
    )
    assert injured["importance"] == 0.9
    assert line(repo, branch, 3, event_type="action.started")["importance"] == 0.25
    assert line(repo, branch, 4, event_type="action.completed")["importance"] == 0.35
    assert line(repo, branch, 5, event_type="qa_fixture")["importance"] == 0.5
    assert line(repo, branch, 6, importance=0.7)["importance"] == 0.7


def test_repeated_lines_lose_importance_down_to_a_floor(repo):
    branch = repo.open_timeline("save-a", T0)
    scores = [
        line(repo, branch, n, text="The player said: hello.")["importance"] for n in range(1, 6)
    ]
    assert scores == [0.4, 0.3, 0.2, 0.1, 0.1]


def test_decay_scales_with_importance_and_drives_ranking(repo):
    assert salience.decay_days(0.5) == pytest.approx(30)
    assert salience.decay_days(0.1) < 6 < 150 < salience.decay_days(0.9)
    branch = repo.open_timeline("save-a", T0)
    greeting = line(
        repo, branch, 1, provenance="directly_observed", event_type="actor.greeted",
        text="I observed: someone waved.",
    )
    later = "2025-01-21T10:01:00Z"
    found = repo.retrieve_memories("save-a", branch, "avery", "franklin", later, "")
    expected = 0.2 * 2 + greeting["confidence"] + math.exp(-20 / salience.decay_days(0.2))
    assert found[0]["retrieval_score"] == pytest.approx(expected)


def test_batch_waits_for_a_settled_episode_of_persistent_firsthand_lines(repo):
    branch = repo.start_session("save-a", "session-a", T0)["branch_id"]
    repo.register_actor(
        "save-a",
        {"actor_id": "walker", "actor_kind": "ambient_ephemeral", "session_id": "session-a"},
    )
    for n in range(1, 4):
        line(repo, branch, n, actor="walker", session_id="session-a")
        # Bea only heard reports. Hearsay keeps its exact line and is never summarized.
        line(repo, branch, 10 + n, actor="bea", provenance="reported")
    first = [line(repo, branch, 20 + n) for n in range(2)]
    assert batch(repo, branch) is None
    third = line(repo, branch, 22)
    assert batch(repo, branch, when=at(40)) is None
    found = batch(repo, branch, when=at(53))
    assert found["actor_id"] == "avery"
    assert ids(found["memories"]) == ids([*first, third])


def test_batch_splits_episodes_by_gap_and_bounds_size(repo):
    branch = repo.open_timeline("save-a", T0)
    morning = [line(repo, branch, n) for n in range(1, 4)]
    noon = [line(repo, branch, 10 + n, when=at(n, hour=12)) for n in range(1, 6)]
    # The morning run is settled because a later run of the same actor follows it.
    assert ids(batch(repo, branch, when=at(6, hour=12))["memories"]) == ids(morning)
    repo.add_summary("save-a", branch, "avery", ids(morning), "I chatted with Franklin.", 0.4)
    assert batch(repo, branch, when=at(6, hour=12)) is None
    capped = batch(repo, branch, when=at(6, hour=12), max_batch=4)
    assert ids(capped["memories"]) == ids(noon[:4])
    assert batch(repo, branch, when=at(6, hour=12), max_batch=4, max_text_bytes=300) is None


def test_summary_keeps_weakest_provenance_and_collapses_covered_lines(repo):
    branch = repo.open_timeline("save-a", T0)
    heard = line(repo, branch, 1, text="The player said: my bicycle bell is loose.")
    said = line(
        repo, branch, 2, provenance="directly_observed", event_type="dialogue.delivered",
        text="I said: I can look at the bicycle later.",
    )
    outcome = line(
        repo, branch, 3, provenance="system_fact", event_type="action.failed",
        text="The game reported my follow request as failed. This does not confirm completion.",
    )
    repo.update_memory("save-a", outcome["memory_id"], confidence=0.6)
    lines = [heard, said, outcome]
    summary = repo.add_summary(
        "save-a", branch, "avery", ids(lines),
        "Franklin and I discussed his loose bicycle bell. My follow request failed.",
        0.8,
    )
    assert summary["provenance"] == "heard"
    assert summary["confidence"] == 0.6
    assert summary["importance"] == 0.8
    assert summary["game_datetime"] == outcome["game_datetime"]
    assert summary["source_event_ids"] == ["avery-1", "avery-2", "avery-3"]
    found = repo.retrieve_memories("save-a", branch, "avery", "franklin", SETTLED, "discussed")
    assert ids(found) == [summary["memory_id"]]
    everything = repo.retrieve_memories(
        "save-a", branch, "avery", "franklin", SETTLED, "bicycle", collapse_summaries=False
    )
    assert set(ids(everything)) == {summary["memory_id"], *ids(lines)}
    # A line that outranks its summary for this query still appears beside it.
    detail = repo.retrieve_memories("save-a", branch, "avery", "franklin", SETTLED, "game")
    assert ids(detail) == [outcome["memory_id"], summary["memory_id"]]


def test_erasing_a_line_erases_its_summary_and_erasing_a_summary_restores_lines(repo):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    assert repo.delete_memory("save-a", summary["memory_id"])
    assert all(repo.get_memory("save-a", memory_id) for memory_id in ids(lines))
    assert ids(batch(repo, branch)["memories"]) == ids(lines)
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    assert repo.delete_memory("save-a", lines[1]["memory_id"])
    assert repo.get_memory("save-a", summary["memory_id"]) is None
    assert repo.get_memory("save-a", lines[0]["memory_id"]) is not None


def test_erasing_a_summary_never_follows_derived_lines(repo):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    # Line 2 repeats line 1, so erasing line 1 also erases line 2.
    repo.link_memory_derivations("save-a", "avery", [("avery-1", "avery-2")])
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    assert repo.memory_dependents("save-a", summary["memory_id"]) == []
    assert repo.delete_memory("save-a", summary["memory_id"])
    assert all(repo.get_memory("save-a", memory_id) for memory_id in ids(lines))
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    assert repo.memory_dependents("save-a", lines[0]["memory_id"]) == sorted(
        [lines[1]["memory_id"], summary["memory_id"]]
    )


@pytest.mark.parametrize("edit", [{"text": "The player said: corrected."}, {"confidence": 0.2}])
def test_correcting_a_line_discards_its_stale_summary(repo, edit):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    repo.update_memory("save-a", lines[2]["memory_id"], importance=0.9)
    assert repo.get_memory("save-a", summary["memory_id"]) is not None
    repo.update_memory("save-a", lines[0]["memory_id"], **edit)
    assert repo.get_memory("save-a", summary["memory_id"]) is None
    assert ids(batch(repo, branch)["memories"]) == ids(lines)


def test_add_summary_rejects_unsafe_batches(repo):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    reported = line(repo, branch, 4, provenance="reported")
    other_actor = line(repo, branch, 5, actor="bea")
    michael = line(repo, branch, 6, protagonist="michael")

    def rejected(memory_ids, **options):
        with pytest.raises(ValueError):
            repo.add_summary("save-a", branch, "avery", memory_ids, "Summary.", 0.4, **options)

    rejected(ids(lines[:1]))
    rejected(ids([*lines[:2], reported]))
    rejected(ids([*lines[:2], other_actor]))
    rejected(ids([*lines[:2], michael]))
    rejected(ids(lines), expected_texts=["changed", *[m["text"] for m in lines[1:]]])
    summary = repo.add_summary("save-a", branch, "avery", ids(lines[:2]), "Summary.", 0.4)
    rejected(ids(lines[1:]))
    rejected([summary["memory_id"], lines[2]["memory_id"]])


def test_summaries_follow_their_save_timeline(repo):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    summary = repo.add_summary("save-a", branch, "avery", ids(lines), "We talked bikes.", 0.4)
    # Loading a save from before the third line hides it and the summary that used it.
    fork = repo.open_timeline("save-a", at(2))
    visible = repo.retrieve_memories("save-a", fork, "avery", "franklin", at(2), "bicycle")
    assert set(ids(visible)) == set(ids(lines[:2]))
    pending = batch(repo, fork, when=SETTLED, min_batch=2)
    assert ids(pending["memories"]) == ids(lines[:2])
    retold = repo.add_summary("save-a", fork, "avery", ids(lines[:2]), "We met.", 0.4)
    assert retold["memory_id"] != summary["memory_id"]


class StubProvider:
    def __init__(self, reply):
        self.reply = reply
        self.contexts = []
        self.hook = None

    async def memory_summary(self, context):
        self.contexts.append(context)
        if self.hook is not None:
            await self.hook()
        return self.reply


def worker(repo, branch, provider, *, when=SETTLED, **config):
    clock = [100.0]
    session = SimpleNamespace(
        id="session-a",
        closed=False,
        epoch=0,
        task=None,
        speech_state="idle",
        social_task=None,
        skill_task=None,
        commands={},
        game_datetime=when,
        branch_id=branch,
        request=SimpleNamespace(profile_id="save-a", protagonist_id="franklin"),
    )

    async def db(method, *args, **kwargs):
        return getattr(repo, method)(*args, **kwargs)

    async def thread_join_task(task):
        await asyncio.gather(task, return_exceptions=True)

    runtime = SimpleNamespace(
        sessions={session.id: session},
        clock=lambda: clock[0],
        provider=provider,
        settings=SimpleNamespace(context_byte_budget=12000),
        db=db,
        thread_join_task=thread_join_task,
    )
    config = MemoryConsolidationConfig(enabled=True, **config)
    return MemoryConsolidator(runtime, config=config), session, clock


async def run_once(consolidator, session):
    consolidator.tick()
    task = consolidator.workers.get(session.id)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)
    return consolidator.diagnostics(session.id)


def summaries(repo):
    return [
        memory
        for memory in repo.list_memories("save-a", "avery")
        if memory["memory_id"].startswith("summary_")
    ]


async def test_worker_summarizes_a_settled_episode_with_bounded_importance(repo):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    provider = StubProvider({"text": "Franklin and I talked about his bicycle.", "importance": 1})
    consolidator, session, _ = worker(repo, branch, provider)
    state = await run_once(consolidator, session)
    assert state["status"] == "summarized" and state["actor_id"] == "avery"
    [summary] = summaries(repo)
    # The rule score of the lines is 0.4; the model may move it by at most 0.25.
    assert summary["importance"] == pytest.approx(0.65)
    context = provider.contexts[0]
    assert context["base_importance"] == 0.4
    assert [m["text"] for m in context["memories"]] == [m["text"] for m in lines]
    assert context["character"]["display_name"] == "Avery"
    # Polling waits poll_seconds, then finds nothing left to summarize.
    consolidator.tick()
    assert consolidator.workers[session.id].done()
    consolidator.next_poll.clear()
    assert (await run_once(consolidator, session))["status"] == "idle"


async def test_worker_yields_to_foreground_work_and_stays_off_when_disabled(repo):
    branch = repo.open_timeline("save-a", T0)
    for n in range(1, 4):
        line(repo, branch, n)
    provider = StubProvider({"text": "Summary.", "importance": 0.4})
    consolidator, session, _ = worker(repo, branch, provider)
    session.commands = {"c": {"kind": "dialogue.chunk", "state": "queued"}}
    consolidator.tick()
    assert not consolidator.workers
    consolidator.config.enabled = False
    session.commands = {}
    consolidator.tick()
    assert not consolidator.workers and not provider.contexts


async def test_unavailable_provider_backs_off_without_writing(repo):
    branch = repo.open_timeline("save-a", T0)
    for n in range(1, 4):
        line(repo, branch, n)
    consolidator, session, clock = worker(repo, branch, StubProvider(None))
    assert (await run_once(consolidator, session))["status"] == "unavailable"
    clock[0] += consolidator.config.poll_seconds
    consolidator.tick()
    assert consolidator.workers[session.id].done()
    assert not summaries(repo)


@pytest.mark.parametrize("change", ["epoch", "edit", "delete", "invalid"])
async def test_changes_during_generation_write_nothing(repo, change):
    branch = repo.open_timeline("save-a", T0)
    lines = [line(repo, branch, n) for n in range(1, 4)]
    reply = {"text": "Summary.", "importance": 0.4}
    if change == "invalid":
        reply = {**reply, "importance": 2}
    provider = StubProvider(reply)
    consolidator, session, _ = worker(repo, branch, provider)

    async def hook():
        if change == "epoch":
            session.epoch += 1
        elif change == "edit":
            repo.update_memory("save-a", lines[0]["memory_id"], text="The player said: other.")
        elif change == "delete":
            repo.delete_memory("save-a", lines[1]["memory_id"])

    provider.hook = hook
    state = await run_once(consolidator, session)
    assert state["status"] == ("canceled" if change == "epoch" else "rejected")
    assert not summaries(repo)


async def test_join_cancels_an_in_flight_summary(repo):
    branch = repo.open_timeline("save-a", T0)
    for n in range(1, 4):
        line(repo, branch, n)
    provider = StubProvider({"text": "Summary.", "importance": 0.4})
    started, release = asyncio.Event(), asyncio.Event()

    async def hook():
        started.set()
        await release.wait()

    provider.hook = hook
    consolidator, session, _ = worker(repo, branch, provider)
    consolidator.tick()
    await started.wait()
    await consolidator.join(session)
    assert consolidator.diagnostics(session.id)["status"] == "canceled"
    assert not summaries(repo)


def provider_settings(**changes):
    return Settings(
        provider="openai",
        cloud_opt_in=True,
        dialogue_model="fixture",
        ai_gateway=GatewayConfig(
            background=DailyBudget(max_requests=4, max_estimated_usd=1),
            prices={"fixture": ModelPrice(input_usd_per_million=1, output_usd_per_million=2)},
        ),
        **changes,
    )


CONTEXT = {
    "character": {"display_name": "Avery", "biography": ""},
    "protagonist_id": "franklin",
    "base_importance": 0.4,
    "memories": [{"index": 1, "game_datetime": T0, "provenance": "heard", "text": "Hi."}],
}


def completion(content, *, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": content if isinstance(content, str) else json.dumps(content)
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 300, "completion_tokens": 40},
        },
    )


async def request_summary(handle, monkeypatch, config=None):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-summary-key")
    with Repository() as repository:
        provider = create_provider(
            config or provider_settings(), repository, transport=httpx.MockTransport(handle)
        )
        try:
            return await provider.memory_summary(CONTEXT), provider.safe_status()
        finally:
            await provider.aclose()


async def test_summary_request_uses_background_budget_and_json_mode(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return completion({"text": "Avery greeted Franklin.", "importance": 0.3})

    summary, status = await request_summary(handle, monkeypatch)
    assert summary == {"text": "Avery greeted Franklin.", "importance": 0.3}
    payload = json.loads(requests[0].content)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 400
    assert "Keep every refusal, negation, and uncertainty" in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][1]["content"]) == CONTEXT
    assert status["usage"]["background"]["requests"] == 1
    assert status["usage"]["foreground"]["requests"] == 0


@pytest.mark.parametrize(
    "content,finish",
    [
        ({"text": "Fine.", "importance": 0.3}, "length"),
        ({"text": "Fine.", "importance": 2}, "stop"),
        ({"text": "Fine.", "importance": 0.3, "tags": ["x"]}, "stop"),
        ('{"text": "Fine.", "text": "Other.", "importance": 0.3}', "stop"),
        ({"text": "x" * 601, "importance": 0.3}, "stop"),
    ],
)
async def test_invalid_summary_reply_is_unavailable_and_keeps_reservation(
    monkeypatch, content, finish
):
    summary, status = await request_summary(
        lambda _: completion(content, finish_reason=finish), monkeypatch
    )
    assert summary is None
    assert status["usage"]["background"]["uncertain_requests"] == 1


async def test_summary_without_background_budget_sends_nothing(monkeypatch):
    config = provider_settings()
    config.ai_gateway.background.max_requests = 0
    requests = []
    summary, status = await request_summary(
        lambda request: requests.append(request) or completion({}), monkeypatch, config
    )
    assert summary is None and not requests


async def test_fake_summary_is_labeled_and_disabled_provider_returns_nothing():
    for name in ("disabled", "fake"):
        with Repository() as repository:
            provider = create_provider(Settings(provider=name), repository)
            try:
                summary = await provider.memory_summary(CONTEXT)
            finally:
                await provider.aclose()
        if name == "disabled":
            assert summary is None
        else:
            assert summary == {"text": "Simulated summary: Hi.", "importance": 0.4}


async def test_runtime_summarizes_through_the_gateway_and_stops_with_the_session(client):
    runtime, repo = client.app.state.runtime, client.app.state.repository
    repo.register_actor(
        "fake-demo",
        {"actor_id": "npc-1", "actor_kind": "curated_persistent", "display_name": "Known"},
    )
    sid, _ = await open_ready(client, actor_overrides={"actor_kind": "curated_persistent"})
    session = runtime.sessions[sid]
    for n in range(3):
        event_id = f"runtime-line-{n}"
        repo.record_event(
            "fake-demo",
            session.branch_id,
            {
                "event_id": event_id,
                "game_datetime": session.game_datetime,
                "protagonist_id": "franklin",
                "event_type": "player.utterance_text",
                "payload": {"text": f"line {n}"},
                "provenance": "heard",
                "observer_ids": ["npc-1"],
            },
        )
        repo.add_memory(
            "fake-demo", session.branch_id, "npc-1", "franklin", session.game_datetime,
            f"The player said: line {n}.", [event_id], "heard",
        )
    runtime.memory_consolidator.config.enabled = True
    session.game_datetime = "2013-09-17T13:00:00"
    runtime.memory_consolidator.tick()
    await asyncio.gather(runtime.memory_consolidator.workers[sid], return_exceptions=True)
    assert runtime.memory_consolidator.diagnostics(sid)["status"] == "summarized"
    [summary] = [
        memory for memory in repo.list_memories("fake-demo", "npc-1")
        if memory["memory_id"].startswith("summary_")
    ]
    assert summary["text"].startswith("Simulated summary: The player said: line 0.")
    # The fake provider is free; a real provider needs a background allowance.
    assert runtime.provider.usage_summary()["background"]["requests"] == 0
    await runtime.close_session(session)
    assert sid not in runtime.memory_consolidator.workers
