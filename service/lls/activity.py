"""Shared ped activity labels. Narrative context only, never eligibility or policy.

The adapter samples movement, the first listed scenario, and a few flags with the
same table. The service turns them into short phrases for the speaking actor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import MappingProxyType

TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "ped_activity.json"
FLAGS = ("on_phone", "swimming", "injured")
LABEL = re.compile(r"^[a-z][a-z_]{0,31}$")


def _load():
    table = json.loads(TABLE_PATH.read_bytes())
    if (
        table.get("schema_version") != 1
        or table.get("grants_capabilities") is not False
        or table.get("verification") != "unverified"
    ):
        raise ValueError("Unsupported ped activity table")
    movements = table["movements"]
    if movements.get("unknown") != "" or not all(
        LABEL.match(k) and isinstance(p, str) for k, p in movements.items()
    ):
        raise ValueError("Invalid activity movements")
    phrases, names = {}, set()
    for entry in [*table["scenarios"], table["other"]]:
        name, label, phrase = entry.get("scenario"), entry["label"], entry["phrase"]
        if name in names or not LABEL.match(label) or not phrase:
            raise ValueError("Invalid activity scenario")
        names.add(name)
        # The wire carries only the label, so one label must mean one phrase.
        if phrases.setdefault(label, phrase) != phrase:
            raise ValueError(f"Activity label has two phrases: {label}")
    if set(table["flags"]) != set(FLAGS):
        raise ValueError("Activity flags must match the snapshot fields")
    return (
        MappingProxyType(movements),
        MappingProxyType(phrases),
        MappingProxyType(table["flags"]),
    )


MOVEMENTS, SCENARIOS, FLAG_PHRASES = _load()


def describe(activity):
    """Short present-tense phrases for an adapter activity sample."""
    if not activity:
        return []
    phrases = []
    scenario = SCENARIOS.get(activity.get("scenario"))
    movement = activity.get("movement")
    if scenario:
        phrases.append(scenario)
    # A scenario such as sitting already implies standing still.
    if MOVEMENTS.get(movement) and not (scenario and movement == "still"):
        phrases.append(MOVEMENTS[movement])
    phrases += [FLAG_PHRASES[flag] for flag in FLAGS if activity.get(flag)]
    return phrases
