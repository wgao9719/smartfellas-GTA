import asyncio
import copy
import json
from pathlib import Path

import pytest

from service.lls import replay
from service.lls.replay import MAX_BUNDLE_BYTES, ReplayBundle, load_bundle, run_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_conversation.json"


def fixture():
    return json.loads(FIXTURE.read_text())


async def test_recorded_trace_is_repeatable_and_never_uses_environment(tmp_path, monkeypatch):
    live = tmp_path / "must-not-create.sqlite3"
    monkeypatch.setenv("LLS_DATABASE", str(live))
    monkeypatch.setenv("LLS_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "must-not-appear")
    monkeypatch.setenv("LLS_CLOUD_OPT_IN", "1")
    first = await run_replay(load_bundle(FIXTURE))
    second = await run_replay(load_bundle(FIXTURE))
    assert first == second
    assert not live.exists()
    assert first["cloud_calls"] == first["native_calls"] == 0
    assert first["recorded_turns_used"] == 1
    assert not first["missing_responses"]
    assert not first["unsettled_turn"]
    assert all(c["state"] == "completed" for c in first["commands"])
    assert first["action_outcomes"][-1]["status"] == "completed"
    report = json.dumps(first)
    assert "must-not-appear" not in report
    assert "I can face you." not in report
    assert "token" not in report


async def test_replay_checks_completion_order_and_preserves_duplicate_receipts():
    source = fixture()
    source["steps"].insert(7, {"type": "ack", "command_seq": 4, "status": "completed"})
    source["steps"].append(copy.deepcopy(source["steps"][-1]))
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["steps"][7]["http_status"] == 409
    assert report["steps"][-1]["duplicate"]
    assert [o["status"] for o in report["action_outcomes"]] == ["started", "completed"]


async def test_virtual_clock_expires_actions_without_wall_clock_waits():
    source = fixture()
    source["steps"] = source["steps"][:4] + [{"type": "advance", "seconds": 16}]
    report = await run_replay(ReplayBundle.model_validate(source))
    action = next(c for c in report["commands"] if c["kind"] == "action.execute")
    assert action["state"] == "expired"
    assert report["action_outcomes"][-1]["status"] == "expired"
    assert not report["session_closed"]


async def test_virtual_clock_expires_session_and_cancels_remaining_dialogue():
    source = fixture()
    source["steps"] = source["steps"][:4] + [{"type": "advance", "seconds": 31}]
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["session_closed"]
    assert all(
        c["state"] == "canceled" for c in report["commands"] if c["kind"] == "dialogue.chunk"
    )
    assert report["action_outcomes"][-1]["status"] == "expired"


async def test_close_preserves_action_history():
    source = fixture()
    source["steps"].append({"type": "close"})
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["session_closed"]
    assert [o["status"] for o in report["action_outcomes"]] == ["started", "completed"]
    source["steps"] = source["steps"][:4] + [{"type": "close"}]
    canceled = await run_replay(ReplayBundle.model_validate(source))
    assert canceled["action_outcomes"][-1]["status"] == "canceled"


async def test_report_distinguishes_queued_text_from_actual_delivery():
    source = fixture()
    full = await run_replay(ReplayBundle.model_validate(source))
    for step in source["steps"]:
        if step.get("observed"):
            step["observed"]["text"] = ""
    empty = await run_replay(ReplayBundle.model_validate(source))
    assert full["commands"] == empty["commands"]
    assert full["steps"][4]["submitted_text_characters"] > 0
    assert empty["steps"][4]["submitted_text_characters"] == 0
    assert full["steps"][4]["submitted_text_sha256"] != empty["steps"][4]["submitted_text_sha256"]


async def test_turn_timeout_cannot_report_settled_when_runtime_suppresses_cancel(monkeypatch):
    async def slow_dialogue(self, context):
        await asyncio.sleep(1)
        yield "Too late."

    monkeypatch.setattr(replay, "TURN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(replay.RecordedProvider, "dialogue", slow_dialogue)
    source = fixture()
    source["steps"] = source["steps"][:4]
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["timed_out_turns"] == 1
    assert report["steps"][-1]["status"] == "timed_out"
    assert not report["unsettled_turn"]


def test_extreme_clock_is_rejected_before_command_arithmetic():
    source = fixture()
    source["start_utc"] = "9999-12-31T23:59:59+00:00"
    with pytest.raises(ValueError, match="datetime range"):
        ReplayBundle.model_validate(source)


async def test_recorded_model_cannot_override_action_policy():
    source = fixture()
    source["session"]["capabilities"].append("follow_player")
    source["turns"][0]["action"] = {"action_id": "follow_player"}
    source["steps"] = source["steps"][:4]
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["policy_decisions"] == ["consent_required"]
    assert all(c["kind"] != "action.execute" for c in report["commands"])


async def test_replay_refuses_unrecorded_model_calls():
    source = fixture()
    source["turns"] = []
    source["steps"] = source["steps"][:4]
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["missing_responses"] == 1
    assert report["cloud_calls"] == 0
    assert all(c["kind"] != "action.execute" for c in report["commands"])


async def test_replay_uses_real_schema_and_event_idempotency():
    source = fixture()
    original = copy.deepcopy(source["steps"][0])
    source["steps"] = [original, copy.deepcopy(original), copy.deepcopy(original)]
    source["steps"][-1]["event"]["payload"]["paused"] = True
    report = await run_replay(ReplayBundle.model_validate(source))
    assert report["steps"][1]["duplicates"] == [True]
    assert report["steps"][2]["http_status"] == 409


def test_bundle_limits_and_sanitization_attestation(tmp_path):
    source = fixture()
    source["sanitized"] = False
    with pytest.raises(ValueError):
        ReplayBundle.model_validate(source)
    source["sanitized"] = True
    source["api_key"] = "not-allowed"
    with pytest.raises(ValueError):
        ReplayBundle.model_validate(source)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_BUNDLE_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_bundle(oversized)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"sanitized":true,"sanitized":true}')
    with pytest.raises(ValueError, match="duplicate"):
        load_bundle(duplicate)


def test_unsettled_turn_cannot_race_virtual_clock():
    source = fixture()
    source["steps"][3] = {"type": "advance", "seconds": 16}
    with pytest.raises(ValueError, match="immediate wait_turn"):
        ReplayBundle.model_validate(source)


@pytest.mark.parametrize("seconds", [-1, 601, float("inf"), float("nan"), True])
def test_clock_advances_are_bounded_and_finite(seconds):
    source = fixture()
    source["steps"] = [{"type": "advance", "seconds": seconds}]
    with pytest.raises(ValueError):
        ReplayBundle.model_validate(source)
