"""Run the real C# worker against an isolated Python HTTP service.

Usage: uv run python tests/live_bridge_check.py
This verifies transport contracts, never GTA native behavior.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", action="store_true", help="Use synthetic audio devices")
    args = parser.parse_args()
    bundled = ROOT / ".dotnet" / ("dotnet.exe" if os.name == "nt" else "dotnet")
    dotnet = os.environ.get("LLS_DOTNET") or shutil.which("dotnet")
    dotnet = dotnet or (str(bundled) if bundled.is_file() else None)
    if not dotnet:
        raise SystemExit("Install .NET 10 or set LLS_DOTNET to its executable.")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="lls-contract-") as temporary:
        environment = {
            **{
                name: value
                for name, value in os.environ.items()
                if not name.startswith(("LLS_", "LLM_"))
            },
            "LLS_HOST": "127.0.0.1",
            "LLS_PORT": str(port),
            "LLS_DATABASE": str(Path(temporary) / "probe.sqlite3"),
            "LLS_PROVIDER": "fake",
            "LLS_CLOUD_OPT_IN": "0",
            "LLS_BRIDGE_TOKEN": "",
            "LLS_DEV_LAN": "0",
            "LLS_RELEASE": "1",
        }
        with (Path(temporary) / "service.log").open("w+") as log:
            server_arguments = [
                sys.executable,
                "tests/bridge_fixture_server.py",
                "--port",
                str(port),
                "--database",
                str(Path(temporary) / "probe.sqlite3"),
            ]
            if args.speech:
                server_arguments.append("--speech")
            service = subprocess.Popen(
                server_arguments,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if service.poll() is not None:
                        raise RuntimeError("Isolated service exited before readiness.")
                    try:
                        with urlopen(endpoint + "/health", timeout=0.5) as response:
                            health = json.load(response)
                        if health["provider"] != "fake":
                            raise RuntimeError("Contract service did not select the fake provider.")
                        break
                    except (OSError, URLError):
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Isolated service did not become ready.")
                subprocess.run(
                    [
                        dotnet,
                        "run",
                        "--project",
                        "adapter/LivingNPC.BridgeProbe",
                        "--configuration",
                        "Release",
                        "--",
                        endpoint,
                        *(["--speech"] if args.speech else []),
                    ],
                    cwd=ROOT,
                    env=environment,
                    check=True,
                    timeout=90,
                )
            finally:
                service.terminate()
                try:
                    service.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=3)


if __name__ == "__main__":
    main()
