"""Verify changing social topics over real HTTP and service process restarts.

All hearing evidence and provider output are explicit simulations. This probe
never opens GTA, audio devices, a model server, or a paid model endpoint.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.__main__ import (  # noqa: E402
    fixture_event,
    fixture_role_fields,
    fixture_session,
)


@contextmanager
def server(directory):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    environment = {"PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with (directory / "service.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "tests/bridge_fixture_server.py", "--social", "--port", str(port),
             "--database", str(directory / "social.sqlite3")],
            cwd=ROOT, env=environment, stdout=log, stderr=log,
        )
        try:
            with httpx.Client(base_url=endpoint, timeout=0.5, trust_env=False) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Social fixture exited: " + log.read()[-4000:])
                    try:
                        if client.get("/health").json()["provider"] == "fake":
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Social fixture did not become ready")
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def exercise(endpoint, phase, *, speaker, listener=None, expected, erase=False):
    with httpx.Client(base_url=endpoint, timeout=10, trust_env=False) as client:
        def post(path, body):
            response = client.post(path, json=body)
            if response.is_error:
                raise RuntimeError(f"{path}: {response.status_code}: {response.text}")
            return response.json()

        request = fixture_session()
        request["profile_id"] = "social-probe"
        request["game_datetime"] = (
            datetime(2013, 9, 17, 12, tzinfo=timezone.utc) + timedelta(minutes=phase)
        ).isoformat()
        request["manifest"]["capability_flags"].append("development:social:v1")
        opened = post("/v1/bridge/sessions", request)
        sid = opened["session_id"]
        client.headers["Authorization"] = "Bearer " + opened["token"]
        sequence = 0
        actors = {
            f"social-npc-{index}": {
                "runtime_handle": 40 + index, "runtime_instance_id": f"social-ped-{index}",
                "ownership": "ambient", "control_policy": "ambient",
                "exists": True, "alive": True, "distance": 2 + index,
                "mission_entity": False, "busy": False,
                "position": {"x": float(index), "y": 0.0, "z": 0.0},
                "name": f"Social Fixture {index}", "actor_kind": "curated_persistent",
                "area_id": "fixture-block", "interior_id": "street", **fixture_role_fields(),
            } for index in range(3)
        }

        def event(kind, payload, actor_id=None):
            nonlocal sequence
            sequence += 1
            value = fixture_event(sid, sequence, kind, payload, actor_id)
            value["event_id"] = "adaptation-probe-" + uuid4().hex
            value["game_datetime"] = request["game_datetime"]
            return value

        def refresh():
            post("/v1/bridge/events:batch", {
                "session_id": sid,
                "events": [event("world.snapshot", {
                    "online": False, "mission_active": False, "cutscene_active": False,
                    "random_event_active": False, "paused": False, "protagonist_id": "franklin",
                }), *[
                    event("actor.snapshot", snapshot, actor) for actor, snapshot in actors.items()
                ]],
            })

        def body(**fields):
            return {
                "session_id": sid, "runtime_generation": 1, "protagonist_id": "franklin",
                "scene_id": f"adaptation-{phase}", "game_datetime": request["game_datetime"],
                **fields,
            }

        refresh()
        invited = post("/v1/bridge/social/invite", body(
            initiator_id=speaker,
            participants={actor: value["runtime_instance_id"] for actor, value in actors.items()},
        ))
        assert invited["status"] == "active", invited
        generated = post("/v1/bridge/social/turn", body(actor_id=speaker))
        response = client.get("/v1/bridge/commands", params={"wait_seconds": 0})
        response.raise_for_status()
        commands = response.json()["commands"]
        assert not any(item["kind"] == "action.execute" for item in commands)
        command = next(item for item in commands if item["id"] == generated["command_id"])
        quote = command["payload"]["text"]
        assert quote == expected, quote
        post(f"/v1/bridge/commands/{command['id']}/ack", {
            "session_id": sid, "runtime_generation": 1, "status": "completed",
            "observed": {"delivery": "simulated", "text": quote},
        })
        refresh()
        witness_actor = listener or "social-npc-0"
        state = actors[witness_actor]
        witnesses = [{
                "actor_id": witness_actor, "runtime_instance_id": state["runtime_instance_id"],
                "sampled_at": datetime.now(timezone.utc).isoformat(), "position": state["position"],
                "area_id": state["area_id"], "interior_id": state["interior_id"],
                "exists": True, "alive": True, "can_hear": bool(listener), "confidence": 0.9,
            }]
        heard = post(
            "/v1/bridge/social/delivery", body(command_id=command["id"], witnesses=witnesses)
        )
        assert set(heard["heard"]) == ({listener} if listener else set())
        post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
        page = client.get("/dashboard")
        page.raise_for_status()
        credentials = json.loads(
            re.search(r"const credentials = (\{[^\n]+\});", page.text).group(1)
        )
        client.headers["Authorization"] = "Bearer " + credentials["token"]
        client.headers["Origin"] = endpoint
        client.headers["X-CSRF-Token"] = credentials["csrf"]
        response = client.get("/v1/admin/profiles/social-probe/memories")
        response.raise_for_status()
        reported = [row for row in response.json()["memories"] if row["provenance"] == "reported"]
        assert all(row["actor_id"] == "social-npc-1" for row in reported)
        assert len(reported) == (0 if phase == 3 else min(phase + 1, 2))
        if erase:
            for row in reported:
                response = client.request(
                    "DELETE", "/v1/admin/profiles/social-probe/memories/" + row["memory_id"],
                    json={"confirm_memory_id": row["memory_id"]},
                )
                response.raise_for_status()
                assert response.json()["deleted"]
        usage = client.get("/v1/admin/provider").json()["usage"]
        assert usage["foreground"]["requests"] == usage["background"]["requests"] == 0
        return quote


def main():
    with tempfile.TemporaryDirectory(prefix="lls-social-adaptation-") as temporary:
        directory = Path(temporary)
        for phase in range(2):
            with server(directory) as endpoint:
                exercise(endpoint, phase, speaker="social-npc-0", listener="social-npc-1",
                         expected="I enjoy quiet streets.")
        with server(directory) as endpoint:
            exercise(endpoint, 2, speaker="social-npc-1", expected=(
                "social-npc-0, you said: I enjoy quiet streets. What do you think now?"
            ), erase=True)
        # No direct database edits: the HTTP memory erasure must affect the next process.
        with server(directory) as endpoint:
            exercise(endpoint, 3, speaker="social-npc-1", expected="I prefer the busy boardwalk.")
    print(json.dumps({
        "status": "passed", "mode": "simulated_social_adaptation_http",
        "confirmed_encounters": 2, "service_processes": 4,
        "topic_changed_after_hearing": True, "memory_erasure_removed_topic": True,
        "private_direction_preserved": True, "model_requests": 0,
        "native_verified": False, "game_verified": False,
    }))


if __name__ == "__main__":
    main()
