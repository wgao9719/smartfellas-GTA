"""Verify virtual neighborhood routes over loopback HTTP and a service restart."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.__main__ import fixture_event, fixture_session  # noqa: E402
from service.lls.neighborhood import demo_pack  # noqa: E402
from service.lls.neighborhood_bridge import DEVELOPMENT_NEIGHBORHOOD_CAPABILITY  # noqa: E402

START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=3)


@contextmanager
def server(directory, pack):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {
        "PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1",
        "LLS_PROVIDER": "disabled", "LLS_RELEASE": "0", "LLS_CLOUD_OPT_IN": "0",
        "LLS_DEV_NEIGHBORHOOD": "1", "LLS_NEIGHBORHOOD_PACK": str(pack),
        "LLS_DATABASE": str(directory / "neighborhood.sqlite3"), "LLS_PORT": str(port),
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with (directory / "service.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "service.lls"], cwd=ROOT, env=environment,
            stdout=log, stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False,
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Neighborhood service exited: " + log.read()[-4000:])
                    try:
                        health = client.get("/health", timeout=0.5)
                        if health.status_code == 200 and health.json()["provider"] == "disabled":
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Neighborhood service did not become ready")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def post(client, path, body):
    response = client.post(path, json=body)
    if response.is_error:
        raise RuntimeError(f"{path}: {response.status_code}: {response.text}")
    return response.json()


def open_session(client, at, parent=None):
    request = fixture_session()
    request.update(profile_id="neighborhood-probe", game_datetime=at.isoformat())
    request["capabilities"] = []
    request["manifest"]["capability_flags"].append(DEVELOPMENT_NEIGHBORHOOD_CAPABILITY)
    if parent:
        request["parent_branch_id"] = parent
    opened = post(client, "/v1/bridge/sessions", request)
    client.headers["Authorization"] = "Bearer " + opened["token"]
    return opened


def world(client, session, at, seq):
    event = fixture_event(session["session_id"], seq, "world.snapshot", {
        "online": False, "mission_active": False, "cutscene_active": False,
        "random_event_active": False, "paused": False, "protagonist_id": "franklin",
    })
    event["game_datetime"] = at.isoformat()
    post(client, "/v1/bridge/events:batch", {
        "session_id": session["session_id"], "events": [event],
    })


def body(session, at):
    return {
        "session_id": session["session_id"], "runtime_generation": 1,
        "protagonist_id": "franklin", "game_datetime": at.isoformat(),
        "neighborhood_id": "authored_demo",
    }


def main():
    with tempfile.TemporaryDirectory(prefix="lls-neighborhood-http-") as temporary:
        directory = Path(temporary)
        pack = directory / "authored.json"
        pack.write_text(demo_pack(48).model_dump_json(), encoding="utf-8")
        with server(directory, pack) as client:
            opened = open_session(client, START)
            world(client, opened, START, 1)
            initial = post(client, "/v1/bridge/neighborhood/initialize", body(opened, START))
            world(client, opened, END, 2)
            before = post(client, "/v1/bridge/neighborhood/snapshot", body(opened, END))
            assert before["state_digest"] == initial["state_digest"]
            events = 0
            for index in range(100):
                world(client, opened, END, index + 3)
                result = post(client, "/v1/bridge/neighborhood/advance", {
                    **body(opened, END), "budget": {"max_days": 3, "background_events": 256},
                })
                assert result["events_written"] <= 256
                events += result["events_written"]
                if result["caught_up"]:
                    break
                assert result["events_written"] > 0
            else:
                raise AssertionError("Neighborhood did not catch up within the bounded probe")
            assert len(result["actors"]) == 48
            commands = client.get("/v1/bridge/commands", params={"wait_seconds": 0}).json()
            assert commands["commands"] == []
            post(client, f"/v1/bridge/sessions/{opened['session_id']}/close", {
                "runtime_generation": 1,
            })
        with server(directory, pack) as client:
            reopened = open_session(client, END, parent=opened["branch_id"])
            restarted = post(client, "/v1/bridge/neighborhood/snapshot", body(reopened, END))
            assert restarted["state_digest"] == result["state_digest"]
            assert restarted["config_hash"] == result["config_hash"]
            assert restarted["caught_up"] and restarted["backlog_actors"] == 0
        print(json.dumps({
            "status": "passed", "mode": "simulated_neighborhood_http",
            "actors": 48, "game_days": 3, "advancement_calls": index + 1,
            "events_written": events, "backlog_actors": 0,
            "restart_equivalent": True, "automatic_advancement": False,
            "model_requests": 0, "native_operations": 0,
            "game_verified": False, "native_verified": False,
        }))


if __name__ == "__main__":
    main()
