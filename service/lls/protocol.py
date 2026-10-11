"""Strict bridge contracts. Narrative strings never acquire execution authority."""

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from . import activity as activity_table
from .action_registry import validate_arguments
from .display import is_display_safe

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Protagonist = Literal["michael", "franklin", "trevor"]
ActionId = Literal[
    "face_player",
    "follow_player",
    "stop_following",
    "go_to_safe_point",
    "wait_here",
    "wander_nearby",
    "flee_from_threat",
    "play_verified_gesture",
    "run_to_point",
    "look_at",
    "hands_up",
    "cower",
    "enter_vehicle",
    "leave_vehicle",
    "drive_to_point",
    "cruise_vehicle",
    "fight_target",
    "shoot_at_target",
]
RoleClass = Literal[
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
]
Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]


def display_safe(value: str) -> str:
    """Match the adapter: BMP only, no control or format characters, no markup."""
    if value != value.strip() or any(
        character in "~<>" or ord(character) > 0xFFFF or unicodedata.category(character)[0] == "C"
        for character in value
    ):
        raise ValueError("display names must be plain printable text")
    return value


DisplayName = Annotated[str, Field(min_length=1, max_length=48), AfterValidator(display_safe)]


def display_safe(value):
    # Producers normalize with display_text. Validation catches a producer that did not.
    if not is_display_safe(value):
        raise ValueError("text must already follow the display rule")
    return value


DisplayText = Annotated[str, AfterValidator(display_safe)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def parse_wire_dates(cls, value, info):
        if cls.model_fields[info.field_name].annotation is datetime and isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class Manifest(StrictModel):
    game_edition: Literal["legacy"]
    story_mode: Literal[True]
    gta_build: Identifier
    scripthookv_build: Identifier
    shvdn_build: Identifier
    adapter_version: Identifier
    game_os_version: Identifier
    service_os: Identifier
    service_host_mode: Literal["local", "dev_lan"] = "local"
    compat_layer: Literal["none", "crossover"] = "none"
    arch: Literal["x64", "arm64"] = "x64"
    capability_flags: list[Identifier] = Field(default_factory=list, max_length=64)


class SessionOpen(StrictModel):
    schema_version: Literal[1] = 1
    profile_id: Identifier
    parent_branch_id: Identifier | None = None
    protagonist_id: Protagonist
    game_datetime: datetime
    runtime_generation: int = Field(ge=0)
    manifest: Manifest
    capabilities: list[ActionId] = Field(default_factory=list, max_length=16)


class Position(StrictModel):
    x: float
    y: float
    z: float


class PedFacts(StrictModel):
    """Raw adapter samples. The service recomputes the role instead of trusting a label."""

    model_hash: Int32
    ped_type: int = Field(ge=-1, le=255)
    relationship_group_hash: Int32
    human: bool
    animal_model: bool
    cuffed: bool
    in_police_vehicle: bool
    player_group: bool
    mission_entity: bool
    owning_script: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    in_mod_registry: bool
    took_persistence: bool
    belongs_to_mod_script: bool


class ActorActivity(StrictModel):
    """Adapter activity sample. Narrative context only, never eligibility or policy."""

    movement: str = Field(default="unknown", max_length=32)
    scenario: str | None = Field(default=None, max_length=32)
    on_phone: bool = False
    swimming: bool = False
    injured: bool = False

    # A newer adapter table degrades to the generic labels instead of refusing the actor.
    @field_validator("movement")
    @classmethod
    def known_movement(cls, value):
        return value if value in activity_table.MOVEMENTS else "unknown"

    @field_validator("scenario")
    @classmethod
    def known_scenario(cls, value):
        return value if value is None or value in activity_table.SCENARIOS else "other"


class ActionTarget(StrictModel):
    target_id: Identifier
    kind: Literal["ped", "vehicle"]
    name: str = Field(max_length=128)
    position: Position
    is_threat: bool = False
    driver_seat_free: bool = False
    passenger_seat_free: bool = False


class ActionContext(StrictModel):
    targets: list[ActionTarget] = Field(default_factory=list, max_length=8)


class ActorSnapshot(StrictModel):
    runtime_handle: int = Field(gt=0)
    runtime_instance_id: Identifier
    ownership: Literal["ambient", "mod_owned", "game_script_owned", "unknown"]
    control_policy: Literal["ambient", "service_limited", "protected"]
    exists: bool
    alive: bool
    distance: float = Field(ge=0, le=100000)
    mission_entity: bool
    busy: bool
    position: Position
    name: str = Field(min_length=1, max_length=128)
    actor_kind: Literal[
        "curated_persistent", "spawned_persistent", "ambient_ephemeral", "anchored_service"
    ] = "ambient_ephemeral"
    heading: float | None = Field(default=None, ge=0, le=360)
    model: str = Field(default="", max_length=128)
    service_integration_id: Identifier | None = None
    area_id: Identifier | None = None
    interior_id: Identifier | None = None
    can_move: bool = True
    in_vehicle: bool = False
    is_driver: bool = False
    vehicle_stopped: bool = False
    has_firearm: bool = False
    mod_following: bool = False
    action_context: ActionContext = Field(default_factory=ActionContext)
    in_combat: bool = False
    # Optional for schema v1 compatibility; missing facts classify as unknown and protected.
    role_class: RoleClass = "unknown"
    role_reason: str = Field(default="unclassified", pattern=r"^[a-z0-9_]{1,64}$")
    ped_facts: PedFacts | None = None
    activity: ActorActivity | None = None


class WorldSnapshot(StrictModel):
    weather: str | None = Field(default=None, max_length=32)
    online: bool
    mission_active: bool
    cutscene_active: bool
    random_event_active: bool
    paused: bool
    protagonist_id: Protagonist


class Utterance(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    input_mode: Literal["typed", "voice"] = "typed"
    scene_id: Identifier
    whisper: bool = False
    consent: bool = False


class Cancel(StrictModel):
    scene_id: Identifier | None = None


class Empty(StrictModel):
    pass


class PinRequest(StrictModel):
    runtime_instance_id: Identifier


class VoiceControl(StrictModel):
    consent: bool = False
    scene_id: Identifier
    runtime_instance_id: Identifier
    whisper: bool = False


class WorldObservation(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    observer_ids: list[Identifier] = Field(max_length=36)
    provenance: Literal["directly_observed", "heard", "reported", "system_fact", "unverified"]
    confidence: float = Field(ge=0, le=1)
    teller_id: Identifier | None = None


PAYLOADS = {
    "actor.snapshot": ActorSnapshot,
    "actor.removed": Empty,
    "world.snapshot": WorldSnapshot,
    "world.observed": WorldObservation,
    "player.utterance_text": Utterance,
    "voice.ptt_started": VoiceControl,
    "voice.ptt_stopped": VoiceControl,
    "scene.cancel": Cancel,
    "actor.pin_requested": PinRequest,
}


class Event(StrictModel):
    schema_version: Literal[1] = 1
    event_id: Identifier
    session_id: Identifier
    seq: int = Field(ge=1)
    kind: Literal[
        "actor.snapshot",
        "actor.removed",
        "world.snapshot",
        "world.observed",
        "player.utterance_text",
        "voice.ptt_started",
        "voice.ptt_stopped",
        "scene.cancel",
        "actor.pin_requested",
    ]
    occurred_at_utc: datetime
    game_datetime: datetime
    protagonist_id: Protagonist
    actor_id: Identifier | None = None
    runtime_generation: int = Field(ge=0)
    payload: dict

    @field_validator("occurred_at_utc")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("occurred_at_utc must include UTC timezone")
        return value

    @model_validator(mode="after")
    def validate_payload(self):
        self.payload = PAYLOADS[self.kind].model_validate(self.payload).model_dump(mode="json")
        if (
            self.kind
            in {
                "actor.snapshot",
                "actor.removed",
                "player.utterance_text",
                "voice.ptt_started",
                "voice.ptt_stopped",
                "actor.pin_requested",
            }
            and not self.actor_id
        ):
            raise ValueError("actor_id is required for this event")
        return self


class EventBatch(StrictModel):
    session_id: Identifier
    events: list[Event] = Field(min_length=1, max_length=64)


class Ack(StrictModel):
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    status: Literal["started", "completed", "rejected", "expired", "failed", "canceled"]
    reason: str = Field(default="", max_length=1000)
    observed: dict | None = None


class Heartbeat(StrictModel):
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    fps: float = Field(ge=0, le=1000)
    queue_depth: int = Field(ge=0, le=10000)
    actor_count: int = Field(ge=0, le=10000)


class SessionClose(StrictModel):
    runtime_generation: int = Field(ge=0)


class ActionArguments(StrictModel):
    target_id: Identifier | None = None
    seat: Literal["driver", "passenger"] | None = None
    max_follow_meters: float | None = Field(default=None, ge=1, le=30)
    duration_seconds: float | None = Field(default=None, ge=0.1, le=30)
    radius: float | None = Field(default=None, ge=1, le=20)
    point: Position | None = None
    gesture_id: Identifier | None = None
    threat_actor_id: Identifier | None = None


class ActionProposal(StrictModel):
    action_id: ActionId
    arguments: ActionArguments = Field(default_factory=ActionArguments)
    reason_for_player: str = Field(default="", max_length=250)

    @model_validator(mode="after")
    def exact_arguments(self):
        validate_arguments(self.action_id, self.arguments.model_dump(exclude_none=True))
        return self


class SessionResponse(StrictModel):
    session_id: Identifier
    token: str
    policy_version: str
    schema_version: Literal[1] = 1
    capabilities: list[ActionId]
    branch_id: Identifier


class DialogueChunk(StrictModel):
    text: DisplayText = Field(max_length=4000)
    speaker: DisplayText
    scene_id: Identifier
    turn_id: Identifier
    chunk_index: int = Field(ge=0)
    final: bool
    delivery_mode: Literal["subtitle", "audio"] = "subtitle"
    presentation: Literal["overhead"] | None = None
    audio_duration_ms: int | None = Field(default=None, ge=1, le=30000)


class ActionCommand(StrictModel):
    action_id: ActionId
    arguments: ActionArguments
    policy_version: str
    scene_id: Identifier
    consent: bool
    turn_id: Identifier


class StatusMessage(StrictModel):
    state: str
    message: DisplayText
    scene_id: Identifier | None = None


class PinCommand(StrictModel):
    display_name: DisplayName
    actor_kind: Literal["spawned_persistent"]


class SocialSceneCommand(StrictModel):
    scene_id: Identifier
    participants: dict[Identifier, Identifier] = Field(min_length=2, max_length=3)
    duration_seconds: float = Field(default=45, gt=0, le=45)

    @model_validator(mode="after")
    def distinct_bindings(self):
        if len(set(self.participants.values())) != len(self.participants):
            raise ValueError("social participants require distinct instances")
        return self


class Command(StrictModel):
    id: Identifier
    seq: int = Field(ge=1)
    kind: Literal[
        "dialogue.chunk", "action.execute", "scene.cancel", "status", "actor.pin", "social.scene"
    ]
    session_id: Identifier
    actor_id: Identifier | None
    runtime_generation: int = Field(ge=0)
    runtime_instance_id: Identifier | None
    expires_at: datetime
    idempotency_key: Identifier
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self):
        payload_type = {
            "dialogue.chunk": DialogueChunk,
            "action.execute": ActionCommand,
            "scene.cancel": Cancel,
            "status": StatusMessage,
            "actor.pin": PinCommand,
            "social.scene": SocialSceneCommand,
        }[self.kind]
        payload_type.model_validate(self.payload)
        if (
            self.kind in {"dialogue.chunk", "action.execute", "actor.pin"}
            and not self.runtime_instance_id
        ):
            raise ValueError("actor commands require runtime_instance_id")
        return self


class CommandBatch(StrictModel):
    commands: list[Command]
    cursor: int = Field(ge=0)


class MemoryUpdate(StrictModel):
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    importance: float | None = Field(default=None, ge=0, le=1)


class ProfileErase(StrictModel):
    confirm_profile_id: Identifier
