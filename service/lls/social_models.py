"""Typed development social-scene inputs. None attest native GTA behavior."""

from datetime import datetime

from pydantic import Field, field_validator

from .perception import TimedEvidence
from .protocol import Identifier, Position, Protagonist, StrictModel

DEVELOPMENT_SOCIAL_CAPABILITY = "development:social:v1"


class SocialConfig(StrictModel):
    enabled: bool = False
    max_turns: int = Field(default=6, ge=2, le=12)
    max_duration_seconds: float = Field(default=120, ge=5, le=180)
    decision_timeout_seconds: float = Field(default=10, ge=0.01, le=30)
    max_active_scenes: int = Field(default=4, ge=1, le=8)
    max_distance_meters: float = Field(default=10, gt=0, le=20)
    max_sample_age_seconds: float = Field(default=2, gt=0, le=5)
    scene_cooldown_seconds: float = Field(default=30, ge=0, le=300)
    max_text_characters: int = Field(default=500, ge=50, le=1000)
    context_byte_budget: int = Field(default=12000, ge=4096, le=24000)


class SocialStart(StrictModel):
    scene_id: Identifier
    profile_id: Identifier
    branch_id: Identifier
    session_id: Identifier
    protagonist_id: Protagonist
    runtime_generation: int = Field(ge=0)
    initiator_id: Identifier
    participant_bindings: dict[Identifier, Identifier] = Field(min_length=2, max_length=3)
    area_id: Identifier
    interior_id: Identifier
    game_datetime: datetime
    development_attestation: bool = False


class SocialActorState(TimedEvidence):
    actor_id: Identifier
    runtime_instance_id: Identifier
    position: Position
    area_id: Identifier
    interior_id: Identifier
    exists: bool
    alive: bool
    busy: bool
    mission_entity: bool
    in_combat: bool
    ownership: str
    control_policy: str
    service_integration_id: Identifier | None = None


class SocialEnvironment(TimedEvidence):
    profile_id: Identifier
    branch_id: Identifier
    session_id: Identifier
    protagonist_id: Protagonist
    runtime_generation: int = Field(ge=0)
    game_datetime: datetime
    actors: dict[Identifier, SocialActorState] = Field(min_length=2, max_length=36)
    player_present: bool = False
    foreground_dialogue: bool = True
    danger: bool = True
    online: bool = True
    mission_active: bool = True
    cutscene_active: bool = True
    random_event_active: bool = True
    paused: bool = True


class SocialUtterance(TimedEvidence):
    scene_id: Identifier
    utterance_id: Identifier
    actor_id: Identifier
    runtime_instance_id: Identifier
    text: str = Field(min_length=1, max_length=1000)
    turn_index: int = Field(ge=0, le=11)
    scene_epoch: int = Field(ge=0)
    retell_event_id: Identifier | None = None


class SocialDecision(StrictModel):
    decision: str

    @field_validator("decision")
    @classmethod
    def allowed_decision(cls, value):
        if value not in {"accept", "refuse", "continue", "withdraw"}:
            raise ValueError("unsupported social decision")
        return value
