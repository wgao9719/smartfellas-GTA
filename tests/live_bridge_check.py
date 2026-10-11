"""Run the real C# worker against an isolated Python HTTP service.

Usage: uv run python tests/live_bridge_check.py
This verifies transport contracts, never GTA native behavior.
"""

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.display import DISPLAY_RULE, is_display_safe  # noqa: E402


def stored_payloads(database, event_type):
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute("SELECT data FROM events").fetchall()
    events = [json.loads(row[0]) for row in rows]
    return [event["payload"] for event in events if event["event_type"] == event_type]


def verify_pin(database, probe):
    """The promoted actor and its pre-pin conversation outlive the closed session."""
    sys.path.insert(0, str(ROOT))
    from service.lls.storage import Repository

    profile, actor_id = probe["profile_id"], probe["pinned_actor_id"]
    with Repository(database) as repo:
        actor = repo.get_actor(profile, actor_id) or {}
        memories = repo.list_memories(profile, actor_id, limit=500)
        before = [m for m in memories if m["text"] == "The player said: Please look at me."]
        ended = repo.connection.execute(
            "SELECT COUNT(*) FROM game_sessions WHERE profile_id=? AND ended_at IS NOT NULL",
            (profile,),
        ).fetchone()[0]
        officer = repo.get_actor(profile, "probe-cop")
    result = {
        "status": "passed",
        "mode": "pinned_identity_durability",
        "actor_kind": actor.get("actor_kind"),
        "pre_pin_memories": len(before),
        "durable_memories": sum(m["session_id"] is None for m in memories),
        "memories": len(memories),
        "sessions_closed": ended,
        "ambient_officer_purged": officer is None,
        "game_verified": False,
    }
    if (
        result["actor_kind"] != "spawned_persistent"
        or actor.get("session_id") is not None
        or not before
        or result["durable_memories"] != len(memories)
        or ended != 1
        or officer is not None
    ):
        raise RuntimeError("Pinned identity did not survive session close: " + json.dumps(result))
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", action="store_true", help="Use synthetic audio devices")
    parser.add_argument("--whisper", action="store_true", help="Whisper the typed or spoken input")
    parser.add_argument("--pin", action="store_true", help="Pin a pedestrian and check durability")
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
        database = Path(temporary) / "probe.sqlite3"
        environment = {
            **{
                name: value
                for name, value in os.environ.items()
                if not name.startswith(("LLS_", "LLM_"))
            },
            "LLS_HOST": "127.0.0.1",
            "LLS_PORT": str(port),
            "LLS_DATABASE": str(database),
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
                str(database),
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
                probe = subprocess.run(
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
                        *(["--whisper"] if args.whisper else []),
                        *(["--pin"] if args.pin else []),
                    ],
                    cwd=ROOT,
                    env=environment,
                    check=True,
                    timeout=90,
                    capture_output=args.pin,
                    text=True,
                )
                # Pin mode runs its own conversation and checks durability in verify_pin.
                if not args.pin:
                    # Durable receipts must quote display-safe text with the sanitized speaker.
                    delivered = stored_payloads(database, "dialogue.delivered")
                    assert len(delivered) == 2, delivered
                    for payload in delivered:
                        assert is_display_safe(payload["text"]) and payload["text"].strip(), payload
                        assert payload["display_rule"] == DISPLAY_RULE, payload
                        assert payload["speaker"] == "Probe Neighbor", payload
                    # The adapter parses "/w"; the service keeps the text and the locked volume.
                    [spoken] = stored_payloads(database, "player.utterance_text")
                    assert spoken["text"] == "Please look at me.", spoken
                    assert spoken["whisper"] is args.whisper, spoken
                    assert spoken["input_mode"] == ("voice" if args.speech else "typed"), spoken
                    print(
                        json.dumps(
                            {
                                "check": "durable_receipts",
                                "delivered_events": len(delivered),
                                "display_rule": DISPLAY_RULE,
                                "speaker": "Probe Neighbor",
                                "utterance_events": 1,
                                "utterance_whisper": spoken["whisper"],
                                "utterance_input_mode": spoken["input_mode"],
                            },
                            sort_keys=True,
                        )
                    )
            finally:
                service.terminate()
                try:
                    service.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=3)
            if args.pin:
                print(probe.stdout, end="")
                lines = probe.stdout.strip().splitlines()
                verify_pin(Path(temporary) / "probe.sqlite3", json.loads(lines[-1]))


if __name__ == "__main__":
    main()
