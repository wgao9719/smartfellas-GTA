"""Deterministic witness filtering for adapter-attested local incidents.

These contracts describe evidence from an adapter. They do not prove that a
native API supplies it. Unknown visibility, hearing, or location fails closed.
The bridge must compare each witness binding with its live actor registry.
"""

from datetime import datetime
from math import dist
from typing import Literal

from pydantic import Field, field_validator

from .protocol import Identifier, Position, StrictModel


class TimedEvidence(StrictModel):
    sampled_at: datetime

    @field_validator("sampled_at")
    @classmethod
    def utc_time(cls, value):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("perception evidence requires UTC time")
        return value


class Incident(TimedEvidence):
    event_id: Identifier
    event_type: Literal[
        "player.spoke",
        "actor.spoke",
        "player.weapon_drawn",
        "actor.injured",
        "vehicle.collision",
        "world.weather_changed",
        "actor.greeted",
    ]
    position: Position
    area_id: Identifier
    interior_id: Identifier
    text: str = Field(min_length=1, max_length=1000)
    modality: Literal["visual", "audible", "report"]
    source_actor_id: Identifier | None = None
    whisper: bool = False
    confidence: float = Field(ge=0, le=1)
    # A report may concern a different place and time. It never becomes a
    # witnessed world incident merely because a listener heard the report.
    reported_event_id: Identifier | None = None


class Witness(TimedEvidence):
    actor_id: Identifier
    runtime_instance_id: Identifier
    position: Position
    area_id: Identifier | None = None
    interior_id: Identifier | None = None
    exists: bool
    alive: bool
    line_of_sight: bool | None = None
    within_view: bool | None = None
    can_hear: bool | None = None
    confidence: float = Field(ge=0, le=1)


class PerceptionLimits(StrictModel):
    normal_hearing_meters: float = Field(default=20, gt=0, le=40)
    whisper_hearing_meters: float = Field(default=3, gt=0, le=5)
    sight_meters: float = Field(default=40, gt=0, le=80)
    max_age_seconds: float = Field(default=2, gt=0, le=5)
    min_confidence: float = Field(default=0.5, ge=0, le=1)


class Evidence(StrictModel):
    event_id: Identifier
    event_type: str
    actor_id: Identifier
    runtime_instance_id: Identifier
    provenance: Literal["directly_observed", "heard", "reported"]
    text: str
    confidence: float
    distance_meters: float
    teller_id: Identifier | None = None
    reported_event_id: Identifier | None = None


class DeliveredUtterance(TimedEvidence):
    """Receipt from a trusted conversation delivery path, never model-authored."""

    event_id: Identifier
    actor_id: Identifier
    runtime_instance_id: Identifier
    text: str = Field(min_length=1, max_length=1000)


def witnesses_for(
    incident: Incident,
    candidates: list[Witness],
    now: datetime,
    live_bindings: dict[str, str],
    limits: PerceptionLimits | None = None,
    *,
    delivered_utterances: dict[str, DeliveredUtterance] | None = None,
) -> list[Evidence]:
    """Return only eligible, bound witnesses. Never infer unknown sensor values."""
    limits = limits or PerceptionLimits()
    if now.tzinfo is None or now.utcoffset().total_seconds() != 0:
        raise ValueError("current perception time requires UTC")
    if len(candidates) > 36:
        raise ValueError("perception candidate limit exceeded")
    incident_age = (now - incident.sampled_at).total_seconds()
    if not 0 <= incident_age <= limits.max_age_seconds:
        return []
    if incident.modality == "report":
        receipt = (delivered_utterances or {}).get(incident.event_id)
        if (
            not incident.source_actor_id
            or receipt is None
            or receipt.actor_id != incident.source_actor_id
            or receipt.event_id != incident.event_id
            or live_bindings.get(receipt.actor_id) != receipt.runtime_instance_id
            or receipt.text != incident.text
            or not 0 <= (now - receipt.sampled_at).total_seconds() <= limits.max_age_seconds
        ):
            return []
    seen = set()
    output = []
    for witness in candidates:
        if witness.actor_id in seen:
            raise ValueError("duplicate witness identity")
        seen.add(witness.actor_id)
        age = (now - witness.sampled_at).total_seconds()
        if not 0 <= age <= limits.max_age_seconds or not witness.exists or not witness.alive:
            continue
        if live_bindings.get(witness.actor_id) != witness.runtime_instance_id:
            continue
        if witness.area_id != incident.area_id or witness.interior_id != incident.interior_id:
            continue
        confidence = min(incident.confidence, witness.confidence)
        if confidence < limits.min_confidence:
            continue
        distance = dist(
            (incident.position.x, incident.position.y, incident.position.z),
            (witness.position.x, witness.position.y, witness.position.z),
        )
        if incident.modality == "visual":
            if witness.line_of_sight is not True or witness.within_view is not True:
                continue
            if distance > limits.sight_meters:
                continue
            provenance = "directly_observed"
        else:
            hearing_range = (
                limits.whisper_hearing_meters if incident.whisper else limits.normal_hearing_meters
            )
            if witness.can_hear is not True or distance > hearing_range:
                continue
            provenance = "reported" if incident.modality == "report" else "heard"
        output.append(
            Evidence(
                event_id=incident.event_id,
                event_type=incident.event_type,
                actor_id=witness.actor_id,
                runtime_instance_id=witness.runtime_instance_id,
                provenance=provenance,
                text=incident.text,
                confidence=confidence,
                distance_meters=distance,
                teller_id=incident.source_actor_id if incident.modality == "report" else None,
                reported_event_id=incident.reported_event_id,
            )
        )
    return output
