"""Player pinning: in-place promotion of a live civilian pedestrian (FR-ID-03).

The actor keeps its identifier, so every provenance and observer check stays valid.
The store promotes the identity first. The adapter then registers the binding.
"""

import asyncio
import hashlib
import math
from datetime import datetime

from fastapi import HTTPException

from .roles import label

PIN_CAPABILITY = "actor_pin"
PIN_INTERVAL_SECONDS = 10
MAX_PINS_PER_SESSION = 8
PIN_TERMINAL = {"completed", "rejected", "expired", "failed", "canceled"}


def default_pin_name(actor_id):
    return "Resident " + hashlib.sha256(actor_id.encode()).hexdigest()[:4]


class PinRuntime:
    def pin_refusal(self, session, actor_id, runtime_instance_id=None):
        """Return a plain-language refusal, or None. The store rechecks the stored kind."""
        if PIN_CAPABILITY not in session.request.manifest.capability_flags:
            return "This game adapter cannot pin pedestrians."
        actor = session.actors.get(actor_id)
        if session.closed or actor is None:
            return "That pedestrian is no longer available."
        if runtime_instance_id not in (None, actor.get("runtime_instance_id")):
            return "That pedestrian changed. Select them again before pinning."
        if not 0 <= self.clock() - actor.get("_sampled_at", -math.inf) <= 5:
            return "That pedestrian's state is out of date. Try again."
        if actor.get("role_class") != "civilian":
            return "Only ordinary residents can be pinned. Detected: " + label(
                actor.get("role_class", "unknown")
            ) + "."
        if actor.get("actor_kind") != "ambient_ephemeral":
            return "Only pedestrians met in this session can be pinned."
        if actor.get("ownership") != "ambient" or actor.get("mission_entity", True):
            return "The game controls this pedestrian, so it cannot be pinned."
        if actor.get("control_policy") != "ambient":
            return "This pedestrian is protected and cannot be pinned."
        if not self.conversation_policy.check(actor, session.world).allowed:
            return "That pedestrian is not available right now."
        if len(session.pins) >= MAX_PINS_PER_SESSION:
            return f"This session already pinned {MAX_PINS_PER_SESSION} pedestrians."
        if self.clock() - session.last_pin_at < PIN_INTERVAL_SECONDS:
            return "Wait a few seconds before pinning another pedestrian."
        return None

    def pin_eligibility(self, session, actor_id):
        if actor_id in session.pins:
            return False, "Already pinned in this session."
        refusal = self.pin_refusal(session, actor_id)
        return refusal is None, refusal

    async def pin_locked(self, session, actor_id, runtime_instance_id=None, *, display_name=None):
        """Promote in place, then queue actor.pin. Return (pin, refusal). Hold session.lock."""
        pin = session.pins.get(actor_id)
        if pin is not None:
            return pin, None
        refusal = self.pin_refusal(session, actor_id, runtime_instance_id)
        if refusal:
            return None, refusal
        name = display_name or default_pin_name(actor_id)
        # No in-flight reply or action may race the binding change.
        self.cancel(session)
        await self.stop_session_work(session)
        if session.task:
            await asyncio.gather(session.task, return_exceptions=True)
        self.command(session, "scene.cancel", None, {"scene_id": None})
        try:
            await self.db(
                "pin_actor",
                session.request.profile_id,
                actor_id,
                session.id,
                display_name=name,
                pinned_at_game_datetime=session.game_datetime,
                branch_id=session.branch_id,
            )
        except ValueError:
            return None, "Only pedestrians met in this session can be pinned."
        command = self.command(
            session,
            "actor.pin",
            actor_id,
            {"display_name": name, "actor_kind": "spawned_persistent"},
        )
        pin = {
            "actor_id": actor_id,
            "display_name": name,
            "command_id": command["id"],
            "state": "pending",
        }
        session.pins[actor_id] = pin
        session.last_pin_at = self.clock()
        session.trace.append({"actor_id": actor_id, "status": "pin_requested"})
        return pin, None

    async def pin_requested_locked(self, session, event):
        pin, refusal = await self.pin_locked(
            session, event.actor_id, event.payload["runtime_instance_id"]
        )
        # Refusals are status lines, never HTTP failures the adapter must interpret.
        if refusal:
            self.pin_status(session, "pin_refused", refusal)
        elif pin["state"] == "embodied":
            self.pin_status(session, "pinned", f"{pin['display_name']} is already pinned.")
        elif pin["state"] != "pending":
            self.pin_status(session, "pin_unavailable", "That pedestrian is no longer available.")

    def pin_status(self, session, state, message):
        # No actor binding: a refused target may be protected, which would block the line.
        self.command(session, "status", None, {"state": state, "message": message})

    async def acknowledge_pin_locked(self, session, command, ack):
        if command["state"] in PIN_TERMINAL:
            return {"id": command["id"], "status": command["state"], "duplicate": True}
        status = ack.status
        if status == "completed" and (ack.observed or {}).get("actor_id") != command["actor_id"]:
            raise HTTPException(422, "pin completion must name its actor")
        if datetime.fromisoformat(command["expires_at"]) <= self.utc_now():
            status = "expired"
        if status in PIN_TERMINAL:
            command["state"], command["reason"] = status, ack.reason
            self.settle_pin(session, command)
        session.changed.set()
        return {"id": command["id"], "status": command["state"], "duplicate": False}

    def settle_pin(self, session, command):
        """Report a terminal actor.pin outcome exactly once."""
        pin = session.pins.get(command["actor_id"])
        if (
            pin is None
            or pin["command_id"] != command["id"]
            or pin["state"] != "pending"
            or command["state"] not in PIN_TERMINAL
        ):
            return
        name = pin["display_name"]
        if command["state"] == "completed":
            pin["state"] = "embodied"
            self.pin_status(
                session,
                "pinned",
                f"Pinned as {name}. This conversation will be remembered. "
                "They may not return after you leave.",
            )
            return
        # The identity is saved. This engine entity is no longer its embodiment.
        pin["state"] = "unembodied"
        session.actors.pop(command["actor_id"], None)
        self.cancel(session)
        self.pin_status(
            session,
            "pin_unavailable",
            f"Saved as {name}, but this pedestrian is no longer available.",
        )

    def forget_pinned_locked(self, session, actor_id):
        """Drop live state for a forgotten identity. Later snapshots for it are ignored."""
        if actor_id in session.actors or actor_id in session.pins:
            session.actors.pop(actor_id, None)
            pin = session.pins.setdefault(
                actor_id, {"actor_id": actor_id, "display_name": "", "command_id": None}
            )
            pin["state"] = "forgotten"

    @staticmethod
    def pin_detached(session, actor_id):
        pin = session.pins.get(actor_id)
        return pin is not None and pin["state"] in {"unembodied", "forgotten"}
