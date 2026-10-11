"""Shared narrative metadata and bounded argument schemas, never native authority."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "action_registry.json"
_RAW = REGISTRY_PATH.read_bytes()
REGISTRY_SHA256 = hashlib.sha256(_RAW).hexdigest()
_REGISTRY = json.loads(_RAW)
if _REGISTRY.get("schema_version") != 1 or _REGISTRY.get("grants_capabilities") is not False:
    raise ValueError("Unsupported action metadata registry")
REGISTRY_VERSION = _REGISTRY["registry_version"]
_ACTIONS = {item["id"]: item for item in _REGISTRY["actions"]}
if len(_ACTIONS) != len(_REGISTRY["actions"]) or not 1 <= len(_ACTIONS) <= 64:
    raise ValueError("Invalid or duplicate action registry IDs")
ACTION_IDS = frozenset(_ACTIONS)
ACTION_DESCRIPTIONS = MappingProxyType({key: item["description"] for key, item in _ACTIONS.items()})


def action_schema(action_id: str) -> dict:
    """Return a copy so prompt construction cannot change future validation."""
    if action_id not in _ACTIONS:
        raise ValueError("Unknown action ID")
    return copy.deepcopy(_ACTIONS[action_id]["arguments"])


def action_metadata(action_id: str) -> dict:
    if action_id not in _ACTIONS:
        raise ValueError("Unknown action ID")
    return copy.deepcopy(_ACTIONS[action_id])


def _validate(schema, value, path):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema["properties"]
        if schema.get("additionalProperties") is not False:
            raise ValueError("Registry objects must reject additional properties")
        if set(value) - set(properties):
            raise ValueError(f"{path} contains unsupported arguments")
        if not set(schema["required"]) <= set(value):
            raise ValueError(f"{path} is missing required arguments")
        for key, child in value.items():
            _validate(properties[key], child, path + "." + key)
    elif kind == "number":
        try:
            finite = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
            )
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{path} must be a finite number")
        if not schema["minimum"] <= value <= schema["maximum"]:
            raise ValueError(f"{path} is outside the permitted range")
    elif kind == "string":
        if (
            not isinstance(value, str)
            or not schema["minLength"] <= len(value) <= schema["maxLength"]
        ):
            raise ValueError(f"{path} must be a bounded string")
    else:
        raise ValueError("Unsupported registry schema primitive")


def validate_arguments(action_id: str, arguments: dict) -> None:
    """Structural validation only. The current policy and adapter still authorize execution."""
    _validate(action_schema(action_id), arguments, "arguments")
