"""Recipient-owned ambient decisions and authored zero-request small talk."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .storage import _date

BANK = json.loads(
    (Path(__file__).resolve().parents[2] / "data" / "ambient_smalltalk.json").read_text()
)


def decision(context, activity, game_datetime):
    """Only the owner's traits and retained experience affect its choice."""
    persona = json.loads(context["actor"]["persona"])
    social = context["social"]
    phase = social["phase"]
    activity = activity.model_dump() if activity else {}
    unavailable = any(activity.get(k) for k in ("on_phone", "swimming", "injured"))
    unavailable |= activity.get("movement") in {"running", "sprinting"}
    experience = social["experience"]
    boundary = experience.get("own_last_boundary_at")
    if boundary:
        age = (datetime.fromisoformat(_date(game_datetime.isoformat()))
               - datetime.fromisoformat(boundary)).total_seconds()
        unavailable |= 0 <= age < 600
    traits = (str(persona.get("personality", "")) + " " + str(persona.get("mood", ""))).lower()
    private = any(word in traits for word in ("guarded", "private", "suspicious", "impatient"))
    familiar = any(c["confirmed_scene_count"] >= 2 for c in experience["contacts"].values())
    unavailable |= phase == "invitation" and private and not familiar
    return ("refuse" if unavailable else "accept") if phase == "invitation" else (
        "withdraw" if unavailable else "continue"
    )


def smalltalk(context):
    social = context["social"]
    if social["phase"] != "turn":
        return json.dumps({"decision": "refuse" if social["phase"] == "invitation" else "withdraw"})
    if social.get("retell_text"):
        return social["retell_text"]
    # A paired exchange keeps the fallback coherent; no invented external events.
    seed = hashlib.sha256(social["scene_id"].encode()).digest()[0]
    exchange = BANK[seed % len(BANK)]
    return exchange[min(social.get("turn_index", 0), len(exchange) - 1)]
