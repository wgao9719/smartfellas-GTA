"""Validate original character packs without asserting unverified game facts."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from .action_registry import ACTION_IDS
from .storage import Repository

PILOT_PACK_PATH = Path(__file__).resolve().parents[2] / "data" / "pilot_pack.json"
# Source checkout places data beside service. Installed distributions can pass
# an explicit pack path instead of relying on the checkout location.
if not PILOT_PACK_PATH.exists():
    PILOT_PACK_PATH = Path(__file__).resolve().parents[2].parent / "data" / "pilot_pack.json"

ALLOWED_ACTIONS = ACTION_IDS
REQUIRED_PROFILE_FIELDS = {
    "id",
    "display_name",
    "occupation",
    "district",
    "short_bio",
    "long_bio",
    "speech_style",
    "motivations",
    "taboo_topics",
    "starting_relationship",
    "relationships",
    "voice",
    "spawn_windows",
    "allowed_actions",
    "flags",
}


def validate_pilot_pack(pack: dict) -> dict:
    if pack.get("schema_version") != 1:
        raise ValueError("unsupported pilot pack schema")
    actors = pack.get("actors", [])
    if not 12 <= len(actors) <= 20:
        raise ValueError("pilot pack must contain 12 to 20 actors")
    ids, names, voices = set(), set(), set()
    for actor in actors:
        if missing := REQUIRED_PROFILE_FIELDS - actor.keys():
            raise ValueError(f"incomplete actor profile: {sorted(missing)}")
        UUID(actor["id"])
        if actor["id"] in ids or actor["display_name"] in names:
            raise ValueError("pilot identities and display names must be unique")
        ids.add(actor["id"])
        names.add(actor["display_name"])
        if actor.get("actor_kind") != "curated_persistent":
            raise ValueError("pilot actors must have curated persistent identities")
        if not set(actor["allowed_actions"]) <= ALLOWED_ACTIONS:
            raise ValueError("pilot pack names an unsupported action")
        voice = actor["voice"]
        if (
            voice.get("status") != "unverified_placeholder"
            or voice.get("asset") is not None
            or voice.get("real_person_imitation") is not False
        ):
            raise ValueError(
                "pilot voice placeholders must not imply verified assets or impersonation"
            )
        if voice["voice_id"] in voices:
            raise ValueError("each pilot actor requires a stable distinct voice choice")
        voices.add(voice["voice_id"])
        for window in actor["spawn_windows"]:
            if not 0 <= window["start_hour"] < window["end_hour"] <= 24:
                raise ValueError("invalid spawn window")
            if (
                window.get("coordinates") is not None
                or window.get("status") != "pending_windows_rig_validation"
            ):
                raise ValueError("pilot locations require explicit Windows rig validation")
    if pack.get("runtime_spawn_enabled") is not False:
        raise ValueError("unverified pilot pack cannot enable runtime spawning")
    if len({actor["occupation"] for actor in actors}) < 3:
        raise ValueError("pilot pack needs three distinct occupations")
    if len({actor.get("persona", {}).get("view_on_neighborhood_change") for actor in actors}) < 3:
        raise ValueError("pilot pack needs three distinct neighborhood views")
    relationships = set()
    for actor in actors:
        for relationship in actor["relationships"]:
            target = relationship["actor_id"]
            if target not in ids or target == actor["id"]:
                raise ValueError("relationship target must be another pilot actor")
            if relationship.get("provenance") != "authored_profile":
                raise ValueError("authored relationships cannot claim observed history")
            relationships.add(tuple(sorted((actor["id"], target))))
    if len(relationships) < 2:
        raise ValueError("pilot pack needs two pre-existing relationships")
    fixtures = pack.get("knowledge_fixtures", [])
    if not fixtures:
        raise ValueError("pilot pack requires differentiated knowledge fixtures")
    for fixture in fixtures:
        known, unknown = set(fixture["expected_knowers"]), set(fixture["expected_non_knowers"])
        if (
            not fixture.get("fixture_only")
            or len(known) < 2
            or len(unknown) < 1
            or known & unknown
            or not (known | unknown) <= ids
        ):
            raise ValueError("fixture must distinguish two witnesses from a non-witness")
        if set(fixture["event"]["observer_ids"]) != known:
            raise ValueError("fixture observers must match the expected knowers")
    return pack


def load_pilot_pack(path: str | Path | None = None) -> dict:
    with Path(path or PILOT_PACK_PATH).open(encoding="utf-8") as stream:
        return validate_pilot_pack(json.load(stream))


def install_pilot_pack(
    repository: Repository, profile_id: str, path: str | Path | None = None
) -> list[dict]:
    """Register biographies only. Never make authored fixtures into game events."""
    pack = load_pilot_pack(path)
    repository.create_profile(profile_id)
    actors = []
    for actor in pack["actors"]:
        existing = repository.get_actor(profile_id, actor["id"])
        # Reloading a content pack must not overwrite a user's profile edits.
        actors.append(existing or repository.register_actor(profile_id, actor))
    return actors


def seed_pilot_fixture(
    repository: Repository,
    profile_id: str,
    branch_id: str,
    fixture_id: str = "pilot-shade-board",
    path: str | Path | None = None,
) -> dict:
    """Explicit deterministic replay helper; runtime startup never calls this."""
    pack = load_pilot_pack(path)
    fixture = next(
        (item for item in pack["knowledge_fixtures"] if item["fixture_id"] == fixture_id), None
    )
    if fixture is None:
        raise ValueError("unknown pilot fixture")
    event = fixture["event"]
    repository.record_event(profile_id, branch_id, event)
    for actor_id in fixture["expected_knowers"]:
        repository.add_memory(
            profile_id,
            branch_id,
            actor_id,
            None,
            event["game_datetime"],
            "I read a notice about a proposed shade canopy discussion. "
            "I have not seen a canopy built.",
            [event["event_id"]],
            "directly_observed",
            importance=0.7,
        )
    return fixture
