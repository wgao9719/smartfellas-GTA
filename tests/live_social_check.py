"""Exercise simulated NPC social scenes over real loopback HTTP, without GTA."""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.__main__ import (  # noqa: E402
    fixture_event,
    fixture_role_fields,
    fixture_session,
)


def exercise(endpoint, *, autonomous=False):
    with httpx.Client(base_url=endpoint, timeout=10, trust_env=False) as client:
        request = fixture_session()
        request["profile_id"] = "social-probe"
        request["manifest"]["capability_flags"].append("development:social:v1")
        response = client.post("/v1/bridge/sessions", json=request)
        response.raise_for_status()
        session = response.json()
        sid = session["session_id"]
        client.headers["Authorization"] = "Bearer " + session["token"]
        sequence = 0
        scene_id = "social-probe-scene"
        actors = {}
        for index in range(3):
            actors[f"social-npc-{index}"] = {
                "runtime_handle": 40 + index,
                "runtime_instance_id": f"social-ped-{index}",
                "ownership": "ambient",
                "control_policy": "ambient",
                "exists": True,
                "alive": True,
                "distance": 2 + index,
                "mission_entity": False,
                "busy": False,
                "position": {"x": float(index), "y": 0.0, "z": 0.0},
                "name": f"Social Fixture {index}",
                "actor_kind": "curated_persistent",
                **fixture_role_fields(),
                "area_id": "fixture-block",
                "interior_id": "street",
            }

        def refresh():
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
            for actor_id, snapshot in actors.items():
                sequence += 1
                events.append(fixture_event(sid, sequence, "actor.snapshot", snapshot, actor_id))
            response = client.post(
                "/v1/bridge/events:batch", json={"session_id": sid, "events": events}
            )
            response.raise_for_status()

        def body(**fields):
            return {
                "session_id": sid,
                "runtime_generation": 1,
                "protagonist_id": "franklin",
                "scene_id": scene_id,
                "game_datetime": request["game_datetime"],
                **fields,
            }

        def post(path, value):
            response = client.post(path, json=value)
            if response.is_error:
                raise RuntimeError(f"{path}: {response.status_code}: {response.text}")
            return response.json()

        refresh()
        participants = set(sorted(actors)[:2]) if autonomous else set(actors)
        if not autonomous:
            invited = post(
                "/v1/bridge/social/invite",
                body(
                    initiator_id="social-npc-0",
                    participants={
                        key: value["runtime_instance_id"] for key, value in actors.items()
                    },
                ),
            )
            assert invited["status"] == "active", invited
            assert all(value == "accept" for value in invited["decisions"].values()), invited
        delivered = []
        seen_commands = set()
        for turn_index in range(2 if autonomous else 6):
            refresh()
            if autonomous:
                deadline = time.monotonic() + 8
                command = None
                while time.monotonic() < deadline:
                    refresh()
                    response = client.get("/v1/bridge/commands", params={"wait_seconds": 0})
                    response.raise_for_status()
                    commands = response.json()["commands"]
                    assert not any(item["kind"] == "action.execute" for item in commands)
                    command = next(
                        (
                            item
                            for item in commands
                            if item["kind"] == "dialogue.chunk" and item["id"] not in seen_commands
                        ),
                        None,
                    )
                    if command:
                        break
                    time.sleep(0.1)
                if command is None:
                    state = client.get("/v1/diagnostics").json()
                    raise AssertionError(
                        f"Automatic turn did not arrive: {state['social_scheduler']}"
                    )
                seen_commands.add(command["id"])
                scene_id = command["payload"]["scene_id"]
                actor_id = command["actor_id"]
                assert actor_id in participants
            else:
                actor_id = f"social-npc-{turn_index % 3}"
                generated = post("/v1/bridge/social/turn", body(actor_id=actor_id))
                response = client.get("/v1/bridge/commands", params={"wait_seconds": 0})
                response.raise_for_status()
                commands = response.json()["commands"]
                assert not any(item["kind"] == "action.execute" for item in commands)
                command = next(item for item in commands if item["id"] == generated["command_id"])
            quote = command["payload"]["text"]
            post(
                f"/v1/bridge/commands/{command['id']}/ack",
                {
                    "session_id": sid,
                    "runtime_generation": 1,
                    "status": "completed",
                    "observed": {"delivery": "simulated", "text": quote},
                },
            )
            if autonomous and turn_index == 0:
                # Cross a maintenance tick while withholding hearing evidence.
                # Subtitle delivery alone must not generate the next social turn.
                time.sleep(1.2)
                refresh()
                state = client.get("/v1/diagnostics").json()["social_scheduler"]
                assert state["status"] == "waiting" and state["queued_turns"] == 1, state
            witnesses = [
                {
                    "actor_id": listener,
                    "runtime_instance_id": snapshot["runtime_instance_id"],
                    "sampled_at": datetime.now(timezone.utc).isoformat(),
                    "position": snapshot["position"],
                    "area_id": snapshot["area_id"],
                    "interior_id": snapshot["interior_id"],
                    "exists": True,
                    "alive": True,
                    "can_hear": True,
                    "confidence": 0.9,
                }
                for listener, snapshot in actors.items()
                if listener in participants and listener != actor_id
            ]
            receipt = post(
                "/v1/bridge/social/delivery", body(command_id=command["id"], witnesses=witnesses)
            )
            assert set(receipt["heard"]) == participants - {actor_id}, receipt
            delivered.append({"actor_id": actor_id, "text": quote})
        if autonomous:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                refresh()
                state = client.get("/v1/diagnostics").json()["social_scheduler"]
                if state["status"] == "completed":
                    break
                time.sleep(0.1)
            assert state["reason"] == "turn_limit" and state["queued_turns"] == 2, state
        else:
            refused = client.post("/v1/bridge/social/turn", json=body(actor_id="social-npc-0"))
            assert refused.status_code == 409
        post(
            f"/v1/bridge/sessions/{sid}/close",
            {"runtime_generation": 1},
        )
        page = client.get("/dashboard")
        page.raise_for_status()
        credentials = json.loads(
            re.search(r"const credentials = (\{[^\n]+\});", page.text).group(1)
        )
        client.headers["Authorization"] = "Bearer " + credentials["token"]
        history = client.get("/v1/admin/profiles/social-probe/memories")
        history.raise_for_status()
        memories = history.json()["memories"]
        reported = [item for item in memories if item["provenance"] == "reported"]
        assert len(reported) == (2 if autonomous else 12)
        assert len({entry["text"] for entry in delivered}) == len(participants)
        if autonomous:
            assert all(item["actor_id"] in participants for item in memories)
        usage = client.get("/v1/admin/provider").json()["usage"]
        assert usage["foreground"]["requests"] == usage["background"]["requests"] == 0
        return {
            "status": "passed",
            "mode": "simulated_autonomous_social_http" if autonomous else "simulated_social_http",
            "participants": len(participants),
            "delivered_turns": len(delivered),
            "reported_memories": len(reported),
            "distinct_opinions": len(participants),
            "automatic_initiation_and_turns": autonomous,
            "hearing_evidence": "explicit_simulated_bridge_samples",
            "turn_limit_enforced": True,
            "model_requests": 0,
            "game_verified": False,
            "native_verified": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autonomous", action="store_true")
    args = parser.parse_args()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("LLS_", "LLM_"))
    }
    with tempfile.TemporaryDirectory(prefix="lls-social-probe-") as directory:
        with (Path(directory) / "service.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tests/bridge_fixture_server.py",
                    "--social",
                    *(["--social-autonomy"] if args.autonomous else []),
                    "--port",
                    str(port),
                    "--database",
                    str(Path(directory) / "fixture.sqlite3"),
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
                            raise RuntimeError("Fixture exited: " + log.read()[-4000:])
                        try:
                            if client.get("/health").json()["provider"] == "fake":
                                break
                        except httpx.TransportError:
                            time.sleep(0.05)
                    else:
                        raise RuntimeError("Social fixture did not become ready")
                print(json.dumps(exercise(endpoint, autonomous=args.autonomous)))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == "__main__":
    main()
