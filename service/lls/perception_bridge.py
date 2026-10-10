"""Typed development sensor input, separate from ordinary narrative events.

The runtime authorizes this envelope and computes witnesses itself. No endpoint
accepts a pre-approved Evidence list or arbitrary global observer identities.
"""

from datetime import datetime

from pydantic import Field, model_validator

from .perception import Incident, Witness
from .protocol import Identifier, Protagonist, StrictModel

DEVELOPMENT_PERCEPTION_CAPABILITY = "development:perception:v1"


class PerceptionBatch(StrictModel):
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    protagonist_id: Protagonist
    seq: int = Field(ge=1)
    game_datetime: datetime
    scene_id: Identifier
    incident: Incident
    witnesses: list[Witness] = Field(min_length=1, max_length=36)

    @model_validator(mode="after")
    def bound_witnesses_and_reports(self):
        identifiers = [w.actor_id for w in self.witnesses]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate witness identity")
        if self.incident.modality == "report" and (
            not self.incident.source_actor_id or not self.incident.reported_event_id
        ):
            raise ValueError("reports require a teller and source lineage")
        return self
