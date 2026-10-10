"""Service authorization outcomes survive retries without inventing native evidence."""

import asyncio
import json
import re
import threading
from contextlib import aclosing
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from service.lls.__main__ import fixture_session
from service.lls.actions import POLICY_VERSION
from service.lls.admin import create_admin_router
from service.lls.protocol import Ack, SessionOpen
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.storage import Repository

T1 = "2013-09-17T12:01:00.000000+00:00"
T2 = "2013-09-17T12:02:00.000000+00:00"
PROFILE = "fake-demo"
ACTOR = "outcome-actor"
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
async def rig(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "outcomes.db"), provider="fake", max_commands=8
    )
    repo = Repository(settings.database_path)
    runtime = Runtime(settings, repo)
    session = await runtime.open(SessionOpen.model_validate(fixture_session()))
    for actor_id in (ACTOR, "other-actor"):
        repo.register_actor(PROFILE, {"id": actor_id, "display_name": actor_id})
        session.actors[actor_id] = {"runtime_instance_id": actor_id + "-instance"}
    yield SimpleNamespace(runtime=runtime, repo=repo, session=session, settings=settings)
    await runtime.shutdown()
    repo.close()


def action(rig):
    return rig.runtime.command(
        rig.session,
        "action.execute",
        ACTOR,
        {
            "action_id": "wait_here",
            "arguments": {"duration_seconds": 1},
            "policy_version": POLICY_VERSION,
            "scene_id": "outcome-scene",
            "turn_id": "outcome-turn",
            "consent": False,
        },
    )


def rows(repo, event_id):
    return repo.connection.execute(
        "SELECT branch_id,data FROM events WHERE profile_id=? AND event_id=?",
        (PROFILE, event_id),
    ).fetchall()


def ack(rig, status):
    return Ack(
        session_id=rig.session.id,
        runtime_generation=1,
        status=status,
        reason="adapter observation",
        observed={"task_dispatched": True},
    )


async def admin_client(rig):
    app = FastAPI()
    app.include_router(create_admin_router(rig.settings, rig.runtime, rig.repo))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url=ORIGIN
    )
    page = await client.get("/dashboard")
    credentials = json.loads(re.search(r"const credentials = (\{[^\n]+\});", page.text)[1])
    client.headers.update(
        {
            "Authorization": "Bearer " + credentials["token"],
            "X-CSRF-Token": credentials["csrf"],
            "Origin": ORIGIN,
        }
    )
    return client


@pytest.mark.parametrize("status", ["expired", "canceled"])
async def test_close_persists_private_service_fact_at_terminal_game_time(rig, status):
    command = action(rig)
    rig.session.game_datetime = T1
    if status == "expired":
        command["expires_at"] = (rig.runtime.utc_now() - timedelta(seconds=1)).isoformat()
        rig.runtime.expire_commands(rig.session)
    else:
        rig.runtime.cancel(rig.session)
    event_id = command["id"] + ":service:" + status
    await rig.runtime.close_session(rig.session)
    assert not rig.session.outcome_pending
    reopened = Repository(rig.settings.database_path)
    try:
        stored = rows(reopened, event_id)
        assert len(stored) == 1
        event = json.loads(stored[0]["data"])
        assert event["game_datetime"] == T1
        assert event["observer_ids"] == [ACTOR]
        assert event["payload"]["conversation_turn_id"] == "outcome-turn"
        assert event["payload"]["authority"] == "service"
        assert event["payload"]["native_completion_confirmed"] is False
        assert event["payload"]["native_withdrawal_confirmed"] is False
        memory = reopened.get_memory(PROFILE, event_id + ":memory")
        assert memory["actor_id"] == ACTOR and memory["protagonist_id"] == "player:franklin"
        assert memory["source_event_ids"] == [event_id]
        assert "remain unconfirmed" in memory["text"]
        assert not reopened.retrieve_memories(
            PROFILE, rig.session.branch_id, "other-actor", "franklin", T2
        )
        assert not reopened.retrieve_memories(
            PROFILE, rig.session.branch_id, ACTOR, "michael", T2
        )
    finally:
        reopened.close()


async def test_projection_retry_pins_identity_time_payload_and_deduplicates(rig, monkeypatch):
    command = action(rig)
    rig.session.game_datetime = T1
    rig.runtime.cancel(rig.session)
    event_id = command["id"] + ":service:canceled"
    original = rig.repo.add_memory

    def inserted_then_failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost projection response")

    monkeypatch.setattr(rig.repo, "add_memory", inserted_then_failed)
    with pytest.raises(HTTPException) as failure:
        await rig.runtime.flush_outcomes(rig.session)
    assert failure.value.status_code == 503
    branch = rig.session.branch_id
    rig.session.game_datetime = T2
    rig.session.branch_id = "not-the-original-branch"
    rig.session.request.protagonist_id = "michael"
    command["actor_id"] = "other-actor"
    command["payload"]["action_id"] = "face_player"
    command["payload"]["turn_id"] = "replacement-turn"
    try:
        rig.runtime.record_outcome(rig.session, command, "canceled", "changed reason")
        monkeypatch.setattr(rig.repo, "add_memory", original)
        await rig.runtime.flush_outcomes(rig.session)
        await rig.runtime.flush_outcomes(rig.session)
        stored = rows(rig.repo, event_id)
        assert len(stored) == 1 and stored[0]["branch_id"] == branch
        event = json.loads(stored[0]["data"])
        assert event["game_datetime"] == T1 and event["protagonist_id"] == "player:franklin"
        assert event["observer_ids"] == [ACTOR]
        assert event["payload"]["action_id"] == "wait_here"
        assert event["payload"]["conversation_turn_id"] == "outcome-turn"
        assert event["payload"]["reason"] == "scene canceled"
        assert rig.repo.get_memory(PROFILE, event_id + ":memory")["game_datetime"] == T1
        assert not rig.session.outcome_pending
    finally:
        rig.session.branch_id = branch
        rig.session.request.protagonist_id = "franklin"


async def test_pending_outcomes_bound_queue_and_prevent_eviction(rig, monkeypatch):
    commands = [action(rig) for _ in range(rig.settings.max_commands)]
    rig.runtime.cancel(rig.session)
    assert len(rig.session.outcome_pending) == rig.settings.max_commands
    for _ in range(3):
        rig.runtime.cancel(rig.session)
        with pytest.raises(HTTPException) as failure:
            action(rig)
        assert failure.value.status_code == 503
    assert list(rig.session.commands) == [c["id"] for c in commands]
    original = rig.repo.record_event
    monkeypatch.setattr(rig.repo, "record_event", lambda *args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(HTTPException):
        await rig.runtime.flush_outcomes(rig.session)
    assert len(rig.session.outcome_pending) == rig.settings.max_commands
    monkeypatch.setattr(rig.repo, "record_event", original)
    await rig.runtime.flush_outcomes(rig.session)
    action(rig)
    assert len(rig.session.commands) == rig.settings.max_commands
    assert not rig.session.outcome_pending


async def test_canceled_flush_joins_native_thread_and_retries_safely(rig, monkeypatch):
    command = action(rig)
    rig.runtime.cancel(rig.session)
    entered, release = threading.Event(), threading.Event()
    original = rig.repo.add_memory

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(rig.repo, "add_memory", blocked)
    task = asyncio.create_task(rig.runtime.flush_outcomes(rig.session))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert command["id"] in rig.session.outcome_pending
        await rig.runtime.flush_outcomes(rig.session)
        assert not rig.session.outcome_pending
        assert len(rows(rig.repo, command["id"] + ":service:canceled")) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mutation", ["reset", "delete_profile"])
async def test_erasure_joins_projection_then_prevents_retry_resurrection(
    rig, monkeypatch, mutation
):
    command = action(rig)
    rig.runtime.cancel(rig.session)
    event_id = command["id"] + ":service:canceled"
    entered, release = threading.Event(), threading.Event()
    original = rig.repo.add_memory

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(rig.repo, "add_memory", blocked)
    flush = asyncio.create_task(rig.runtime.flush_outcomes(rig.session))
    client = await admin_client(rig)
    reset = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        request = (
            client.post(
                f"/v1/admin/profiles/{PROFILE}/actors/{ACTOR}/reset",
                json={"confirm_actor_id": ACTOR},
            )
            if mutation == "reset" else client.request(
                "DELETE", f"/v1/admin/profiles/{PROFILE}", json={"confirm_profile_id": PROFILE}
            )
        )
        reset = asyncio.create_task(request)
        await asyncio.sleep(0.02)
        assert not reset.done()
        release.set()
        await flush
        response = await reset
        assert response.status_code == 200, response.text
        assert rig.repo.get_memory(PROFILE, event_id + ":memory") is None
        rig.runtime.record_outcome(rig.session, command, "canceled", "retry")
        await rig.runtime.flush_outcomes(rig.session)
        assert not rig.session.outcome_pending
        assert rig.repo.get_memory(PROFILE, event_id + ":memory") is None
    finally:
        release.set()
        await asyncio.gather(flush, *([reset] if reset else []), return_exceptions=True)
        await client.aclose()


async def test_failed_projection_blocks_admin_reset_then_retry_succeeds(rig, monkeypatch):
    previous = action(rig)
    rig.runtime.cancel(rig.session)
    await rig.runtime.flush_outcomes(rig.session)
    old_memory_id = previous["id"] + ":service:canceled:memory"
    command = action(rig)
    original = rig.repo.add_memory
    monkeypatch.setattr(rig.repo, "add_memory", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    async with aclosing(await admin_client(rig)) as client:
        response = await client.post(
            f"/v1/admin/profiles/{PROFILE}/actors/{ACTOR}/reset",
            json={"confirm_actor_id": ACTOR},
        )
        assert response.status_code == 503
        assert rig.repo.get_memory(PROFILE, old_memory_id) is not None
        assert command["id"] in rig.session.outcome_pending
        monkeypatch.setattr(rig.repo, "add_memory", original)
        response = await client.post(
            f"/v1/admin/profiles/{PROFILE}/actors/{ACTOR}/reset",
            json={"confirm_actor_id": ACTOR},
        )
        assert response.status_code == 200, response.text
        assert not rig.session.outcome_pending
        assert rig.repo.get_memory(PROFILE, old_memory_id) is None
        assert rig.repo.get_memory(PROFILE, command["id"] + ":service:canceled:memory") is None


async def test_unrelated_admin_memory_edit_flushes_pending_outcomes(rig):
    previous = action(rig)
    rig.runtime.cancel(rig.session)
    await rig.runtime.flush_outcomes(rig.session)
    old_memory_id = previous["id"] + ":service:canceled:memory"
    command = action(rig)
    async with aclosing(await admin_client(rig)) as client:
        response = await client.patch(
            f"/v1/admin/profiles/{PROFILE}/memories/{old_memory_id}",
            json={"text": "An edited memory."},
        )
        assert response.status_code == 200, response.text
    assert not rig.session.outcome_pending
    assert rig.repo.get_memory(PROFILE, command["id"] + ":service:canceled:memory") is not None


@pytest.mark.parametrize("status", ["canceled", "expired", "completed"])
async def test_service_cancel_during_ack_keeps_distinct_evidence_without_publication(
    rig, monkeypatch, status
):
    command = action(rig)
    if status == "completed":
        await rig.runtime.acknowledge(rig.session, command["id"], ack(rig, "started"))
    entered, release = threading.Event(), threading.Event()
    original = rig.repo.record_event

    def blocked(profile, branch, event):
        if event["event_id"] == command["id"] + ":" + status:
            entered.set()
            assert release.wait(3)
        return original(profile, branch, event)

    monkeypatch.setattr(rig.repo, "record_event", blocked)
    task = asyncio.create_task(
        rig.runtime.acknowledge(rig.session, command["id"], ack(rig, status))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        rig.runtime.cancel(rig.session)
        await rig.runtime.flush_outcomes(rig.session)
        release.set()
        result = await task
        assert result["status"] == "canceled" and command["state"] == "canceled"
        adapter_event = json.loads(rows(rig.repo, command["id"] + ":" + status)[0]["data"])
        service_event = json.loads(rows(rig.repo, command["id"] + ":service:canceled")[0]["data"])
        assert adapter_event["payload"]["reason"] == "adapter observation"
        assert service_event["payload"]["authority"] == "service"
        assert service_event["payload"]["native_completion_confirmed"] is False
        assert not rig.session.outcome_pending
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_skill_revocation_persists_authorization_without_learning_receipts(rig):
    command = action(rig)
    future = asyncio.get_running_loop().create_future()
    state = {
        "commands": {"op": {"command": command, "future": future}},
        "cleanup_requested": True,
    }
    rig.runtime.revoke_skill_commands(rig.session, state)
    await rig.runtime.flush_outcomes(rig.session)
    event = json.loads(rows(rig.repo, command["id"] + ":service:canceled")[0]["data"])
    assert future.cancelled()
    assert event["payload"]["reason"] == "skill authorization withdrawn"
    assert "observed" not in event["payload"]
    assert rig.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


async def test_maintenance_persists_expiry_without_a_poll_or_ack(rig, monkeypatch):
    command = action(rig)
    command["expires_at"] = (rig.runtime.utc_now() - timedelta(seconds=1)).isoformat()
    persisted = asyncio.Event()
    original = rig.runtime.flush_outcomes

    async def observed_flush(session):
        assert session.lock.locked()
        await original(session)
        persisted.set()

    monkeypatch.setattr(rig.runtime, "flush_outcomes", observed_flush)
    task = asyncio.create_task(rig.runtime.maintain())
    try:
        await asyncio.wait_for(persisted.wait(), 2)
        assert command["state"] == "expired"
        assert rig.repo.get_memory(PROFILE, command["id"] + ":service:expired:memory")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
