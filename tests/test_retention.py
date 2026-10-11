"""Wall-clock retention removes raw event history without erasing durable facts."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_bridge import client as client
from test_bridge import open_ready

from service.lls import storage
from service.lls.perception import DeliveredUtterance
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.speech_coordinator import SpeechIdentity, SpeechReceipt
from service.lls.storage import Repository

GAME_TIME = "2013-09-17T12:00:00Z"


async def maintenance_pass(runtime, monkeypatch):
    finished = asyncio.Event()
    original = runtime.expire_sessions

    async def observed_expire_sessions():
        await original()
        finished.set()

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "expire_sessions", observed_expire_sessions)
        task = asyncio.create_task(runtime.maintain())
        try:
            await asyncio.wait_for(finished.wait(), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_maintenance_expires_raw_turn_text_but_preserves_durable_facts(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "retention.sqlite3"))
    with Repository(settings.database_path) as repo:
        repo.register_actor("profile", {"actor_id": "avery", "display_name": "Avery"})
        branch = repo.open_timeline("profile", GAME_TIME)
        repo.record_event(
            "profile",
            branch,
            {
                "event_id": "conversation",
                "event_type": "conversation_completed",
                "game_datetime": GAME_TIME,
                "protagonist_id": "franklin",
                "observer_ids": ["avery"],
                "provenance": "directly_observed",
                "confidence": 1,
                "payload": {"text": "A private raw transcript from last week."},
            },
        )
        saved = repo.add_memory(
            "profile", branch, "avery", "franklin", GAME_TIME,
            "I met Franklin.", ["conversation"], "directly_observed", importance=1,
        )
        repo.apply_relationship_event("profile", branch, "avery", "franklin", "conversation")
        before = repo.get_relationship("profile", branch, "avery", "franklin", GAME_TIME)
        utc = datetime.now(timezone.utc) + timedelta(days=8)
        runtime = Runtime(settings, repo, utc_now=lambda: utc)
        try:
            await maintenance_pass(runtime, monkeypatch)
            exported = repo.export_profile("profile")
            assert "A private raw transcript from last week." not in json.dumps(exported)
            assert repo.get_memory("profile", saved["memory_id"])["text"] == "I met Franklin."
            assert repo.get_relationship(
                "profile", branch, "avery", "franklin", GAME_TIME
            ) == before
            assert repo.retrieve_memories(
                "profile", branch, "avery", "franklin", GAME_TIME, "Franklin"
            )[0]["memory_id"] == saved["memory_id"]
        finally:
            await runtime.shutdown()


async def test_retention_uses_configured_ingestion_age_and_clock_cadence(tmp_path, monkeypatch):
    utc = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monotonic = 0
    monkeypatch.setattr(storage, "_now", lambda: utc.isoformat())
    settings = Settings(
        database_path=str(tmp_path / "clock.sqlite3"), raw_event_retention_days=2,
    )
    with Repository(settings.database_path) as repo:
        repo.register_actor("profile", {"actor_id": "avery"})
        branch = repo.open_timeline("profile", GAME_TIME)

        def record(event_id):
            repo.record_event(
                "profile", branch,
                {
                    "event_id": event_id,
                    "event_type": "action.completed",
                    "game_datetime": GAME_TIME,
                    "protagonist_id": "franklin",
                    "observer_ids": ["avery"],
                    "provenance": "system_fact",
                    "confidence": 1,
                    "payload": {"observed": {"text": event_id + " private turn details"}},
                },
            )

        record("old")
        runtime = Runtime(settings, repo, utc_now=lambda: utc, clock=lambda: monotonic)
        try:
            utc += timedelta(days=1)
            assert await runtime.expire_raw_history() == 0
            utc += timedelta(days=2)
            record("recent")
            monotonic = 59
            assert await runtime.expire_raw_history() == 0
            assert "old private turn details" in json.dumps(repo.export_profile("profile"))
            monotonic = 60
            assert await runtime.expire_raw_history() == 1
            exported = json.dumps(repo.export_profile("profile"))
            assert "old private turn details" not in exported
            assert "recent private turn details" in exported
            monotonic = 120
            assert await runtime.expire_raw_history() == 0
            audit = repo.connection.execute("SELECT * FROM scrub_audit").fetchall()
            assert len(audit) == 1
            assert "private turn details" not in json.dumps([dict(row) for row in audit])
        finally:
            await runtime.shutdown()


def test_retention_window_defaults_and_environment(monkeypatch):
    assert Settings().raw_event_retention_days == 7
    monkeypatch.setenv("LLS_DATABASE", ":memory:")
    monkeypatch.setenv("LLS_RAW_EVENT_RETENTION_DAYS", "3")
    assert Settings.from_env().raw_event_retention_days == 3


@pytest.mark.parametrize("value", ["0", "-1", "366", "none", "1.5"])
def test_retention_window_rejects_invalid_policy(monkeypatch, value):
    monkeypatch.setenv("LLS_DATABASE", ":memory:")
    monkeypatch.setenv("LLS_RAW_EVENT_RETENTION_DAYS", value)
    with pytest.raises(ValueError):
        Settings.from_env()


async def test_retention_scrubs_old_session_caches_without_closing_active_session(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    utc = runtime.utc_now()
    runtime.utc_now = lambda: utc

    def command(text):
        return runtime.command(
            session, "dialogue.chunk", "npc-1",
            {
                "text": text, "speaker": "Neighbor", "scene_id": "scene",
                "turn_id": text, "chunk_index": 0, "final": False,
            },
        )

    old = command("Old raw reply")
    old["state"] = "completed"
    session.player_speech["old"] = {"text": "Old raw transcript", "accepted_at": utc}
    session.delivered_utterances["old"] = DeliveredUtterance(
        event_id="old", actor_id="npc-1", runtime_instance_id="ped-1",
        text="Old heard words", sampled_at=utc,
    )
    identity = SpeechIdentity(sid, 1, "scene", "npc-1", 0, "ped-1")
    runtime.speech._receipts.append(SpeechReceipt(
        identity, "transcript", "completed", 1, text="Old microphone transcript", created_at=utc,
    ))
    utc += timedelta(days=8)
    recent = command("Recent live reply")
    session.player_speech["recent"] = {"text": "Recent transcript", "accepted_at": utc}
    session.delivered_utterances["recent"] = DeliveredUtterance(
        event_id="recent", actor_id="npc-1", runtime_instance_id="ped-1",
        text="Recent heard words", sampled_at=utc,
    )
    recent_receipt = SpeechReceipt(
        identity, "playback", "completed", 1, text="Recent playback",
        delivered_text="Recent playback", created_at=utc,
    )
    runtime.speech._receipts.append(recent_receipt)
    await runtime.expire_raw_history()
    assert old["id"] not in session.commands
    assert list(session.player_speech) == ["recent"]
    assert list(session.delivered_utterances) == ["recent"]
    assert recent["id"] in session.commands and recent["state"] == "queued"
    assert runtime.speech.receipts == (recent_receipt,)
    assert not session.closed
