import io
import json
import sys

from service.lls.__main__ import fake_demo, fixture_event, fixture_role_fields


async def test_demo_prints_ascii_json_through_a_redirected_windows_code_page(tmp_path, monkeypatch):
    """Windows redirects stdout through the ANSI code page, such as cp1252, not UTF-8."""
    name = "Łucja Nowak 李"
    world = {
        "online": False,
        "mission_active": False,
        "cutscene_active": False,
        "random_event_active": False,
        "paused": False,
        "protagonist_id": "franklin",
    }
    actor = {
        "runtime_handle": 42,
        "runtime_instance_id": "fake-ped-1",
        "ownership": "ambient",
        "control_policy": "ambient",
        "exists": True,
        "alive": True,
        "distance": 2,
        "mission_entity": False,
        "busy": False,
        "position": {"x": 0, "y": 0, "z": 0},
        "name": name,
        **fixture_role_fields(),
    }
    events = [
        fixture_event("replay", 1, "world.snapshot", world),
        fixture_event("replay", 2, "actor.snapshot", actor, "demo-npc"),
        fixture_event(
            "replay",
            3,
            "player.utterance_text",
            {"text": "Cześć, sąsiedzie.", "scene_id": "demo-scene"},
            "demo-npc",
        ),
    ]
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stdout)
    summary = await fake_demo(replay)
    stdout.flush()
    printed = stdout.buffer.getvalue().decode("ascii")
    assert json.loads(printed) == summary
    assert any(name in json.dumps(command, ensure_ascii=False) for command in summary["commands"])
