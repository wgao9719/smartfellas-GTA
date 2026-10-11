from types import SimpleNamespace

import pytest

from service.lls.actions import ActionPolicy
from service.lls.protocol import ActionProposal


@pytest.mark.parametrize("action", [
    "follow_player", "stop_following", "go_to_safe_point", "wander_nearby",
    "flee_from_threat", "play_verified_gesture",
])
def test_advertised_p0_actions_are_offered_with_consent(action):
    actor = {
        "exists": True, "alive": True, "distance": 3, "can_move": True,
        "control_policy": "ambient", "ownership": "ambient", "mod_following": True,
        "position": {"x": 0, "y": 0, "z": 0}, "_sampled_at": 100,
    }
    world = dict.fromkeys([
        "online", "mission_active", "cutscene_active", "random_event_active", "paused",
    ], False)
    world["_sampled_at"] = 100
    assert ActionPolicy(clock=lambda: 100).eligible(actor, world, [action], True) == [action]


def test_gesture_policy_is_a_finite_allowlist():
    actor = {
        "exists": True, "alive": True, "distance": 3, "can_move": True,
        "control_policy": "ambient", "ownership": "ambient", "_sampled_at": 100,
    }
    world = {key: False for key in (
        "online", "mission_active", "cutscene_active", "random_event_active", "paused",
    )}
    world["_sampled_at"] = 100
    policy = ActionPolicy(clock=lambda: 100)
    valid = ActionProposal(action_id="play_verified_gesture", arguments={"gesture_id": "wave"})
    assert policy.check(valid, actor, world, ["play_verified_gesture"]).allowed
    invalid = SimpleNamespace(action_id="play_verified_gesture",
                              arguments=SimpleNamespace(gesture_id="dance"))
    decision = policy.check(invalid, actor, world, ["play_verified_gesture"])
    assert not decision.allowed and decision.reason == "gesture_mapping_unverified"


def test_direct_player_phrases_supply_only_bounded_consent():
    actor = {
        "exists": True, "alive": True, "distance": 3, "can_move": True,
        "control_policy": "ambient", "ownership": "ambient", "_sampled_at": 100,
        "position": {"x": 0, "y": 0, "z": 0},
    }
    world = {key: False for key in (
        "online", "mission_active", "cutscene_active", "random_event_active", "paused",
    )}
    world["_sampled_at"] = 100
    policy = ActionPolicy(clock=lambda: 100)
    assert policy.eligible(
        actor, world, ["follow_player"], requested="follow_player"
    ) == ["follow_player"]
    assert policy.eligible(
        actor, world, ["go_to_safe_point"], requested="go_to_safe_point"
    ) == ["go_to_safe_point"]
