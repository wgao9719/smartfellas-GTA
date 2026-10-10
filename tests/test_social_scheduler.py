"""Automatic development conversations still require separate bridge hearing evidence."""

import asyncio
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from test_runtime_social import acknowledge, envelope, listener
from test_runtime_social import heard_memories as memories
from test_runtime_social import social_scene as social_scene

from service.lls.__main__ import fixture_event
from service.lls.settings import Settings
from service.lls.social import FakeSocialProvider
from service.lls.social_bridge import DEVELOPMENT_SOCIAL_CAPABILITY
from service.lls.social_scheduler import SocialScheduler, SocialSchedulerConfig, _Schedule


@pytest.fixture
def automatic(social_scene):
    client, runtime, session, clock = social_scene
    runtime.settings.dev_social_autonomy = True
    runtime.social_scheduler = SocialScheduler(runtime)
    return client, runtime, session, clock


async def tick(fixture):
    _, runtime, session, _ = fixture
    await runtime.social_scheduler.tick(session)
    worker = runtime.social_scheduler.workers.get(session.id)
    if worker is not None:
        await asyncio.wait_for(asyncio.shield(worker), 2)
    return runtime.social_scheduler.diagnostics(session.id)


def fresh(fixture, seconds):
    _, runtime, session, clock = fixture
    clock.value += seconds
    clock.wall += timedelta(seconds=seconds)
    session.world["_sampled_at"] = clock.value
    for actor in session.actors.values():
        actor["_sampled_at"] = clock.value


def heard_memories(runtime, session, actor_id):
    return [
        memory for memory in memories(runtime, session, actor_id)
        if memory["provenance"] == "reported"
    ]


async def heard(fixture, *, can_hear=True):
    client, runtime, session, clock = fixture
    state = runtime.social_scheduler.diagnostics(session.id)
    command = session.commands[state["pending_command_id"]]
    await acknowledge(client, session, command)
    participants = session.social_scenes[state["scene_id"]]["participants"]
    target = next(actor for actor in participants if actor != command["actor_id"])
    actor = session.actors[target]
    response = await client.post(
        "/v1/bridge/social/delivery",
        json=envelope(
            session,
            scene_id=state["scene_id"],
            command_id=command["id"],
            witnesses=[
                listener(
                    session,
                    clock,
                    actor_id=target,
                    runtime_instance_id=actor["runtime_instance_id"],
                    position=actor["position"],
                    can_hear=can_hear,
                )
            ],
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_scheduler_requires_a_separate_nonrelease_opt_in():
    assert Settings().dev_social_autonomy is False
    with pytest.raises(ValidationError, match="social autonomy"):
        Settings(dev_social_autonomy=True)
    with pytest.raises(ValidationError, match="social autonomy"):
        Settings(release=False, dev_social_autonomy=True)
    assert Settings(release=False, dev_social=True, dev_social_autonomy=True).dev_social_autonomy


async def test_maintenance_starts_scene_without_invite_or_turn_requests(automatic):
    _, runtime, session, _ = automatic
    async with asyncio.timeout(2):
        while not runtime.social_scheduler.diagnostics(session.id)["pending_command_id"]:
            await asyncio.sleep(0.01)
    state = runtime.social_scheduler.diagnostics(session.id)
    assert state["status"] == "waiting"
    assert state["reserved_model_calls"] == 4
    assert runtime.social.get_scene(state["scene_id"])["decisions"] == {
        "npc-a": "continue",
        "npc-b": "accept",
    }
    assert len([c for c in session.commands.values() if c.get("_social")]) == 1
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())


async def test_subtitle_ack_cannot_advance_scheduler_or_create_listener_memory(automatic):
    client, runtime, session, _ = automatic
    first = await tick(automatic)
    command = session.commands[first["pending_command_id"]]
    await acknowledge(client, session, command)
    for _ in range(3):
        after = await tick(automatic)
    assert after["queued_turns"] == 1
    assert after["reason"] == "waiting_for_hearing"
    assert after["reserved_model_calls"] == 4
    assert heard_memories(runtime, session, "npc-b") == []
    assert command["id"] + ":delivered" in session.delivered_utterances
    response = await client.get("/v1/diagnostics", params={"session_id": session.id})
    assert response.status_code == 200
    diagnostic = response.json()["social_scheduler"]
    assert command["payload"]["text"] not in json.dumps(diagnostic)
    assert diagnostic["hearing_source"] == "external_social_delivery_evidence"


async def test_confirmed_recipient_hearing_allows_next_independent_turn(automatic):
    _, runtime, session, _ = automatic
    first = await tick(automatic)
    result = await heard(automatic)
    assert set(result["heard"]) == {"npc-b"}
    after = await tick(automatic)
    assert after["scene_id"] == first["scene_id"]
    assert after["queued_turns"] == 2
    assert after["reserved_model_calls"] == 6
    assert session.commands[after["pending_command_id"]]["actor_id"] == "npc-b"
    assert len(heard_memories(runtime, session, "npc-b")) == 1


async def test_unknown_hearing_stops_instead_of_scheduling_another_turn(automatic):
    _, runtime, session, _ = automatic
    first = await tick(automatic)
    assert (await heard(automatic, can_hear=None))["heard"] == {}
    after = await tick(automatic)
    assert after["status"] == "canceled"
    assert after["reason"] == "listener_not_confirmed"
    assert after["queued_turns"] == 1
    assert heard_memories(runtime, session, "npc-b") == []
    assert runtime.social.get_scene(first["scene_id"])["status"] == "canceled"


async def test_hearing_timeout_preserves_diagnostic_and_cancels_pending_subtitle(automatic):
    _, runtime, session, _ = automatic
    first = await tick(automatic)
    fresh(automatic, 9)
    after = await tick(automatic)
    assert after["status"] == "canceled"
    assert after["reason"] == after["last_wait_reason"] == "waiting_for_hearing"
    assert after["last_scene_id"] == first["scene_id"]
    assert session.commands[first["pending_command_id"]]["state"] == "canceled"
    assert heard_memories(runtime, session, "npc-b") == []


@pytest.mark.parametrize(
    "denial", ["setting", "capability", "player", "stale", "danger", "foreground"]
)
async def test_missing_authority_or_safety_does_not_start_model_work(automatic, denial):
    _, runtime, session, clock = automatic
    if denial == "setting":
        runtime.settings.dev_social_autonomy = False
    elif denial == "capability":
        session.request.manifest.capability_flags.remove(DEVELOPMENT_SOCIAL_CAPABILITY)
    elif denial == "player":
        for actor in session.actors.values():
            actor["distance"] = 21
    elif denial == "stale":
        clock.value += 3
    elif denial == "danger":
        session.world["mission_active"] = True
    else:
        session.speech_state = "recording"
    await tick(automatic)
    assert runtime.social_scheduler.diagnostics(session.id)["reserved_model_calls"] == 0
    assert not runtime.social.scenes_for_session(session.id)


async def test_scheduler_budget_is_reserved_in_addition_to_gateway_accounting(automatic):
    _, runtime, session, _ = automatic
    runtime.social_scheduler.config.max_calls_per_session = 4
    first = await tick(automatic)
    await heard(automatic)
    after = await tick(automatic)
    assert after["status"] == "canceled"
    assert after["reason"] == "model_call_limit"
    assert after["reserved_model_calls"] == 4
    assert runtime.provider.usage_summary()["background"]["requests"] == 0
    assert runtime.social.get_scene(first["scene_id"])["status"] == "canceled"


async def test_refusal_cooldowns_pair_fairness_and_initiator_acceptance(automatic):
    _, runtime, session, _ = automatic
    actor = runtime.repo.get_actor(session.request.profile_id, "npc-a")
    actor["persona"]["social"]["accept_invitations"] = False
    runtime.repo.register_actor(session.request.profile_id, actor)
    first = await tick(automatic)
    assert first["status"] == "refused"
    assert runtime.social.get_scene(first["last_scene_id"])["decisions"] == {"npc-a": "refuse"}
    assert (await tick(automatic))["scene_attempts"] == 1
    fresh(automatic, 121)
    second = await tick(automatic)
    second_scene = runtime.social.get_scene(second["last_scene_id"])
    # Wall time cannot erase an actor's retained game-time refusal boundary.
    assert second_scene["participant_ids"] == ["npc-b", "npc-c"]
    assert second_scene["decisions"] == {"npc-b": "continue", "npc-c": "accept"}
    async with session.lock:
        runtime.cancel(session)
        await runtime.stop_session_work(session)
    fresh(automatic, 121)
    session.game_datetime = (
        datetime.fromisoformat(session.game_datetime) + timedelta(seconds=600)
    ).isoformat()
    third = await tick(automatic)
    third_scene = runtime.social.get_scene(third["last_scene_id"])
    assert third_scene["participant_ids"] == ["npc-c", "npc-a"]
    assert third_scene["decisions"] == {"npc-c": "accept", "npc-a": "refuse"}
    assert third["scene_attempts"] == 3


async def test_recipient_can_withdraw_before_its_automatic_turn(automatic):
    _, runtime, session, _ = automatic
    await tick(automatic)
    await heard(automatic)
    actor = runtime.repo.get_actor(session.request.profile_id, "npc-b")
    actor["persona"]["social"]["withdraw"] = True
    runtime.repo.register_actor(session.request.profile_id, actor)
    after = await tick(automatic)
    assert after["status"] == "withdrawn"
    assert after["reason"] == "participant_withdrew"
    assert after["queued_turns"] == 1
    fresh(automatic, 121)
    next_scene = await tick(automatic)
    assert runtime.social.get_scene(next_scene["scene_id"])["participant_ids"] == ["npc-c", "npc-a"]


@pytest.mark.parametrize("operation", ["foreground", "provider", "shutdown"])
async def test_interruption_joins_scheduler_without_session_lock_deadlock(automatic, operation):
    client, runtime, session, _ = automatic
    started, stopped = asyncio.Event(), asyncio.Event()

    class Blocked(FakeSocialProvider):
        async def dialogue(self, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            yield "unreachable"

    runtime.provider.primary = Blocked()
    await runtime.social_scheduler.tick(session)
    await asyncio.wait_for(started.wait(), 1)
    await runtime.social_scheduler.tick(session)
    assert runtime.social_scheduler.diagnostics(session.id)["reserved_model_calls"] == 4
    if operation == "foreground":
        event = fixture_event(
            session.id,
            session.last_seq + 1,
            "player.utterance_text",
            {
                "text": "Hello",
                "scene_id": "foreground-scene",
            },
            "npc-a",
        )
        response = await asyncio.wait_for(
            client.post(
                "/v1/bridge/events:batch",
                json={
                    "session_id": session.id,
                    "events": [event],
                },
            ),
            2,
        )
        assert response.status_code == 200, response.text
        # Stop the independent foreground fixture request before teardown.
        async with session.lock:
            runtime.cancel(session)
            await runtime.stop_session_work(session)
    elif operation == "provider":
        replacement = runtime.settings.model_copy(update={"provider": "disabled"})
        await asyncio.wait_for(runtime.configure_provider(replacement), 2)
    else:
        await asyncio.wait_for(runtime.close_session(session), 2)
    assert stopped.is_set()
    assert not runtime.social_scheduler.workers
    assert not any(c.get("_social") for c in session.commands.values())


async def test_erasure_joins_pending_storage_before_forgetting_scheduler(automatic, monkeypatch):
    _, runtime, session, _ = automatic
    entered, release = threading.Event(), threading.Event()
    original = runtime.social.store.decision

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(runtime.social.store, "decision", blocked)
    await runtime.social_scheduler.tick(session)
    assert await asyncio.to_thread(entered.wait, 1)

    async def erase():
        async with session.lock:
            runtime.cancel(session)
            await runtime.stop_session_work(session, erase=True)
            await runtime.db("reset_actor", session.request.profile_id, "npc-a")

    task = asyncio.create_task(erase())
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 2)
    assert not runtime.social_scheduler.workers
    assert session.id not in runtime.social_scheduler.states
    assert runtime.social.scenes_for_session(session.id) == []


async def test_scheduler_config_is_copied_and_bounded(automatic):
    _, runtime, _, _ = automatic
    original = SocialSchedulerConfig()
    scheduler = SocialScheduler(runtime, original)
    original.max_calls_per_session = 100
    assert scheduler.config.max_calls_per_session == 24
    with pytest.raises(ValidationError):
        SocialSchedulerConfig(max_concurrent_workers=3)
    for pause in (29, 3601):
        with pytest.raises(ValidationError):
            SocialSchedulerConfig(boundary_cooldown_game_seconds=pause)


async def test_scheduler_turn_cap_requires_hearing_for_each_turn(automatic):
    _, runtime, session, _ = automatic
    runtime.social_scheduler.config.max_turns_per_scene = 2
    await tick(automatic)
    await heard(automatic)
    await tick(automatic)
    assert (await tick(automatic))["reason"] == "waiting_for_hearing"
    await heard(automatic)
    after = await tick(automatic)
    assert after["status"] == "completed"
    assert after["reason"] == "turn_limit"
    assert after["queued_turns"] == 2
    assert after["reserved_model_calls"] == 6
    for actor_id in ("npc-a", "npc-b"):
        heard_memories = [
            memory
            for memory in memories(runtime, session, actor_id)
            if memory["provenance"] == "reported"
        ]
        assert len(heard_memories) == 1
    assert memories(runtime, session, "npc-c") == []


async def test_refused_scene_counts_toward_attempt_cap(automatic):
    _, runtime, session, _ = automatic
    runtime.social_scheduler.config.max_scene_attempts = 1
    actor = runtime.repo.get_actor(session.request.profile_id, "npc-a")
    actor["persona"]["social"]["accept_invitations"] = False
    runtime.repo.register_actor(session.request.profile_id, actor)
    await tick(automatic)
    fresh(automatic, 121)
    state = await tick(automatic)
    assert state["reason"] == "scene_attempt_limit"
    assert state["scene_attempts"] == 1
    assert state["reserved_model_calls"] == 4


@pytest.mark.parametrize("deadline", ["worker", "scene"])
async def test_deadline_cancels_and_joins_blocked_model_work(automatic, deadline):
    _, runtime, session, _ = automatic
    scheduler = runtime.social_scheduler
    started, stopped = asyncio.Event(), asyncio.Event()

    class Blocked(FakeSocialProvider):
        async def dialogue(self, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            yield "unreachable"

    runtime.provider.primary = Blocked()
    scheduler.config.worker_timeout_seconds = 1
    await scheduler.tick(session)
    await asyncio.wait_for(started.wait(), 1)
    if deadline == "scene":
        fresh(automatic, scheduler.config.scene_timeout_seconds + 1)
    else:
        await asyncio.wait_for(stopped.wait(), 2)
        worker = scheduler.workers.get(session.id)
        if worker is not None:
            await asyncio.wait_for(asyncio.shield(worker), 1)
    state = await tick(automatic)
    assert stopped.is_set()
    assert state["reason"] == f"{deadline}_time_limit"
    assert state["status"] == "canceled"
    assert not scheduler.workers
    assert not any(command.get("_social") for command in session.commands.values())


async def test_failure_before_scene_creation_cleans_state_and_allows_later_attempt(
    automatic, monkeypatch
):
    _, runtime, session, _ = automatic
    original = runtime.social_call

    async def fail(*args):
        raise RuntimeError("failed before scene creation")

    monkeypatch.setattr(runtime, "social_call", fail)
    first = await tick(automatic)
    state = await tick(automatic)
    assert state["status"] == "canceled"
    assert state["reason"] == "social_operation_failed"
    assert state["scene_id"] is None
    monkeypatch.setattr(runtime, "social_call", original)
    fresh(automatic, 121)
    later = await tick(automatic)
    assert later["status"] == "waiting"
    assert later["scene_id"] != first["last_scene_id"]


async def test_expired_scene_cannot_publish_before_next_maintenance_tick(automatic):
    _, runtime, session, _ = automatic
    scheduler = runtime.social_scheduler
    started, release = asyncio.Event(), asyncio.Event()

    class DelayedTurn(FakeSocialProvider):
        async def dialogue(self, context):
            if context.get("social", {}).get("phase") == "turn":
                started.set()
                await release.wait()
            async for text in super().dialogue(context):
                yield text

    runtime.provider.primary = DelayedTurn()
    await scheduler.tick(session)
    await asyncio.wait_for(started.wait(), 1)
    worker = scheduler.workers[session.id]
    fresh(automatic, scheduler.config.scene_timeout_seconds + 1)
    release.set()
    await asyncio.wait_for(asyncio.shield(worker), 1)
    assert not any(command.get("_social") for command in session.commands.values())
    state = await tick(automatic)
    assert state["reason"] == "scene_time_limit"
    assert state["queued_turns"] == 0


@pytest.mark.parametrize("same_session", [True, False])
async def test_concurrent_ticks_respect_worker_capacity_after_candidate_lookup(
    automatic, monkeypatch, same_session
):
    _, runtime, session, _ = automatic
    scheduler = runtime.social_scheduler
    other = session if same_session else replace(session, id="other", lock=asyncio.Lock())
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.social_scheduler_candidates
    started = []

    async def candidates(current):
        actors = await original(current)
        entered.set()
        await release.wait()
        return actors

    async def work(current, state, participants):
        started.append(current.id)
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "social_scheduler_candidates", candidates)
    monkeypatch.setattr(scheduler, "_work", work)
    first = asyncio.create_task(scheduler.tick(session))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(scheduler.tick(other))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    await asyncio.sleep(0)
    assert len(started) == 1
    assert sum(state.calls for state in scheduler.states.values()) == 4
    workers = list(scheduler.workers.values())
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    scheduler.workers.clear()


@pytest.mark.parametrize(
    ("base", "count", "pause"),
    [(600, 1, 600), (600, 2, 1200), (600, 3, 1800), (600, 20, 1800), (1800, 3, 3600)],
)
async def test_own_boundaries_reduce_frequency_with_a_bounded_game_time_pause(
    automatic, base, count, pause
):
    _, runtime, session, _ = automatic
    scheduler = runtime.social_scheduler
    scheduler.config.boundary_cooldown_game_seconds = base
    actors = {key: session.actors[key] for key in ("npc-a", "npc-b")}
    history = {
        "npc-a": {
            "last_boundary_at": session.game_datetime,
            "recent_boundary_count": count,
            "peers": {},
        }
    }
    start = datetime.fromisoformat(session.game_datetime)
    for elapsed in (0, pause - 0.001):
        assert scheduler._pair(
            _Schedule(), actors, history, (start + timedelta(seconds=elapsed)).isoformat()
        ) is None
    assert scheduler._pair(
        _Schedule(), actors, history, (start + timedelta(seconds=pause)).isoformat()
    ) == ("npc-a", "npc-b")


async def confirmed_contact(fixture, scene_id, *, can_hear=True):
    """Collect a real bridge delivery: npc-b hears npc-c in a separate scene."""
    client, runtime, session, clock = fixture
    response = await client.post(
        "/v1/bridge/social/invite",
        json=envelope(
            session,
            scene_id=scene_id,
            initiator_id="npc-c",
            participants={actor: session.actors[actor]["runtime_instance_id"]
                          for actor in ("npc-c", "npc-b")},
        ),
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/v1/bridge/social/turn",
        json=envelope(session, scene_id=scene_id, actor_id="npc-c"),
    )
    assert response.status_code == 200, response.text
    command = session.commands[response.json()["command_id"]]
    await acknowledge(client, session, command)
    response = await client.post(
        "/v1/bridge/social/delivery",
        json=envelope(
            session, scene_id=scene_id, command_id=command["id"],
            witnesses=[listener(session, clock, can_hear=can_hear)],
        ),
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["heard"]) == ({"npc-b"} if can_hear else set())
    async with session.lock:
        runtime.cancel(session)
        await runtime.stop_session_work(session)
    fresh(fixture, 121)
    session.game_datetime = (
        datetime.fromisoformat(session.game_datetime) + timedelta(seconds=60)
    ).isoformat()


@pytest.mark.parametrize(("scenes", "can_hear"), [(1, True), (2, None), (2, True)])
async def test_repeated_confirmed_contact_changes_partner_choice_without_defeating_fairness(
    automatic, scenes, can_hear
):
    _, runtime, session, _ = automatic
    runtime.settings.dev_social_autonomy = False
    for index in range(scenes):
        await confirmed_contact(automatic, f"prior-contact-{index}", can_hear=can_hear)
    runtime.settings.dev_social_autonomy = True
    first = await tick(automatic)
    summary = runtime.social.get_scene(first["scene_id"])
    repeated = scenes >= 2 and can_hear is True
    assert summary["participant_ids"] == (["npc-b", "npc-c"] if repeated else ["npc-a", "npc-b"])
    assert set(summary["decisions"]) == set(summary["participant_ids"])
    assert all(decision in {"accept", "continue"} for decision in summary["decisions"].values())
    assert first["reserved_model_calls"] == 4
    if repeated:
        # An untried pair still precedes another attempt at the familiar pair.
        async with session.lock:
            runtime.cancel(session)
            await runtime.stop_session_work(session)
        fresh(automatic, 121)
        second = await tick(automatic)
        assert runtime.social.get_scene(second["scene_id"])["participant_ids"] == ["npc-a", "npc-b"]
        assert second["reserved_model_calls"] == 8


async def test_familiar_partner_must_still_explicitly_accept_each_invitation(automatic):
    _, runtime, session, _ = automatic
    runtime.settings.dev_social_autonomy = False
    for index in range(2):
        await confirmed_contact(automatic, f"prior-contact-{index}")
    actor = runtime.repo.get_actor(session.request.profile_id, "npc-c")
    actor["persona"]["social"]["accept_invitations"] = False
    runtime.repo.register_actor(session.request.profile_id, actor)
    runtime.settings.dev_social_autonomy = True
    state = await tick(automatic)
    scene = runtime.social.get_scene(state["last_scene_id"])
    assert scene["participant_ids"] == ["npc-b", "npc-c"]
    assert scene["decisions"] == {"npc-b": "accept", "npc-c": "refuse"}
    assert state["status"] == "refused"
    assert state["queued_turns"] == 0
    assert state["reserved_model_calls"] == 4


@pytest.mark.parametrize("stage", ["candidates", "history"])
@pytest.mark.parametrize(
    "change", ["setting", "capability", "epoch", "time", "binding", "stale", "danger", "foreground"]
)
async def test_storage_wait_revalidates_current_authority_before_reserving_calls(
    automatic, monkeypatch, stage, change
):
    _, runtime, session, clock = automatic
    scheduler = runtime.social_scheduler

    def invalidate():
        if change == "setting":
            runtime.settings.dev_social_autonomy = False
        elif change == "capability":
            session.request.manifest.capability_flags.remove(DEVELOPMENT_SOCIAL_CAPABILITY)
        elif change == "epoch":
            session.epoch += 1
        elif change == "time":
            session.game_datetime = (
                datetime.fromisoformat(session.game_datetime) + timedelta(seconds=1)
            ).isoformat()
        elif change == "binding":
            for actor in session.actors.values():
                actor["runtime_instance_id"] += "-replacement"
        elif change == "stale":
            clock.value += 3
        elif change == "danger":
            session.world["mission_active"] = True
        else:
            session.speech_state = "recording"

    if stage == "candidates":
        original = runtime.social_scheduler_candidates

        async def candidates(current):
            result = await original(current)
            invalidate()
            return result

        monkeypatch.setattr(runtime, "social_scheduler_candidates", candidates)
    else:
        original = runtime.thread_job

        async def storage(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if function == scheduler.adaptation.read:
                invalidate()
            return result

        monkeypatch.setattr(runtime, "thread_job", storage)

    state = await tick(automatic)
    assert state["scene_attempts"] == state["reserved_model_calls"] == 0
    assert not runtime.social.scenes_for_session(session.id)


async def test_history_failure_reserves_no_calls_and_bulk_retry_can_recover(automatic, monkeypatch):
    _, runtime, session, _ = automatic
    scheduler = runtime.social_scheduler
    original = scheduler.adaptation.read
    calls = []

    def failed(*args, **kwargs):
        calls.append(args[4])
        raise OSError("history read unavailable")

    monkeypatch.setattr(scheduler.adaptation, "read", failed)
    first = await tick(automatic)
    assert first["reserved_model_calls"] == first["scene_attempts"] == 0
    assert first["reason"] == "social_history_unavailable"
    assert calls == [("npc-a", "npc-b", "npc-c")]
    monkeypatch.setattr(scheduler.adaptation, "read", original)
    after = await tick(automatic)
    assert after["status"] == "waiting"
    assert after["reserved_model_calls"] == 4


@pytest.mark.parametrize("phase", ["invitation", "participation"])
async def test_invalid_model_decision_keeps_its_reason_without_creating_a_boundary(
    automatic, phase
):
    _, runtime, session, _ = automatic

    class InvalidDecision(FakeSocialProvider):
        async def dialogue(self, context):
            if context.get("social", {}).get("phase") == phase:
                yield "not a valid decision"
                return
            async for text in super().dialogue(context):
                yield text

    runtime.provider.primary = InvalidDecision()
    state = await tick(automatic)
    assert state["reason"] == f"invalid_{phase}_decision"
    assert state["queued_turns"] == 0
    history = runtime.social_scheduler.adaptation.read(
        session.request.profile_id, session.branch_id, session.request.protagonist_id,
        session.game_datetime, tuple(session.actors), session_id=session.id,
    )
    assert all(row["last_boundary_at"] is None for row in history.values())
    assert all(row["recent_boundary_count"] == 0 for row in history.values())
