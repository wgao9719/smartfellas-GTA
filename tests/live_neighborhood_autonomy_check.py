"""Verify automatic virtual catch-up through real HTTP, without advance requests."""

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import httpx
from live_cleanup import temporary_directory
from live_neighborhood_check import ROOT, START, open_session, post, world

from service.lls.neighborhood import demo_pack


@contextmanager
def server(directory, pack):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
        "LLS_PROVIDER": "disabled",
        "LLS_RELEASE": "0",
        "LLS_CLOUD_OPT_IN": "0",
        "LLS_DEV_NEIGHBORHOOD": "1",
        "LLS_DEV_NEIGHBORHOOD_AUTONOMY": "1",
        "LLS_NEIGHBORHOOD_PACK": str(pack),
        "LLS_DATABASE": str(directory / "automatic.sqlite3"),
        "LLS_PORT": str(port),
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with (directory / "service.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "service.lls"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                trust_env=False,
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Automatic service exited: " + log.read()[-4000:])
                    try:
                        if client.get("/health", timeout=0.5).json()["provider"] == "disabled":
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Automatic service did not become ready")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def request(session, at):
    return {
        "session_id": session["session_id"],
        "runtime_generation": 1,
        "protagonist_id": "franklin",
        "game_datetime": at.isoformat(),
        "neighborhood_id": "authored_demo",
    }


def main():
    end = START + timedelta(hours=7)
    with temporary_directory(prefix="lls-neighborhood-auto-") as folder:
        directory = Path(folder)
        pack = directory / "authored.json"
        pack.write_text(demo_pack(30).model_dump_json(), encoding="utf-8")
        with server(directory, pack) as client:
            session = open_session(client, START)
            world(client, session, START, 1)
            # An entire maintenance interval must not invent an authored baseline.
            time.sleep(1.2)
            before = post(client, "/v1/bridge/neighborhood/snapshot", request(session, START))
            assert before["configured"] is False
            post(client, "/v1/bridge/neighborhood/initialize", request(session, START))
            sequence = 2
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                world(client, session, end, sequence)
                sequence += 1
                result = post(client, "/v1/bridge/neighborhood/snapshot", request(session, end))
                diagnostic = client.get("/v1/diagnostics").json()["neighborhood"]["scheduler"]
                assert diagnostic["automatic_initialization"] is False
                assert not client.get("/v1/bridge/commands", params={"wait_seconds": 0}).json()[
                    "commands"
                ]
                if result["caught_up"]:
                    break
                time.sleep(0.4)
            else:
                raise RuntimeError("Automatic catch-up did not finish: " + json.dumps(diagnostic))
            assert result["automatic_advancement"] is True
            assert result["backlog_actors"] == 0
            assert 0 < diagnostic["confirmed_events_written"] <= diagnostic["reserved_events"]
            assert diagnostic["attempts"] <= 8
            assert result["native_verified"] is result["game_verified"] is False
            digest = result["state_digest"]
            post(
                client,
                f"/v1/bridge/sessions/{session['session_id']}/close",
                {"runtime_generation": 1},
            )
        with server(directory, pack) as client:
            session = open_session(client, end)
            world(client, session, end, 1)
            restarted = post(client, "/v1/bridge/neighborhood/snapshot", request(session, end))
            assert restarted["state_digest"] == digest
            assert restarted["caught_up"] and restarted["configured"]
        print(
            json.dumps(
                {
                    "status": "passed",
                    "mode": "simulated_automatic_neighborhood_http",
                    "actors": 30,
                    "game_hours": 7,
                    "manual_advance_requests": 0,
                    "automatic_attempts": diagnostic["attempts"],
                    "confirmed_events_written": diagnostic["confirmed_events_written"],
                    "explicit_initialization_required": True,
                    "backlog_actors": 0,
                    "restart_state_preserved": True,
                    "model_requests": 0,
                    "native_operations": 0,
                    "game_verified": False,
                    "native_verified": False,
                }
            )
        )


if __name__ == "__main__":
    main()
