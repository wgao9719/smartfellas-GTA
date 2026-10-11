"""Deterministic authorization. Capabilities cannot be supplied by model text."""

import time
from dataclasses import dataclass
from math import dist

from .action_registry import ACTION_DESCRIPTIONS as ACTION_DESCRIPTIONS
from .action_registry import ACTION_IDS
from .protocol import ActionProposal

POLICY_VERSION = "1"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


class ActionPolicy:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock

    def eligible(self, actor, world, capabilities, consent=False):
        eligible = []
        for action in capabilities:
            if action not in ACTION_IDS:
                continue
            # Targeted movement and animations require additional engine proofs.
            if action in {"go_to_safe_point", "play_verified_gesture"}:
                continue
            proposal = ActionProposal(action_id=action)
            if self.check(proposal, actor, world, capabilities, consent).allowed:
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
        if proposal.action_id == "flee_from_threat" and not actor.get("observed_threat", False):
            return Decision(False, "no_observed_threat")
        if proposal.action_id == "play_verified_gesture":
            return Decision(False, "gesture_mapping_unverified")
        if proposal.action_id == "go_to_safe_point":
            point = proposal.arguments.point
            if (
                not point
                or dist(tuple(actor["position"].values()), (point.x, point.y, point.z)) > 15
            ):
                return Decision(False, "point_outside_local_area")
            return Decision(False, "navigation_unverified")
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
