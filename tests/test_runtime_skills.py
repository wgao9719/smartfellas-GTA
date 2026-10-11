"""Authenticated skill generation, bridge trials, retention, and cancellation."""

import asyncio
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from service.lls.__main__ import fixture_event, fixture_role_fields, fixture_session
from service.lls.app import create_app
from service.lls.runtime_skills import DEVELOPMENT_SKILLS_CAPABILITY
from service.lls.settings import Settings
from service.lls.skills import PrimitiveReceipt, SkillInterpreter


class Clock:
    value = 100.0
    wall = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)

    def monotonic(self):
        return self.value

    def utc_now(self):
        return self.wall


@pytest.fixture
async def skill_scene(tmp_path):
    clock = Clock()
    app = create_app(
        Settings(
            database_path=str(tmp_path / "skills.db"),
            provider="fake",
            release=False,
            dev_skills=True,
        ),
        runtime_options={"clock": clock.monotonic, "utc_now": clock.utc_now},
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 11)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            request = fixture_session()
            request["manifest"]["capability_flags"].append(DEVELOPMENT_SKILLS_CAPABILITY)
            opened = (await client.post("/v1/bridge/sessions", json=request)).json()
            client.headers["Authorization"] = "Bearer " + opened["token"]
            runtime = app.state.runtime
            session = runtime.sessions[opened["session_id"]]
            runtime.repo.register_actor(
                session.request.profile_id,
                {
                    "actor_id": "npc",
                    "actor_kind": "curated_persistent",
                    "display_name": "Avery",
                    "control_policy": "ambient",
                    "allowed_actions": ["face_player", "wait_here"],
                },
            )
            events = [
                fixture_event(
                    session.id,
                    1,
                    "world.snapshot",
                    {
                        "online": False,
                        "mission_active": False,
                        "cutscene_active": False,
                        "random_event_active": False,
                        "paused": False,
                        "protagonist_id": "franklin",
                    },
                ),
                fixture_event(
                    session.id,
                    2,
                    "actor.snapshot",
                    {
                        "runtime_handle": 42,
                        "runtime_instance_id": "ped",
                        "ownership": "ambient",
                        "control_policy": "ambient",
                        "exists": True,
                        "alive": True,
                        "distance": 2,
                        "mission_entity": False,
                        "busy": False,
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "name": "Avery",
                        "actor_kind": "curated_persistent",
                        **fixture_role_fields(),
                    },
                    "npc",
                ),
            ]
            response = await client.post(
                "/v1/bridge/events:batch",
                json={
                    "session_id": session.id,
                    "events": events,
                },
            )
            assert response.status_code == 200, response.text
            # The fake provider's first candidate deliberately requires this guard.
            session.actors["npc"]["mod_following"] = True
            runtime.repo.record_event(
                session.request.profile_id,
                session.branch_id,
                {
                    "event_id": "need",
                    "game_datetime": session.game_datetime,
                    "protagonist_id": "franklin",
                    "event_type": "player.utterance_text",
                    "payload": {"text": "Please face me and wait briefly."},
                    "provenance": "heard",
                    "observer_ids": ["npc"],
                },
            )
            yield client, runtime, session, clock


def envelope(session, **fields):
    return {
        "session_id": session.id,
        "branch_id": session.branch_id,
        "runtime_generation": 1,
        "protagonist_id": "franklin",
        "actor_id": "npc",
        "runtime_instance_id": "ped",
        "game_datetime": session.game_datetime,
        "source_event_ids": ["need"],
        **fields,
    }


async def generate(client, session, **fields):
    response = await client.post(
        "/v1/bridge/skills/generate",
        json=envelope(
            session,
            skill_id="courtesy",
            version=1,
            **fields,
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def command_for(session, task, seen=()):
    for _ in range(200):
        commands = [
            c
            for c in session.commands.values()
            if c["kind"] == "action.execute"
            and c["id"] not in seen
            and c["state"] not in {"completed", "canceled"}
        ]
        if commands:
            return commands[-1]
        if task.done():
            response = task.result()
            pytest.fail(f"skill ended before a command: {response.status_code} {response.text}")
        await asyncio.sleep(0.005)
    pytest.fail("skill did not queue a command")


async def ack(client, session, command, status, **fields):
    return await client.post(
        f"/v1/bridge/commands/{command['id']}/ack",
        json={
            "session_id": session.id,
            "runtime_generation": 1,
            "status": status,
            "reason": "task_dispatched" if status == "started" else "observed",
            "observed": {"task_dispatched": True} if status == "started" else None,
            **fields,
        },
    )


def observations(command, **fields):
    return {
        "distance_meters": 0.0,
        "heading_error_degrees": 3.0,
        "elapsed_ms": command["payload"]["arguments"]["duration_seconds"] * 1000,
        "displacement_meters": 0.0,
        **fields,
    }


async def complete(client, session, task):
    commands = []
    for _ in range(2):
        command = await command_for(session, task, [c["id"] for c in commands])
        commands.append(command)
        assert (await ack(client, session, command, "started")).status_code == 200
        result = await ack(client, session, command, "completed", observed=observations(command))
        assert result.status_code == 200, result.text
    result = await asyncio.wait_for(task, 1)
    assert result.status_code == 200, result.text
    return result.json(), commands


def start_trial(client, session, candidate):
    return asyncio.create_task(
        client.post(
            "/v1/bridge/skills/trial",
            json=envelope(
                session,
                candidate_id=candidate["record_id"],
            ),
        )
    )


@pytest.mark.parametrize("setting", ["dev_skills", "dev_neighborhood"])
def test_release_forbids_development_settings(setting):
    with pytest.raises(ValidationError, match="release builds refuse"):
        Settings(**{setting: True})


@pytest.mark.parametrize("gate", ["setting", "capability", "auth"])
async def test_skill_gate(skill_scene, gate):
    client, runtime, session, _ = skill_scene
    if gate == "setting":
        runtime.settings.dev_skills = False
    elif gate == "capability":
        session.request.manifest.capability_flags.remove(DEVELOPMENT_SKILLS_CAPABILITY)
    else:
        client.headers["Authorization"] = "Bearer invalid"
    result = await client.post(
        "/v1/bridge/skills/generate",
        json=envelope(
            session,
            skill_id="courtesy",
            version=1,
        ),
    )
    assert result.status_code == (401 if gate == "auth" else 403)
    assert not runtime.skill_owners


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch_id", "other"),
        ("protagonist_id", "trevor"),
        ("runtime_generation", 2),
        ("runtime_instance_id", "reused"),
        ("actor_id", "missing"),
        ("game_datetime", "2013-09-17T11:00:00"),
        ("source_event_ids", ["missing"]),
    ],
)
async def test_skill_identity_and_sources(skill_scene, field, value):
    client, _, session, _ = skill_scene
    result = await client.post(
        "/v1/bridge/skills/generate",
        json=envelope(
            session,
            skill_id="courtesy",
            version=1,
            **{field: value},
        ),
    )
    assert result.status_code == 409


async def test_complete_authenticated_learning_and_later_execution(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    assert candidate["kind"] == "candidate"
    response, commands = await complete(client, session, start_trial(client, session, candidate))
    trial = response["result"]
    assert trial["result"]["status"] == "completed"
    assert trial["result"]["verification"] == "bridge_acknowledged"
    assert trial["result"]["game_verified"] is False
    assert len(trial["terminal_source_event_ids"]) == 4
    assert commands[0]["payload"]["scene_id"] == commands[1]["payload"]["scene_id"]
    assert commands[0]["payload"]["turn_id"] != commands[1]["payload"]["turn_id"]
    adopted = await client.post(
        "/v1/bridge/skills/adopt",
        json=envelope(
            session,
            trial_id=trial["record_id"],
        ),
    )
    assert adopted.status_code == 200, adopted.text
    task = asyncio.create_task(
        client.post(
            "/v1/bridge/skills/execute",
            json=envelope(
                session,
                skill_id="courtesy",
            ),
        )
    )
    execution, later = await complete(client, session, task)
    assert execution["result"]["status"] == "completed"
    assert execution["result"]["game_verified"] is False
    assert commands[0]["id"] != later[0]["id"]
    assert commands[0]["payload"]["scene_id"] != later[0]["payload"]["scene_id"]
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 8
    diagnostics = await client.get("/v1/diagnostics", params={"session_id": session.id})
    assert diagnostics.status_code == 200
    assert "_skill" not in diagnostics.text


async def test_manual_trial_preflights_late_guard_before_any_bridge_dispatch(skill_scene):
    client, runtime, session, _ = skill_scene
    original = await generate(client, session)
    source = json.loads(original["source"])
    source["version"] = 2
    source["steps"] = [*source["steps"][1:], source["steps"][0]]
    candidate = runtime.skill_store.register_candidate(
        session.skill_run["identity"], session.game_datetime, source, ["need"]
    )
    session.actors["npc"]["mod_following"] = False
    task = start_trial(client, session, candidate)
    try:
        for _ in range(200):
            if task.done() or any(
                command["kind"] == "action.execute" for command in session.commands.values()
            ):
                break
            await asyncio.sleep(0.005)
        assert not any(
            command["kind"] == "action.execute" for command in session.commands.values()
        ), "manual trial dispatched before its failing simulated preflight"
        response = await asyncio.wait_for(task, 1)
        assert response.status_code == 200, response.text
        trial = response.json()["result"]
        assert trial["result"]["status"] == "refused"
        assert trial["result"]["reason"] == "precondition_mod_following"
        assert trial["result"]["verification"] == "simulated"
        assert trial["fingerprint"] == candidate["fingerprint"]
        session.actors["npc"]["mod_following"] = True
        completed, commands = await complete(
            client, session, start_trial(client, session, candidate)
        )
        assert completed["result"]["result"]["verification"] == "bridge_acknowledged"
        assert len(commands) == 2
    finally:
        await runtime.close_session(session)
        await task


@pytest.mark.parametrize("change_after_preflight", [False, True])
async def test_manual_trial_requires_revalidation_after_dependency_change(
    skill_scene, monkeypatch, change_after_preflight
):
    client, runtime, session, _ = skill_scene
    dependency = await generate(client, session)
    identity = session.skill_run["identity"]
    source = json.loads(dependency["source"])
    source["skill_id"] = "composed"
    source["steps"] = [{"op": "call", "skill_id": "courtesy", "version": 1}]
    candidate = runtime.skill_store.register_candidate(
        identity, session.game_datetime, source, ["need"]
    )
    preflight = runtime.preflight_skill
    validated = await preflight(session, candidate["record_id"])
    assert validated["result"]["status"] == "completed"
    changed = json.loads(dependency["source"])
    changed["steps"][1]["proposal"]["arguments"]["duration_seconds"] = 0.25

    def change_dependency():
        # Versions are immutable through the API; emulate a stale dependency on disk.
        with runtime.repo._lock, runtime.repo.connection:
            runtime.repo.connection.execute(
                "UPDATE skill_versions SET source=? WHERE profile_id=? AND skill_id=?",
                (json.dumps(changed), identity.profile_id, "courtesy"),
            )

    if change_after_preflight:

        async def stale_preflight(session, candidate_id):
            result = await preflight(session, candidate_id)
            change_dependency()
            return result

        monkeypatch.setattr(runtime, "preflight_skill", stale_preflight)
    else:
        change_dependency()
    response = await start_trial(client, session, candidate)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "invalid stored state or timeline input"
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())

    monkeypatch.setattr(runtime, "preflight_skill", preflight)
    changed["version"] = 2
    runtime.skill_store.register_candidate(identity, session.game_datetime, changed, ["need"])
    source["version"] = 2
    source["steps"][0]["version"] = 2
    revised = runtime.skill_store.register_candidate(
        identity, session.game_datetime, source, ["need"], revises=candidate["record_id"]
    )
    assert revised["fingerprint"] != validated["fingerprint"]
    completed, commands = await complete(client, session, start_trial(client, session, revised))
    assert completed["result"]["result"]["verification"] == "bridge_acknowledged"
    assert len(commands) == 2
    with runtime.repo._lock:
        _, stamp = runtime.skill_store._scope(identity, session.game_datetime)
        persisted = runtime.skill_store._record(
            runtime.skill_store._owned(
                identity, stamp, session.skill_run["program"]["preflight_id"], {"trial"}
            )
        )
    assert persisted["parent_id"] == revised["record_id"]
    assert persisted["fingerprint"] == revised["fingerprint"]
    assert persisted["result"]["fingerprint"] == revised["fingerprint"]
    assert persisted["result"]["status"] == "completed"
    assert persisted["result"]["verification"] == "simulated"


@pytest.mark.parametrize("mismatch", ["candidate", "record_fingerprint", "result_fingerprint"])
async def test_manual_trial_rejects_mismatched_persisted_preflight(
    skill_scene, monkeypatch, mismatch
):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    identity = session.skill_run["identity"]
    source = json.loads(candidate["source"])
    source["version"] = 2
    other = runtime.skill_store.register_candidate(
        identity, session.game_datetime, source, ["need"]
    )
    preflight = runtime.preflight_skill

    async def mismatched_preflight(session, candidate_id):
        result = await preflight(session, candidate_id)
        with runtime.repo._lock, runtime.repo.connection:
            if mismatch == "result_fingerprint":
                row = runtime.repo.connection.execute(
                    "SELECT data FROM skill_records WHERE record_id=?", (result["record_id"],)
                ).fetchone()
                data = json.loads(row["data"])
                data["result"]["fingerprint"] = other["fingerprint"]
                runtime.repo.connection.execute(
                    "UPDATE skill_records SET data=? WHERE record_id=?",
                    (json.dumps(data), result["record_id"]),
                )
            else:
                column = "parent_id" if mismatch == "candidate" else "fingerprint"
                value = other["record_id"] if mismatch == "candidate" else other["fingerprint"]
                runtime.repo.connection.execute(
                    f"UPDATE skill_records SET {column}=? WHERE record_id=?",
                    (value, result["record_id"]),
                )
        return result

    monkeypatch.setattr(runtime, "preflight_skill", mismatched_preflight)
    response = await start_trial(client, session, candidate)
    assert response.status_code == 409, response.text
    assert "matching passing preflight" in response.text
    assert not any(c["kind"] == "action.execute" for c in session.commands.values())


async def test_refused_trial_revised_code_and_retention(skill_scene):
    client, _, session, _ = skill_scene
    session.actors["npc"]["mod_following"] = False
    original = await generate(client, session)
    refused = await start_trial(client, session, original)
    assert refused.status_code == 200, refused.text
    trial = refused.json()["result"]
    assert trial["result"]["status"] == "refused"
    response = await client.post(
        "/v1/bridge/skills/adopt",
        json=envelope(
            session,
            trial_id=trial["record_id"],
        ),
    )
    assert response.status_code == 409
    response = await client.post(
        "/v1/bridge/skills/generate",
        json=envelope(
            session,
            skill_id="courtesy",
            version=2,
            revises=trial["record_id"],
        ),
    )
    assert response.status_code == 200, response.text
    revised = response.json()["result"]
    assert revised["fingerprint"] != original["fingerprint"]
    success, _ = await complete(client, session, start_trial(client, session, revised))
    assert success["result"]["result"]["status"] == "completed"


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"distance_meters": -1},
        {"distance_meters": "0"},
        {"distance_meters": 0, "heading_error_degrees": 21},
    ],
)
async def test_malformed_receipt_does_not_persist_or_advance(skill_scene, bad):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    assert (await ack(client, session, command, "started")).status_code == 200
    result = await ack(client, session, command, "completed", observed=bad)
    assert result.status_code == 422
    assert command["state"] == "started" and not task.done()
    assert (
        runtime.repo.connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_id=?", (command["id"] + ":completed",)
        ).fetchone()[0]
        == 0
    )
    await runtime.close_session(session)
    assert (await asyncio.wait_for(task, 1)).status_code == 409


async def test_started_required_duplicate_and_distance_budget(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    assert (
        await ack(client, session, command, "completed", observed=observations(command))
    ).status_code == 409
    assert (await ack(client, session, command, "started")).status_code == 200
    assert (await ack(client, session, command, "started")).json()["duplicate"]
    result = await ack(
        client, session, command, "completed", observed=observations(command, distance_meters=0.1)
    )
    assert result.status_code == 200
    result = await asyncio.wait_for(task, 1)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["result"]["reason"] == "primitive_distance_limit_exceeded"
    assert len([c for c in session.commands.values() if c["kind"] == "action.execute"]) == 1
    assert (
        await ack(client, session, command, "completed", observed=observations(command))
    ).json()["duplicate"]
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2


@pytest.mark.parametrize("operation", ["close", "provider", "reset", "control"])
async def test_lifecycle_joins_without_lock_or_ack_deadlock(skill_scene, operation):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    preflights = runtime.repo.connection.execute(
        "SELECT * FROM skill_records WHERE kind='trial'"
    ).fetchall()
    assert len(preflights) == 1
    assert json.loads(preflights[0]["data"])["result"]["verification"] == "simulated"
    if operation == "close":
        await asyncio.wait_for(runtime.close_session(session), 1)
    elif operation == "provider":
        values = runtime.settings.model_dump()
        values["provider"] = "disabled"
        await asyncio.wait_for(runtime.configure_provider(Settings.model_validate(values)), 1)
    elif operation == "control":
        event = fixture_event(
            session.id, 3, "scene.cancel", {"scene_id": command["payload"]["scene_id"]}
        )
        response = await asyncio.wait_for(client.post("/v1/bridge/control", json=event), 1)
        assert response.status_code == 200
    else:
        page = await client.get("/dashboard")
        credentials = json.loads(
            re.search(r"const credentials = (\{[^\n]+\});", page.text).group(1)
        )
        response = await asyncio.wait_for(
            client.post(
                f"/v1/admin/profiles/{session.request.profile_id}/actors/npc/reset",
                json={"confirm_actor_id": "npc"},
                headers={
                    "Authorization": "Bearer " + credentials["token"],
                    "X-CSRF-Token": credentials["csrf"],
                    "Origin": "http://127.0.0.1:8765",
                },
            ),
            1,
        )
        assert response.status_code == 200, response.text
    assert (await asyncio.wait_for(task, 1)).status_code == 409
    assert command["state"] == "canceled"
    assert not runtime.skill_owners
    assert session.skill_task is None
    assert (
        runtime.repo.connection.execute(
            "SELECT * FROM skill_records WHERE kind='trial'"
        ).fetchall()
        == ([] if operation == "reset" else preflights)
    )


async def test_compound_deadline_is_shared_and_late_ack_cannot_complete(skill_scene):
    client, _, session, clock = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    first = await command_for(session, task)
    await ack(client, session, first, "started")
    clock.value += 0.6
    clock.wall += timedelta(seconds=0.6)
    await ack(client, session, first, "completed", observed=observations(first))
    second = await command_for(session, task, [first["id"]])
    assert second["expires_at"] == first["expires_at"]
    await ack(client, session, second, "started")
    clock.value += 2
    response = await ack(client, session, second, "completed", observed=observations(second))
    assert response.status_code == 409
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_ack_time_and_projection_retry_preserve_canonical_event(skill_scene, monkeypatch):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    first = await command_for(session, task)
    session.game_datetime = "2013-09-17T12:01:00"
    assert (await ack(client, session, first, "started")).status_code == 200
    original = runtime.repo.add_memory
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise ValueError("projection unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "add_memory", fail_once)
    session.game_datetime = "2013-09-17T12:02:00"
    response = await ack(client, session, first, "completed", observed=observations(first))
    assert response.status_code == 409
    assert first["state"] == "started" and not task.done()
    session.game_datetime = "2013-09-17T12:03:00"
    response = await ack(client, session, first, "completed", observed=observations(first))
    assert response.status_code == 200, response.text
    second = await command_for(session, task, [first["id"]])
    await ack(client, session, second, "started")
    session.game_datetime = "2013-09-17T12:04:00"
    await ack(client, session, second, "completed", observed=observations(second))
    result = await asyncio.wait_for(task, 1)
    assert result.status_code == 200, result.text
    trial = result.json()["result"]
    assert trial["game_datetime"].startswith("2013-09-17T12:04:00")
    stamps = {
        row["event_id"]: row["game_datetime"]
        for row in runtime.repo.connection.execute(
            "SELECT event_id,game_datetime FROM events WHERE event_id LIKE ?", (first["id"] + ":%",)
        )
    }
    assert stamps[first["id"] + ":started"].startswith("2013-09-17T12:01:00")
    assert stamps[first["id"] + ":completed"].startswith("2013-09-17T12:02:00")
    memory = runtime.repo.connection.execute(
        "SELECT game_datetime FROM memories WHERE memory_id=?", (first["id"] + ":completed:memory",)
    ).fetchone()[0]
    assert memory == stamps[first["id"] + ":completed"]
    outcome = next(
        o for o in session.action_outcomes if o["event_id"] == first["id"] + ":completed"
    )
    assert outcome["game_datetime"] == "2013-09-17T12:02:00"


async def test_ack_projection_cannot_restore_completion_after_interpreter_timeout(
    skill_scene, monkeypatch
):
    client, runtime, session, clock = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    first = await command_for(session, task)
    assert (await ack(client, session, first, "started")).status_code == 200
    entered = threading.Event()
    release = threading.Event()
    original = runtime.repo.add_memory

    def blocked_projection(*args, **kwargs):
        if kwargs.get("memory_id") == first["id"] + ":completed:memory":
            entered.set()
            if not release.wait(5):
                raise ValueError("test did not release projection")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repo, "add_memory", blocked_projection)
    receipt = asyncio.create_task(
        ack(client, session, first, "completed", observed=observations(first))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # The real interpreter timeout must revoke the command even though both
        # injected clocks remain frozen. The canonical observation is committed.
        assert runtime.repo.connection.execute(
            "SELECT 1 FROM events WHERE event_id=?", (first["id"] + ":completed",)
        ).fetchone()
        result = await asyncio.wait_for(asyncio.shield(task), 3)
        assert result.status_code == 200, result.text
        trial = result.json()["result"]
        assert trial["result"]["status"] == "interrupted"
        assert trial["result"]["reason"] == "skill_deadline"
        assert trial["result"]["completed_operations"] == 0
        assert clock.value == 100.0
        assert first["state"] == "canceled"
        assert first["_skill"]["future"].cancelled()
        assert not receipt.done()
    finally:
        release.set()
        response = await asyncio.wait_for(receipt, 1)
    assert response.status_code == 409, response.text
    assert first["state"] == "canceled"
    assert len([c for c in session.commands.values() if c["kind"] == "action.execute"]) == 1
    assert runtime.repo.connection.execute(
        "SELECT 1 FROM memories WHERE memory_id=?", (first["id"] + ":completed:memory",)
    ).fetchone()
    adoption = await client.post(
        "/v1/bridge/skills/adopt", json=envelope(session, trial_id=trial["record_id"])
    )
    assert adoption.status_code == 409
    assert session.skill_task is None
    assert not runtime.skill_owners


async def test_candidate_cannot_persist_after_provider_swallows_cancellation(skill_scene):
    client, runtime, session, _ = skill_scene
    entered = asyncio.Event()
    finished = asyncio.Event()
    original = runtime.provider.primary.skill_candidate

    async def delayed(context, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            finished.set()
            return await original(context, **kwargs)

    runtime.provider.primary.skill_candidate = delayed
    task = asyncio.create_task(
        client.post(
            "/v1/bridge/skills/generate",
            json=envelope(
                session,
                skill_id="courtesy",
                version=1,
            ),
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    values = runtime.settings.model_dump()
    values["provider"] = "disabled"
    await asyncio.wait_for(runtime.configure_provider(Settings.model_validate(values)), 1)
    assert finished.is_set()
    assert (await asyncio.wait_for(task, 1)).status_code == 409
    assert runtime.repo.connection.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


async def test_source_deletion_and_binding_replacement_stop_receipts(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    runtime.repo.delete_event(session.request.profile_id, "need")
    response = await ack(client, session, command, "started")
    assert response.status_code == 409
    await runtime.close_session(session)
    assert (await asyncio.wait_for(task, 1)).status_code == 409
    assert (
        runtime.repo.connection.execute(
            "SELECT COUNT(*) FROM skill_records WHERE kind='trial'"
        ).fetchone()[0]
        == 0
    )


async def test_follow_candidate_cannot_reach_bridge(skill_scene):
    client, runtime, session, _ = skill_scene

    async def following(context, **kwargs):
        return {
            "skill_id": "courtesy",
            "version": 1,
            "action_class": "following",
            "max_duration_seconds": 2,
            "max_distance_meters": 3,
            "steps": [
                {
                    "op": "action",
                    "proposal": {
                        "action_id": "follow_player",
                        "arguments": {"max_follow_meters": 3},
                    },
                }
            ],
        }

    runtime.provider.primary.skill_candidate = following
    candidate = await generate(client, session, action_class="following")
    response = await start_trial(client, session, candidate)
    assert response.status_code == 409
    assert not session.commands


async def test_current_capability_is_checked_before_each_operation(skill_scene):
    client, _, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    first = await command_for(session, task)
    await ack(client, session, first, "started")
    session.actors["npc"]["allowed_actions"] = ["face_player"]
    await ack(client, session, first, "completed", observed=observations(first))
    result = await asyncio.wait_for(task, 1)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["result"]["reason"] == "capability_unavailable"
    assert len([c for c in session.commands.values() if c["kind"] == "action.execute"]) == 1


async def test_selected_retirement_revokes_execution_before_next_operation(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    trial, _ = await complete(client, session, start_trial(client, session, candidate))
    response = await client.post(
        "/v1/bridge/skills/adopt",
        json=envelope(
            session,
            trial_id=trial["result"]["record_id"],
        ),
    )
    assert response.status_code == 200, response.text
    previous = {c["id"] for c in session.commands.values()}
    task = asyncio.create_task(
        client.post(
            "/v1/bridge/skills/execute",
            json=envelope(
                session,
                skill_id="courtesy",
            ),
        )
    )
    command = await command_for(session, task, previous)
    await ack(client, session, command, "started")
    identity = session.skill_run["identity"]
    runtime.skill_store.retire(identity, session.game_datetime, "courtesy", ["need"])
    assert runtime.skill_store.select(identity, session.game_datetime, "courtesy") is None
    response = await ack(client, session, command, "completed", observed=observations(command))
    assert response.status_code == 409
    assert (await asyncio.wait_for(task, 1)).status_code == 409
    assert command["state"] == "canceled"
    assert (
        len(
            [
                c
                for c in session.commands.values()
                if c["kind"] == "action.execute" and c["id"] not in previous
            ]
        )
        == 1
    )


async def test_runtime_adoption_requires_bridge_trial_not_local_simulation(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    identity = session.skill_run["identity"]

    async def simulated(identity, operation_id, proposal, deadline):
        return PrimitiveReceipt(operation_id=operation_id, status="completed", distance_meters=0)

    async def cancel(identity, operation_id):
        pass

    trial = await runtime.skill_store.record_trial(
        candidate["record_id"],
        identity,
        session.game_datetime,
        interpreter=SkillInterpreter(enabled=True, clock=runtime.clock),
        environment=runtime.skill_environment,
        execute=simulated,
        cancel=cancel,
    )
    assert trial["result"]["status"] == "completed"
    assert trial["result"]["verification"] == "simulated"
    response = await client.post(
        "/v1/bridge/skills/adopt",
        json=envelope(
            session,
            trial_id=trial["record_id"],
        ),
    )
    assert response.status_code == 409


async def test_started_receipt_requires_explicit_dispatch_observation(skill_scene):
    client, runtime, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    response = await ack(client, session, command, "started", observed={})
    assert response.status_code == 422
    assert command["state"] == "queued"
    await runtime.close_session(session)
    assert (await asyncio.wait_for(task, 1)).status_code == 409


async def test_failed_receipt_keeps_unknown_distance_and_cannot_adopt(skill_scene):
    client, _, session, _ = skill_scene
    candidate = await generate(client, session)
    task = start_trial(client, session, candidate)
    command = await command_for(session, task)
    response = await ack(client, session, command, "rejected", reason="native_task_refused")
    assert response.status_code == 200, response.text
    result = await asyncio.wait_for(task, 1)
    assert result.status_code == 200, result.text
    trial = result.json()["result"]
    assert trial["result"]["status"] == "failed"
    assert trial["result"]["distance_meters"] is None
    assert trial["result"]["verification"] == "bridge_acknowledged"
    adoption = await client.post(
        "/v1/bridge/skills/adopt",
        json=envelope(
            session,
            trial_id=trial["record_id"],
        ),
    )
    assert adoption.status_code == 409
