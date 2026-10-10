"""Run 200 acknowledged turns with 36 simulated NPCs through the real service.

No model or GTA connection occurs. A virtual clock advances six seconds per turn
to respect the service turn budget. Wall times measure this synthetic ASGI workload,
not model latency, game frame time, or real-time throughput.
"""

import asyncio
import json
import math
import sys
import tempfile
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.lls.__main__ import fixture_session  # noqa: E402
from service.lls.app import create_app  # noqa: E402
from service.lls.providers import FakeProvider  # noqa: E402
from service.lls.runtime import TERMINAL  # noqa: E402
from service.lls.settings import Settings  # noqa: E402
from service.lls.storage import Repository  # noqa: E402

ACTORS = tuple(f"load-npc-{index:03}" for index in range(36))
PERSISTENT = set(ACTORS[:12])
UNINVOLVED = ACTORS[11]
PROFILE = "synthetic-load"
TURNS = 200


class LoadHttpClient:
    """Respect the real rolling HTTP gate using this client's admission history."""

    def __init__(self, client, *, clock=time.perf_counter, sleep=asyncio.sleep):
        self.client = client
        self.clock = clock
        self.sleep = sleep
        self.admitted = deque(maxlen=600)
        self.rate_retries = 0
        self.rate_wait_seconds = 0.0

    async def request(self, method, path, **kwargs):
        for _ in range(16):
            response = await self.client.request(method, path, **kwargs)
            stamp = self.clock()
            while self.admitted and self.admitted[0] < stamp - 60:
                self.admitted.popleft()
            if response.status_code != 429 or response.json().get("detail") != (
                "request rate exceeded"
            ):
                # Completion is at or after middleware admission, so it gives a
                # conservative expiry estimate. Route-level 409s also consume slots.
                self.admitted.append(stamp)
                return response
            # The server uses a rolling window. A successful retry does not reset
            # all 600 old admissions. Wait for the next recorded slot to expire.
            pause = max(0.05, self.admitted[0] + 60.05 - stamp) if self.admitted else 0.05
            self.rate_retries += 1
            self.rate_wait_seconds += pause
            await self.sleep(pause)
        raise RuntimeError("HTTP rate gate did not recover within the retry limit")


class LoadClock:
    def __init__(self):
        self.seconds = 1000
        self.start = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def monotonic(self):
        return self.seconds

    def utc_now(self):
        return self.start + timedelta(seconds=self.seconds - 1000)


class LoadProvider(FakeProvider):
    """The outer model boundary checks real context isolation before replying."""

    def __init__(self):
        self.calls = 0
        self.context_bytes = 0
        self.actors_seen = set()

    async def dialogue(self, context):
        actor_id = context["actor"]["actor_id"]
        assert actor_id != UNINVOLVED
        serialized = json.dumps(context)
        assert not any(other in serialized for other in ACTORS if other != actor_id)
        assert all(actor_id in memory["text"] for memory in context["memories"])
        if actor_id not in self.actors_seen:
            assert any("private seed" in memory["text"] for memory in context["memories"])
        self.actors_seen.add(actor_id)
        self.calls += 1
        self.context_bytes = max(self.context_bytes, len(serialized.encode()))
        yield f"Synthetic reply {self.calls:03} for {actor_id}."


def count(repo, table, where="", args=()):
    return repo.connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE profile_id=? " + where, (PROFILE, *args)
    ).fetchone()[0]


async def run_load():
    clock = LoadClock()
    provider = LoadProvider()
    latencies = []
    peaks = Counter()
    limits = {
        "actors": 36,
        "commands": 256,
        "pending_commands": 2,
        "delivery_turns": 32,
        "delivered_utterances": 64,
        "seen": 2048,
        "sequences": 128,
        "trace": 200,
    }
    expected = {}
    retries = 0
    backpressure = 0
    with tempfile.TemporaryDirectory(prefix="lls-synthetic-load-") as directory:
        database = Path(directory) / "load.sqlite3"
        settings = Settings(database_path=str(database), provider="fake")
        app = create_app(
            settings,
            runtime_options={
                "clock": clock.monotonic,
                "utc_now": clock.utc_now,
            },
        )
        runtime, repo = app.state.runtime, app.state.repository
        # Retain the actual GatewayProvider and replace only its outer fake model.
        runtime.provider.primary = provider
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 44000)),
                base_url="http://127.0.0.1:8765",
            ) as client:
                # The actual workload retains the real middleware clock, bucket,
                # rate limit, source IP, and physical waits.
                http = LoadHttpClient(client)
                wire_request = http.request

                async def post(path, body):
                    response = await wire_request("POST", path, json=body)
                    assert response.status_code == 200, response.text
                    return response.json()

                request = fixture_session()
                request.update(profile_id=PROFILE, capabilities=[])
                opened = await post("/v1/bridge/sessions", request)
                sid = opened["session_id"]
                client.headers["Authorization"] = "Bearer " + opened["token"]
                session = runtime.sessions[sid]
                sequence = 0

                def event(kind, payload, actor_id=None):
                    nonlocal sequence
                    sequence += 1
                    return {
                        "event_id": f"load-event-{sequence}",
                        "seq": sequence,
                        "session_id": sid,
                        "runtime_generation": 1,
                        "occurred_at_utc": clock.utc_now().isoformat(),
                        "game_datetime": request["game_datetime"],
                        "protagonist_id": "franklin",
                        "kind": kind,
                        "actor_id": actor_id,
                        "payload": payload,
                    }

                snapshots = {}
                for index, actor_id in enumerate(ACTORS):
                    kind = "curated_persistent" if actor_id in PERSISTENT else "ambient_ephemeral"
                    repo.register_actor(
                        PROFILE,
                        {
                            "actor_id": actor_id,
                            "actor_kind": kind,
                            "session_id": sid,
                            "display_name": actor_id,
                            "allowed_actions": [],
                        },
                    )
                    seed = "load-seed-" + actor_id
                    repo.record_event(
                        PROFILE,
                        session.branch_id,
                        {
                            "event_id": seed,
                            "game_datetime": request["game_datetime"],
                            "protagonist_id": "franklin",
                            "observer_ids": [actor_id],
                            "provenance": "heard",
                            "confidence": 1,
                            "payload": {"text": f"loadprobe private seed for {actor_id}"},
                        },
                    )
                    repo.add_memory(
                        PROFILE,
                        session.branch_id,
                        actor_id,
                        "franklin",
                        request["game_datetime"],
                        f"loadprobe private seed for {actor_id}",
                        [seed],
                        "heard",
                        session_id=sid,
                    )
                    snapshots[actor_id] = {
                        "runtime_handle": 100 + index,
                        "runtime_instance_id": f"load-ped-{index}",
                        "ownership": "ambient",
                        "control_policy": "ambient",
                        "exists": True,
                        "alive": True,
                        "distance": 2,
                        "mission_entity": False,
                        "busy": False,
                        "position": {"x": float(index), "y": 0, "z": 0},
                        "name": actor_id,
                        "actor_kind": kind,
                    }

                participants = [actor for actor in ACTORS if actor != UNINVOLVED]
                started = time.perf_counter()
                for turn_index in range(TURNS):
                    clock.seconds += 6
                    actor_id = participants[turn_index % len(participants)]
                    turn_start = time.perf_counter()
                    events = [
                        event(
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
                    events += [
                        event("actor.snapshot", value, actor) for actor, value in snapshots.items()
                    ]
                    events.append(
                        event(
                            "player.utterance_text",
                            {
                                "text": f"loadprobe question {turn_index:03} for {actor_id}",
                                "scene_id": f"load-scene-{turn_index}",
                            },
                            actor_id,
                        )
                    )
                    await post("/v1/bridge/events:batch", {"session_id": sid, "events": events})
                    await asyncio.wait_for(session.task, 5)
                    polled = await wire_request(
                        "GET", "/v1/bridge/commands", params={"wait_seconds": 0}
                    )
                    assert polled.status_code == 200, polled.text
                    commands = polled.json()["commands"]
                    assert len(commands) == 2
                    assert all(
                        c["kind"] == "dialogue.chunk" and c["actor_id"] == actor_id
                        for c in commands
                    )
                    spoken = next(c for c in commands if c["payload"]["text"])
                    final = next(c for c in commands if c["payload"]["final"])
                    expected[spoken["id"]] = (actor_id, spoken["payload"]["text"])
                    assert repo.get_memory(PROFILE, spoken["id"] + ":memory") is None

                    if turn_index % 25 == 0:
                        blocked = event(
                            "player.utterance_text",
                            {
                                "text": "This must wait for delivery",
                                "scene_id": "blocked-scene",
                            },
                            actor_id,
                        )
                        response = await wire_request(
                            "POST",
                            "/v1/bridge/events:batch",
                            json={
                                "session_id": sid,
                                "events": [blocked],
                            },
                        )
                        assert response.status_code == 409, response.text
                        backpressure += 1

                    sizes = {
                        name: len(getattr(session, name))
                        for name in limits
                        if name != "pending_commands"
                    }
                    sizes["pending_commands"] = sum(
                        command["state"] not in TERMINAL for command in session.commands.values()
                    )
                    for name, size in sizes.items():
                        assert size <= limits[name], (name, size)
                        peaks[name] = max(peaks[name], size)

                    # Marker-first delivery proves generation alone does not create recall.
                    for command in (final, spoken):
                        receipt = {
                            "session_id": sid,
                            "runtime_generation": 1,
                            "status": "completed",
                            "observed": {
                                "delivery": "simulated",
                                "text": command["payload"]["text"],
                            },
                        }
                        await post(f"/v1/bridge/commands/{command['id']}/ack", receipt)
                        if command is final:
                            assert repo.get_memory(PROFILE, spoken["id"] + ":memory") is None
                    if turn_index % 25 == 0:
                        duplicate = await post(f"/v1/bridge/commands/{spoken['id']}/ack", receipt)
                        assert duplicate["duplicate"] is True
                        retries += 1
                    remembered = repo.get_memory(PROFILE, spoken["id"] + ":memory")
                    assert remembered["text"] == "I said: " + spoken["payload"]["text"]
                    assert remembered["actor_id"] == actor_id
                    latencies.append((time.perf_counter() - turn_start) * 1000)
                elapsed = time.perf_counter() - started
                assert provider.calls == TURNS and len(provider.actors_seen) == 35
                assert provider.context_bytes <= settings.context_byte_budget
                assert peaks["commands"] == settings.max_commands
                assert count(repo, "memories", "AND text LIKE 'I said: %'") == TURNS
                assert count(repo, "memories", "AND actor_id=?", (UNINVOLVED,)) == 1
                branch = session.branch_id
                await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})

        persistent_outputs = 0
        with Repository(database) as reopened:
            for command_id, (actor_id, text) in expected.items():
                source = reopened.connection.execute(
                    "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                    (PROFILE, command_id + ":delivered"),
                ).fetchone()
                assert source is not None
                source = json.loads(source[0])
                assert source["observer_ids"] == [actor_id] and source["payload"]["text"] == text
                memory = reopened.get_memory(PROFILE, command_id + ":memory")
                if actor_id in PERSISTENT:
                    assert memory["actor_id"] == actor_id and memory["text"] == "I said: " + text
                    persistent_outputs += 1
                else:
                    assert memory is None  # Session-scoped ambient recall expires on close.
            assert count(reopened, "memories", "AND actor_id=?", (UNINVOLVED,)) == 1
            assert (
                reopened.get_relationship(
                    PROFILE, branch, UNINVOLVED, "franklin", request["game_datetime"]
                )["familiarity"]
                == 0
            )
            completions = reopened.connection.execute(
                "SELECT data FROM events WHERE profile_id=? "
                "AND event_id LIKE '%:conversation_completed'",
                (PROFILE,),
            ).fetchall()
            assert len(completions) == TURNS
            assert all(UNINVOLVED not in json.loads(row[0])["observer_ids"] for row in completions)

    ordered = sorted(latencies)
    return {
        "domain": "development_simulation",
        "game_verified": False,
        "native_verified": False,
        "actors": {
            "tracked": 36,
            "persistent": 12,
            "ephemeral": 24,
            "participating": 35,
            "uninvolved": 1,
        },
        "turns": TURNS,
        "model_boundary": "deterministic_fake",
        "transport": "in_process_asgi",
        "confirmed_delivery_events_after_restart": len(expected),
        "confirmed_completion_events_after_restart": len(completions),
        "persistent_output_memories_after_restart": persistent_outputs,
        "ephemeral_output_memories_removed_on_close": TURNS - persistent_outputs,
        "uninvolved_actor_received_outputs": 0,
        "context_isolation_checks": provider.calls,
        "maximum_context_bytes": provider.context_bytes,
        "queue_peaks": dict(peaks),
        "queue_limits": limits,
        "blocked_while_delivery_pending": backpressure,
        "duplicate_delivery_checks": retries,
        "http_rate_limit_retries": http.rate_retries,
        "timing": {
            "scope": "synthetic ASGI turns including snapshots, SQLite, polling and ACKs",
            "virtual_elapsed_seconds": 6 * TURNS,
            "measured_wall_seconds": elapsed,
            "http_rate_limit_wait_seconds": http.rate_wait_seconds,
            "turn_wall_ms_p50": ordered[len(ordered) // 2],
            "turn_wall_ms_p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "native_frame_time_measured": False,
            "model_latency_measured": False,
        },
    }


def main():
    attempts = []

    def deny_network(event, args):
        if event in {
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "socket.connect",
            "socket.sendto",
            "socket.sendmsg",
        }:
            attempts.append(event)
            raise OSError("synthetic load check forbids network access")

    sys.addaudithook(deny_network)
    report = asyncio.run(run_load())
    assert attempts == [], attempts
    report["network_attempts"] = len(attempts)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
