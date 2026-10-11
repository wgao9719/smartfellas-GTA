"""The crash probe follows the same ambient retention policy as normal close."""

import json
import sqlite3
from contextlib import closing

import pytest
from live_bridge_crash_check import verify_recovery, verify_started

from service.lls.app import create_app
from service.lls.settings import Settings
from service.lls.storage import Repository


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "crash.db"
    checkpoint = {"profile_id": "probe", "session_id": "old-session", "command_id": "action"}
    stamp = "2013-09-17T12:00:00Z"
    with Repository(path) as repo:
        branch = repo.start_session("probe", "old-session", stamp)["branch_id"]
        repo.register_actor("probe", {
            "id": "probe-npc", "actor_kind": "ambient_ephemeral", "session_id": "old-session",
        })
        repo.bind_instance("probe", "probe-npc", "old-session", 42, 1)
        repo.record_event("probe", branch, {
            "event_id": "action:started", "event_type": "action.started",
            "game_datetime": stamp, "protagonist_id": "franklin", "provenance": "system_fact",
            "observer_ids": ["probe-npc"],
            "payload": {"action_id": "face_player", "observed": {"task_dispatched": True}},
        })
        repo.add_memory(
            "probe", branch, "probe-npc", "franklin", stamp,
            "The game reported my request as started. This does not confirm completion.",
            ["action:started"], "system_fact", session_id="old-session",
            memory_id="action:started:memory",
        )
    return path, checkpoint


@pytest.mark.parametrize("close", ["normal", "startup"])
async def test_crash_check_accepts_session_only_memory_cleanup(history, close):
    path, checkpoint = history
    with closing(sqlite3.connect(path)) as connection:
        started = verify_started(connection, checkpoint)
    if close == "normal":
        with Repository(path) as repo:
            repo.end_session(checkpoint["profile_id"], checkpoint["session_id"])
    else:
        app = create_app(Settings(database_path=str(path), provider="fake"))
        async with app.router.lifespan_context(app):
            pass
    with closing(sqlite3.connect(path)) as connection:
        verify_recovery(connection, checkpoint, started)
    with Repository(path) as repo:
        exported = repo.export_profile(checkpoint["profile_id"])
        assert exported["events"] == [started]
        assert exported["memories"] == []


def test_crash_check_rejects_retained_ambient_history(history):
    path, checkpoint = history
    with closing(sqlite3.connect(path)) as connection:
        started = verify_started(connection, checkpoint)
        with pytest.raises(AssertionError, match="Session-only action memory"):
            verify_recovery(connection, checkpoint, started)


@pytest.mark.parametrize("change", ["delete", "alter", "complete"])
def test_crash_check_rejects_lost_changed_or_invented_receipts(history, change):
    path, checkpoint = history
    with closing(sqlite3.connect(path)) as connection:
        started = verify_started(connection, checkpoint)
    with Repository(path) as repo:
        repo.end_session(checkpoint["profile_id"], checkpoint["session_id"])
        with repo.connection:
            if change == "delete":
                repo.connection.execute("DELETE FROM events")
            elif change == "alter":
                altered = {**started, "event_type": "action.completed"}
                repo.connection.execute("UPDATE events SET data=?", (json.dumps(altered),))
            else:
                completed = {**started, "event_id": "action:completed",
                             "event_type": "action.completed"}
                branch = repo.connection.execute("SELECT branch_id FROM branches").fetchone()[0]
                repo.record_event(checkpoint["profile_id"], branch, completed)
    with closing(sqlite3.connect(path)) as connection:
        with pytest.raises(AssertionError):
            verify_recovery(connection, checkpoint, started)
