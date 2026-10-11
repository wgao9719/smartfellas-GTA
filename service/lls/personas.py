"""Generated personas for ambient pedestrians.

Each draw is a pure function of the actor id and the names already in use, so replay
and re-registration agree for the same stored actors. The ped model supplies only
presentation, age band, clothing, and whether the person is local or visiting. The
persona is narrative background. It never grants capabilities or establishes world facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .display import is_display_safe
from .roles import MODEL_NAMES, joaat

TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "ambient_personas.json"
POOLS = ("last_names", "personalities", "speech_styles", "motivations", "quirks", "moods")
MODEL_PATTERN = re.compile(r"^[as]_([mf])_([ymo])_([a-z]+?)(?:_?\d+)?$")
PRESENTATION = {"m": "man", "f": "woman"}
AGE = {"y": "young", "m": "middle-aged", "o": "older"}
# Follows the background's sentence about where the person lives.
DISCLAIMER = (
    "These details are improvised background, not established world facts. The player "
    "knows only display_name until you share your name."
)


def _strings(value, name):
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item and is_display_safe(item) for item in value)
    ):
        raise ValueError(f"Invalid persona pool: {name}")
    return tuple(value)


def _backgrounds(table, pools, occupations):
    """Return ({key: (bio, excludes, adds)}, {archetype: key}). Local is the default."""
    backgrounds, settings = {}, {}
    traits = set().union(*pools.values())
    for key, entry in table["backgrounds"].items():
        excludes = frozenset(_strings(entry["excludes"], key) if "excludes" in entry else ())
        adds = {field: _strings(items, key) for field, items in entry.get("adds", {}).items()}
        if not excludes <= traits or not adds.keys() <= set(POOLS) - {"last_names"}:
            raise ValueError(f"Background excludes or adds an unknown trait: {key}")
        if any(not set(pools[f]) - excludes and not adds.get(f) for f in POOLS):
            raise ValueError(f"Background leaves an empty trait pool: {key}")
        for archetype in entry.get("archetypes", []):
            if archetype not in occupations or settings.setdefault(archetype, key) != key:
                raise ValueError(f"Invalid background archetype: {archetype}")
        backgrounds[key] = (_strings([entry["bio"]], key)[0], excludes, adds)
    if "local" not in backgrounds or "local" in settings.values():
        raise ValueError("Persona table needs a default local background")
    return backgrounds, settings


def _load():
    table = json.loads(TABLE_PATH.read_bytes())
    if table.get("schema_version") != 1 or not isinstance(table.get("table_version"), str):
        raise ValueError("Unsupported ambient persona table")
    names = {key: _strings(table["first_names"][key], key) for key in ("female", "male", "neutral")}
    pools = {key: _strings(table[key], key) for key in POOLS}
    occupations = {key: _strings(items, key) for key, items in table["occupations"].items()}
    looks = table["looks"]
    tags = {}
    for archetype, items in table["model_archetypes"].items():
        if archetype not in occupations or not isinstance(looks.get(archetype), str):
            raise ValueError(f"Persona archetype lacks occupations or look: {archetype}")
        for tag in _strings(items, archetype):
            if tags.setdefault(tag, archetype) != archetype:
                raise ValueError(f"Duplicate persona model tag: {tag}")
    if not {"general", "older"} <= occupations.keys():
        raise ValueError("Persona table needs general and older occupations")
    longest = max(len(f) for f in (*names["female"], *names["male"], *names["neutral"]))
    if longest + 1 + max(map(len, pools["last_names"])) > 48:
        raise ValueError("Generated names must fit a 48-character display name")
    backgrounds, settings = _backgrounds(table, pools, occupations)
    traits = set().union(
        *pools.values(),
        *occupations.values(),
        *(items for _, _, adds in backgrounds.values() for items in adds.values()),
    )
    clashes = set()
    for pair in table["clashes"]:
        if not isinstance(pair, list) or len(set(pair)) != 2 or not set(pair) <= traits:
            raise ValueError(f"Invalid trait clash: {pair}")
        clashes.add(frozenset(pair))
    return (
        table["table_version"],
        names,
        pools,
        occupations,
        looks,
        tags,
        backgrounds,
        settings,
        frozenset(clashes),
    )


(
    TABLE_VERSION,
    _NAMES,
    _POOLS,
    _OCCUPATIONS,
    _LOOKS,
    _TAGS,
    _BACKGROUNDS,
    _SETTINGS,
    _CLASHES,
) = _load()
# Every targetable ped has a civilian model, so its name is recoverable from the hash.
MODEL_BY_HASH = {joaat(name): name for name, role in MODEL_NAMES.items() if role == "civilian"}


def describe_model(model_hash):
    """Return (presentation, age, archetype) from a civilian model name, or Nones."""
    match = MODEL_PATTERN.match(MODEL_BY_HASH.get(model_hash, ""))
    if match is None:
        return None, None, None
    gender, age, tag = match.groups()
    return PRESENTATION[gender], AGE[age], _TAGS.get(tag)


def _index(actor_id, field, count):
    digest = hashlib.sha256(f"{TABLE_VERSION}\0{actor_id}\0{field}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


def ambient_persona(actor_id, model_hash=None, taken_names=frozenset()):
    """Draw a persona whose name avoids taken_names whenever a free name remains."""
    presentation, age, archetype = describe_model(model_hash)
    bio, excludes, adds = _BACKGROUNDS[_SETTINGS.get(archetype, "local")]
    chosen = []

    def pool(key):
        return _POOLS[key] + adds.get(key, ())

    # Later traits skip anything already chosen or listed as clashing with it.
    def pick(field, items):
        options = [
            item
            for item in items
            if item not in excludes
            and item not in chosen
            and not any(frozenset((item, other)) in _CLASHES for other in chosen)
        ]
        chosen.append(options[_index(actor_id, field, len(options))])
        return chosen[-1]

    gender = {"man": "male", "woman": "female"}.get(presentation)
    first_names = (_NAMES[gender] if gender else ()) + _NAMES["neutral"]
    names = [f"{first} {last}" for first in first_names for last in _POOLS["last_names"]]
    start = _index(actor_id, "name", len(names))
    name = next((n for n in names[start:] + names[:start] if n not in taken_names), names[start])
    occupations = _OCCUPATIONS[archetype or "general"]
    if age == "older":
        occupations += _OCCUPATIONS["older"]
    occupation = pick("occupation", occupations)
    personality = pick("personality", pool("personalities"))
    motivation = pick("motivation", pool("motivations"))
    persona = {
        "name": name,
        "occupation": occupation,
        "short_bio": f"{occupation}. {personality}.",
        "long_bio": f"{bio} {DISCLAIMER}",
        "personality": personality,
        "speech_style": pick("speech_style", pool("speech_styles")),
        "motivations": [motivation, pick("motivation_2", pool("motivations"))],
        "quirk": pick("quirk", pool("quirks")),
        "mood": pick("mood", pool("moods")),
        "generated_from": f"ambient_personas:{TABLE_VERSION}",
    }
    if presentation:
        look = _LOOKS.get(archetype)
        persona["appearance"] = f"{age} {presentation}" + (f" {look}" if look else "")
    return persona


def names_in_use(actors):
    """Display and persona names of stored actors, which a new persona must not reuse."""
    names = set()
    for actor in actors:
        persona = actor.get("persona")
        names.add(actor.get("display_name"))
        names.add(persona.get("name") if isinstance(persona, dict) else None)
    return {name for name in names if isinstance(name, str)}


def persona_name(actor):
    """A generated persona's name when it can be a display name, else None."""
    name = ((actor or {}).get("persona") or {}).get("name")
    return name if isinstance(name, str) and 0 < len(name) <= 48 and is_display_safe(name) else None
