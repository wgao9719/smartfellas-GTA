"""Learning simulations use the real gateway/store/interpreter, without model or GTA calls."""

import asyncio
import json
from dataclasses import replace
from time import monotonic
from types import SimpleNamespace

import pytest

from service.lls.providers import FakeProvider, create_provider
from service.lls.settings import Settings
from service.lls.skill_learning import SkillLearner, SkillLearningConfig, SkillLearningUnavailable
from service.lls.skill_store import SkillStore
from service.lls.skills import PrimitiveReceipt, SkillEnvironment, SkillIdentity, SkillInterpreter
from service.lls.storage import Repository

T0 = "2025-01-01T09:00:00Z"
T1 = "2025-01-01T10:00:00Z"
T2 = "2025-01-01T11:00:00Z"


class RecordingProvider(FakeProvider):
    def __init__(self):
        self.contexts = []
        self.hook = None

    async def skill_candidate(self, context, **kwargs):
        self.contexts.append(context)
        if self.hook:
            await self.hook()
        return await super().skill_candidate(context, **kwargs)


@pytest.fixture
async def learning(tmp_path):
    repo = Repository(tmp_path / "skills.sqlite3")
    branch = repo.open_timeline("profile", T0)
    for actor in ("avery", "bea"):
        repo.register_actor("profile", {"id": actor, "display_name": actor})
    event = {
        "event_id": "need",
        "game_datetime": T1,
        "protagonist_id": "franklin",
        "event_type": "speech.delivered",
        "observer_ids": ["avery"],
        "provenance": "heard",
        "confidence": 1,
        "payload": {
            "text": "Please face me and wait a moment.",
            "visibility_scope": "actor",
            "scope_id": "avery",
            "hidden_truth": "This hidden field must never reach the provider.",
        },
    }
    repo.record_event("profile", branch, event)
    identity = SkillIdentity("profile", branch, "franklin", "session", 1, "avery", "ped-1")
    store = SkillStore(repo)
    provider = create_provider(Settings(provider="fake", release=False), repo)
    recorder = RecordingProvider()
    provider.primary = recorder
    state = SimpleNamespace(current=identity, safe=True)
    learner = SkillLearner(
        store,
        provider,
        is_current=lambda expected: state.current == expected and state.safe,
        config=SkillLearningConfig(enabled=True, release=False),
    )
    fixture = SimpleNamespace(
        repo=repo,
        store=store,
        provider=provider,
        recorder=recorder,
        identity=identity,
        state=state,
        learner=learner,
        event=event,
        operations=[],
    )

    def environment(expected):
        sampled = monotonic()
        return SkillEnvironment(
            state.current,
            {
                "_sampled_at": sampled,
                "exists": True,
                "alive": True,
                "busy": False,
                "in_combat": False,
                "distance": 2,
                "ownership": "ambient",
                "control_policy": "ambient",
                "mission_entity": False,
                "can_move": True,
            },
            {
                "_sampled_at": sampled,
                "online": False,
                "mission_active": False,
                "cutscene_active": False,
                "random_event_active": False,
                "paused": False,
            },
            ("face_player", "wait_here"),
        )

    async def execute(expected, operation_id, proposal, deadline):
        fixture.operations.append(proposal.action_id)
        return PrimitiveReceipt(operation_id=operation_id, status="completed")

    async def cancel(expected, operation_id):
        pass

    fixture.environment, fixture.execute, fixture.cancel = environment, execute, cancel
    try:
        yield fixture
    finally:
        await provider.aclose()
        repo.close()


def request(version=1, **changes):
    return {
        "skill_id": "courteous_pause",
        "version": version,
        "source_event_ids": ["need"],
        **changes,
    }


async def trial(fixture, candidate):
    return await fixture.store.record_trial(
        candidate["record_id"],
        fixture.identity,
        T2,
        interpreter=SkillInterpreter(enabled=True),
        environment=fixture.environment,
        execute=fixture.execute,
        cancel=fixture.cancel,
    )


async def test_generation_trial_revision_adoption_reopen_and_private_repertoire(learning):
    f = learning
    first = await f.learner.generate(f.identity, T2, request())
    assert f.store.select(f.identity, T2) is None
    assert not f.operations
    refused = await trial(f, first)
    assert refused["result"]["status"] == "refused"
    with pytest.raises(ValueError, match="completed"):
        f.store.adopt(f.identity, T2, refused["record_id"], ["need"])
    revised = await f.learner.generate(f.identity, T2, request(2, revises=refused["record_id"]))
    completed = await trial(f, revised)
    assert completed["result"]["status"] == "completed"
    assert f.operations == ["face_player", "wait_here"]
    adoption = f.store.adopt(f.identity, T2, completed["record_id"], ["need"])
    assert adoption["kind"] == "adopt"
    with Repository(f.repo.path) as reopened:
        stored = SkillStore(reopened)
        selected = stored.select(f.identity, T2, action_class="conversational_courtesy")
        assert selected["version"] == 2
        assert selected["compiled"].fingerprint == revised["fingerprint"]
        assert stored.select(replace(f.identity, actor_id="bea"), T2) is None
        assert stored.select(replace(f.identity, protagonist_id="michael"), T2) is None
    assert len(f.recorder.contexts) == 2
    assert "hidden_truth" not in json.dumps(f.recorder.contexts)
    assert "audit_revision" not in json.dumps(f.recorder.contexts)
    assert f.recorder.contexts[1]["previous_candidate"]["version"] == 1
    assert f.provider.usage_summary()["background"]["requests"] == 0
    assert completed["result"]["game_verified"] is False


@pytest.mark.parametrize(
    "reason", ["missing", "actor", "protagonist", "future", "disabled", "stale"]
)
async def test_invalid_need_and_binding_never_dispatch_provider(learning, reason):
    f = learning
    identity, at, body = f.identity, T2, request()
    if reason == "missing":
        body["source_event_ids"] = ["absent"]
    elif reason == "actor":
        identity = replace(identity, actor_id="bea")
        f.state.current = identity
    elif reason == "protagonist":
        identity = replace(identity, protagonist_id="michael")
        f.state.current = identity
    elif reason == "future":
        at = T0
    elif reason == "disabled":
        f.learner.config = SkillLearningConfig()
    else:
        f.state.current = replace(identity, runtime_generation=2)
    with pytest.raises(ValueError):
        await f.learner.generate(identity, at, body)
    assert not f.recorder.contexts
    assert not f.store.history(f.identity, T2)


@pytest.mark.parametrize("mutation", ["reset", "delete_reimport", "binding", "interrupt"])
async def test_response_after_source_or_binding_change_cannot_register_candidate(
    learning, mutation
):
    f = learning

    async def change():
        if mutation == "reset":
            f.repo.reset_actor("profile", "avery")
        elif mutation == "delete_reimport":
            f.repo.delete_event("profile", "need")
            f.repo.record_event("profile", f.identity.branch_id, f.event)
        elif mutation == "binding":
            f.state.current = replace(f.identity, runtime_instance_id="ped-2")
        else:
            f.state.safe = False

    f.recorder.hook = change
    with pytest.raises(ValueError):
        await f.learner.generate(f.identity, T2, request())
    assert len(f.recorder.contexts) == 1
    assert not f.store.history(f.identity, T2)


async def test_suppressed_provider_cancellation_cannot_register(learning):
    f = learning
    entered = asyncio.Event()

    async def suppress():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    f.recorder.hook = suppress
    task = asyncio.create_task(f.learner.generate(f.identity, T2, request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not f.store.history(f.identity, T2)
    assert not f.learner._active


async def test_concurrent_generation_for_one_actor_is_rejected(learning):
    f = learning
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await release.wait()

    f.recorder.hook = pause
    task = asyncio.create_task(f.learner.generate(f.identity, T2, request()))
    await entered.wait()
    with pytest.raises(SkillLearningUnavailable, match="already"):
        await f.learner.generate(f.identity, T2, request())
    release.set()
    assert (await task)["kind"] == "candidate"
    assert len(f.recorder.contexts) == 1


async def test_foreign_revision_rejected_before_model_dispatch(learning):
    with pytest.raises(ValueError, match="revision"):
        await learning.learner.generate(learning.identity, T2, request(2, revises="other-trial"))
    assert not learning.recorder.contexts


async def test_model_cannot_change_requested_version(learning):
    f = learning

    async def mismatch(context, **kwargs):
        result = await FakeProvider().skill_candidate(context)
        result["version"] = 9
        return result

    f.recorder.skill_candidate = mismatch
    with pytest.raises(ValueError, match="requested skill identity"):
        await f.learner.generate(f.identity, T2, request())
    assert not f.store.history(f.identity, T2)


def test_release_config_refuses_learning():
    with pytest.raises(ValueError, match="release"):
        SkillLearningConfig(enabled=True)


async def test_model_cannot_copy_private_words_into_shared_action_class(learning):
    f = learning

    async def private_class(context, **kwargs):
        result = await FakeProvider().skill_candidate(context)
        result["action_class"] = "averys_private_memory"
        return result

    f.recorder.skill_candidate = private_class
    with pytest.raises(ValueError, match="action class"):
        await f.learner.generate(f.identity, T2, request())
    assert f.repo.connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


async def test_context_limit_is_enforced_before_provider_dispatch(learning):
    f = learning
    f.learner.config = SkillLearningConfig(enabled=True, release=False, context_byte_budget=2048)
    source_ids = []
    for index in range(8):
        event = {
            **f.event,
            "event_id": f"long-{index}",
            "payload": {"text": "x" * 500},
        }
        f.repo.record_event("profile", f.identity.branch_id, event)
        source_ids.append(event["event_id"])
    with pytest.raises(ValueError, match="byte budget"):
        await f.learner.generate(f.identity, T2, request(source_event_ids=source_ids))
    assert not f.recorder.contexts


async def test_erasure_from_another_connection_at_registration_is_atomic(learning, monkeypatch):
    f = learning
    register = f.store.register_candidate

    def erase_before_registration(*args, **kwargs):
        with Repository(f.repo.path) as second:
            second.delete_event("profile", "need")
            second.record_event("profile", f.identity.branch_id, f.event)
        return register(*args, **kwargs)

    monkeypatch.setattr(f.store, "register_candidate", erase_before_registration)
    with pytest.raises(ValueError, match="audit|erasure|sources changed"):
        await f.learner.generate(f.identity, T2, request())
    assert f.repo.connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


def test_malformed_observer_metadata_does_not_prevent_actor_erasure(learning):
    f = learning
    f.repo.record_event(
        "profile",
        f.identity.branch_id,
        {**f.event, "event_id": "bad-observers", "observer_ids": "prefix-avery-suffix"},
    )
    f.repo.reset_actor("profile", "avery")
    with pytest.raises(ValueError, match="forgotten"):
        f.store.need_context(f.identity, T2, ["need"])
