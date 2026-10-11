"""Shared pedestrian role evidence. Labels can only restrict control, never grant it.

The adapter classifies sampled game facts with the same table. The service
recomputes the role from the snapshot's facts and keeps the stricter answer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "ped_roles.json"
ROLES = (
    "civilian",
    "service_worker",
    "police",
    "military",
    "security",
    "emergency",
    "gang",
    "prisoner",
    "animal",
    "protagonist",
    "story_character",
    "unknown",
)
LABELS = MappingProxyType(
    {
        "civilian": "resident",
        "service_worker": "service worker",
        "police": "police officer",
        "military": "military personnel",
        "security": "security guard",
        "emergency": "emergency worker",
        "gang": "gang member",
        "prisoner": "detained person",
        "animal": "animal",
        "protagonist": "protagonist",
        "story_character": "story character",
        "unknown": "unrecognized pedestrian",
    }
)


def joaat(text: str) -> int:
    """GET_HASH_KEY as a signed int32: one-at-a-time with ASCII lowercasing."""
    value = 0
    for byte in text.encode("utf-8"):
        value = (value + (byte + 32 if 65 <= byte <= 90 else byte)) & 0xFFFFFFFF
        value = (value + (value << 10)) & 0xFFFFFFFF
        value ^= value >> 6
    value = (value + (value << 3)) & 0xFFFFFFFF
    value ^= value >> 11
    value = (value + (value << 15)) & 0xFFFFFFFF
    return value - (1 << 32) if value >= 1 << 31 else value


def _load():
    raw = TABLE_PATH.read_bytes()
    table = json.loads(raw)
    if (
        table.get("schema_version") != 1
        or table.get("grants_capabilities") is not False
        or table.get("verification") != "unverified"
        or not isinstance(table.get("table_version"), str)
    ):
        raise ValueError("Unsupported ped role table")
    policies = table["role_policies"]
    if set(policies) != set(ROLES) or any(
        policy != ("ambient" if role == "civilian" else "protected")
        for role, policy in policies.items()
    ):
        raise ValueError("Only civilians may receive the ambient policy")
    sections = {}
    for section, key in (
        ("ped_types", int),
        ("relationship_groups", joaat),
        ("models", joaat),
    ):
        mapping = {}
        for role, items in table[section].items():
            if role not in ROLES or role == "unknown" or not isinstance(items, list):
                raise ValueError("Invalid role table entry")
            for item in items:
                if section == "ped_types" and (type(item) is not int or not 0 <= item <= 255):
                    raise ValueError("Invalid ped type")
                if key(item) in mapping:
                    raise ValueError("Duplicate role table key")
                mapping[key(item)] = role
        sections[section] = MappingProxyType(mapping)
    return table, hashlib.sha256(raw).hexdigest(), sections


_TABLE, TABLE_SHA256, _SECTIONS = _load()
_GROUPS = _SECTIONS["relationship_groups"]
TABLE_VERSION = _TABLE["table_version"]
ROLE_POLICIES = MappingProxyType(dict(_TABLE["role_policies"]))
MODEL_NAMES = MappingProxyType(
    {name: role for role, names in _TABLE["models"].items() for name in names}
)


def label(role: str) -> str:
    return LABELS.get(role, LABELS["unknown"])


def ownership_from_facts(facts: dict | None) -> tuple[str, str | None]:
    """Mirror the adapter's §11.4 ownership rule. Missing facts are unknown."""
    if not facts:
        return "unknown", "facts_unavailable"
    if facts.get("in_mod_registry"):
        ours = (
            facts.get("belongs_to_mod_script")
            if facts.get("took_persistence")
            else not facts.get("mission_entity", True)
        )
        return ("mod_owned", None) if ours else (
            "game_script_owned",
            "registered_ped_taken_by_script",
        )
    if facts.get("mission_entity", True):
        return "game_script_owned", "mission_entity"
    if facts.get("owning_script"):
        return "game_script_owned", "owning_script"
    return "ambient", None


def role_from_facts(facts: dict | None) -> tuple[str, str]:
    """Return (role, reason). Civilian requires model, ped type, and group agreement."""
    if not facts:
        return "unknown", "facts_unavailable"
    if facts.get("animal_model", True) or not facts.get("human", False):
        return "animal", "animal"
    if facts.get("cuffed", True):
        return "prisoner", "cuffed"
    if facts.get("player_group", True):
        return "story_character", "player_group_member"
    signals = (
        ("model", _SECTIONS["models"].get(facts.get("model_hash"))),
        ("ped_type", _SECTIONS["ped_types"].get(facts.get("ped_type"))),
        ("relationship_group", _GROUPS.get(facts.get("relationship_group_hash"))),
    )
    for name, role in signals:
        if role not in (None, "civilian"):
            return role, f"{name}_{role}"
    if facts.get("in_police_vehicle", True):
        return "prisoner", "in_police_vehicle"
    for name, role in signals:
        if role is None:
            return "unknown", f"{name}_unlisted"
    return "civilian", "civilian_evidence"


def classify(facts: dict | None) -> dict:
    """The adapter's full classification: role, ownership, policy, and reason."""
    role, reason = role_from_facts(facts)
    ownership, owner_reason = ownership_from_facts(facts)
    protected = ownership in {"game_script_owned", "unknown"}
    return {
        "role": role,
        "ownership": ownership,
        "policy": "protected" if protected else ROLE_POLICIES[role],
        "reason": owner_reason if protected else reason,
    }


def revalidate(payload: dict) -> tuple[str, str]:
    """Keep the stricter of the adapter's role and the role recomputed from its facts."""
    role, reason = role_from_facts(payload.get("ped_facts"))
    claimed = payload.get("role_class", "unknown")
    if claimed in (role, "civilian") or role != "civilian":
        return role, reason
    return claimed, payload.get("role_reason") or "adapter_reported"
