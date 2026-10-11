"""Exercise development skill learning and retained execution over real loopback HTTP."""

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from live_cleanup import temporary_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.__main__ import (  # noqa: E402
    fixture_event,
    fixture_role_fields,
    fixture_session,
)


@contextmanager
def server(directory, *, autonomous=False):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with (directory / "service.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "tests/bridge_fixture_server.py",
                "--skills",
                *(["--skill-autonomy"] if autonomous else []),
                "--port",
                str(port),
                "--database",
                str(directory / "skills.sqlite3"),
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=endpoint, timeout=0.5, trust_env=False) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Skill fixture exited: " + log.read()[-4000:])
                    try:
                        if client.get("/health").json()["provider"] == "fake":
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Skill fixture did not become ready")
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


async def exercise(endpoint, *, retained=False):
    async with httpx.AsyncClient(base_url=endpoint, timeout=10, trust_env=False) as client:

        async def post(path, body):
            response = await client.post(path, json=body)
            if response.is_error:
                raise RuntimeError(f"{path}: {response.status_code}: {response.text}")
            return response.json()

        request = fixture_session()
        request["profile_id"] = "skill-probe"
        request["manifest"]["capability_flags"].append("development:skills:v1")
        opened = await post("/v1/bridge/sessions", request)
        sid = opened["session_id"]
        client.headers["Authorization"] = "Bearer " + opened["token"]
        sequence = 0

        async def refresh():
            nonlocal sequence
            sequence += 1
            events = [
                fixture_event(
                    sid,
                    sequence,
                    "world.snapshot",
                    {
                        "online": False,
                        "mission_active": False,
                        "cutscene_active": False,
                        "random_event_active": False,
                        "paused": False,
                        "protagonist_id": "franklin",
                    },
                )
            ]
            sequence += 1
            events.append(
                fixture_event(
                    sid,
                    sequence,
                    "actor.snapshot",
                    {
                        "runtime_handle": 42,
                        "runtime_instance_id": "skill-ped",
                        "ownership": "ambient",
                        "control_policy": "ambient",
                        "exists": True,
                        "alive": True,
                        "distance": 2,
                        "mission_entity": False,
                        "busy": False,
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "name": "Skill Fixture",
                        "actor_kind": "curated_persistent",
                        **fixture_role_fields(),
                    },
                    "skill-npc",
                )
            )
            await post("/v1/bridge/events:batch", {"session_id": sid, "events": events})

        def body(**fields):
            return {
                "session_id": sid,
                "branch_id": opened["branch_id"],
                "runtime_generation": 1,
                "protagonist_id": "franklin",
                "actor_id": "skill-npc",
                "runtime_instance_id": "skill-ped",
                "game_datetime": request["game_datetime"],
                "source_event_ids": ["skill-need"],
                **fields,
            }

        await refresh()
        if not retained:
            candidate = (
                await post(
                    "/v1/bridge/skills/generate",
                    body(
                        skill_id="courtesy",
                        version=1,
                    ),
                )
            )["result"]
            refusal = (
                await post(
                    "/v1/bridge/skills/trial",
                    body(
                        candidate_id=candidate["record_id"],
                    ),
                )
            )["result"]
            assert refusal["result"]["status"] == "refused", refusal
            rejected = await client.post(
                "/v1/bridge/skills/adopt",
                json=body(
                    trial_id=refusal["record_id"],
                ),
            )
            assert rejected.status_code == 409, rejected.text
            revised = (
                await post(
                    "/v1/bridge/skills/generate",
                    body(
                        skill_id="courtesy",
                        version=2,
                        revises=refusal["record_id"],
                    ),
                )
            )["result"]
            assert revised["fingerprint"] != candidate["fingerprint"]
            operation, fields = "trial", {"candidate_id": revised["record_id"]}
        else:
            operation, fields = "execute", {"skill_id": "courtesy"}
        await refresh()
        task = asyncio.create_task(post("/v1/bridge/skills/" + operation, body(**fields)))
        commands = []
        try:
            for index in range(2):
                deadline = time.monotonic() + 3
                command = None
                while time.monotonic() < deadline:
                    response = await client.get("/v1/bridge/commands", params={"wait_seconds": 0})
                    response.raise_for_status()
                    command = next(
                        (
                            item
                            for item in response.json()["commands"]
                            if item["kind"] == "action.execute"
                            and item["id"] not in {prior["id"] for prior in commands}
                        ),
                        None,
                    )
                    if command is not None:
                        break
                    if task.done():
                        raise RuntimeError(f"Skill ended before command: {task.result()}")
                    await asyncio.sleep(0.01)
                assert command is not None
                assert command["payload"]["action_id"] == (
                    "face_player" if index == 0 else "wait_here"
                )
                commands.append(command)
                assert not task.done(), "Skill completed without its terminal ACK"
                path = f"/v1/bridge/commands/{command['id']}/ack"
                base = {"session_id": sid, "runtime_generation": 1}
                evidence = {
                    "distance_meters": 0.0,
                    "heading_error_degrees": 3.0,
                    "elapsed_ms": command["payload"]["arguments"]["duration_seconds"] * 1000,
                    "displacement_meters": 0.0,
                }
                if index == 0:
                    response = await client.post(
                        path,
                        json={
                            **base,
                            "status": "completed",
                            "observed": evidence,
                        },
                    )
                    assert response.status_code == 409, response.text
                await post(
                    path,
                    {
                        **base,
                        "status": "started",
                        "reason": "task_dispatched",
                        "observed": {"task_dispatched": True},
                    },
                )
                if index == 0:
                    response = await client.post(
                        path,
                        json={
                            **base,
                            "status": "completed",
                            "observed": {},
                        },
                    )
                    assert response.status_code == 422, response.text
                    assert not task.done()
                diagnostics = await client.get("/v1/diagnostics")
                diagnostics.raise_for_status()
                assert diagnostics.json()["skills"]["worker_active"]
                assert "_skill" not in diagnostics.text
                await post(path, {**base, "status": "completed", "observed": evidence})
            result = await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
                await asyncio.gather(task, return_exceptions=True)
        outcome = result["result"] if retained else result["result"]["result"]
        assert outcome["status"] == "completed", result
        assert outcome["verification"] == "bridge_acknowledged", result
        assert outcome["game_verified"] is False
        assert result["native_cleanup_confirmed"] is False
        assert commands[0]["payload"]["scene_id"] == commands[1]["payload"]["scene_id"]
        if not retained:
            assert len(result["result"]["terminal_source_event_ids"]) == 4
            adopted = await post(
                "/v1/bridge/skills/adopt",
                body(
                    trial_id=result["result"]["record_id"],
                ),
            )
            assert adopted["result"]["version"] == 2
        await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
        page = await client.get("/dashboard")
        credentials = json.loads(re.search(r"const credentials = (\{[^\n]+\});", page.text)[1])
        client.headers["Authorization"] = "Bearer " + credentials["token"]
        memories = (await client.get("/v1/admin/profiles/skill-probe/memories")).json()["memories"]
        assert len(memories) == (8 if retained else 4), memories
        usage = (await client.get("/v1/admin/provider")).json()["usage"]
        assert usage["foreground"]["requests"] == usage["background"]["requests"] == 0
        return [command["id"] for command in commands]


def main():
    with temporary_directory(prefix="lls-skill-probe-") as folder:
        directory = Path(folder)
        with server(directory) as endpoint:
            trial_commands = asyncio.run(exercise(endpoint))
        with server(directory) as endpoint:
            retained_commands = asyncio.run(exercise(endpoint, retained=True))
        assert set(trial_commands).isdisjoint(retained_commands)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "mode": "simulated_skill_http",
                    "candidate_versions": 2,
                    "refused_trial_rejected": True,
                    "acknowledged_trial": True,
                    "retained_execution_after_restart": True,
                    "primitive_commands": 4,
                    "canonical_action_memories": 8,
                    "invalid_ack_rejected": True,
                    "model_requests": 0,
                    "native_calls": 0,
                    "game_verified": False,
                    "native_verified": False,
                }
            )
        )


if __name__ == "__main__":
    main()
