"""Real HTTP provider paths stop before DNS/socket access without cloud consent.

Each probe runs in a subprocess with synthetic credentials and a temporary database.
Only dashboard/bridge traffic uses ASGITransport. Model HTTP keeps its real transport,
but resolver/socket hooks prevent every actual outbound request.
"""

import asyncio
import errno
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8765"
REMOTE = "https://consent-probe.invalid/v1"
LOCAL = "http://127.0.0.1:19473/v1"
MODEL = "synthetic-consent-model"


class NetworkProbe:
    """Record attempted network access without invoking a real resolver or connect."""

    def __init__(self):
        self.events = []

    def install(self):
        def resolve(host, port, family=0, type=0, proto=0, flags=0):
            host = host.decode() if isinstance(host, bytes) else host
            self.events.append({"kind": "dns", "host": host, "port": port})
            # TEST-NET-1 is an invented answer. No system DNS lookup occurs.
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("192.0.2.73", int(port)),
                )
            ]

        def connect(sock, address):
            self.events.append({"kind": "connect", "host": address[0], "port": address[1]})
            raise ConnectionRefusedError(errno.ECONNREFUSED, "network consent test blocked connect")

        def audit(event, args):
            # Backstop alternate socket/resolver entry points, including UDP.
            if event in {
                "socket.getaddrinfo",
                "socket.gethostbyname",
                "socket.gethostbyaddr",
                "socket.getnameinfo",
                "socket.connect",
                "socket.sendto",
                "socket.sendmsg",
            }:
                self.events.append({"kind": "unexpected_native_network", "event": event})
                raise OSError("network consent test blocked native network access")

        socket.getaddrinfo = resolve
        socket.socket.connect = connect
        socket.socket.connect_ex = connect
        sys.addaudithook(audit)


async def probe_service(database, operation):
    probe = NetworkProbe()
    probe.install()
    import httpx

    from service.lls.__main__ import fixture_event, fixture_role_fields, fixture_session
    from service.lls.app import create_app
    from service.lls.providers import OpenAIProvider
    from service.lls.settings import Settings

    app = create_app(Settings(database_path=str(database)))
    runtime = app.state.runtime
    phases = {}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4000)),
            base_url=ORIGIN,
        ) as client:
            page = await client.get("/dashboard")
            assert page.status_code == 200
            credentials = json.loads(re.search(r"const credentials = (\{[^\n]+\});", page.text)[1])
            client.headers.update(
                {
                    "Authorization": "Bearer " + credentials["token"],
                    "X-CSRF-Token": credentials["csrf"],
                    "Origin": ORIGIN,
                }
            )

            async def request(method, path, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.status_code == 200, response.text
                return response.json()

            async def save(provider, *, fallback=False):
                configuration = (await request("GET", "/v1/admin/provider"))["configuration"]
                configuration.pop("cloud_opt_in")
                configuration.update(
                    provider=provider,
                    dialogue_model=MODEL,
                    action_model=MODEL,
                    openai_base_url=REMOTE,
                    local_base_url=LOCAL,
                )
                if operation == "embedding":
                    configuration["embedding"] = {"provider": provider, "model": MODEL}
                configuration["ai_gateway"].update(
                    background={"max_requests": 20, "max_estimated_usd": 2},
                    prices={MODEL: {"input_usd_per_million": 1, "output_usd_per_million": 2}},
                    circuit_failure_limit=10,
                    local_fallback={"endpoint": LOCAL, "dialogue_model": MODEL}
                    if fallback
                    else None,
                )
                result = await request("PUT", "/v1/admin/provider", json=configuration)
                assert result["cloud_consent"] is False
                assert isinstance(runtime.provider.primary, OpenAIProvider)
                assert runtime.provider.primary.transport is None
                return result

            async def generate_through_bridge(label):
                # Exercise Runtime generation through the authenticated bridge, not a
                # hand-built provider object or an injected model HTTP transport.
                body = fixture_session()
                body["profile_id"] = "consent-" + label
                opened = await request("POST", "/v1/bridge/sessions", json=body)
                sid = opened["session_id"]
                headers = {"Authorization": "Bearer " + opened["token"]}
                events = [
                    fixture_event(
                        sid,
                        1,
                        "world.snapshot",
                        {
                            "online": False,
                            "mission_active": False,
                            "cutscene_active": False,
                            "random_event_active": False,
                            "paused": False,
                            "protagonist_id": "franklin",
                        },
                    ),
                    fixture_event(
                        sid,
                        2,
                        "actor.snapshot",
                        {
                            "runtime_handle": 42,
                            "runtime_instance_id": "probe-ped",
                            "ownership": "ambient",
                            "control_policy": "ambient",
                            "exists": True,
                            "alive": True,
                            "distance": 2,
                            "mission_entity": False,
                            "busy": False,
                            "position": {"x": 0, "y": 0, "z": 0},
                            "name": "Synthetic Neighbor",
                            **fixture_role_fields(),
                        },
                        "probe-npc",
                    ),
                    fixture_event(
                        sid,
                        3,
                        "player.utterance_text",
                        {
                            "text": "Hello.",
                            "scene_id": "probe-scene",
                        },
                        "probe-npc",
                    ),
                ]
                await request(
                    "POST",
                    "/v1/bridge/events:batch",
                    headers=headers,
                    json={"session_id": sid, "events": events},
                )
                session = runtime.sessions[sid]
                assert session.task is not None
                await asyncio.wait_for(session.task, 5)
                assert any(
                    command["kind"] == "dialogue.chunk" for command in session.commands.values()
                )
                assert not any(
                    command["kind"] == "action.execute" for command in session.commands.values()
                )
                await request(
                    "POST",
                    f"/v1/bridge/sessions/{sid}/close",
                    headers=headers,
                    json={"runtime_generation": 1},
                )

            async def invoke(label):
                start = len(probe.events)
                if operation == "generation":
                    await generate_through_bridge(label)
                elif operation == "action":
                    # A fresh task prevents a previous failed dialogue route from
                    # skipping action authorization before it reaches the real gate.
                    result = await asyncio.create_task(
                        runtime.provider.action(
                            {"input": "Please wait."}, "I can wait.", ["wait_here"]
                        )
                    )
                    assert result is None
                elif operation == "embedding":
                    # Use the Runtime-owned index and its current gateway after each
                    # dashboard configuration change. No HTTP transport is replaced.
                    repo = runtime.repo
                    profile = "embedding-" + label
                    repo.register_actor(profile, {"id": "probe-npc"})
                    stamp = "2025-01-01T10:00:00Z"
                    branch = repo.open_timeline(profile, stamp)
                    repo.record_event(
                        profile,
                        branch,
                        {
                            "event_id": "embedding-source",
                            "game_datetime": stamp,
                            "protagonist_id": "franklin",
                            "event_type": "fixture",
                            "observer_ids": ["probe-npc"],
                            "provenance": "heard",
                            "confidence": 0.8,
                            "payload": {"text": "A synthetic bicycle memory."},
                        },
                    )
                    repo.add_memory(
                        profile,
                        branch,
                        "probe-npc",
                        "franklin",
                        stamp,
                        "A synthetic bicycle memory.",
                        ["embedding-source"],
                        "heard",
                    )
                    if runtime.memory_index.provider is not None:
                        assert runtime.memory_index.provider.transport is None
                    result = await runtime.memory_index.retrieve_memories(
                        profile,
                        branch,
                        "probe-npc",
                        "franklin",
                        stamp,
                        "bicycle",
                    )
                    assert result[0]["source_event_ids"] == ["embedding-source"]
                else:
                    result = await asyncio.create_task(
                        runtime.provider.skill_candidate(
                            {
                                "skill_request": {"skill_id": "probe-courtesy", "version": 1},
                                "previous_trials": [],
                            }
                        )
                    )
                    assert result is None
                phases[label] = probe.events[start:]

            await invoke("disabled")
            await save("openai")
            await invoke("before_consent")
            status = await request("GET", "/v1/admin/provider")
            granted = await request(
                "POST",
                "/v1/admin/provider/consent",
                json={
                    "endpoint": REMOTE,
                    "configuration_revision": status["configuration_revision"],
                },
            )
            assert granted["cloud_consent"] is True
            await invoke("consent_on")
            revoked = await request("POST", "/v1/admin/provider/revoke", json={})
            assert revoked["cloud_consent"] is False
            assert revoked["active_remote_requests"] == 0
            await invoke("consent_off")
            await save("local_openai")
            await invoke("local_without_cloud")
            # An explicitly selected fallback is another independent local authority.
            if operation not in {"action", "embedding"}:
                await save("openai", fallback=True)
                await invoke("local_fallback_without_cloud")
            health = await request("GET", "/health")
            assert health["status"] == "ok" and health["game_verified"] is False
    return {"phases": phases, "all_events": probe.events}


@pytest.mark.parametrize("operation", ["generation", "action", "candidate", "embedding"])
def test_real_service_cloud_consent_precedes_dns_and_socket_access(tmp_path, operation):
    # Do not inherit actual credentials, proxy settings, or service configuration.
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
        "LLM_API_KEY": "synthetic-network-consent-key",
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path / "probe.db"), operation],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    phases = report["phases"]
    for phase in ("disabled", "before_consent", "consent_off"):
        assert phases[phase] == [], (phase, phases[phase])
    assert {event["kind"] for event in phases["consent_on"]} == {"dns", "connect"}
    assert {event["host"] for event in phases["consent_on"] if event["kind"] == "dns"} == {
        "consent-probe.invalid"
    }
    for phase in (name for name in phases if name.startswith("local_")):
        assert phases[phase], phase
        assert all(
            event["kind"] == "connect" and ipaddress.ip_address(event["host"]).is_loopback
            for event in phases[phase]
        ), (phase, phases[phase])
    # Dashboard, health, startup, and shutdown must not hide other model connections.
    assert report["all_events"] == [event for events in phases.values() for event in events]


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe_service(Path(sys.argv[1]), sys.argv[2]))))
