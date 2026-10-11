"""Retention database failures must not stop session cleanup and later retries."""

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_session_cleanup import assert_cleaned
from test_session_cleanup import rig as rig


async def test_retention_failure_preserves_cleanup_and_retries(rig, monkeypatch):
    runtime, repo, session = rig.runtime, rig.repo, rig.session
    utc = runtime.utc_now() + timedelta(days=8)
    runtime.utc_now = lambda: utc

    def unavailable(*args):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(repo, "end_session", unavailable)
        with pytest.raises(HTTPException, match="session cleanup pending"):
            await runtime.close_session(session)
    assert session.cleanup_pending

    expire_raw_events = repo.expire_raw_events
    expire_sessions = runtime.expire_sessions
    attempts = []
    cleanup_states = []
    finished = asyncio.Event()

    def fail_once(cutoff):
        attempts.append(cutoff)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return expire_raw_events(cutoff)

    async def tick():
        await expire_sessions()
        cleanup_states.append(session.cleanup_pending)
        if len(attempts) >= 2:
            finished.set()

    monkeypatch.setattr(repo, "expire_raw_events", fail_once)
    monkeypatch.setattr(runtime, "expire_sessions", tick)
    maintenance = asyncio.create_task(runtime.maintain())
    try:
        await asyncio.wait_for(finished.wait(), 4)
        assert not maintenance.done()
        assert len(attempts) == 2
        assert cleanup_states == [False, False]
        assert_cleaned(repo, session, rig.retained)
        assert "A bicycle conversation." not in json.dumps(
            repo.export_profile(session.request.profile_id)
        )
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
