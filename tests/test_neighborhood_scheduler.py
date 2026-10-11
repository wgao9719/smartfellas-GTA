"""Bounded automatic virtual progress, with no initialization or native embodiment."""

import asyncio
import json
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_neighborhood_bridge import PREFIX, START, envelope, world
from test_neighborhood_bridge import bridge as bridge

from service.lls.neighborhood import EmbodimentRequest, NeighborhoodScope
from service.lls.neighborhood_scheduler import NeighborhoodScheduler, NeighborhoodSchedulerConfig
from service.lls.protocol import Position
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.skill_scheduler import SkillScheduler


@pytest.fixture
async def automatic(bridge):
    client, runtime, session, clock = bridge
    # Lifespan maintenance ticks every wall-clock second, but scheduler pacing uses the frozen
    # fake clock: on a slow runner an unrequested tick would take the attempt a test drives.
    (maintenance,) = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "cr_code", None) is Runtime.maintain.__code__
    ]
    maintenance.cancel()
    await asyncio.gather(maintenance, return_exceptions=True)
    runtime.settings.dev_neighborhood_autonomy = True
    runtime.neighborhood.scheduler = NeighborhoodScheduler(runtime.neighborhood)
    return client, runtime, session, clock


async def tick(fixture):
    _, runtime, session, _ = fixture
    scheduler = runtime.neighborhood.scheduler
    scheduler.tick()
    if scheduler.workers:
        await asyncio.wait_for(asyncio.gather(*list(scheduler.workers.values())), 3)
    return scheduler.diagnostics(session.id)


async def initialize(fixture, *, at=START + timedelta(hours=7)):
    client, _, session, _ = fixture
    result = await client.post(PREFIX + "initialize", json=envelope(session))
    assert result.status_code == 200, result.text
    await world(client, session, at=at)


def fresh(fixture, seconds=3):
    _, _, session, clock = fixture
    clock[0] += seconds
    session.world["_sampled_at"] = clock[0]


def event_count(runtime):
    return runtime.repo.connection.execute(
        "SELECT COUNT(*) FROM events WHERE json_extract(data,'$.event_type') LIKE 'neighborhood.%'"
    ).fetchone()[0]


def test_separate_default_off_and_nonrelease_gates():
    assert Settings().dev_neighborhood_autonomy is False
    for fields in ({}, {"release": False}):
        with pytest.raises(ValidationError, match="neighborhood autonomy"):
            Settings(**fields, dev_neighborhood_autonomy=True)
    assert Settings(
        release=False,
        dev_neighborhood=True,
        dev_neighborhood_autonomy=True,
    ).dev_neighborhood_autonomy


async def test_automatic_worker_never_initializes_an_authored_pack(automatic):
    client, runtime, session, _ = automatic
    state = await tick(automatic)
    assert state["reason"] == "explicit_initialization_required"
    assert state["configured"] is False
    assert event_count(runtime) == 0
    fresh(automatic, 61)
    assert (await tick(automatic))["attempts"] == 1
    await initialize(automatic)
    fresh(automatic)
    state = await tick(automatic)
    assert state["configured"]
    assert 0 < state["confirmed_events_written"] <= 64
    assert state["reserved_events"] == 128
    assert not session.commands
    response = await client.get("/v1/diagnostics", params={"session_id": session.id})
    assert response.status_code == 200
    assert response.json()["neighborhood"]["scheduler"]["backlog_actors"] == state["backlog_actors"]


async def test_runtime_maintenance_advances_without_advance_request(automatic):
    _, runtime, session, _ = automatic
    await initialize(automatic)
    before = event_count(runtime)
    maintenance = asyncio.create_task(runtime.maintain())
    try:
        async with asyncio.timeout(3):
            while event_count(runtime) == before:
                await asyncio.sleep(0.01)
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
    assert runtime.neighborhood.scheduler.diagnostics(session.id)["attempts"] == 1
    assert not session.commands


@pytest.mark.parametrize("denial", ["setting", "capability", "world", "unsafe", "foreground"])
async def test_gates_and_foreground_priority_prevent_work(automatic, denial):
    _, runtime, session, clock = automatic
    await initialize(automatic)
    before = event_count(runtime)
    if denial == "setting":
        runtime.settings.dev_neighborhood_autonomy = False
    elif denial == "capability":
        session.request.manifest.capability_flags.remove("development:neighborhood:v1")
    elif denial == "world":
        clock[0] += 6
    elif denial == "unsafe":
        session.world["mission_active"] = True
    else:
        session.speech_state = "recording"
    state = await tick(automatic)
    assert state["attempts"] == 0
    assert event_count(runtime) == before


async def test_scope_and_minute_budgets_are_not_multiplied_by_duplicate_sessions(automatic):
    _, runtime, session, _ = automatic
    await initialize(automatic, at=START + timedelta(days=3))
    scheduler = runtime.neighborhood.scheduler
    scheduler.config.max_events_per_minute = scheduler.config.max_events_per_scope = 64
    duplicate = replace(session, id="duplicate", lock=asyncio.Lock())
    runtime.sessions[duplicate.id] = duplicate
    first = await tick(automatic)
    assert first["attempts"] == 1
    fresh(automatic, 61)
    second = await tick(automatic)
    assert second["scope_count"] == 1
    assert second["reserved_events"] == 64
    assert second["reason"] == "scope_budget_exhausted"
    assert scheduler.diagnostics(duplicate.id)["attempts"] == 1
    runtime.sessions.pop(duplicate.id)


async def test_fair_scope_selection_and_shared_duplicate_foreground(automatic):
    _, runtime, first, _ = automatic
    await initialize(automatic)
    second = replace(
        first,
        id="other",
        request=first.request.model_copy(update={"protagonist_id": "michael"}),
        lock=asyncio.Lock(),
        world={**first.world, "protagonist_id": "michael"},
    )
    runtime.sessions[second.id] = second
    await tick(automatic)
    await tick(automatic)
    scheduler = runtime.neighborhood.scheduler
    assert scheduler.diagnostics(first.id)["attempts"] == 1
    assert scheduler.diagnostics(second.id)["attempts"] == 1
    assert scheduler.diagnostics(second.id)["configured"] is False
    duplicate = replace(first, id="duplicate", lock=asyncio.Lock(), speech_state="recording")
    runtime.sessions[duplicate.id] = duplicate
    fresh(automatic)
    assert (await tick(automatic))["reason"] == "foreground_priority"
    runtime.sessions.pop(second.id)
    runtime.sessions.pop(duplicate.id)


async def test_active_skill_worker_excludes_neighborhood_between_skill_calls(automatic):
    _, runtime, session, _ = automatic
    await initialize(automatic)
    runtime.skill_scheduler = SkillScheduler(runtime)
    release = asyncio.Event()
    worker = asyncio.create_task(release.wait())
    runtime.skill_scheduler.workers[session.id] = worker
    before = event_count(runtime)
    try:
        assert session.skill_task is None
        assert not session.commands
        state = await tick(automatic)
        assert state["reason"] == "foreground_priority"
        assert state["attempts"] == 0
        assert event_count(runtime) == before
    finally:
        release.set()
        await worker
    # A completed skill worker does not prevent the next automatic step.
    state = await tick(automatic)
    assert state["attempts"] == 1
    assert state["confirmed_events_written"] > 0


@pytest.mark.parametrize(
    "operation", ["erase", "close", "provider", "foreground", "skill_worker", "rewind", "timeout"]
)
async def test_joined_worker_cannot_append_after_authority_changes(
    automatic, monkeypatch, operation
):
    _, runtime, session, _ = automatic
    await initialize(automatic)
    before = event_count(runtime)
    entered, release = threading.Event(), threading.Event()
    original = runtime.repo.record_events_atomic

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "record_events_atomic", blocked)
    scheduler = runtime.neighborhood.scheduler
    scheduler.config.worker_timeout_seconds = 1
    scheduler.tick()
    assert await asyncio.to_thread(entered.wait, 1)
    worker = next(iter(scheduler.workers.values()))
    assert not session.lock.locked()
    operation_task = None
    if operation == "close":
        operation_task = asyncio.create_task(runtime.close_session(session))
    elif operation == "provider":
        operation_task = asyncio.create_task(runtime.configure_provider(runtime.settings))
    elif operation == "erase":

        async def erase():
            async with session.lock:
                runtime.cancel(session)
                await runtime.stop_session_work(session, erase=True)
                await runtime.db("reset_actor", session.request.profile_id, "neighbor-001")

        operation_task = asyncio.create_task(erase())
    elif operation == "foreground":
        session.speech_state = "recording"
    elif operation == "skill_worker":
        runtime.skill_scheduler = SkillScheduler(runtime)
        runtime.skill_scheduler.workers[session.id] = asyncio.create_task(asyncio.Event().wait())
    elif operation == "rewind":
        session.game_datetime = START.isoformat()
    else:
        await asyncio.sleep(1.1)
        assert not worker.done()
    if operation_task:
        await asyncio.sleep(0.02)
        assert not operation_task.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 2)
    if operation_task:
        await asyncio.wait_for(operation_task, 2)
    assert event_count(runtime) == before
    assert not scheduler.workers
    assert not session.commands or all(
        c["kind"] != "action.execute" for c in session.commands.values()
    )
    if operation == "timeout":
        assert scheduler.diagnostics(session.id)["reason"] == "worker_time_limit"


@pytest.mark.parametrize("blocker", ["live_binding", "reservation"])
async def test_automatic_append_preserves_engine_binding_and_reservation_checks(
    automatic, monkeypatch, blocker
):
    client, runtime, session, _ = automatic
    await initialize(automatic, at=START)
    result = await client.post(PREFIX + "advance", json=envelope(session))
    assert result.status_code == 200, result.text
    await world(client, session, at=START + timedelta(hours=7))
    entered, release = threading.Event(), threading.Event()
    original = runtime.repo.record_events_atomic

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "record_events_atomic", blocked)
    scheduler = runtime.neighborhood.scheduler
    scheduler.tick()
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # Use the normal writer for the concurrent authority change.
        monkeypatch.setattr(runtime.repo, "record_events_atomic", original)
        if blocker == "live_binding":
            runtime.repo.bind_instance(
                session.request.profile_id, "neighbor-001", session.id, 10, 1
            )
        else:
            scope = NeighborhoodScope(
                profile_id=session.request.profile_id,
                branch_id=session.branch_id,
                protagonist_id=session.request.protagonist_id,
                neighborhood_id="authored_demo",
            )
            runtime.neighborhood.engine.reserve_embodiment(
                scope,
                START,
                EmbodimentRequest(
                    actor_id="neighbor-001",
                    session_id=session.id,
                    runtime_generation=session.request.runtime_generation,
                    player_position=Position(x=0, y=0, z=0),
                ),
            )
        before = event_count(runtime)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*list(scheduler.workers.values())), 2)
    assert event_count(runtime) == before
    assert scheduler.diagnostics(session.id)["reason"] == "advancement_rejected"


async def test_cancellation_while_waiting_for_session_lock_does_not_deadlock(automatic):
    _, runtime, session, _ = automatic
    await initialize(automatic)
    async with session.lock:
        scheduler = runtime.neighborhood.scheduler
        scheduler.tick()
        await asyncio.sleep(0)
        runtime.cancel(session)
        await asyncio.wait_for(runtime.stop_session_work(session), 1)
    assert not scheduler.workers


async def test_source_delete_reimport_cannot_restore_automatic_baseline(automatic, monkeypatch):
    _, runtime, session, _ = automatic
    await initialize(automatic)
    entered, release = threading.Event(), threading.Event()
    original = runtime.repo.record_events_atomic

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "record_events_atomic", blocked)
    scheduler = runtime.neighborhood.scheduler
    scheduler.tick()
    assert await asyncio.to_thread(entered.wait, 1)
    row = runtime.repo.connection.execute(
        "SELECT * FROM events WHERE json_extract(data,'$.event_type')='neighborhood.configured'"
    ).fetchone()
    runtime.repo.delete_event(session.request.profile_id, row["event_id"])
    runtime.repo.record_event(session.request.profile_id, row["branch_id"], json.loads(row["data"]))
    before = event_count(runtime)
    release.set()
    await asyncio.wait_for(asyncio.gather(*list(scheduler.workers.values())), 2)
    assert event_count(runtime) == before
    assert scheduler.diagnostics(session.id)["reason"] == "advancement_rejected"


async def test_reset_actor_stays_suppressed_without_automatic_reinitialization(automatic):
    client, runtime, session, _ = automatic
    await initialize(automatic)
    runtime.repo.reset_actor(session.request.profile_id, "neighbor-001")
    await tick(automatic)
    result = await client.post(PREFIX + "snapshot", json=envelope(session))
    actor = next(a for a in result.json()["actors"] if a["actor_id"] == "neighbor-001")
    assert actor["status"] == "suppressed"
    assert runtime.neighborhood.scheduler.diagnostics(session.id)["backlog_actors"] > 0


async def test_manual_advance_serializes_with_automatic_worker(automatic, monkeypatch):
    client, runtime, session, _ = automatic
    await initialize(automatic)
    entered, release = threading.Event(), threading.Event()
    original = runtime.repo.record_events_atomic

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "record_events_atomic", blocked)
    scheduler = runtime.neighborhood.scheduler
    scheduler.tick()
    assert await asyncio.to_thread(entered.wait, 1)
    worker = next(iter(scheduler.workers.values()))
    manual = asyncio.create_task(client.post(PREFIX + "advance", json=envelope(session)))
    await asyncio.sleep(0.02)
    assert not manual.done()
    release.set()
    await asyncio.wait_for(worker, 2)
    result = await asyncio.wait_for(manual, 2)
    assert result.status_code == 200, result.text
    assert scheduler.diagnostics(session.id)["attempts"] == 1
    assert runtime.repo.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_scheduler_configuration_is_copied_and_bounded(automatic):
    _, runtime, _, _ = automatic
    config = NeighborhoodSchedulerConfig()
    scheduler = NeighborhoodScheduler(runtime.neighborhood, config)
    config.max_events_per_step = 256
    assert scheduler.config.max_events_per_step == 64
    with pytest.raises(ValidationError):
        NeighborhoodSchedulerConfig(max_events_per_step=257)
