"""Explicit development attestations for simulated NPC social scenes."""

from datetime import datetime

from pydantic import Field, model_validator

from .perception import Witness
from .protocol import Identifier, Protagonist, StrictModel

DEVELOPMENT_SOCIAL_CAPABILITY = "development:social:v1"


class SocialEnvelope(StrictModel):
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    protagonist_id: Protagonist
    scene_id: Identifier
    game_datetime: datetime


class SocialInvite(SocialEnvelope):
    initiator_id: Identifier
    participants: dict[Identifier, Identifier] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def initiator_is_participant(self):
        if self.initiator_id not in self.participants:
            raise ValueError("the initiator must be a participant")
        if len(set(self.participants.values())) != len(self.participants):
            raise ValueError("participants require separate runtime instances")
        return self


class SocialReview(SocialEnvelope):
    actor_id: Identifier


class SocialTurn(SocialReview):
    retell_event_id: Identifier | None = None


class SocialDelivery(SocialEnvelope):
    command_id: Identifier
    witnesses: list[Witness] = Field(min_length=1, max_length=36)

    @model_validator(mode="after")
    def unique_listeners(self):
        if len({w.actor_id for w in self.witnesses}) != len(self.witnesses):
            raise ValueError("duplicate social listener")
        return self
