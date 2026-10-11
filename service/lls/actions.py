"""Deterministic authorization. Capabilities cannot be supplied by model text."""

import re
import time
from dataclasses import dataclass
from math import dist

from .action_registry import ACTION_DESCRIPTIONS as ACTION_DESCRIPTIONS
from .action_registry import ACTION_IDS
from .protocol import ActionProposal

POLICY_VERSION = "1"
VERIFIED_GESTURES = frozenset({"wave", "shrug", "point"})


def requested_action(text):
    """Narrow direct requests supply consent only for the named bounded action."""
    text = text.strip().lower().rstrip(".!?")
    text = re.sub(r"^(?:please |(?:can|could|would) you )", "", text)
    text = re.sub(r"^(?:please )|(?: please)$", "", text)
    return {
        "follow me": "follow_player", "come with me": "follow_player",
        "stop following": "stop_following", "stop following me": "stop_following",
        "wait here": "wait_here", "look at me": "face_player", "face me": "face_player",
        "go somewhere safe": "go_to_safe_point", "go to a safe spot": "go_to_safe_point",
        "wander nearby": "wander_nearby", "walk around nearby": "wander_nearby",
        "get out of here": "flee_from_threat", "move away from me": "flee_from_threat",
        "wave": "play_verified_gesture", "shrug": "play_verified_gesture",
        "point": "play_verified_gesture",
    }.get(text)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


class ActionPolicy:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock

    def eligible(self, actor, world, capabilities, consent=False, requested=None):
        eligible = []
        for action in capabilities:
            if action not in ACTION_IDS:
                continue
            proposal = ActionProposal(
                action_id=action,
                arguments={"gesture_id": "wave"} if action == "play_verified_gesture" else {},
            )
            decision = self.check(
                proposal, actor, world, capabilities, consent or action == requested
            )
            if decision.allowed:
                eligible.append(action)
        return eligible

    def check(self, proposal, actor, world, capabilities, consent=False):
        presence = check_presence(actor, world, clock=self.clock)
        if not presence.allowed:
            return presence
        if actor.get("control_policy") != "ambient" or actor.get("ownership") not in {
            "ambient",
            "mod_owned",
        }:
            return Decision(False, "protected_actor")
        if actor.get("mission_entity") and actor.get("ownership") != "mod_owned":
            return Decision(False, "game_script_owned")
        if proposal.action_id not in capabilities:
            return Decision(False, "capability_unavailable")
        if (
            proposal.action_id in {"follow_player", "go_to_safe_point", "wander_nearby"}
            and not consent
        ):
            return Decision(False, "consent_required")
        if proposal.action_id not in {
            "face_player",
            "wait_here",
            "play_verified_gesture",
        } and not actor.get("can_move", True):
            return Decision(False, "actor_cannot_move")
        if proposal.action_id == "stop_following" and not actor.get("mod_following", False):
            return Decision(False, "no_mod_follow_task")
        if (proposal.action_id == "flee_from_threat" and not consent
                and not actor.get("observed_threat", False)):
            return Decision(False, "no_observed_threat")
        if proposal.action_id == "flee_from_threat":
            threat = proposal.arguments.threat_position
            if threat and proposal.arguments.threat_actor_id:
                return Decision(False, "ambiguous_threat")
            if threat and dist(tuple(actor["position"][k] for k in ("x", "y", "z")),
                               (threat.x, threat.y, threat.z)) > 15:
                return Decision(False, "threat_outside_local_area")
        if (
            proposal.action_id == "play_verified_gesture"
            and proposal.arguments.gesture_id not in VERIFIED_GESTURES
        ):
            return Decision(False, "gesture_mapping_unverified")
        if proposal.action_id == "go_to_safe_point":
            point = proposal.arguments.point
            if (
                point
                and dist(tuple(actor["position"][k] for k in ("x", "y", "z")),
                         (point.x, point.y, point.z)) > 15
            ):
                return Decision(False, "point_outside_local_area")
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
