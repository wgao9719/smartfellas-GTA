"""Run the service or an explicit, self-contained fake-game demonstration."""

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

from .app import create_app
from .roles import joaat
from .settings import Settings


def fixture_session():
    return {
        "profile_id": "fake-demo",
        "save_slot": "fake-slot",
        "profile_confirmed": True,
        "protagonist_id": "franklin",
        "runtime_generation": 1,
        "game_datetime": "2013-09-17T12:00:00",
        "capabilities": ["face_player", "wait_here"],
        "manifest": {
            "game_edition": "legacy",
            "story_mode": True,
            "gta_build": "fake",
            "scripthookv_build": "fake",
            "shvdn_build": "fake",
            "adapter_version": "fake",
            "game_os_version": "fake",
            "service_os": "fake",
            "capability_flags": ["fake_game"],
        },
    }


def fixture_ped_facts(model="a_m_y_business_01", **overrides):
    """Full civilian evidence from a simulated adapter. Tests override single facts."""
    return {
        "model_hash": joaat(model),
        "ped_type": 4,
        "relationship_group_hash": joaat("CIVMALE"),
        "human": True,
        "animal_model": False,
        "cuffed": False,
        "in_police_vehicle": False,
        "player_group": False,
        "mission_entity": False,
        "in_mod_registry": False,
        "took_persistence": False,
        "belongs_to_mod_script": False,
        **overrides,
    }


def fixture_role_fields(**facts):
    return {
        "role_class": "civilian",
        "role_reason": "civilian_evidence",
        "ped_facts": fixture_ped_facts(**facts),
    }


def fixture_actor_snapshot(**overrides):
    """A complete ambient civilian snapshot. Missing facts would classify as protected."""
    return {
        "runtime_handle": 42,
        "runtime_instance_id": "ped-1",
        "ownership": "ambient",
        "control_policy": "ambient",
        "exists": True,
        "alive": True,
        "distance": 2,
        "mission_entity": False,
        "busy": False,
        "position": {"x": 0, "y": 0, "z": 0},
        "name": "Neighbor",
        **fixture_role_fields(),
        **overrides,
    }


def fixture_event(session_id, seq, kind, payload, actor_id=None):
    return {
        "event_id": f"demo-{seq}",
        "session_id": session_id,
        "seq": seq,
        "kind": kind,
        "occurred_at_utc": datetime.now(timezone.utc).isoformat(),
        "game_datetime": "2013-09-17T12:00:00",
        "protagonist_id": "franklin",
        "runtime_generation": 1,
        "actor_id": actor_id,
        "payload": payload,
    }


async def fake_demo(replay_path=None):
    """Drive real HTTP endpoints with a fake game. Never connect to GTA or a cloud model."""
    with tempfile.TemporaryDirectory(prefix="lls-demo-") as folder:
        app = create_app(
            Settings(database_path=str(Path(folder) / "demo.sqlite3"), provider="fake")
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 40000))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8765"
            ) as client:
                opened = await client.post("/v1/bridge/sessions", json=fixture_session())
                opened.raise_for_status()
                session = opened.json()
                client.headers["Authorization"] = "Bearer " + session["token"]
                sid = session["session_id"]
                if replay_path:
                    events = json.loads(Path(replay_path).read_text(encoding="utf-8"))
                    if not isinstance(events, list):
                        raise ValueError("replay must be a JSON array of sanitized event envelopes")
                    for event in events:
                        event["session_id"] = sid
                else:
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
                            fixture_actor_snapshot(
                                runtime_instance_id="fake-ped-1", name="Demo Neighbor"
                            ),
                            "demo-npc",
                        ),
                        fixture_event(
                            sid,
                            3,
                            "player.utterance_text",
                            {"text": "Please look at me.", "scene_id": "demo-scene"},
                            "demo-npc",
                        ),
                    ]
                for event in events:
                    endpoint = (
                        "/v1/bridge/control"
                        if event["kind"]
                        in {"scene.cancel", "voice.ptt_started", "voice.ptt_stopped"}
                        else "/v1/bridge/events:batch"
                    )
                    body = (
                        event
                        if endpoint.endswith("control")
                        else {"session_id": sid, "events": [event]}
                    )
                    response = await client.post(endpoint, json=body)
                    response.raise_for_status()
                task = app.state.runtime.sessions[sid].task
                if task:
                    await task
                response = await client.get("/v1/bridge/commands", params={"wait_seconds": 0})
                response.raise_for_status()
                commands = response.json()["commands"]
                for command in commands:
                    if command["kind"] == "action.execute":
                        response = await client.post(
                            f"/v1/bridge/commands/{command['id']}/ack",
                            json={"session_id": sid, "runtime_generation": 1, "status": "started"},
                        )
                        response.raise_for_status()
                    response = await client.post(
                        f"/v1/bridge/commands/{command['id']}/ack",
                        json={
                            "session_id": sid,
                            "runtime_generation": 1,
                            "status": "completed",
                            "observed": {
                                "delivery": "simulated",
                                "text": command["payload"].get("text", ""),
                            },
                        },
                    )
                    response.raise_for_status()
                summary = {
                    "mode": "fake_game",
                    "cloud_calls": 0,
                    "events": len(events),
                    "commands": [{"kind": c["kind"], "payload": c["payload"]} for c in commands],
                    "game_verified": False,
                }
                # ASCII escapes keep the JSON valid on redirected legacy Windows code pages.
                print(json.dumps(summary, indent=2))
                return summary


def main():
    parser = argparse.ArgumentParser(description="Living Los Santos local service")
    parser.add_argument(
        "--demo", action="store_true", help="Run explicit fake-game demo without cloud calls"
    )
    parser.add_argument(
        "--replay", type=Path, help="Replay sanitized event array against a temporary fake service"
    )
    args = parser.parse_args()
    if args.demo or args.replay:
        asyncio.run(fake_demo(args.replay))
        return
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
