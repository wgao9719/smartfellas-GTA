"""Session-only history is purged even when the first close cannot write SQLite."""

import asyncio
import sqlite3
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_runtime_outcomes import admin_client

from service.lls.__main__ import fixture_session
from service.lls.app import create_app
from service.lls.protocol import SessionOpen
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.storage import Repository


def seed_history(repo, session):
    profile = session.request.profile_id
    for actor, kind in (
        ("ambient", "ambient_ephemeral"),
        ("pinned", "ambient_ephemeral"),
        ("durable", "curated_persistent"),
    ):
        repo.register_actor(profile, {
            "id": actor, "actor_kind": kind, "session_id": session.id,
        })
        repo.record_event(profile, session.branch_id, {
            "event_id": actor + "-event", "game_datetime": session.game_datetime,
            "protagonist_id": "franklin", "provenance": "directly_observed",
            "observer_ids": [actor], "payload": {"text": "A bicycle conversation."},
        })
        repo.add_memory(
            profile, session.branch_id, actor, "franklin", session.game_datetime,
            "I remember an " + actor + " bicycle.", [actor + "-event"], "directly_observed",
            session_id=session.id, memory_id=actor + "-memory",
        )
    repo.pin_actor(
        profile, "pinned", session.id, display_name="Pinned neighbor",
        pinned_at_game_datetime=session.game_datetime, branch_id=session.branch_id,
    )
    return {actor: repo.get_memory(profile, actor + "-memory") for actor in ("pinned", "durable")}


def assert_cleaned(repo, session, retained):
    profile = session.request.profile_id
    assert repo.get_actor(profile, "ambient") is None
    assert repo.get_memory(profile, "ambient-memory") is None
    assert repo.connection.execute(
        "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'ambient'"
    ).fetchone()[0] == 0
    for actor, memory in retained.items():
        assert repo.get_actor(profile, actor)
        assert repo.get_memory(profile, actor + "-memory") == memory
    assert repo.pending_session_cleanups() == []


@pytest.fixture
async def rig(tmp_path):
    settings = Settings(database_path=str(tmp_path / "cleanup.db"), provider="fake")
    repo = Repository(settings.database_path)
    runtime = Runtime(settings, repo)
    try:
        session = await runtime.open(SessionOpen.model_validate(fixture_session()))
        retained = seed_history(repo, session)
        yield SimpleNamespace(
            settings=settings, repo=repo, runtime=runtime, session=session, retained=retained,
        )
    finally:
        await runtime.shutdown()
        repo.close()


@pytest.mark.parametrize("retry", ["close", "maintenance"])
async def test_failed_session_cleanup_is_retried(rig, monkeypatch, retry):
    runtime, repo, session = rig.runtime, rig.repo, rig.session
    end_session = repo.end_session
    attempts = []

    def fail_once(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return end_session(*args)

    monkeypatch.setattr(repo, "end_session", fail_once)
    with pytest.raises(HTTPException, match="session cleanup pending") as error:
        await runtime.close_session(session)
    assert error.value.status_code == 503
    assert repo.get_actor(session.request.profile_id, "ambient") is not None
    assert session.closed and session.cleanup_pending
    assert repo.pending_session_cleanups() == [{
        "profile_id": session.request.profile_id, "session_id": session.id,
    }]
    with pytest.raises(HTTPException) as denied:
        runtime.authenticate(session.token)
    assert denied.value.status_code == 410
    if retry == "close":
        await runtime.close_session(session)
    else:
        finished = asyncio.Event()
        expire = runtime.expire_sessions

        async def tick():
            await expire()
            finished.set()

        monkeypatch.setattr(runtime, "expire_sessions", tick)
        maintenance = asyncio.create_task(runtime.maintain())
        try:
            await asyncio.wait_for(finished.wait(), 3)
            assert not maintenance.done()
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
    assert_cleaned(repo, session, rig.retained)
    assert not session.cleanup_pending
    assert runtime.cleanup_diagnostics()["status"] == "complete"
    await runtime.close_session(session)
    await runtime.expire_sessions()
    assert len(attempts) == 2


@pytest.mark.parametrize("failure_method", ["begin_session_cleanup", "end_session"])
@pytest.mark.parametrize("fail_at_startup", [False, True])
async def test_cleanup_retries_after_service_restart(
    tmp_path, monkeypatch, failure_method, fail_at_startup,
):
    settings = Settings(database_path=str(tmp_path / "restart.db"), provider="fake")
    repo = Repository(settings.database_path)
    runtime = Runtime(settings, repo)
    session = await runtime.open(SessionOpen.model_validate(fixture_session()))
    retained = seed_history(repo, session)

    def fail(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, failure_method, fail)
    try:
        with pytest.raises(HTTPException, match="session cleanup pending"):
            await runtime.close_session(session)
    finally:
        # Shutdown also fails to purge, but must release the service ownership lease.
        await runtime.shutdown()
        repo.close()
    reopened = Repository(settings.database_path)
    assert reopened.unfinished_sessions()
    assert bool(reopened.pending_session_cleanups()) == (failure_method == "end_session")
    end_session = reopened.end_session
    if fail_at_startup:
        monkeypatch.setattr(reopened, "end_session", fail)
    app = create_app(settings, repository=reopened)
    async with app.router.lifespan_context(app):
        replacement = app.state.runtime
        assert replacement.sessions == {}
        if fail_at_startup:
            diagnostic = replacement.cleanup_diagnostics()
            assert diagnostic["status"] == "pending"
            assert diagnostic["sessions"][0]["error_type"] == "OperationalError"
            async with aclosing(await admin_client(SimpleNamespace(
                runtime=replacement, repo=reopened, settings=settings,
            ))) as admin:
                overview = (await admin.get("/v1/admin/overview")).json()
                assert overview["sessions"] == []
                assert overview["session_cleanup"] == diagnostic
            assert reopened.get_memory(session.request.profile_id, "ambient-memory")
            with pytest.raises(HTTPException, match="session cleanup pending"):
                await replacement.open(SessionOpen.model_validate(fixture_session()))
            monkeypatch.setattr(reopened, "end_session", end_session)
            await replacement.expire_sessions()
        assert_cleaned(reopened, session, retained)
        assert replacement.cleanup_diagnostics()["status"] == "complete"
        fresh = await replacement.open(SessionOpen.model_validate(fixture_session()))
        assert fresh.id != session.id


async def test_repeated_cleanup_failure_does_not_stop_maintenance_or_other_sessions(
    rig, monkeypatch,
):
    runtime, repo, session = rig.runtime, rig.repo, rig.session
    other = await runtime.open(SessionOpen.model_validate(
        {**fixture_session(), "profile_id": "other-profile"}
    ))
    session.last_activity = other.last_activity = runtime.clock() - 10000
    end_session = repo.end_session
    attempts = []
    ticked = asyncio.Event()
    expire = runtime.expire_sessions

    def fail_one(profile, sid):
        if sid == session.id:
            attempts.append(sid)
            raise sqlite3.OperationalError("disk is full")
        return end_session(profile, sid)

    async def tick():
        await expire()
        if len(attempts) >= 2:
            ticked.set()

    monkeypatch.setattr(repo, "end_session", fail_one)
    monkeypatch.setattr(runtime, "expire_sessions", tick)
    maintenance = asyncio.create_task(runtime.maintain())
    try:
        await asyncio.wait_for(ticked.wait(), 4)
        assert not maintenance.done()
        assert session.cleanup_pending
        assert other.closed and not other.cleanup_pending
        assert repo.get_actor(session.request.profile_id, "ambient")
        monkeypatch.setattr(repo, "end_session", end_session)
        await expire()
        assert_cleaned(repo, session, rig.retained)
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)


async def test_pending_session_cannot_be_pruned_or_reopened(rig, monkeypatch):
    runtime, repo, session = rig.runtime, rig.repo, rig.session
    runtime.settings.max_sessions = 1

    def fail(*args):
        raise sqlite3.OperationalError("disk is full")

    with monkeypatch.context() as patch:
        patch.setattr(repo, "end_session", fail)
        with pytest.raises(HTTPException):
            await runtime.close_session(session)
        with pytest.raises(HTTPException, match="session cleanup pending"):
            await runtime.open(SessionOpen.model_validate(fixture_session()))
        for _ in range(3):
            other = await runtime.open(SessionOpen.model_validate(
                {**fixture_session(), "profile_id": "other-profile"}
            ))
            patch.setattr(repo, "end_session", Repository.end_session.__get__(repo))
            await runtime.close_session(other)
            patch.setattr(repo, "end_session", fail)
        assert runtime.sessions[session.id] is session
        assert session.cleanup_pending
    await runtime.expire_sessions()
    assert_cleaned(repo, session, rig.retained)


async def test_unavailable_startup_inventory_blocks_open_until_next_tick(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "inventory.db"), provider="fake")
    repo = Repository(settings.database_path)
    unfinished = repo.unfinished_sessions

    def fail():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "unfinished_sessions", fail)
    app = create_app(settings, repository=repo)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.cleanup_diagnostics()["recovery_error_type"] == "OperationalError"
        with pytest.raises(HTTPException, match="session cleanup pending"):
            await runtime.open(SessionOpen.model_validate(fixture_session()))
        monkeypatch.setattr(repo, "unfinished_sessions", unfinished)
        await runtime.expire_sessions()
        assert runtime.cleanup_diagnostics()["status"] == "complete"
        await runtime.open(SessionOpen.model_validate(fixture_session()))


async def test_canceled_startup_releases_repository_and_service_owner(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "cancel-startup.db"), provider="fake")
    app = create_app(settings)
    entered = asyncio.Event()

    async def blocked_recovery():
        entered.set()
        await asyncio.Event().wait()

    async def start():
        async with app.router.lifespan_context(app):
            pytest.fail("startup was not canceled")

    monkeypatch.setattr(app.state.runtime, "expire_sessions", blocked_recovery)
    startup = asyncio.create_task(start())
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        app.state.repository.unfinished_sessions()
    replacement = create_app(settings)
    async with replacement.router.lifespan_context(replacement):
        assert replacement.state.runtime.cleanup_diagnostics()["status"] == "complete"
