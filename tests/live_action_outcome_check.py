"""Verify service expiry/cancellation through real HTTP and process restart.

The shared fixture uses a fake provider and a persistent test actor. No native
action is executed. Service authorization outcomes never prove native effects.
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_skill_check import server  # noqa: E402

from service.lls.__main__ import fixture_event, fixture_session  # noqa: E402
from service.lls.storage import Repository  # noqa: E402

PROFILE = "skill-probe"
ACTOR = "skill-npc"
UNINVOLVED = "outcome-uninvolved"


async def exercise(endpoint):
    async with httpx.AsyncClient(base_url=endpoint, timeout=10, trust_env=False) as client:

        async def post(path, body):
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        request = fixture_session()
        request["profile_id"] = PROFILE
        opened = await post("/v1/bridge/sessions", request)
        sid = opened["session_id"]
        client.headers["Authorization"] = "Bearer " + opened["token"]
        sequence = 0

        def event(kind, payload, actor_id=None):
            nonlocal sequence
            sequence += 1
            value = fixture_event(sid, sequence, kind, payload, actor_id)
            value["event_id"] = "outcome-probe-" + uuid4().hex
            return value

        async def poll():
            response = await client.get("/v1/bridge/commands", params={"wait_seconds": 0})
            response.raise_for_status()
            return response.json()["commands"]

        async def propose(text, scene):
            await post(
                "/v1/bridge/events:batch",
                {
                    "session_id": sid,
                    "events": [
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
                        ),
                        event(
                            "actor.snapshot",
                            {
                                "runtime_handle": 42,
                                "runtime_instance_id": "outcome-ped",
                                "ownership": "ambient",
                                "control_policy": "ambient",
                                "actor_kind": "curated_persistent",
                                "exists": True,
                                "alive": True,
                                "distance": 2,
                                "mission_entity": False,
                                "busy": False,
                                "position": {"x": 0, "y": 0, "z": 0},
                                "name": "Outcome Fixture",
                            },
                            ACTOR,
                        ),
                        event("player.utterance_text", {"text": text, "scene_id": scene}, ACTOR),
                    ],
                },
            )
            async with asyncio.timeout(5):
                while True:
                    commands = await poll()
                    actions = [c for c in commands if c["kind"] == "action.execute"]
                    if actions:
                        assert len(actions) == 1
                        break
                    await asyncio.sleep(0.05)
            for command in commands:
                if command["kind"] == "dialogue.chunk":
                    await post(
                        f"/v1/bridge/commands/{command['id']}/ack",
                        {
                            "session_id": sid,
                            "runtime_generation": 1,
                            "status": "completed",
                            "observed": {
                                "delivery": "simulated",
                                "text": command["payload"]["text"],
                            },
                        },
                    )
            return actions[0]

        expired = await propose("Please look at me.", "expiry-scene")
        remaining = (
            datetime.fromisoformat(expired["expires_at"]) - datetime.now(timezone.utc)
        ).total_seconds()
        assert 0 <= remaining <= 16
        await asyncio.sleep(remaining + 0.15)
        assert all(command["id"] != expired["id"] for command in await poll())
        late = await post(
            f"/v1/bridge/commands/{expired['id']}/ack",
            {"session_id": sid, "runtime_generation": 1, "status": "completed"},
        )
        assert late["status"] == "expired" and late["duplicate"]

        canceled = await propose("Please wait here.", "cancel-scene")
        cancel = event("scene.cancel", {"scene_id": "cancel-scene"})
        await post("/v1/bridge/control", cancel)
        duplicate = await post("/v1/bridge/control", cancel)
        assert duplicate["duplicate"]
        assert all(command["id"] != canceled["id"] for command in await poll())
        await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
        return opened["branch_id"], [(expired, "expired"), (canceled, "canceled")]


def verify(database, branch, outcomes):
    with Repository(database) as repo:
        for command, status in outcomes:
            event_id = command["id"] + ":service:" + status
            rows = repo.connection.execute(
                "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                (PROFILE, event_id),
            ).fetchall()
            assert len(rows) == 1
            event = json.loads(rows[0][0])
            assert event["event_type"] == "action." + status
            assert event["provenance"] == "system_fact"
            assert event["observer_ids"] == [ACTOR]
            payload = event["payload"]
            assert payload["authority"] == "service"
            assert payload["conversation_turn_id"] == command["payload"]["turn_id"]
            assert payload["native_completion_confirmed"] is False
            assert payload["native_withdrawal_confirmed"] is False
            memory = repo.get_memory(PROFILE, event_id + ":memory")
            assert memory and memory["actor_id"] == ACTOR
            assert memory["source_event_ids"] == [event_id]
            assert memory["branch_id"] == branch
            assert repo.connection.execute(
                "SELECT COUNT(*) FROM events WHERE profile_id=? AND event_id=?",
                (PROFILE, command["id"] + ":completed"),
            ).fetchone()[0] == 0
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM memories WHERE profile_id=? AND actor_id=?",
            (PROFILE, UNINVOLVED),
        ).fetchone()[0] == 0


def main():
    with tempfile.TemporaryDirectory(prefix="lls-action-outcomes-") as temporary:
        directory = Path(temporary)
        database = directory / "skills.sqlite3"
        with Repository(database) as repo:
            repo.register_actor(PROFILE, {"id": UNINVOLVED, "display_name": "Uninvolved"})
        with server(directory) as endpoint:
            branch, outcomes = asyncio.run(exercise(endpoint))
            verify(database, branch, outcomes)
        with server(directory):
            verify(database, branch, outcomes)
        print(json.dumps({
            "status": "passed",
            "transport": "real_loopback_http",
            "service_outcomes_after_restart": 2,
            "private_outcome_memories_after_restart": 2,
            "late_completion_rejected": True,
            "duplicate_cancellation_suppressed": True,
            "uninvolved_actor_received_outputs": 0,
            "game_verified": False,
            "native_verified": False,
            "model_boundary": "deterministic_fake",
        }, sort_keys=True))


if __name__ == "__main__":
    main()
