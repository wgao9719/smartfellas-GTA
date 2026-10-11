"""Durable learning simulations. These tests do not establish native GTA execution."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest

from service.lls.skill_store import SkillStore
from service.lls.skills import PrimitiveReceipt, SkillEnvironment, SkillIdentity, SkillInterpreter
from service.lls.storage import Repository

NOW = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)


def definition(*, version=1, name="greeting", consent=False, child=None):
    return {
        "skill_id": name,
        "version": version,
        "action_class": "courtesy",
        "max_duration_seconds": 2.0,
        "max_distance_meters": 0.0,
        "steps": ([{"op": "require", "condition": "consent"}] if consent else [])
        + [
            {"op": "call", "skill_id": child, "version": 1}
            if child
            else {
                "op": "action",
                "proposal": {"action_id": "face_player", "arguments": {"duration_seconds": 1.0}},
            }
        ],
    }


def observation(repo, identity, event_id="need", *, at=NOW, actor=None, **changes):
    event = {
        "event_id": event_id,
        "event_type": "conversation.interrupted",
        "game_datetime": at.isoformat(),
        "protagonist_id": identity.protagonist_id,
        "observer_ids": [actor or identity.actor_id],
        "provenance": "directly_observed",
        "confidence": 1.0,
        "payload": {
            "text": "I did not face the speaker.",
            "private_hidden_detail": "not a prompt excerpt",
        },
        **changes,
    }
    repo.record_event(identity.profile_id, identity.branch_id, event)
    return event


@pytest.fixture
def state(tmp_path):
    with Repository(tmp_path / "skills.sqlite3") as repo:
        session = repo.start_session("profile", "session", (NOW - timedelta(hours=1)).isoformat())
        for actor in ("avery", "bea"):
            repo.register_actor("profile", {"id": actor, "display_name": actor})
        identity = SkillIdentity(
            "profile", session["branch_id"], "franklin", "session", 1, "avery", "avery-1"
        )
        observation(repo, identity)
        item = SimpleNamespace(
            repo=repo, store=SkillStore(repo), identity=identity, commands=[], consent=False
        )
        item.actor = {
            "_sampled_at": 100.0,
            "exists": True,
            "alive": True,
            "busy": False,
            "in_combat": False,
            "distance": 2,
            "control_policy": "ambient",
            "ownership": "ambient",
            "mission_entity": False,
            "can_move": True,
        }
        item.world = {
            "_sampled_at": 100.0,
            "online": False,
            "mission_active": False,
            "cutscene_active": False,
            "random_event_active": False,
            "paused": False,
        }
        item.environment = lambda current: SkillEnvironment(
            current,
            item.actor,
            item.world,
            ("face_player", "wait_here"),
            item.consent,
        )

        async def execute(current, operation_id, proposal, deadline):
            item.commands.append((current, operation_id, proposal.action_id))
            return PrimitiveReceipt(operation_id=operation_id, status="completed")

        async def cancel(current, operation_id):
            return None

        item.execute, item.cancel = execute, cancel
        yield item


def candidate(state, *, source=None, at=NOW, identity=None, sources=None, **options):
    return state.store.register_candidate(
        identity or state.identity,
        at,
        source or definition(),
        sources or ["need"],
        **options,
    )


async def trial(state, registered, *, at=NOW, identity=None, **options):
    return await state.store.record_trial(
        registered["record_id"],
        identity or state.identity,
        at,
        interpreter=options.pop("interpreter", SkillInterpreter(enabled=True, clock=lambda: 100)),
        environment=options.pop("environment", state.environment),
        execute=options.pop("execute", state.execute),
        cancel=options.pop("cancel", state.cancel),
        **options,
    )


async def learned(state, *, source=None, at=NOW, identity=None, sources=None, revises=None):
    who = identity or state.identity
    registered = candidate(
        state, source=source, at=at, identity=who, sources=sources, revises=revises
    )
    outcome = await trial(state, registered, at=at, identity=who)
    adopted = state.store.adopt(who, at, outcome["record_id"], sources or ["need"])
    return registered, outcome, adopted


def bridge(
    state,
    *,
    statuses=("started", "completed"),
    mutate=None,
    receipt_status="completed",
    record_identity=None,
):
    terminal = NOW + timedelta(seconds=1)
    receipt_ids, events = [], []

    async def execute(current, operation_id, proposal, deadline):
        await state.execute(current, operation_id, proposal, deadline)
        for status in statuses:
            event = {
                "event_id": "cmd-" + operation_id + ":" + status,
                "event_type": "action." + status,
                "game_datetime": (NOW if status == "started" else terminal).isoformat(),
                "protagonist_id": current.protagonist_id,
                "observer_ids": [current.actor_id],
                "provenance": "system_fact",
                "confidence": 1.0,
                "payload": {
                    "action_id": proposal.action_id,
                    "conversation_turn_id": operation_id,
                    "reason": "",
                    "observed": {},
                },
            }
            if mutate:
                mutate(event)
            scope = record_identity or current
            state.repo.record_event(scope.profile_id, scope.branch_id, event)
            receipt_ids.append(event["event_id"])
            events.append(event)
        return PrimitiveReceipt(operation_id=operation_id, status=receipt_status)

    def completion():
        assert state.repo.connection.in_transaction
        return {
            "game_datetime": terminal,
            "source_event_ids": receipt_ids,
            "verification": "bridge_acknowledged",
            "game_verified": False,
        }

    return SimpleNamespace(
        execute=execute,
        completion=completion,
        source_ids=receipt_ids,
        events=events,
        terminal=terminal,
    )


async def test_bridge_trial_uses_terminal_time_and_canonical_ack_sources(state):
    registered = candidate(state)
    live = bridge(state)
    outcome = await trial(
        state, registered, execute=live.execute, completion_evidence=live.completion
    )
    assert outcome["result"]["verification"] == "bridge_acknowledged"
    assert outcome["result"]["game_verified"] is False
    assert datetime.fromisoformat(outcome["game_datetime"]) == live.terminal
    assert outcome["source_event_ids"] == ["need", *live.source_ids]
    assert outcome["terminal_source_event_ids"] == live.source_ids
    assert [record["kind"] for record in state.store.history(state.identity, NOW)] == ["candidate"]
    state.store.adopt(state.identity, live.terminal, outcome["record_id"], ["need"])
    assert state.store.select(state.identity, live.terminal)["skill_id"] == "greeting"
    with Repository(state.repo.path) as reopened:
        assert SkillStore(reopened).select(state.identity, live.terminal) is not None
    state.repo.delete_event(state.identity.profile_id, live.source_ids[-1])
    assert state.store.select(state.identity, live.terminal) is None


@pytest.mark.parametrize(
    "change",
    [
        "actor",
        "protagonist",
        "unscoped",
        "provenance",
        "action",
        "operation",
        "future",
        "past",
        "suffix",
    ],
)
async def test_bridge_trial_rejects_forged_or_misbound_canonical_sources(state, change):
    registered = candidate(state)

    def mutate(event):
        if change == "actor":
            event["observer_ids"] = ["bea"]
        elif change == "protagonist":
            event["protagonist_id"] = "michael"
        elif change == "unscoped":
            event["protagonist_id"] = None
        elif change == "provenance":
            event["provenance"] = "reported"
        elif change == "action":
            event["payload"]["action_id"] = "wait_here"
        elif change == "operation":
            event["payload"]["conversation_turn_id"] = "unexecuted-operation"
        elif change == "future":
            event["game_datetime"] = (NOW + timedelta(hours=1)).isoformat()
        elif change == "past":
            event["game_datetime"] = (NOW - timedelta(seconds=1)).isoformat()
        else:
            event["event_id"] += "-forged"

    live = bridge(state, mutate=mutate)
    with pytest.raises(ValueError):
        await trial(state, registered, execute=live.execute, completion_evidence=live.completion)
    assert all(
        record["kind"] == "candidate"
        for record in state.store.history(state.identity, live.terminal)
    )


@pytest.mark.parametrize(
    "statuses",
    [("started",), ("completed",), ("started", "failed"), ("started", "completed", "failed")],
)
async def test_completed_trial_requires_consistent_started_and_completed_receipts(state, statuses):
    registered = candidate(state)
    live = bridge(state, statuses=statuses)
    with pytest.raises(ValueError):
        await trial(state, registered, execute=live.execute, completion_evidence=live.completion)


@pytest.mark.parametrize(
    "change", ["success", "native", "backdated", "missing", "not_callable", "async"]
)
async def test_completion_callback_cannot_replace_result_or_claim_native_success(state, change):
    registered = candidate(state)
    live = bridge(state)

    def completion():
        data = live.completion()
        if change == "success":
            data["status"] = "completed"
        elif change == "native":
            data["game_verified"] = True
        elif change == "backdated":
            data["game_datetime"] = NOW - timedelta(seconds=1)
        else:
            data["source_event_ids"] = ["nonexistent:completed"]
        return data

    async def asynchronous():
        return live.completion()

    callback = {"not_callable": {}, "async": asynchronous}.get(change, completion)
    with pytest.raises((ValueError, TypeError)):
        await trial(state, registered, execute=live.execute, completion_evidence=callback)
    assert all(
        record["kind"] == "candidate"
        for record in state.store.history(state.identity, live.terminal)
    )


async def test_no_ack_evidence_cannot_confirm_completed_runtime_trial(state):
    registered = candidate(state)
    with pytest.raises(ValueError, match="acknowledgment evidence"):
        await trial(state, registered, completion_evidence=lambda: None)
    refused = candidate(state, source=definition(name="refused", consent=True))
    outcome = await trial(state, refused, completion_evidence=lambda: None)
    assert outcome["result"]["status"] == "refused"
    assert outcome["result"]["verification"] == "simulated"


async def test_terminal_callback_rejects_binding_change_after_interpreter_cleanup(state):
    registered = candidate(state)
    live = bridge(state, statuses=("started", "failed"), receipt_status="failed")
    cleaned = False

    async def cancel(*args):
        nonlocal cleaned
        cleaned = True

    def completion():
        assert cleaned
        raise ValueError("live actor binding changed after cleanup")

    with pytest.raises(ValueError, match="binding changed"):
        await trial(
            state, registered, execute=live.execute, cancel=cancel, completion_evidence=completion
        )
    assert all(
        record["kind"] == "candidate"
        for record in state.store.history(state.identity, live.terminal)
    )


async def test_bridge_ack_delete_reimport_during_execution_fails_audit_check(state):
    registered = candidate(state)
    live = bridge(state)

    async def execute(*args):
        result = await live.execute(*args)
        event = live.events[-1]
        with Repository(state.repo.path) as concurrent:
            concurrent.delete_event(state.identity.profile_id, event["event_id"])
            concurrent.record_event(state.identity.profile_id, state.identity.branch_id, event)
        return result

    with pytest.raises(ValueError, match="audit changed"):
        await trial(state, registered, execute=execute, completion_evidence=live.completion)
    assert all(
        record["kind"] == "candidate"
        for record in state.store.history(state.identity, live.terminal)
    )


async def test_bridge_sources_over_sixteen_remain_bounded_visible_and_adoptable(state):
    source = definition()
    source["steps"] *= 9
    source["max_duration_seconds"] = 10.0
    registered = candidate(state, source=source)
    live = bridge(state)

    def completion():
        data = live.completion()
        data["source_event_ids"] = [*data["source_event_ids"], data["source_event_ids"][0]]
        return data

    outcome = await trial(state, registered, execute=live.execute, completion_evidence=completion)
    assert len(outcome["terminal_source_event_ids"]) == 18
    assert len(outcome["source_event_ids"]) == 19
    state.store.adopt(state.identity, live.terminal, outcome["record_id"], ["need"])
    assert state.store.select(state.identity, live.terminal) is not None


async def test_ack_events_on_another_branch_cannot_validate_current_execution(state):
    parent = state.identity
    observation(state.repo, parent, "later", at=NOW + timedelta(hours=1))
    branch_id = state.repo.open_timeline(
        parent.profile_id,
        NOW.isoformat(),
        parent_branch_id=parent.branch_id,
    )
    current = replace(parent, branch_id=branch_id)
    assert current.branch_id != parent.branch_id
    registered = candidate(state, identity=current)
    live = bridge(
        state,
        record_identity=parent,
        mutate=lambda event: event.update(game_datetime=NOW.isoformat()),
    )
    with pytest.raises(ValueError, match="executed operation"):
        await trial(
            state,
            registered,
            identity=current,
            execute=live.execute,
            completion_evidence=live.completion,
        )


async def test_ack_events_in_another_profile_cannot_validate_current_execution(state):
    registered = candidate(state)
    session = state.repo.start_session("other", "other-session", NOW.isoformat())
    other = replace(state.identity, profile_id="other", branch_id=session["branch_id"])
    live = bridge(state, record_identity=other)
    with pytest.raises(ValueError, match="absent from this timeline"):
        await trial(state, registered, execute=live.execute, completion_evidence=live.completion)


async def test_terminal_receipt_cannot_precede_its_start(state):
    registered = candidate(state)

    def mutate(event):
        event["game_datetime"] = (
            NOW + timedelta(seconds=1) if event["event_type"] == "action.started" else NOW
        ).isoformat()

    live = bridge(state, mutate=mutate)
    with pytest.raises(ValueError, match="precedes its start"):
        await trial(state, registered, execute=live.execute, completion_evidence=live.completion)


async def test_final_environment_binding_is_checked_even_when_callback_returns_evidence(state):
    registered = candidate(state)
    live = bridge(state)
    current = state.identity

    def environment(identity):
        return state.environment(current)

    def completion():
        nonlocal current
        current = replace(current, runtime_instance_id="replacement")
        return live.completion()

    with pytest.raises(ValueError, match="binding changed"):
        await trial(
            state,
            registered,
            execute=live.execute,
            environment=environment,
            completion_evidence=completion,
        )


async def test_dependency_retirement_during_bridge_execution_prevents_trial_persistence(state):
    await learned(state, source=definition(name="child"))
    registered = candidate(state, source=definition(name="parent", child="child"))
    live = bridge(state)

    async def execute(*args):
        result = await live.execute(*args)
        state.store.retire(state.identity, live.terminal, "child", ["need"])
        return result

    with pytest.raises(ValueError):
        await trial(state, registered, execute=execute, completion_evidence=live.completion)
    assert not any(
        record["kind"] == "trial" and record["skill_id"] == "parent"
        for record in state.store.history(state.identity, live.terminal)
    )


async def test_actual_trial_revision_adoption_and_reopen_preserve_identity(state):
    first = candidate(state, source=definition(consent=True))
    refused = await trial(state, first)
    assert refused["result"]["status"] == "refused"
    assert refused["result"]["completed_operations"] == 0
    with pytest.raises(ValueError, match="completed own trial"):
        state.store.adopt(state.identity, NOW, refused["record_id"], ["need"])
    second, passed, adopted = await learned(
        state,
        source=definition(version=2),
        revises=refused["record_id"],
    )
    assert passed["result"]["status"] == "completed"
    assert passed["result"]["verification"] == "simulated"
    assert passed["result"]["game_verified"] is False
    assert passed["session_id"] == "session"
    assert passed["runtime_instance_id"] == "avery-1"
    assert passed["fingerprint"] == second["fingerprint"]
    assert len(state.commands) == 1
    with Repository(state.repo.path) as reopened:
        selected = SkillStore(reopened).select(state.identity, NOW, "greeting")
        assert selected["version"] == 2
        assert selected["record_id"] == adopted["record_id"]
        assert selected["compiled"].fingerprint == second["fingerprint"]
        assert reopened.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


async def test_shared_code_never_grants_another_actor_repertoire_or_trial(state):
    registered, passed, _ = await learned(state)
    bea = replace(state.identity, actor_id="bea", runtime_instance_id="bea-1")
    assert state.store.library(bea, NOW) == {}
    assert state.store.select(bea, NOW) is None
    with pytest.raises(ValueError, match="observe or receive"):
        candidate(state, identity=bea)
    observation(state.repo, bea, "bea-heard", provenance="reported")
    with pytest.raises(ValueError, match="visible timeline"):
        state.store.adopt(bea, NOW, passed["record_id"], ["bea-heard"])
    copied = candidate(state, identity=bea, sources=["bea-heard"])
    assert copied["fingerprint"] == registered["fingerprint"]
    assert state.store.select(bea, NOW) is None
    own = await trial(state, copied, identity=bea)
    state.store.adopt(bea, NOW, own["record_id"], ["bea-heard"])
    assert state.store.select(bea, NOW)["actor_id"] == "bea"
    assert state.repo.connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 1
    assert "not a prompt excerpt" not in json.dumps(
        dict(state.repo.connection.execute("SELECT * FROM skill_versions").fetchone())
    )


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"text": "Secret", "visibility_scope": "actor", "scope_id": "bea"}},
        {"observer_ids": ["bea"]},
        {"observer_ids": "prefix-avery-suffix"},
        {"protagonist_id": "michael"},
        {"provenance": "unverified"},
    ],
)
def test_private_need_sources_fail_closed(state, change):
    observation(state.repo, state.identity, "invalid", **change)
    with pytest.raises(ValueError):
        state.store.need_context(state.identity, NOW, ["invalid"])
    with pytest.raises(ValueError):
        candidate(state, sources=["invalid"])


def test_private_need_context_is_bounded_and_detects_delete_reimport(state):
    before = state.store.need_context(state.identity, NOW, ["need"])
    assert before["source_events"][0]["text"] == "I did not face the speaker."
    assert "private_hidden_detail" not in json.dumps(before)
    state.repo.delete_event("profile", "need")
    observation(state.repo, state.identity)
    after = state.store.need_context(state.identity, NOW, ["need"])
    assert after["audit_revision"] > before["audit_revision"]
    assert after["source_events"] == before["source_events"]


def test_registration_rechecks_source_audit_inside_write_transaction(state):
    before = state.store.need_context(state.identity, NOW, ["need"])
    with Repository(state.repo.path) as concurrent:
        concurrent.delete_event("profile", "need")
        observation(concurrent, state.identity)
    with pytest.raises(ValueError, match="audit changed"):
        candidate(state, expected_audit_revision=before["audit_revision"])
    assert state.store.history(state.identity, NOW) == []
    assert state.repo.connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


async def test_reused_trial_identifier_never_repeats_execution(state):
    registered = candidate(state)
    await trial(state, registered, record_id="owned-trial")
    assert len(state.commands) == 1
    with pytest.raises(ValueError, match="already used"):
        await trial(state, registered, record_id="owned-trial")
    assert len(state.commands) == 1


def test_reset_suppresses_unused_observations_without_harming_other_actors(state):
    observation(state.repo, state.identity, "shared", observer_ids=["avery", "bea"])
    state.repo.reset_actor("profile", "avery")
    with pytest.raises(ValueError, match="forgotten"):
        state.store.need_context(state.identity, NOW, ["need"])
    with pytest.raises(ValueError, match="forgotten"):
        candidate(state, sources=["shared"])
    bea = replace(state.identity, actor_id="bea", runtime_instance_id="bea-1")
    assert candidate(state, identity=bea, sources=["shared"])["actor_id"] == "bea"


async def test_reset_source_deletion_and_reimport_do_not_resurrect_adoption(state):
    await learned(state)
    state.repo.reset_actor("profile", "avery")
    assert state.store.history(state.identity, NOW) == []
    assert state.store.select(state.identity, NOW) is None
    state.repo.delete_event("profile", "need")
    observation(state.repo, state.identity)
    with pytest.raises(ValueError, match="forgotten"):
        candidate(state)
    with Repository(state.repo.path) as reopened:
        with pytest.raises(ValueError, match="forgotten"):
            SkillStore(reopened).need_context(state.identity, NOW, ["need"])
        assert reopened.connection.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 1


async def test_source_deletion_cascades_owned_trials_and_adoptions(state):
    await learned(state)
    assert state.repo.delete_event("profile", "need")
    assert state.store.history(state.identity, NOW) == []
    assert state.store.select(state.identity, NOW) is None
    assert state.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


@pytest.mark.parametrize("mutate", ["reset", "source_delete", "rebind"])
async def test_pending_trial_cannot_resurrect_deleted_or_rebound_state(state, mutate):
    registered = candidate(state)
    entered, release = asyncio.Event(), asyncio.Event()
    identity = state.identity

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await state.execute(*args)

    def environment(current):
        return state.environment(identity)

    task = asyncio.create_task(trial(state, registered, execute=blocked, environment=environment))
    await entered.wait()
    if mutate == "reset":
        state.repo.reset_actor("profile", "avery")
    elif mutate == "source_delete":
        state.repo.delete_event("profile", "need")
    else:
        identity = replace(identity, runtime_instance_id="replacement")
    release.set()
    if mutate == "rebind":
        result = await task
        assert result["result"]["status"] == "interrupted"
        assert result["runtime_instance_id"] == "avery-1"
        with pytest.raises(ValueError, match="completed"):
            state.store.adopt(state.identity, NOW, result["record_id"], ["need"])
    else:
        with pytest.raises(ValueError, match="visible timeline"):
            await task
        assert state.store.history(state.identity, NOW) == []


async def test_cancellation_joins_cleanup_and_retains_only_canceled_outcome(state):
    registered = candidate(state)
    executing, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        executing.set()
        await asyncio.Event().wait()

    async def cancel(*args):
        cleanup.set()
        await release.wait()

    task = asyncio.create_task(trial(state, registered, execute=blocked, cancel=cancel))
    await executing.wait()
    task.cancel()
    await cleanup.wait()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    outcome = state.store.history(state.identity, NOW)[0]["result"]
    assert outcome["status"] == "canceled"
    assert outcome["completed_operations"] is None
    assert state.store.select(state.identity, NOW) is None


async def test_trial_cannot_ingest_fabricated_result_or_duck_typed_interpreter(state):
    registered = candidate(state)

    class PretendInterpreter:
        async def run(self, *args, **kwargs):
            return {"status": "completed"}

    with pytest.raises(TypeError, match="actual SkillInterpreter"):
        await trial(state, registered, interpreter=PretendInterpreter())
    actual = SkillInterpreter(enabled=True, clock=lambda: 100)
    actual.run = PretendInterpreter().run
    result = await trial(state, registered, interpreter=actual)
    assert result["result"]["completed_operations"] == 1
    assert len(state.commands) == 1
    with pytest.raises(TypeError):
        await state.store.record_trial(
            registered["record_id"], state.identity, NOW, result={"status": "completed"}
        )


def test_candidate_versions_are_immutable_and_code_cannot_include_private_context(state):
    registered = candidate(state)
    changed = definition(consent=True)
    with pytest.raises(ValueError, match="immutable"):
        candidate(state, source=changed)
    copied = candidate(state)
    assert copied["fingerprint"] == registered["fingerprint"]
    invalid = definition(version=2)
    invalid["steps"][0]["proposal"]["reason_for_player"] = "Avery's secret memory."
    with pytest.raises(ValueError):
        candidate(state, source=invalid)
    invalid = definition(version=2)
    invalid["memories"] = ["Avery's secret memory."]
    with pytest.raises(ValueError):
        candidate(state, source=invalid)


async def test_rewind_protagonist_and_profile_isolation(state):
    early = NOW + timedelta(minutes=1)
    first, old_trial, _ = await learned(state, at=early)
    later = NOW + timedelta(hours=1)
    await learned(state, source=definition(version=2), at=later, revises=first["record_id"])
    fork_time = NOW + timedelta(minutes=5)
    branch = state.repo.open_timeline("profile", fork_time.isoformat())
    earlier = replace(state.identity, branch_id=branch, session_id="rewind")
    assert state.store.select(earlier, later)["version"] == 1
    assert all(record["version"] == 1 for record in state.store.history(earlier, later))
    michael = replace(state.identity, protagonist_id="michael")
    assert state.store.select(michael, later) is None
    with pytest.raises(ValueError):
        state.store.adopt(michael, later, old_trial["record_id"], ["need"])
    state.repo.register_actor("other", {"id": "avery", "display_name": "Avery"})
    other_branch = state.repo.open_timeline("other", NOW.isoformat())
    other = replace(state.identity, profile_id="other", branch_id=other_branch)
    assert state.store.select(other, later) is None
    assert state.store.library(other, later) == {}
    before_need = state.repo.open_timeline(
        "profile",
        (NOW - timedelta(minutes=1)).isoformat(),
        parent_branch_id=state.identity.branch_id,
    )
    assert state.store.select(replace(state.identity, branch_id=before_need), later) is None


async def test_rollback_retirement_and_latest_selection_are_explicit(state):
    first, old_trial, _ = await learned(state)
    await learned(state, source=definition(name="other_skill"))
    _, latest_trial, _ = await learned(
        state, source=definition(version=2), revises=first["record_id"]
    )
    assert state.store.select(state.identity, NOW)["skill_id"] == "greeting"
    state.store.adopt(state.identity, NOW, old_trial["record_id"], ["need"])
    assert state.store.select(state.identity, NOW, "greeting")["version"] == 1
    state.store.retire(state.identity, NOW, "greeting", ["need"])
    assert state.store.select(state.identity, NOW, "greeting") is None
    state.store.adopt(state.identity, NOW, latest_trial["record_id"], ["need"])
    assert state.store.select(state.identity, NOW, "greeting")["version"] == 2
    assert ("greeting", 1) not in state.store.library(state.identity, NOW)


async def test_retired_or_changed_dependencies_require_revalidation(state):
    child, child_trial, _ = await learned(state, source=definition(name="child"))
    parent, _, _ = await learned(state, source=definition(name="parent", child="child"))
    assert state.store.select(state.identity, NOW, "parent") is not None
    state.store.retire(state.identity, NOW, "child", ["need"])
    assert state.store.select(state.identity, NOW, "parent") is None
    # Even an unavailable implementation can be explicitly retired.
    state.store.retire(state.identity, NOW, "parent", ["need"])
    state.store.adopt(state.identity, NOW, child_trial["record_id"], ["need"])
    assert state.store.select(state.identity, NOW, "parent") is None
    state.store.registry_fingerprint = "changed-registry"
    assert state.store.select(state.identity, NOW, "child") is None
    with pytest.raises(ValueError, match="revalidation"):
        await trial(state, child)
    assert parent["dependency_records"] == [child["record_id"]]


async def test_source_deletion_invalidates_composed_dependent_even_if_own_source_survives(state):
    observation(state.repo, state.identity, "parent-need")
    await learned(state, source=definition(name="child"))
    await learned(state, source=definition(name="parent", child="child"), sources=["parent-need"])
    state.repo.delete_event("profile", "need")
    assert state.store.select(state.identity, NOW, "parent") is None
    assert state.repo.connection.execute(
        "SELECT 1 FROM events WHERE event_id='parent-need'"
    ).fetchone()


def test_ephemeral_actor_cannot_retain_skills(state):
    state.repo.register_actor(
        "profile", {"id": "ambient", "actor_kind": "ambient_ephemeral", "session_id": "session"}
    )
    ambient = replace(state.identity, actor_id="ambient")
    observation(state.repo, ambient, "ambient-need")
    with pytest.raises(ValueError, match="persistent"):
        candidate(state, identity=ambient, sources=["ambient-need"])


async def test_profile_erasure_removes_skills_and_does_not_break_foreign_keys(state):
    await learned(state)
    assert state.repo.delete_profile("profile")
    for table in ("skill_versions", "skill_records", "skill_record_sources"):
        assert state.repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert state.repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_atomic_event_batch_rolls_back_collisions_and_failed_preconditions(state):
    initial = state.repo.export_profile("profile")
    good = {**initial["events"][0], "event_id": "first"}
    collision = {**initial["events"][0], "payload": {"text": "changed"}}
    with pytest.raises(ValueError, match="collision"):
        state.repo.record_events_atomic("profile", state.identity.branch_id, [good, collision])
    assert state.repo.export_profile("profile") == initial

    def reject(repo):
        assert repo is state.repo
        raise ValueError("reservation is held")

    with pytest.raises(ValueError, match="reservation"):
        state.repo.record_events_atomic(
            "profile", state.identity.branch_id, [good], precondition=reject
        )
    assert state.repo.export_profile("profile") == initial
    assert state.repo.record_events_atomic("profile", state.identity.branch_id, [good]) == [True]
    assert state.repo.record_events_atomic("profile", state.identity.branch_id, [good]) == [False]


def test_atomic_precondition_serializes_exclusive_claims_across_connections(state):
    barrier = Barrier(2)

    def claim(number):
        with Repository(state.repo.path) as repo:
            barrier.wait()
            event = {
                "event_id": f"claim-{number}",
                "event_type": "simulation.reserved",
                "game_datetime": NOW.isoformat(),
                "protagonist_id": "franklin",
                "observer_ids": ["avery"],
                "provenance": "system_fact",
                "payload": {},
            }

            def exclusive(current):
                rows = current.connection.execute(
                    "SELECT 1 FROM events WHERE event_id LIKE 'claim-%'"
                ).fetchall()
                if rows:
                    raise ValueError("claimed")

            try:
                repo.record_events_atomic(
                    "profile", state.identity.branch_id, [event], precondition=exclusive
                )
                return True
            except ValueError:
                return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, [1, 2])) == [False, True]


def test_v2_migration_preserves_canonical_content_and_adds_empty_registry(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    with Repository(path) as repo:
        repo.register_actor("profile", {"id": "avery"})
        branch = repo.open_timeline("profile", NOW.isoformat())
        identity = SkillIdentity("profile", branch, "franklin", "s", 1, "avery", "a-1")
        observation(repo, identity)
        repo.connection.executescript(
            "DROP TABLE skill_record_sources; DROP TABLE skill_records; "
            "DROP TABLE skill_versions; PRAGMA user_version=2;"
        )
    with Repository(path) as repo:
        assert repo.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert repo.export_profile("profile")["events"][0]["event_id"] == "need"
        assert repo.export_profile("profile")["skill_versions"] == []
