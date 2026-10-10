"""Automatic development practice from independently verified external receipts."""

import asyncio
import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_runtime_skills import ack, observations
from test_runtime_skills import skill_scene as skill_scene

from service.lls.actions import POLICY_VERSION
from service.lls.skill_scheduler import SkillScheduler, SkillSchedulerConfig


@pytest.fixture
async def automatic(skill_scene):
    client, runtime, session, clock = skill_scene
    runtime.settings.dev_skill_autonomy = True
    runtime.settings.skill_scheduler = SkillSchedulerConfig(actor_cooldown_seconds=0)
    runtime.skill_scheduler = SkillScheduler(runtime, runtime.settings.skill_scheduler)
    session.actors["npc"]["mod_following"] = False
    return client, runtime, session, clock


async def external(
    automatic,
    suffix="one",
    *,
    status="failed",
    reason="orientation_not_reached",
    observed=None,
    dispatch=True,
    origin="external",
    actor_id="npc",
):
    client, runtime, session, _ = automatic
    input_id = "input-" + suffix
    runtime.repo.record_event(
        session.request.profile_id,
        session.branch_id,
        {
            "event_id": input_id,
            "game_datetime": session.game_datetime,
            "protagonist_id": "franklin",
            "event_type": "player.utterance_text",
            "payload": {"text": "Please face me briefly."},
            "provenance": "heard",
            "observer_ids": [actor_id],
        },
    )
    command = runtime.command(
        session,
        "action.execute",
        actor_id,
        {
            "action_id": "face_player",
            "arguments": {"duration_seconds": 0.5},
            "policy_version": POLICY_VERSION,
            "scene_id": "external-" + suffix,
            "consent": False,
            "turn_id": "turn-" + suffix,
        },
    )
    command["_skill_origin"] = origin
    command["_skill_source_event_id"] = input_id
    started = await ack(
        client,
        session,
        command,
        "started",
        reason="task_dispatched" if dispatch else "ordinary_start",
        observed={"task_dispatched": dispatch},
    )
    assert started.status_code == 200
    observed = (
        observed
        if observed is not None
        else {
            "distance_meters": 0.0,
            "heading_error_degrees": 40 if status == "failed" else 3,
        }
    )
    result = await ack(client, session, command, status, reason=reason, observed=observed)
    assert result.status_code == 200, result.text
    return command


async def run_practice(automatic):
    client, runtime, session, _ = automatic
    scheduler = runtime.skill_scheduler
    scheduler.tick()
    task = scheduler.workers[session.id]
    seen = set()
    for _ in range(200):
        commands = [
            command
            for command in session.commands.values()
            if command.get("_skill")
            and command["id"] not in seen
            and command["state"] not in {"completed", "canceled"}
        ]
        for command in commands:
            seen.add(command["id"])
            assert (await ack(client, session, command, "started")).status_code == 200
            completed = await ack(
                client, session, command, "completed", observed=observations(command)
            )
            assert completed.status_code == 200, completed.text
        if task.done():
            await task
            return seen
        await asyncio.sleep(0.005)
    pytest.fail(str(scheduler.diagnostics(session.id)))


async def test_automatic_revision_bridge_adoption_and_later_selected_practice(automatic):
    _, runtime, session, _ = automatic
    first = await external(automatic)
    assert runtime.skill_scheduler.diagnostics(session.id)["status"] == "queued"
    assert len(await run_practice(automatic)) == 2
    state = runtime.skill_scheduler.diagnostics(session.id)
    assert state["status"] == "adopted", state
    identity = runtime.skill_scheduler._identity(session, "npc")
    skill_id = runtime.skill_scheduler.skill_id(identity, "face_player")
    selected = runtime.skill_store.select(identity, session.game_datetime, skill_id)
    assert selected["version"] == 2
    second_actor(runtime, session)
    other_identity = runtime.skill_scheduler._identity(session, "other")
    assert runtime.skill_store.select(other_identity, session.game_datetime) is None
    history = runtime.skill_store.history(identity, session.game_datetime, skill_id=skill_id)
    trials = [record for record in history if record["kind"] == "trial"]
    assert {(r["version"], r["result"]["status"], r["result"]["verification"]) for r in trials} == {
        (1, "refused", "simulated"),
        (2, "completed", "simulated"),
        (2, "completed", "bridge_acknowledged"),
    }
    assert not runtime.skill_scheduler.pending
    assert all(
        command.get("_skill_origin") == "skill"
        for command in session.commands.values()
        if command.get("_skill")
    )
    row = runtime.repo.connection.execute("SELECT * FROM skill_episodes").fetchone()
    assert row["candidate_requests"] == 2 and row["trials"] == 1
    assert first["id"] + ":started" in json.loads(row["source_ids"])
    before = len(history)
    await external(automatic, "later", status="completed", reason="heading_observed")
    assert len(await run_practice(automatic)) == 2
    assert runtime.skill_scheduler.diagnostics(session.id)["status"] == "completed"
    assert (
        len(runtime.skill_store.history(identity, session.game_datetime, skill_id=skill_id))
        == before
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"dispatch": False},
        {"origin": "skill"},
        {"observed": {}},
        {"reason": "heading_unavailable"},
        {"reason": "stale_actor"},
        {"reason": "native_task_refused"},
        {"observed": {"distance_meters": True, "heading_error_degrees": 40}},
        {"observed": {"distance_meters": 0, "heading_error_degrees": 3}},
    ],
)
async def test_weak_receipts_and_engine_failures_cannot_create_needs(automatic, fields):
    await external(automatic, **fields)
    assert not automatic[1].skill_scheduler.pending
    assert (
        automatic[1].repo.connection.execute("SELECT COUNT(*) FROM skill_episodes").fetchone()[0]
        == 0
    )


async def test_repetition_requires_two_distinct_external_inputs_and_no_clock_unlock(automatic):
    _, runtime, session, clock = automatic
    await external(automatic, "one", status="completed")
    assert not runtime.skill_scheduler.pending
    clock.value += 100
    clock.wall += timedelta(seconds=100)
    runtime.skill_scheduler.tick()
    assert not runtime.skill_scheduler.workers
    session.actors["npc"]["_sampled_at"] = clock.value
    session.world["_sampled_at"] = clock.value
    await external(automatic, "one", status="completed")
    assert not runtime.skill_scheduler.pending
    await external(automatic, "two", status="completed")
    assert len(runtime.skill_scheduler.pending) == 1


async def test_restart_abandons_episode_without_replaying_or_blocking_new_evidence(automatic):
    client, runtime, session, _ = automatic
    command = await external(automatic)
    old = runtime.skill_scheduler
    runtime.skill_scheduler = SkillScheduler(runtime, runtime.settings.skill_scheduler)
    runtime.skill_scheduler.tick()
    assert not runtime.skill_scheduler.workers
    row = runtime.repo.connection.execute("SELECT state FROM skill_episodes").fetchone()
    assert row[0] == "abandoned"
    await ack(
        client,
        session,
        command,
        "failed",
        reason="orientation_not_reached",
        observed={"distance_meters": 0, "heading_error_degrees": 40},
    )
    assert not runtime.skill_scheduler.pending
    await external(automatic, "fresh")
    assert len(runtime.skill_scheduler.pending) == 1
    # The retired scheduler cannot reclaim the abandoned durable episode either.
    old.tick()
    assert not old.workers


async def test_candidate_context_exposes_only_typed_actor_visible_outcomes(automatic):
    _, runtime, session, _ = automatic
    command = await external(
        automatic,
        observed={
            "distance_meters": 0,
            "heading_error_degrees": 40,
            "secret": "do not copy this",
            "nested": {"private": "text"},
        },
    )
    identity = runtime.skill_scheduler._identity(session, "npc")
    context = runtime.skill_store.need_context(
        identity, session.game_datetime, [command["id"] + ":failed"]
    )
    evidence = context["source_events"][0]["action_evidence"]
    assert evidence == {
        "action_id": "face_player",
        "status": "failed",
        "reason": "orientation_not_reached",
        "observed": {"distance_meters": 0, "heading_error_degrees": 40},
    }


async def test_lifecycle_joins_scheduler_waiting_for_session_lock(automatic):
    _, runtime, session, _ = automatic
    await external(automatic)
    async with session.lock:
        runtime.skill_scheduler.tick()
        await asyncio.sleep(0)
        await asyncio.wait_for(runtime.skill_scheduler.join(session, erase=True), 1)
    assert not runtime.skill_scheduler.workers
    assert session.skill_task is None
    assert (
        runtime.repo.connection.execute(
            "SELECT COUNT(*) FROM skill_records WHERE kind='candidate'"
        ).fetchone()[0]
        == 0
    )


async def test_cancel_before_worker_first_turn_releases_durable_claim(automatic):
    _, runtime, session, _ = automatic
    await external(automatic)
    runtime.skill_scheduler.tick()
    await runtime.skill_scheduler.join(session)
    row = runtime.repo.connection.execute("SELECT state FROM skill_episodes").fetchone()
    assert row[0] == "canceled"
    await external(automatic, "fresh")
    assert len(runtime.skill_scheduler.pending) == 1
    assert not runtime.skill_scheduler.active


async def test_social_worker_reservation_prevents_skill_claim(automatic):
    _, runtime, session, _ = automatic
    await external(automatic)
    original = runtime.social_scheduler
    social_worker = asyncio.create_task(asyncio.Event().wait())
    runtime.social_scheduler = SimpleNamespace(workers={session.id: social_worker})
    try:
        runtime.skill_scheduler.tick()
        assert not runtime.skill_scheduler.workers
        assert len(runtime.skill_scheduler.pending) == 1
        assert (
            runtime.repo.connection.execute("SELECT attempts FROM skill_episodes").fetchone()[0]
            == 0
        )
    finally:
        social_worker.cancel()
        await asyncio.gather(social_worker, return_exceptions=True)
        runtime.social_scheduler = original


async def test_provider_suppressed_cancellation_cannot_persist_during_join(automatic, monkeypatch):
    _, runtime, session, _ = automatic
    entered = asyncio.Event()
    finished = asyncio.Event()
    original = runtime.provider.primary.skill_candidate

    async def suppressing(context, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
            result = await original(context, **kwargs)
            finished.set()
            return result

    monkeypatch.setattr(runtime.provider.primary, "skill_candidate", suppressing)
    await external(automatic)
    runtime.skill_scheduler.tick()
    await asyncio.wait_for(entered.wait(), 1)
    async with session.lock:
        await asyncio.wait_for(runtime.skill_scheduler.join(session), 1)
    assert finished.is_set()
    assert not runtime.skill_scheduler.active
    assert runtime.skill_scheduler.diagnostics(session.id)["status"] == "canceled"
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


async def test_source_deletion_cancels_before_generation_even_after_reimport(automatic):
    _, runtime, session, _ = automatic
    command = await external(automatic)
    source_id = command["id"] + ":started"
    raw = runtime.repo.connection.execute(
        "SELECT data FROM events WHERE event_id=?", (source_id,)
    ).fetchone()[0]
    runtime.repo.delete_event(session.request.profile_id, source_id)
    runtime.repo.record_event(session.request.profile_id, session.branch_id, json.loads(raw))
    runtime.skill_scheduler.tick()
    await asyncio.wait_for(runtime.skill_scheduler.workers[session.id], 1)
    assert runtime.skill_scheduler.diagnostics(session.id)["status"] == "failed"
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


async def test_candidate_revision_limit_stops_without_self_failure_loop(automatic, monkeypatch):
    _, runtime, session, clock = automatic
    original = runtime.provider.primary.skill_candidate

    async def stubborn(context, **kwargs):
        result = await original(context, **kwargs)
        result["steps"].insert(0, {"op": "require", "condition": "mod_following"})
        return result

    monkeypatch.setattr(runtime.provider.primary, "skill_candidate", stubborn)
    await external(automatic)
    runtime.skill_scheduler.tick()
    await asyncio.wait_for(runtime.skill_scheduler.workers[session.id], 1)
    assert runtime.skill_scheduler.diagnostics(session.id)["status"] == "stopped"
    count = runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0]
    for _ in range(3):
        clock.value += 100
        runtime.skill_scheduler.tick()
    assert (
        runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == count
    )
    row = runtime.repo.connection.execute("SELECT * FROM skill_episodes").fetchone()
    assert row["candidate_requests"] == 2 and row["trials"] == 0


async def test_immutable_deadline_and_request_budget_stop_generation(automatic, monkeypatch):
    _, runtime, session, clock = automatic
    original = runtime.provider.primary.skill_candidate

    async def delayed(context, **kwargs):
        result = await original(context, **kwargs)
        clock.wall += timedelta(seconds=46)
        return result

    monkeypatch.setattr(runtime.provider.primary, "skill_candidate", delayed)
    await external(automatic)
    runtime.skill_scheduler.tick()
    await asyncio.wait_for(runtime.skill_scheduler.workers[session.id], 1)
    row = runtime.repo.connection.execute("SELECT * FROM skill_episodes").fetchone()
    assert row["state"] == "failed" and row["candidate_requests"] == 1 and row["trials"] == 0
    assert not any(c.get("_skill") for c in session.commands.values())
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


def second_actor(runtime, session):
    runtime.repo.register_actor(
        session.request.profile_id,
        {
            "actor_id": "other",
            "actor_kind": "curated_persistent",
            "display_name": "Other",
            "control_policy": "ambient",
            "allowed_actions": ["face_player", "wait_here"],
        },
    )
    session.actors["other"] = {**session.actors["npc"], "runtime_instance_id": "other-ped"}


async def test_actor_privacy_queue_capacity_and_fair_selection(automatic, monkeypatch):
    _, runtime, session, _ = automatic
    second_actor(runtime, session)

    async def unavailable(context, **kwargs):
        return None

    monkeypatch.setattr(runtime.provider.primary, "skill_candidate", unavailable)
    await external(automatic, "first")
    runtime.skill_scheduler.tick()
    await runtime.skill_scheduler.workers[session.id]
    await external(automatic, "second")
    await external(automatic, "other", actor_id="other")
    runtime.skill_scheduler.tick()
    assert runtime.skill_scheduler.active[session.id].identity.actor_id == "other"
    await runtime.skill_scheduler.workers[session.id]
    assert (
        runtime.skill_store.history(
            runtime.skill_scheduler._identity(session, "other"), session.game_datetime
        )
        == []
    )
    runtime.skill_scheduler.config.max_queued_per_profile = 1
    await external(automatic, "other-again", actor_id="other")
    row = runtime.repo.connection.execute(
        "SELECT state FROM skill_episodes WHERE actor_id='other' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "suppressed"


async def test_profile_request_cap_and_session_cap_are_reserved_before_work(automatic):
    _, runtime, session, _ = automatic
    runtime.skill_scheduler.config.max_candidate_requests_per_minute = 2
    await external(automatic)
    await run_practice(automatic)
    second_actor(runtime, session)
    await external(automatic, "second", actor_id="other")
    runtime.skill_scheduler.tick()
    await runtime.skill_scheduler.workers[session.id]
    assert runtime.skill_scheduler.diagnostics(session.id)["reason"] == (
        "scheduler candidate request limit"
    )
    row = runtime.repo.connection.execute(
        "SELECT candidate_requests FROM skill_episodes WHERE actor_id='other'"
    ).fetchone()
    assert row[0] == 0
    runtime.skill_scheduler.config.max_episodes_per_session = 2
    await external(automatic, "third", actor_id="other")
    runtime.skill_scheduler.tick()
    assert (
        runtime.repo.connection.execute(
            "SELECT reason FROM skill_episodes ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        == "session_episode_limit"
    )


async def test_cancel_during_bridge_projection_cannot_adopt_or_continue(automatic, monkeypatch):
    client, runtime, session, _ = automatic
    await external(automatic)
    runtime.skill_scheduler.tick()
    command = None
    for _ in range(100):
        command = next((c for c in session.commands.values() if c.get("_skill")), None)
        if command is not None:
            break
        await asyncio.sleep(0.005)
    assert command is not None
    assert (await ack(client, session, command, "started")).status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = runtime.repo.add_memory

    def blocked(*args, **kwargs):
        if kwargs.get("memory_id") == command["id"] + ":completed:memory":
            entered.set()
            if not release.wait(3):
                raise ValueError("test projection release missing")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "add_memory", blocked)
    receipt = asyncio.create_task(
        ack(client, session, command, "completed", observed=observations(command))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        runtime.skill_scheduler.cancel(session)
        await asyncio.wait_for(runtime.skill_scheduler.join(session), 1)
        assert command["state"] == "canceled"
    finally:
        release.set()
        response = await asyncio.wait_for(receipt, 1)
    assert response.status_code == 409
    assert (
        runtime.repo.connection.execute(
            "SELECT COUNT(*) FROM skill_records WHERE kind='adopt'"
        ).fetchone()[0]
        == 0
    )
    assert len([c for c in session.commands.values() if c.get("_skill")]) == 1


@pytest.mark.parametrize(
    "reason,retired",
    [
        ("orientation_not_reached", True),
        ("heading_unavailable", False),
    ],
)
async def test_retirement_requires_measured_failure_not_engine_error(automatic, reason, retired):
    client, runtime, session, _ = automatic
    await external(automatic)
    await run_practice(automatic)
    previous = set(session.commands)
    await external(automatic, "later", status="completed")
    runtime.skill_scheduler.tick()
    command = None
    for _ in range(100):
        command = next(
            (c for c in session.commands.values() if c.get("_skill") and c["id"] not in previous),
            None,
        )
        if command is not None:
            break
        await asyncio.sleep(0.005)
    assert command is not None
    assert (await ack(client, session, command, "started")).status_code == 200
    result = await ack(
        client,
        session,
        command,
        "failed",
        reason=reason,
        observed={"distance_meters": 0, "heading_error_degrees": 40},
    )
    assert result.status_code == 200
    await runtime.skill_scheduler.workers[session.id]
    state = runtime.skill_scheduler.diagnostics(session.id)
    assert state["status"] == ("retired" if retired else "stopped"), state["reason"]
    identity = runtime.skill_scheduler._identity(session, "npc")
    selected = runtime.skill_store.select(identity, session.game_datetime)
    assert (selected is None) is retired
    assert not runtime.skill_scheduler.pending
