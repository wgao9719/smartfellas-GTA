"""Real HTTP practice cycle driven only by ordinary utterances and action receipts."""

import asyncio
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx
from live_skill_check import server

from service.lls.__main__ import fixture_event, fixture_role_fields, fixture_session


async def exercise(endpoint, *, retained=False):
    async with httpx.AsyncClient(base_url=endpoint, timeout=10, trust_env=False) as client:
        async def post(path, body):
            assert "/skills/" not in path, "The probe must never drive a skill route"
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        request = fixture_session()
        request["profile_id"] = "skill-probe"
        request["manifest"]["capability_flags"].append("development:skills:v1")
        opened = await post("/v1/bridge/sessions", request)
        sid = opened["session_id"]
        client.headers["Authorization"] = "Bearer " + opened["token"]
        sequence = 0

        async def event(kind, payload, actor=None):
            nonlocal sequence
            sequence += 1
            data = fixture_event(sid, sequence, kind, payload, actor)
            data["event_id"] = f"{sid}-{sequence}"
            await post("/v1/bridge/events:batch", {"session_id": sid, "events": [data]})

        async def refresh():
            await event("world.snapshot", {
                "online": False, "mission_active": False, "cutscene_active": False,
                "random_event_active": False, "paused": False, "protagonist_id": "franklin",
            })
            await event("actor.snapshot", {
                "runtime_handle": 42, "runtime_instance_id": "skill-ped",
                "ownership": "ambient", "control_policy": "ambient", "exists": True,
                "alive": True, "distance": 2, "mission_entity": False, "busy": False,
                "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "name": "Skill Fixture",
                "actor_kind": "curated_persistent", **fixture_role_fields(),
            }, "skill-npc")

        await refresh()
        await event("player.utterance_text", {
            "text": "Please look at me.", "scene_id": "ordinary-scene",
        }, "skill-npc")
        handled, external, practice = set(), [], []
        deadline = time.monotonic() + 12
        refreshed = time.monotonic()
        terminal = "completed" if retained else "adopted"
        last_state = None
        while time.monotonic() < deadline:
            if time.monotonic() - refreshed >= 0.4:
                await refresh()
                refreshed = time.monotonic()
            response = await client.get("/v1/bridge/commands", params={"wait_seconds": 0})
            response.raise_for_status()
            for command in response.json()["commands"]:
                if command["id"] in handled:
                    continue
                handled.add(command["id"])
                path = f"/v1/bridge/commands/{command['id']}/ack"
                base = {"session_id": sid, "runtime_generation": 1}
                if command["kind"] == "dialogue.chunk":
                    await post(path, {**base, "status": "completed", "observed": {
                        "text": command["payload"]["text"], "delivery": "simulated",
                    }})
                elif command["kind"] == "action.execute":
                    ordinary = command["payload"]["scene_id"] == "ordinary-scene"
                    (external if ordinary else practice).append(command)
                    await post(path, {**base, "status": "started", "reason": "task_dispatched",
                                      "observed": {"task_dispatched": True}})
                    duration = command["payload"]["arguments"].get("duration_seconds", 0)
                    failed = ordinary and not retained
                    evidence = {
                        "distance_meters": 0.0, "heading_error_degrees": 45.0 if failed else 3.0,
                        "elapsed_ms": duration * 1000, "displacement_meters": 0.0,
                    }
                    await post(path, {**base, "status": "failed" if failed else "completed",
                                      "reason": "orientation_not_reached" if failed else "",
                                      "observed": evidence})
                    duplicate = await post(path, {**base, "status": "completed",
                                                   "observed": evidence})
                    assert duplicate["duplicate"]
            diagnostics = await client.get("/v1/diagnostics")
            diagnostics.raise_for_status()
            last_state = diagnostics.json()["skills"]["scheduler"]
            if last_state["status"] == terminal and not last_state["worker_active"]:
                break
            await asyncio.sleep(0.06)
        else:
            raise AssertionError(f"automatic cycle did not finish: {last_state}")
        assert len(external) == 1 and len(practice) == 2
        assert [command["payload"]["action_id"] for command in practice] == [
            "face_player", "wait_here",
        ]
        assert last_state["episode_attempts"] == 1
        # Time and repeated snapshots alone must never produce another episode.
        for _ in range(4):
            await refresh()
            await asyncio.sleep(0.35)
        final = (await client.get("/v1/diagnostics")).json()["skills"]["scheduler"]
        assert final["episode_attempts"] == 1 and final["queued_episodes"] == 0
        assert not final["worker_active"]
        await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
        return [command["id"] for command in practice]


def main():
    with tempfile.TemporaryDirectory(prefix="lls-skill-autonomy-") as folder:
        directory = Path(folder)
        with server(directory, autonomous=True) as endpoint:
            trial = asyncio.run(exercise(endpoint))
        with server(directory, autonomous=True) as endpoint:
            retained = asyncio.run(exercise(endpoint, retained=True))
        assert set(trial).isdisjoint(retained)
        # Close explicitly: Windows cannot delete an open database with the temp folder.
        with closing(sqlite3.connect(directory / "skills.sqlite3")) as connection:
            versions = connection.execute(
                "SELECT version FROM skill_records WHERE kind='candidate' ORDER BY version"
            ).fetchall()
            assert versions == [(1,), (2,)], versions
            assert connection.execute(
                "SELECT COUNT(*) FROM skill_records WHERE kind='adopt'"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM skill_records WHERE actor_id!='skill-npc'"
            ).fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM skill_experiences").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM skill_episodes").fetchone()[0] == 2
        print(json.dumps({
            "status": "passed", "mode": "automatic_skill_practice_http",
            "external_actions": 2, "candidate_versions": 2, "practice_primitives": 4,
            "retained_practice_after_restart": True, "direct_skill_route_requests": 0,
            "duplicate_receipts_suppressed": True, "time_alone_does_not_create_needs": True,
            "model_requests": 0, "native_calls": 0, "game_verified": False,
        }))


if __name__ == "__main__":
    main()
