import copy
import json
from typing import get_args

import pytest

from service.lls.action_registry import (
    ACTION_DESCRIPTIONS,
    ACTION_IDS,
    REGISTRY_PATH,
    action_metadata,
    action_schema,
    validate_arguments,
)
from service.lls.protocol import ActionId


def test_registry_matches_protocol_and_never_activates_native_capabilities():
    registry = json.loads(REGISTRY_PATH.read_text())
    assert ACTION_IDS == set(get_args(ActionId))
    assert set(ACTION_DESCRIPTIONS) == ACTION_IDS
    assert registry["grants_capabilities"] is False
    for name in ACTION_IDS:
        executor = action_metadata(name)["executor"]
        assert executor["validation"] == "unverified"
        assert executor["verified_game_builds"] == []
        assert action_metadata(name)["preconditions"]


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("face_player", {"duration_seconds": 0.1}),
        ("wait_here", {"duration_seconds": 30}),
        ("follow_player", {"max_follow_meters": 15}),
        ("stop_following", {}),
        ("go_to_safe_point", {"point": {"x": 0, "y": 1, "z": -2}}),
        ("wander_nearby", {"radius": 20}),
        ("flee_from_threat", {"threat_actor_id": "actor-1"}),
        ("play_verified_gesture", {"gesture_id": "wave"}),
        ("flee_from_threat", {"threat_actor_id": "😀" * 128}),
    ],
)
def test_finite_argument_contract(action, arguments):
    validate_arguments(action, arguments)


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("native_hash", {}),
        ("face_player", {"duration_seconds": True}),
        ("face_player", {"duration_seconds": "1"}),
        ("face_player", {"duration_seconds": 0}),
        ("face_player", {"duration_seconds": float("nan")}),
        ("face_player", {"duration_seconds": float("inf")}),
        ("face_player", {"duration_seconds": 10**500}),
        ("face_player", {"native_hash": 123}),
        ("stop_following", {"duration_seconds": 1}),
        ("play_verified_gesture", {}),
        ("go_to_safe_point", {"point": {"x": 0, "y": 1}}),
        ("go_to_safe_point", {"point": {"x": 999999, "y": 1, "z": 0}}),
        ("play_verified_gesture", {"gesture_id": ""}),
        ("play_verified_gesture", {"gesture_id": "a" * 129}),
    ],
)
def test_invalid_argument_contract(action, arguments):
    with pytest.raises(ValueError):
        validate_arguments(action, arguments)


def test_prompt_schema_copy_cannot_mutate_authoritative_bounds():
    schema = action_schema("face_player")
    original = copy.deepcopy(schema)
    schema["properties"]["duration_seconds"]["maximum"] = 1_000_000
    assert action_schema("face_player") == original
    with pytest.raises(ValueError):
        validate_arguments("face_player", {"duration_seconds": 1000})
