"""Deterministic authorization. Capabilities cannot be supplied by model text."""

import time
from dataclasses import dataclass
from math import dist

from .action_registry import ACTION_DESCRIPTIONS as ACTION_DESCRIPTIONS
from .action_registry import ACTION_IDS

POLICY_VERSION = "1"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


# Native implementations remain opt-in development capabilities. Metadata alone
# never enables them. These sets also keep parameterized actions selectable.
CONSENT_ACTIONS = frozenset({
    "follow_player", "go_to_safe_point", "run_to_point", "wander_nearby",
    "enter_vehicle", "leave_vehicle", "drive_to_point", "cruise_vehicle",
    "fight_target", "shoot_at_target",
})
VEHICLE_ACTIONS = frozenset({"leave_vehicle", "drive_to_point", "cruise_vehicle"})
POINT_ACTIONS = frozenset({"go_to_safe_point", "run_to_point", "drive_to_point"})
TARGET_ACTIONS = frozenset({"look_at", "enter_vehicle", "fight_target", "shoot_at_target"})
STATIONARY_ACTIONS = frozenset({
    "face_player", "wait_here", "look_at", "hands_up", "cower", "play_verified_gesture",
    "stop_following",
})


class ActionPolicy:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock

    def eligible(self, actor, world, capabilities, consent=False):
        return [action for action in capabilities if action in ACTION_IDS
                and self.availability(action, actor, world, capabilities, consent).allowed]

    def availability(self, action, actor, world, capabilities, consent=False):
        presence = check_presence(actor, world, clock=self.clock)
        if not presence.allowed:
            return presence
        if actor.get("control_policy") != "ambient" or actor.get("ownership") not in {
            "ambient", "mod_owned",
        }:
            return Decision(False, "protected_actor")
        if actor.get("mission_entity") and actor.get("ownership") != "mod_owned":
            return Decision(False, "game_script_owned")
        if action not in ACTION_IDS or action not in capabilities:
            return Decision(False, "capability_unavailable")
        if action in CONSENT_ACTIONS and not consent:
            return Decision(False, "consent_required")
        if actor.get("distance", 1000) > 15:
            return Decision(False, "actor_out_of_range")
        if action in VEHICLE_ACTIONS:
            if not actor.get("in_vehicle"):
                return Decision(False, "not_in_vehicle")
            if action == "leave_vehicle" and not actor.get("vehicle_stopped"):
                return Decision(False, "vehicle_moving")
            if action != "leave_vehicle" and not actor.get("is_driver"):
                return Decision(False, "not_driver")
        elif actor.get("in_vehicle"):
            return Decision(False, "actor_in_vehicle")
        elif action not in STATIONARY_ACTIONS and not actor.get("can_move", True):
            return Decision(False, "actor_cannot_move")
        if action == "stop_following" and not actor.get("mod_following", False):
            return Decision(False, "no_mod_follow_task")
        targets = actor.get("action_context", {}).get("targets", [])
        if action == "flee_from_threat" and not any(t["is_threat"] for t in targets):
            return Decision(False, "no_observed_threat")
        if action in TARGET_ACTIONS:
            kind = "vehicle" if action == "enter_vehicle" else "ped"
            if not any(t["kind"] == kind for t in targets):
                return Decision(False, "no_visible_target")
        if action == "shoot_at_target" and not actor.get("has_firearm"):
            return Decision(False, "no_equipped_firearm")
        return Decision(True, "authorized")

    def check(self, proposal, actor, world, capabilities, consent=False):
        action = proposal.action_id
        available = self.availability(action, actor, world, capabilities, consent)
        if not available.allowed:
            return available
        args = proposal.arguments
        if args.duration_seconds is not None and args.duration_seconds > (
            5 if action in {"face_player", "wait_here"} else 10
        ):
            return Decision(False, "invalid_duration")
        if action == "play_verified_gesture" and args.gesture_id != "wave":
            return Decision(False, "unknown_gesture")
        if action in POINT_ACTIONS:
            point = args.point
            if not point or dist(
                tuple(actor["position"][k] for k in ("x", "y", "z")),
                (point.x, point.y, point.z),
            ) > 15:
                return Decision(False, "point_outside_local_area")
        if action in TARGET_ACTIONS or action == "flee_from_threat":
            target_id = args.threat_actor_id if action == "flee_from_threat" else args.target_id
            target = next((t for t in actor.get("action_context", {}).get("targets", [])
                           if t["target_id"] == target_id), None)
            kind = "vehicle" if action == "enter_vehicle" else "ped"
            if not target or target["kind"] != kind:
                return Decision(False, "target_unavailable")
            if action == "flee_from_threat" and not target["is_threat"]:
                return Decision(False, "no_observed_threat")
            if action == "enter_vehicle" and not target.get(args.seat + "_seat_free", False):
                return Decision(False, "seat_unavailable")
        return Decision(True, "authorized")


def check_presence(actor, world, *, clock=time.monotonic):
    if world and not 0 <= clock() - world.get("_sampled_at", 0) <= 5:
        return Decision(False, "stale_world")
    if actor and not 0 <= clock() - actor.get("_sampled_at", 0) <= 5:
        return Decision(False, "stale_actor")
    if not world or any(
        world.get(k, True)
        for k in (
            "online",
            "mission_active",
            "cutscene_active",
            "random_event_active",
            "paused",
        )
    ):
        return Decision(False, "unsafe_or_unknown_world")
    if not actor or not actor.get("exists") or not actor.get("alive"):
        return Decision(False, "actor_unavailable")
    if actor.get("busy") or actor.get("in_combat") or actor.get("distance", 1000) > 20:
        return Decision(False, "actor_busy_or_distant")
    return Decision(True, "available")


class ConversationPolicy:
    """Conversation permission is independent from physical task availability."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock

    def check(self, actor, world):
        presence = check_presence(actor, world, clock=self.clock)
        if not presence.allowed:
            return presence
        if actor.get("control_policy") == "service_limited":
            if (
                actor.get("actor_kind") == "anchored_service"
                and actor.get("ownership") == "game_script_owned"
                and actor.get("_service_authorized") is True
            ):
                return Decision(True, "verified_service_conversation")
            return Decision(False, "service_integration_unverified")
        if actor.get("control_policy") != "ambient":
            return Decision(False, "protected_actor")
        if actor.get("ownership") not in {"ambient", "mod_owned"}:
            return Decision(False, "unknown_or_script_owned_actor")
        if actor.get("mission_entity") and actor.get("ownership") != "mod_owned":
            return Decision(False, "game_script_owned")
        return Decision(True, "conversation_available")
