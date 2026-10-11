"""Kill an owned temporary HTTP service during a fake C# game action, then restart it."""

import json
import os
import queue
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from live_cleanup import temporary_directory

ROOT = Path(__file__).resolve().parents[1]


def verify_started(connection, checkpoint):
    row = connection.execute(
        "SELECT data FROM events WHERE profile_id=? AND event_id=?",
        (checkpoint["profile_id"], checkpoint["command_id"] + ":started"),
    ).fetchone()
    assert row is not None, "Started ACK was not durable before process death"
    started = json.loads(row[0])
    assert started["event_type"] == "action.started"
    assert started["payload"]["observed"]["task_dispatched"] is True
    memory = connection.execute(
        "SELECT m.text,m.session_id,a.actor_kind,a.session_id FROM memories m "
        "JOIN actors a USING(profile_id,actor_id) WHERE m.profile_id=? AND m.memory_id=?",
        (checkpoint["profile_id"], checkpoint["command_id"] + ":started:memory"),
    ).fetchone()
    assert memory and "This does not confirm completion." in memory[0]
    assert memory[1:] == (
        checkpoint["session_id"], "ambient_ephemeral", checkpoint["session_id"],
    ), memory
    return started


def verify_recovery(connection, checkpoint, started):
    receipts = connection.execute(
        "SELECT data FROM events WHERE profile_id=? AND event_id LIKE ?",
        (checkpoint["profile_id"], checkpoint["command_id"] + ":%"),
    ).fetchall()
    assert [json.loads(row[0]) for row in receipts] == [started], receipts
    memory = connection.execute(
        "SELECT text FROM memories WHERE profile_id=? AND memory_id=?",
        (checkpoint["profile_id"], checkpoint["command_id"] + ":started:memory"),
    ).fetchone()
    assert memory is None, "Session-only action memory survived recovery"
    for table in ("actors", "memories", "actor_instances", "session_cleanup_pending"):
        retained = connection.execute(
            f"SELECT 1 FROM {table} WHERE profile_id=? AND session_id=? LIMIT 1",
            (checkpoint["profile_id"], checkpoint["session_id"]),
        ).fetchone()
        assert retained is None, f"Session cleanup left rows in {table}"
    ended = connection.execute(
        "SELECT ended_at FROM game_sessions WHERE profile_id=? AND session_id=?",
        (checkpoint["profile_id"], checkpoint["session_id"]),
    ).fetchone()
    assert ended and ended[0], "Recovered session was not closed"


def profile_export(endpoint, profile):
    with urlopen(endpoint + "/dashboard", timeout=5) as response:
        page = response.read().decode("utf-8")
    credentials = json.loads(re.search(r"const credentials = (\{[^\n]+\});", page)[1])
    request = Request(endpoint + "/v1/admin/profiles/" + profile + "/export", headers={
        "Authorization": "Bearer " + credentials["token"], "Origin": endpoint,
    })
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def main():
    bundled = ROOT / ".dotnet" / ("dotnet.exe" if os.name == "nt" else "dotnet")
    dotnet = os.environ.get("LLS_DOTNET") or shutil.which("dotnet")
    dotnet = dotnet or (str(bundled) if bundled.is_file() else None)
    if not dotnet:
        raise SystemExit("Install .NET 10 or set LLS_DOTNET to its executable.")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    environment = {"PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    # On Windows dotnet, MSBuild, and NuGet need PATH, the user profile, AppData, and TEMP.
    # Like live_bridge_check, inherit them while excluding developer service settings.
    dotnet_environment = {
        **{
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("LLS_", "LLM_"))
        },
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    }
    with temporary_directory(prefix="lls-process-death-") as temporary:
        database = Path(temporary) / "probe.sqlite3"
        with (Path(temporary) / "service.log").open("w+") as log:
            arguments = [
                sys.executable,
                "tests/bridge_fixture_server.py",
                "--port",
                str(port),
                "--database",
                str(database),
            ]

            def start_service():
                child = subprocess.Popen(
                    arguments, cwd=ROOT, env=environment, stdout=log, stderr=log
                )
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if child.poll() is not None:
                            raise RuntimeError("Owned service exited before readiness")
                        try:
                            with urlopen(endpoint + "/health", timeout=0.5) as response:
                                health = json.load(response)
                            assert health["provider"] == "fake" and health["game_verified"] is False
                            return child
                        except (OSError, URLError):
                            time.sleep(0.05)
                    raise RuntimeError("Owned service did not become ready")
                except BaseException:
                    child.kill()
                    child.wait(timeout=3)
                    raise

            service = start_service()
            probe = None
            try:
                build = subprocess.run(
                    [
                        dotnet,
                        "build",
                        "adapter/LivingNPC.BridgeProbe",
                        "-c",
                        "Release",
                    ],
                    cwd=ROOT,
                    env=dotnet_environment,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                if build.returncode:
                    raise RuntimeError(
                        f"C# probe build failed with exit code {build.returncode}:\n"
                        + build.stdout
                        + build.stderr
                    )
                probe = subprocess.Popen(
                    [
                        dotnet,
                        "adapter/LivingNPC.BridgeProbe/bin/Release/net10.0/LivingNPC.BridgeProbe.dll",
                        endpoint,
                        "--crash",
                    ],
                    cwd=ROOT,
                    env=dotnet_environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                lines = queue.Queue()

                def read_stdout():
                    for line in probe.stdout:
                        lines.put(line)
                    lines.put(None)

                reader = threading.Thread(target=read_stdout, daemon=True)
                reader.start()

                def message():
                    line = lines.get(timeout=20)
                    if line is None:
                        raise RuntimeError("C# probe ended early: " + probe.stderr.read())
                    return json.loads(line)

                checkpoint = message()
                assert checkpoint["phase"] == "started", checkpoint
                # Close explicitly: Windows cannot delete an open database with the temp folder.
                with closing(sqlite3.connect(database)) as connection:
                    started = verify_started(connection, checkpoint)
                # Popen owns this exact process. No process search or broad kill occurs.
                service.kill()
                service.wait(timeout=3)
                withdrawn = message()
                assert withdrawn == {"phase": "withdrawn", "releases": 1}, withdrawn
                service = start_service()
                result = message()
                assert result["status"] == "passed", result
                assert probe.wait(timeout=5) == 0, probe.stderr.read()
                with closing(sqlite3.connect(database)) as connection:
                    verify_recovery(connection, checkpoint, started)
                exported = profile_export(endpoint, checkpoint["profile_id"])
                assert [
                    event for event in exported["events"]
                    if event["event_id"].startswith(checkpoint["command_id"] + ":")
                ] == [started]
                assert not any(
                    memory["session_id"] == checkpoint["session_id"]
                    for memory in exported["memories"]
                )
                print(
                    json.dumps(
                        {
                            **result,
                            "started_ack_persisted_before_kill": True,
                            "started_receipt_visible_in_profile_export": True,
                            "ambient_history_purged_after_restart": True,
                            "canonical_completion_after_restart": False,
                            "service_process_killed": True,
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if probe is not None:
                    if probe.poll() is None:
                        probe.kill()
                        probe.wait(timeout=3)
                    probe.stdout.close()
                    probe.stderr.close()
                if service.poll() is None:
                    service.terminate()
                    try:
                        service.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        service.kill()
                        service.wait(timeout=3)


if __name__ == "__main__":
    main()
