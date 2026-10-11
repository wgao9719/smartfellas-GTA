"""Kill an owned temporary HTTP service during a fake C# game action, then restart it."""

import json
import os
import queue
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


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
    with tempfile.TemporaryDirectory(prefix="lls-process-death-") as temporary:
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
                    row = connection.execute(
                        "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                        (checkpoint["profile_id"], checkpoint["command_id"] + ":started"),
                    ).fetchone()
                    assert row is not None, "Started ACK was not durable before process death"
                    assert json.loads(row[0])["payload"]["observed"]["task_dispatched"] is True
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
                    receipts = connection.execute(
                        "SELECT event_id FROM events WHERE profile_id=? AND event_id LIKE ?",
                        (checkpoint["profile_id"], checkpoint["command_id"] + ":%"),
                    ).fetchall()
                    assert receipts == [(checkpoint["command_id"] + ":started",)], receipts
                    memory = connection.execute(
                        "SELECT text FROM memories WHERE profile_id=? AND memory_id=?",
                        (checkpoint["profile_id"], checkpoint["command_id"] + ":started:memory"),
                    ).fetchone()
                    assert memory and "This does not confirm completion." in memory[0]
                print(
                    json.dumps(
                        {
                            **result,
                            "started_ack_persisted_before_kill": True,
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
