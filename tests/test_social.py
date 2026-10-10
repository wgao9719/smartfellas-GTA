"""Development simulations only. No test attests native GTA delivery or hearing."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import httpx
import pytest

from service.lls.gateway_config import DailyBudget, GatewayConfig, ModelPrice
from service.lls.perception import DeliveredUtterance, Witness
from service.lls.providers import create_provider
from service.lls.settings import Settings
from service.lls.social import (
    FakeSocialProvider,
    SocialConflict,
    SocialEngine,
    SocialError,
    SocialUnavailable,
)
from service.lls.social_models import SocialActorState, SocialConfig, SocialEnvironment, SocialStart
from service.lls.storage import RELATIONSHIP_FIELDS, Repository

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
GAME = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)


class RecordingProvider(FakeSocialProvider):
    def __init__(self):
        self.contexts = []

    async def dialogue(self, context):
        self.contexts.append(context)
        async for part in super().dialogue(context):
            yield part


@pytest.fixture
async def scene(tmp_path):
    with Repository(tmp_path / "social.sqlite3") as repo:
        branch = repo.open_timeline("profile", (GAME - timedelta(hours=1)).isoformat())
        actors = {}
        for index, actor in enumerate(("avery", "bea", "clay")):
            repo.register_actor(
                "profile",
                {
                    "id": actor,
                    "display_name": actor,
                    "persona": {
                        "social": {
                            "accept_invitations": True,
                            "opinion": f"{actor} has a different opinion.",
                        }
                    },
                },
            )
            actors[actor] = SocialActorState(
                actor_id=actor,
                runtime_instance_id=actor + "-1",
                sampled_at=NOW,
                position={"x": float(index), "y": 0, "z": 0},
                area_id="block",
                interior_id="street",
                exists=True,
                alive=True,
                busy=False,
                mission_entity=False,
                in_combat=False,
                ownership="ambient",
                control_policy="ambient",
            )
        start = SocialStart(
            scene_id="scene",
            profile_id="profile",
            branch_id=branch,
            session_id="session",
            protagonist_id="franklin",
            runtime_generation=1,
            initiator_id="avery",
            participant_bindings={a: a + "-1" for a in actors},
            area_id="block",
            interior_id="street",
            game_datetime=GAME,
            development_attestation=True,
        )
        env = SocialEnvironment(
            **{
                key: getattr(start, key)
                for key in (
                    "profile_id",
                    "branch_id",
                    "session_id",
                    "protagonist_id",
                    "runtime_generation",
                )
            },
            sampled_at=NOW,
            game_datetime=GAME,
            actors=actors,
            player_present=True,
            foreground_dialogue=False,
            danger=False,
            online=False,
            mission_active=False,
            cutscene_active=False,
            random_event_active=False,
            paused=False,
        )
        provider = create_provider(Settings(provider="fake", release=False), repo)
        recorder = RecordingProvider()
        provider.primary = recorder
        values = SimpleNamespace(
            repo=repo,
            branch=branch,
            start=start,
            env=env,
            provider=provider,
            recorder=recorder,
            monotonic=100.0,
        )
        sequence = count()
        values.engine = SocialEngine(
            repo,
            provider,
            config=SocialConfig(enabled=True, max_turns=3),
            clock=lambda: values.monotonic,
            utc_now=lambda: NOW,
            id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
        )
        yield values
        await values.engine.aclose()
        await provider.aclose()


async def invite(scene):
    return await scene.engine.invite(scene.start, lambda: scene.env)


def witness(scene, actor="bea", **changes):
    state = scene.env.actors[actor]
    return Witness(
        **{
            **state.model_dump(
                include={
                    "actor_id",
                    "runtime_instance_id",
                    "position",
                    "area_id",
                    "interior_id",
                    "exists",
                    "alive",
                    "sampled_at",
                }
            ),
            "can_hear": True,
            "confidence": 0.9,
            **changes,
        }
    )


def receipt(utterance, **changes):
    return DeliveredUtterance(
        **{
            "event_id": utterance.utterance_id + ":delivered",
            "actor_id": utterance.actor_id,
            "runtime_instance_id": utterance.runtime_instance_id,
            "text": utterance.text,
            "sampled_at": NOW,
            **changes,
        }
    )


async def deliver(scene, utterance, listeners=("bea",)):
    return await scene.engine.acknowledge_delivery(
        scene.start.scene_id,
        utterance.utterance_id,
        receipt(utterance),
        [witness(scene, actor) for actor in listeners],
    )


def speech_memories(scene, actor):
    rows = scene.repo.retrieve_memories(
        "profile",
        scene.branch,
        actor,
        "franklin",
        GAME.isoformat(),
        token_budget=10000,
    )
    # Consent decisions are private memories, distinct from received speech.
    return [row for row in rows if row["provenance"] != "system_fact"]


def source_memory(
    scene,
    actor="bea",
    *,
    event_id="source",
    event_time=None,
    memory_time=None,
    protagonist="franklin",
    branch=None,
    **memory_fields,
):
    event_time = event_time or GAME - timedelta(minutes=30)
    branch = branch or scene.branch
    scene.repo.record_event(
        "profile",
        scene.branch,
        {
            "event_id": event_id,
            "event_type": "actor.spoke",
            "game_datetime": event_time.isoformat(),
            "protagonist_id": protagonist,
            "observer_ids": [actor],
            "provenance": "reported",
            "confidence": 0.6,
            "payload": {
                "text": "The pier is quiet.",
                "teller_id": "other",
                "secret_underlying_fact": "The hidden package is under crate 4.",
            },
        },
    )
    return scene.repo.add_memory(
        "profile",
        branch,
        actor,
        protagonist,
        (memory_time or event_time).isoformat(),
        "I heard about the pier.",
        [event_id],
        "reported",
        **memory_fields,
    )


@pytest.mark.parametrize("participants", [2, 3])
async def test_each_actor_decides_and_speaks_from_its_own_context(scene, participants):
    scene.start.participant_bindings = dict(
        list(scene.start.participant_bindings.items())[:participants]
    )
    for actor in scene.start.participant_bindings:
        source_memory(scene, actor, event_id="private-" + actor)
    result = await invite(scene)
    assert result["status"] == "active"
    assert result["decisions"] == {a: "accept" for a in scene.start.participant_bindings}
    for context in scene.recorder.contexts:
        actor = context["actor"]["actor_id"]
        assert context["social"]["decision_owner"] == actor
        assert len(context["memories"]) == 1
        assert context["memories"][0]["source_event_ids"] == ["private-" + actor]
        assert context["eligible_actions"] == {}
        assert context["social"]["heard"] == []
    for actor in scene.start.participant_bindings:
        utterance = await scene.engine.generate_turn("scene", actor)
        assert utterance.text == f"{actor} has a different opinion."
        assert len(speech_memories(scene, actor)) == 1
        await deliver(scene, utterance, listeners=())
    for actor in scene.start.participant_bindings:
        relation = scene.repo.get_relationship(
            "profile", scene.branch, actor, "franklin", GAME.isoformat()
        )
        assert all(relation[field] == 0 for field in RELATIONSHIP_FIELDS)
        assert relation["source_event_ids"] == []


async def test_heard_words_require_delivery_and_hearing_evidence(scene):
    await invite(scene)
    turn = await scene.engine.generate_turn("scene", "avery")
    assert speech_memories(scene, "bea") == []
    result = await scene.engine.acknowledge_delivery(
        "scene", turn.utterance_id, receipt(turn), [witness(scene, can_hear=None)]
    )
    assert result["heard"] == {}
    assert speech_memories(scene, "bea") == []
    second = await scene.engine.generate_turn("scene", "bea")
    result = await deliver(scene, second, ("avery",))
    assert set(result["heard"]) == {"avery"}
    assert speech_memories(scene, "clay") == []
    report = result["heard"]["avery"]
    assert report["text"] == second.text
    assert report["provenance"] == "reported"
    assert report["confidence"] <= 0.7


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "Fabricated words."},
        {"actor_id": "bea"},
        {"runtime_instance_id": "avery-2"},
        {"sampled_at": NOW - timedelta(seconds=1)},
        {"sampled_at": NOW + timedelta(seconds=1)},
    ],
)
async def test_forged_delivery_cannot_create_speech_memories(scene, changes):
    await invite(scene)
    turn = await scene.engine.generate_turn("scene", "avery")
    with pytest.raises(SocialConflict):
        await scene.engine.acknowledge_delivery(
            "scene", turn.utterance_id, receipt(turn, **changes), [witness(scene)]
        )
    assert speech_memories(scene, "avery") == speech_memories(scene, "bea") == []


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_instance_id": "bea-2"},
        {"position": {"x": 5, "y": 0, "z": 0}},
        {"area_id": "elsewhere"},
        {"interior_id": "elsewhere"},
        {"exists": False},
        {"alive": False},
        {"sampled_at": NOW - timedelta(seconds=1)},
    ],
)
async def test_forged_witness_cannot_create_speech_memories(scene, changes):
    await invite(scene)
    turn = await scene.engine.generate_turn("scene", "avery")
    with pytest.raises(SocialConflict):
        await scene.engine.acknowledge_delivery(
            "scene", turn.utterance_id, receipt(turn), [witness(scene, **changes)]
        )
    assert speech_memories(scene, "bea") == []


async def test_uninvited_witness_and_duplicate_witness_are_rejected(scene):
    del scene.start.participant_bindings["clay"]
    await invite(scene)
    turn = await scene.engine.generate_turn("scene", "avery")
    with pytest.raises(SocialConflict):
        await deliver(scene, turn, ("clay",))
    with pytest.raises(SocialConflict, match="duplicate witness"):
        await deliver(scene, turn, ("bea", "bea"))
    assert speech_memories(scene, "bea") == []


async def test_turn_ownership_pending_delivery_and_terminal_duplicate(scene):
    await invite(scene)
    with pytest.raises(SocialConflict, match="own"):
        await scene.engine.generate_turn("scene", "bea")
    turns = []
    for actor in scene.start.participant_bindings:
        turn = await scene.engine.generate_turn("scene", actor)
        turns.append(turn)
        with pytest.raises(SocialConflict, match="awaits delivery"):
            await scene.engine.generate_turn("scene", actor)
        result = await deliver(scene, turn, ("bea",) if actor != "bea" else ())
    assert result["status"] == "completed"
    before = scene.repo.export_profile("profile")
    assert (await deliver(scene, turns[-1]))["duplicate"] is True
    assert scene.repo.export_profile("profile") == before
    with pytest.raises(SocialConflict, match="reused"):
        await scene.engine.acknowledge_delivery(
            "scene", turns[-1].utterance_id, receipt(turns[-1], text="Changed."), []
        )
    with pytest.raises(SocialUnavailable):
        await scene.engine.generate_turn("scene", "avery")


async def test_mutable_inputs_and_returned_results_cannot_rewrite_owned_scene(scene):
    await invite(scene)
    scene.start.participant_bindings["avery"] = "rebound"
    scene.start.initiator_id = "bea"
    turn = await scene.engine.generate_turn("scene", "avery")
    original = turn.model_copy(deep=True)
    turn.text = "Caller mutation."
    result = await deliver(scene, original)
    result["heard"]["bea"]["text"] = "Caller mutation."
    await scene.engine.generate_turn("scene", "bea")
    assert scene.recorder.contexts[-1]["social"]["heard"][0]["text"] == original.text


@pytest.mark.parametrize(
    "output",
    [
        "[]",
        '{"decision":"accept","decision":"refuse"}',
        '{"decision":"accept","friendship":true}',
        '{"decision":"continue"}',
        "I accept.",
        '{"decision":true}',
        "null",
    ],
)
async def test_malformed_acceptance_fails_closed(scene, output):
    class Malformed(RecordingProvider):
        async def dialogue(self, context):
            yield output

    scene.provider.primary = Malformed()
    result = await invite(scene)
    assert result["status"] == "refused"
    assert result["decisions"] == {"avery": "refuse"}
    assert speech_memories(scene, "avery") == []
    assert scene.repo.export_profile("profile")["events"] == []


async def test_recipient_refusal_and_independent_withdrawal(scene):
    actor = scene.repo.get_actor("profile", "bea")
    actor["persona"]["social"]["withdraw"] = True
    scene.repo.register_actor("profile", actor)
    await invite(scene)
    result = await scene.engine.review_participation("scene", "bea")
    assert result["status"] == "withdrawn"
    assert result["decisions"]["avery"] == "accept"
    assert result["decisions"]["bea"] == "withdraw"
    assert speech_memories(scene, "bea") == []


@pytest.mark.parametrize(
    "flag",
    [
        "foreground_dialogue",
        "danger",
        "online",
        "mission_active",
        "cutscene_active",
        "random_event_active",
        "paused",
    ],
)
async def test_protected_world_stops_invitation_before_model_work(scene, flag):
    setattr(scene.env, flag, True)
    with pytest.raises(SocialUnavailable):
        await invite(scene)
    assert scene.recorder.contexts == []


@pytest.mark.parametrize(
    "change", ["ephemeral", "protected", "service", "absent", "binding", "distance"]
)
async def test_unavailable_participants_cannot_join(scene, change):
    actor = scene.repo.get_actor("profile", "bea")
    if change == "ephemeral":
        actor["actor_kind"] = "ambient_ephemeral"
        actor["session_id"] = "session"
        with scene.repo.connection:
            scene.repo.connection.execute(
                "DELETE FROM actors WHERE profile_id=? AND actor_id=?", ("profile", "bea")
            )
        scene.repo.register_actor("profile", actor)
    elif change == "protected":
        actor["control_policy"] = "protected"
        scene.repo.register_actor("profile", actor)
    elif change == "service":
        actor["control_policy"] = "service_limited"
        actor["service_integration_id"] = "shop"
        scene.repo.register_actor("profile", actor)
    elif change == "absent":
        del scene.env.actors["bea"]
    elif change == "binding":
        scene.env.actors["bea"].runtime_instance_id = "bea-2"
    else:
        scene.env.actors["bea"].position.x = 20
    with pytest.raises(SocialUnavailable):
        await invite(scene)
    assert scene.recorder.contexts == []


class WaitingProvider(FakeSocialProvider):
    def __init__(self, phase="turn"):
        self.phase = phase
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def dialogue(self, context):
        if context["social"]["phase"] == self.phase:
            self.entered.set()
            try:
                await self.release.wait()
                async for part in super().dialogue(context):
                    yield part
            finally:
                self.closed.set()
        else:
            async for part in super().dialogue(context):
                yield part


@pytest.mark.parametrize("change", ["danger", "departure", "expiry", "generation"])
async def test_in_flight_turn_revalidates_before_publishing(scene, change):
    await invite(scene)
    provider = WaitingProvider()
    scene.provider.primary = provider
    task = asyncio.create_task(scene.engine.generate_turn("scene", "avery"))
    await asyncio.wait_for(provider.entered.wait(), 1)
    with pytest.raises(SocialConflict):
        await scene.engine.generate_turn("scene", "avery")
    if change == "danger":
        scene.env.danger = True
    elif change == "departure":
        scene.env.player_present = False
    elif change == "expiry":
        scene.monotonic += 121
    else:
        scene.env.runtime_generation += 1
    provider.release.set()
    with pytest.raises(SocialUnavailable):
        await task
    assert provider.closed.is_set()
    assert scene.engine.get_scene("scene")["pending_utterance_id"] is None
    assert speech_memories(scene, "avery") == []


@pytest.mark.parametrize("phase", ["invitation", "turn"])
async def test_forget_session_joins_model_and_clears_retained_state(scene, phase):
    if phase == "turn":
        await invite(scene)
    provider = WaitingProvider(phase)
    scene.provider.primary = provider
    task = asyncio.create_task(
        invite(scene) if phase == "invitation" else scene.engine.generate_turn("scene", "avery")
    )
    await asyncio.wait_for(provider.entered.wait(), 1)
    internal = scene.engine.scenes["scene"]
    await scene.engine.forget_session("session")
    assert task.cancelled()
    assert provider.closed.is_set()
    assert internal.revalidate is None
    assert internal.heard == internal.delivered == {}
    assert scene.engine.scenes_for_session("session") == []


async def test_cancel_joins_blocked_storage_thread(scene, monkeypatch):
    await invite(scene)
    entered, release = threading.Event(), threading.Event()
    original = scene.engine.store.context

    def slow_context(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(scene.engine.store, "context", slow_context)
    generation = asyncio.create_task(scene.engine.generate_turn("scene", "avery"))
    assert await asyncio.to_thread(entered.wait, 1)
    stopped = asyncio.create_task(scene.engine.forget_session("session"))
    await asyncio.sleep(0)
    assert not stopped.done()
    release.set()
    await stopped
    assert generation.cancelled()
    assert scene.engine.scenes == {}


async def test_model_timeout_is_bounded_and_joins_stream(scene):
    scene.engine.config.decision_timeout_seconds = 0.01
    provider = WaitingProvider("invitation")
    scene.provider.primary = provider
    with pytest.raises(SocialUnavailable, match="timed out"):
        await invite(scene)
    assert provider.closed.is_set()
    assert scene.engine.get_scene("scene")["status"] == "canceled"


async def test_exact_quote_retelling_preserves_lineage_without_hidden_payload(scene):
    source_memory(scene, "avery")
    await invite(scene)
    first = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert first.text == "The pier is quiet."
    result = await deliver(scene, first, ("bea",))
    first_report = result["heard"]["bea"]["event_id"]
    second = await scene.engine.generate_turn("scene", "bea", retell_event_id=first_report)
    result = await deliver(scene, second, ("clay",))
    second_report = result["heard"]["clay"]["event_id"]
    exported = scene.repo.export_profile("profile")
    report = next(event for event in exported["events"] if event["event_id"] == second_report)
    assert report["payload"]["retelling_lineage"] == ["source", first_report]
    assert report["payload"]["text"] == "The pier is quiet."
    assert report["confidence"] <= 0.6
    assert report["provenance"] == "reported"
    assert "secret_underlying_fact" not in json.dumps(report)
    assert "crate 4" not in json.dumps(scene.recorder.contexts)
    assert len(speech_memories(scene, "clay")) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "future_memory",
        "alternate_memory",
        "future_event",
        "other_actor",
        "other_protagonist",
        "memory_protagonist",
        "expired_memory",
        "deleted_memory",
        "reset_actor",
        "deleted_source",
    ],
)
async def test_retelling_requires_current_private_retained_source(scene, invalid):
    options = {}
    if invalid == "future_memory":
        options["memory_time"] = GAME + timedelta(hours=1)
    elif invalid == "future_event":
        options["event_time"] = GAME + timedelta(hours=1)
    elif invalid == "other_actor":
        options["actor"] = "bea"
    elif invalid == "other_protagonist":
        options["protagonist"] = "michael"
    elif invalid == "memory_protagonist":
        options["protagonist"] = None
    elif invalid == "expired_memory":
        options["expires_at"] = (GAME - timedelta(minutes=1)).isoformat()
    elif invalid == "alternate_memory":
        options["event_time"] = GAME - timedelta(minutes=50)
        options["memory_time"] = GAME - timedelta(minutes=30)
    memory = source_memory(scene, actor=options.pop("actor", "avery"), **options)
    if invalid == "alternate_memory":
        alternate = scene.repo.open_timeline("profile", (GAME - timedelta(minutes=40)).isoformat())
        scene.repo.delete_memory("profile", memory["memory_id"])
        scene.repo.add_memory(
            "profile",
            alternate,
            "avery",
            "franklin",
            GAME.isoformat(),
            "I heard about the pier on another branch.",
            ["source"],
            "reported",
        )
    elif invalid == "memory_protagonist":
        scene.repo.delete_memory("profile", memory["memory_id"])
        scene.repo.add_memory(
            "profile",
            scene.branch,
            "avery",
            "michael",
            GAME.isoformat(),
            "I heard about the pier with Michael.",
            ["source"],
            "reported",
        )
    elif invalid == "deleted_memory":
        scene.repo.delete_memory("profile", memory["memory_id"])
    elif invalid == "reset_actor":
        scene.repo.reset_actor("profile", "avery")
    elif invalid == "deleted_source":
        scene.repo.delete_event("profile", "source")
    await invite(scene)
    before = len(scene.recorder.contexts)
    with pytest.raises(SocialError):
        await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert len(scene.recorder.contexts) == before


async def test_timezone_normalization_allows_equal_dates_and_rejects_rewinds(scene):
    scene.start.game_datetime = GAME.astimezone(timezone(timedelta(hours=5)))
    await invite(scene)
    scene.env.game_datetime = (GAME - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=8)))
    with pytest.raises(SocialUnavailable, match="backwards"):
        await scene.engine.generate_turn("scene", "avery")


async def test_retelling_source_must_still_exist_at_delivery(scene):
    memory = source_memory(scene, "avery")
    await invite(scene)
    turn = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    scene.repo.delete_memory("profile", memory["memory_id"])
    with pytest.raises(SocialError, match="retained memory"):
        await deliver(scene, turn)
    assert speech_memories(scene, "bea") == []
    assert not any(
        event["event_type"] == "social.quote_delivered"
        for event in scene.repo.export_profile("profile")["events"]
    )


async def test_retelling_cannot_invent_words_or_strip_qualification(scene):
    source_memory(scene, "avery")
    await invite(scene)

    class DistortingProvider(FakeSocialProvider):
        async def dialogue(self, context):
            yield "The pier is definitely dangerous."

    scene.provider.primary = DistortingProvider()
    with pytest.raises(SocialError, match="exact quote"):
        await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert scene.engine.get_scene("scene")["pending_utterance_id"] is None
    assert speech_memories(scene, "bea") == []


async def test_retelling_rejects_sources_from_other_profiles_and_branches(scene):
    scene.repo.register_actor("other", {"id": "avery", "display_name": "Avery"})
    other = scene.repo.open_timeline("other", GAME.isoformat())
    scene.repo.record_event(
        "other",
        other,
        {
            "event_id": "other-source",
            "event_type": "actor.spoke",
            "game_datetime": GAME.isoformat(),
            "protagonist_id": "franklin",
            "observer_ids": ["avery"],
            "provenance": "reported",
            "confidence": 0.7,
            "payload": {"text": "Private words from another profile."},
        },
    )
    scene.repo.add_memory(
        "other",
        other,
        "avery",
        "franklin",
        GAME.isoformat(),
        "I heard private words.",
        ["other-source"],
        "reported",
    )
    source_memory(scene, "avery")
    alternate = scene.repo.open_timeline("profile", (GAME - timedelta(minutes=40)).isoformat())
    scene.repo.record_event(
        "profile",
        alternate,
        {
            "event_id": "alternate-source",
            "event_type": "actor.spoke",
            "game_datetime": GAME.isoformat(),
            "protagonist_id": "franklin",
            "observer_ids": ["avery"],
            "provenance": "reported",
            "confidence": 0.7,
            "payload": {"text": "Private words from another branch."},
        },
    )
    scene.repo.add_memory(
        "profile",
        alternate,
        "avery",
        "franklin",
        GAME.isoformat(),
        "I heard other words.",
        ["alternate-source"],
        "reported",
    )
    await invite(scene)
    for identifier in ("other-source", "alternate-source"):
        with pytest.raises(SocialError, match="absent from this timeline"):
            await scene.engine.generate_turn("scene", "avery", retell_event_id=identifier)
    assert speech_memories(scene, "bea") == []


@pytest.mark.parametrize("provider_name", ["openai", "local_openai"])
async def test_social_calls_use_background_quota_only(scene, monkeypatch, provider_name):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-social-key")
    requests = []

    def handle(request):
        requests.append(request)
        chunk = {"choices": [{"delta": {"content": '{"decision":"accept"}'}}]}
        return httpx.Response(200, content=f"data: {json.dumps(chunk)}\ndata: [DONE]\n".encode())

    settings = Settings(
        provider=provider_name,
        cloud_opt_in=True,
        dialogue_model="test-social",
        ai_gateway=GatewayConfig(
            background=DailyBudget(max_requests=3, max_estimated_usd=1),
            prices={
                "test-social": ModelPrice(
                    input_usd_per_million=1,
                    output_usd_per_million=2,
                )
            },
        ),
    )
    await scene.provider.aclose()
    provider = create_provider(
        settings, scene.repo, transport=httpx.MockTransport(handle), utc_now=lambda: NOW
    )
    scene.engine.replace_provider(provider)
    try:
        await invite(scene)
        usage = provider.usage_summary()
        assert len(requests) == 3
        assert usage["background"]["requests"] == 3
        assert usage["foreground"]["requests"] == 0
    finally:
        await provider.aclose()


def previous_contact(scene, number, *, retold=False):
    """Persist actual SocialStore projections with explicitly simulated hearing."""
    start = scene.start.model_copy(update={"scene_id": f"prior-{number}"})
    stamp = GAME - timedelta(minutes=10 - number)
    utterance = SimpleNamespace(
        actor_id="avery", utterance_id=f"prior-utterance-{number}",
        retell_event_id="source" if retold else None,
    )
    delivery = SimpleNamespace(
        event_id=f"prior-receipt-{number}", sampled_at=NOW,
        text="The pier is quiet." if retold else "I enjoy quiet streets.",
    )
    result = scene.engine.store.delivered(
        start, utterance, delivery,
        [SimpleNamespace(actor_id="bea", confidence=0.9)], stamp,
    )
    return result["bea"]["event_id"]


def social_context(scene, actor="bea", *, budget=12000, start=None, env=None):
    return scene.engine.store.context(
        start or scene.start, actor, env or scene.env, phase="turn", heard=[],
        retelling=None, byte_budget=budget,
    )


async def test_repeated_confirmed_hearing_changes_later_topic_only_for_listener(scene):
    assert social_context(scene)["social"]["topic_continuation"] is None
    previous_contact(scene, 1)
    assert social_context(scene)["social"]["topic_continuation"] is None
    source = previous_contact(scene, 2)
    context = social_context(scene)
    contact = context["social"]["experience"]["contacts"]["avery"]
    assert contact["confirmed_scene_count"] == 2
    assert contact["familiarity"] > 0
    assert context["social"]["topic_continuation"]["event_id"] == source
    assert source in contact["source_event_ids"]
    for actor in ("avery", "clay"):
        private = social_context(scene, actor)["social"]
        assert private["experience"]["contacts"] == {}
        assert private["topic_continuation"] is None
    scene.start.initiator_id = "bea"
    await invite(scene)
    utterance = await scene.engine.generate_turn("scene", "bea")
    assert utterance.text == "avery, you said: I enjoy quiet streets. What do you think now?"
    assert scene.recorder.contexts[-1]["social"]["heard"] == []


async def test_repeated_retellings_do_not_become_direct_shared_topic(scene):
    source_memory(scene, "avery")
    previous_contact(scene, 1, retold=True)
    previous_contact(scene, 2, retold=True)
    context = social_context(scene)["social"]
    assert context["experience"]["contacts"]["avery"]["confirmed_scene_count"] == 2
    assert context["topic_continuation"] is None


@pytest.mark.parametrize("mutation", ["edit", "delete", "reset", "delete_source"])
async def test_memory_controls_remove_topic_continuation(scene, mutation):
    first = previous_contact(scene, 1)
    second = previous_contact(scene, 2)
    assert social_context(scene)["social"]["topic_continuation"] is not None
    if mutation == "edit":
        scene.repo.update_memory("profile", second + ":memory", text="I corrected my recollection.")
    elif mutation == "delete":
        scene.repo.delete_memory("profile", second + ":memory")
    elif mutation == "delete_source":
        scene.repo.delete_event("profile", second)
    else:
        scene.repo.reset_actor("profile", "bea")
    context = social_context(scene)["social"]
    assert context["topic_continuation"] is None
    assert second not in json.dumps(context["experience"])
    if mutation == "reset":
        assert first not in json.dumps(context["experience"])


async def test_topic_continuation_isolated_from_rewind_and_other_protagonist(scene):
    previous_contact(scene, 1)
    previous_contact(scene, 2)
    rewind = scene.env.model_copy(update={"game_datetime": GAME - timedelta(minutes=20)})
    assert social_context(scene, env=rewind)["social"]["topic_continuation"] is None
    switched = scene.start.model_copy(update={"protagonist_id": "michael"})
    assert social_context(scene, start=switched)["social"]["topic_continuation"] is None
    outsiders = scene.start.model_copy(update={"participant_bindings": {
        "bea": "bea-1", "clay": "clay-1",
    }})
    assert social_context(scene, start=outsiders)["social"]["topic_continuation"] is None


async def test_social_adaptation_context_respects_minimum_byte_budget(scene):
    previous_contact(scene, 1)
    previous_contact(scene, 2)
    context = social_context(scene, budget=4096)
    assert len(json.dumps(context, ensure_ascii=False).encode()) <= 4096


async def test_invalid_participation_is_not_a_durable_withdrawal(scene):
    await invite(scene)
    before = scene.repo.export_profile("profile")

    class Malformed(RecordingProvider):
        async def dialogue(self, context):
            yield '{"decision":"withdraw","reason":"invented"}'

    scene.provider.primary = Malformed()
    result = await scene.engine.review_participation("scene", "bea")
    assert result["status"] == "withdrawn"
    assert result["reason"] == "invalid_participation_decision"
    assert scene.repo.export_profile("profile") == before
    assert social_context(scene)["social"]["experience"]["own_last_boundary_at"] is None


async def test_valid_refusal_has_private_retained_anchor(scene):
    actor = scene.repo.get_actor("profile", "bea")
    actor["persona"]["social"]["accept_invitations"] = False
    scene.repo.register_actor("profile", actor)
    result = await invite(scene)
    assert result["status"] == "refused"
    own = social_context(scene)["social"]["experience"]
    assert datetime.fromisoformat(own["own_last_boundary_at"]) == GAME
    assert len(own["own_decision_source_event_ids"]) == 1
    memory = scene.repo.get_memory("profile", own["own_decision_source_event_ids"][0] + ":memory")
    assert memory["text"] == "I chose to refuse this social conversation."
    for actor in ("avery", "clay"):
        context = social_context(scene, actor)
        assert context["social"]["experience"]["own_last_boundary_at"] is None
        assert memory["memory_id"] not in json.dumps(context)
