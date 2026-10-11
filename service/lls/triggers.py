"""Bounded declarative reactions to actor-local, perception-validated evidence.

This engine does not attest game observations or execute actions. Callers must
obtain Evidence from the trusted perception path and reauthorize every action
proposal. An authored reaction never creates a completed game event.
"""

from __future__ import annotations

import math
import random
import string
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import ConfigDict, Field, model_validator

from .perception import Evidence, Incident
from .protocol import ActionId, ActionProposal, Identifier, Protagonist, StrictModel

EVENT_TYPES = frozenset(get_args(Incident.model_fields["event_type"].annotation))
PROVENANCES = {"directly_observed", "heard", "reported"}
CONDITION_TYPES = {
    "confidence": float,
    "distance_meters": float,
    "provenance": str,
    "event_type": str,
    "actor_id": str,
    "area_id": str,
    "protagonist_id": str,
    "role": str,
    "player_present": bool,
    "idle": bool,
}


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)


class Condition(FrozenModel):
    field: Literal[
        "confidence",
        "distance_meters",
        "provenance",
        "event_type",
        "actor_id",
        "area_id",
        "protagonist_id",
        "role",
        "player_present",
        "idle",
    ]
    op: Literal["eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte"]
    value: str | float | bool | list[str | float | bool]

    @model_validator(mode="after")
    def compatible_value(self):
        expected = CONDITION_TYPES[self.field]
        values = self.value if isinstance(self.value, list) else [self.value]
        if not 1 <= len(values) <= 16:
            raise ValueError("conditions require one to sixteen values")
        if self.op in {"in", "not_in"} and not isinstance(self.value, list):
            raise ValueError("membership conditions require a list")
        if self.op not in {"in", "not_in"} and isinstance(self.value, list):
            raise ValueError("scalar conditions require a scalar")
        if self.op in {"lt", "lte", "gt", "gte"} and expected is not float:
            raise ValueError("ordered conditions require numeric evidence")
        for value in values:
            if expected is float:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("numeric condition value required")
                if not math.isfinite(value):
                    raise ValueError("condition values must be finite")
            elif type(value) is not expected:
                raise ValueError("condition value has the wrong type")
        return self

    def matches(self, evidence: Evidence, context: TriggerContext) -> bool:
        value = getattr(
            evidence
            if self.field
            in {"confidence", "distance_meters", "provenance", "event_type", "actor_id"}
            else context,
            self.field,
        )
        operators = {
            "eq": lambda: value == self.value,
            "ne": lambda: value != self.value,
            "in": lambda: value in self.value,
            "not_in": lambda: value not in self.value,
            "lt": lambda: value < self.value,
            "lte": lambda: value <= self.value,
            "gt": lambda: value > self.value,
            "gte": lambda: value >= self.value,
        }
        return operators[self.op]()


class EventMatch(FrozenModel):
    event_types: list[str] = Field(min_length=1, max_length=16)
    provenances: list[Literal["directly_observed", "heard", "reported"]] = Field(
        default_factory=lambda: ["directly_observed", "heard", "reported"], max_length=3
    )

    @model_validator(mode="after")
    def supported_types(self):
        if not set(self.event_types) <= EVENT_TYPES:
            raise ValueError("trigger event type is not supported by perception")
        return self


class Scope(FrozenModel):
    actor_ids: list[Identifier] = Field(default_factory=list, max_length=36)
    area_ids: list[Identifier] = Field(default_factory=list, max_length=36)


class Effect(FrozenModel):
    type: Literal["speak_to_player", "thought", "knowledge_write", "suggested_action"]
    # Only speech uses authored wording. Memory and knowledge copy source text
    # with a deterministic provenance prefix, never arbitrary inferred facts.
    template: str | None = Field(default=None, min_length=1, max_length=500)
    action: ActionProposal | None = None

    @model_validator(mode="after")
    def exact_effect(self):
        if self.type == "suggested_action":
            if self.action is None or self.template is not None:
                raise ValueError("suggested actions require only an ActionProposal")
        elif self.type == "speak_to_player":
            if self.template is None or self.action is not None:
                raise ValueError("speech requires an authored template and no action")
            for _, field, format_spec, conversion in string.Formatter().parse(self.template):
                if field is not None and field not in {"evidence", "teller"}:
                    raise ValueError("only evidence and teller placeholders are allowed")
                if format_spec or conversion:
                    raise ValueError("template conversions and formatting are not permitted")
        elif self.template is not None or self.action is not None:
            raise ValueError("memory and knowledge effects must copy grounded source evidence")
        return self


class TriggerRule(FrozenModel):
    id: Identifier
    enabled: bool = True
    match: EventMatch
    conditions: list[Condition] = Field(default_factory=list, max_length=16)
    scope: Scope = Field(default_factory=Scope)
    priority: int = Field(default=50, ge=0, le=100)
    cooldown_seconds: float = Field(default=60, ge=0, le=86400)
    probability: float = Field(default=1, ge=0, le=1)
    effect: Effect


class TriggerLimits(FrozenModel):
    local_remark_interval_seconds: float = Field(default=45, gt=0, le=3600)
    actor_remark_cooldown_seconds: float = Field(default=120, gt=0, le=86400)
    area_remark_cooldown_seconds: float = Field(default=45, gt=0, le=86400)
    evidence_max_age_seconds: float = Field(default=2, gt=0, le=5)
    effect_ttl_seconds: float = Field(default=3, gt=0, le=10)
    event_dedupe_seconds: float = Field(default=120, ge=10, le=3600)
    max_queue: int = Field(default=64, ge=1, le=256)
    max_state_entries: int = Field(default=2048, ge=16, le=8192)


class TriggerPack(FrozenModel):
    schema_version: Literal[1] = 1
    pack_id: Identifier
    limits: TriggerLimits = Field(default_factory=TriggerLimits)
    rules: list[TriggerRule] = Field(max_length=128)

    @model_validator(mode="after")
    def unique_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("trigger rule identifiers must be unique")
        return self


class TriggerContext(FrozenModel):
    """Trusted current scene state, never supplied by a model or narrative text."""

    profile_id: Identifier
    session_id: Identifier
    timeline_branch: Identifier
    protagonist_id: Protagonist
    game_datetime: datetime
    scene_id: Identifier
    epoch: int = Field(ge=0)
    runtime_generation: int = Field(ge=0)
    area_id: Identifier
    actor_id: Identifier
    runtime_instance_id: Identifier
    role: Identifier
    control_policy: Literal["ambient", "service_limited", "protected"]
    # Clock domain is the injected monotonic clock. This records when the
    # trusted perception path produced the Evidence, not a model timestamp.
    evidence_created_at: float
    foreground_dialogue: bool
    player_present: bool
    alive: bool
    idle: bool
    allowed_actions: tuple[ActionId, ...] = Field(default_factory=tuple, max_length=16)


class TriggerEffect(FrozenModel):
    rule_id: Identifier
    type: Literal["speak_to_player", "thought", "knowledge_write", "suggested_action"]
    priority: int
    profile_id: Identifier
    session_id: Identifier
    timeline_branch: Identifier
    protagonist_id: Protagonist
    game_datetime: datetime
    scene_id: Identifier
    epoch: int
    runtime_generation: int
    actor_id: Identifier
    runtime_instance_id: Identifier
    area_id: Identifier
    source_event_ids: tuple[Identifier, ...]
    provenance: Literal["directly_observed", "heard", "reported"]
    confidence: float
    teller_id: Identifier | None
    reported_event_id: Identifier | None
    visibility_scope: Literal["actor"] = "actor"
    text: str | None = None
    action: ActionProposal | None = None
    requires_authorization: bool = False
    consent: Literal[False] = False


class _StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_trigger_pack(path: str | Path) -> TriggerPack:
    with Path(path).open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("trigger YAML exceeds 64 KiB")
    source = data.decode("utf-8")
    # Disallow aliases before construction, preventing cyclic or amplified packs.
    if any(
        isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
        for token in yaml.scan(source)
    ):
        raise ValueError("YAML anchors and aliases are not supported")
    return TriggerPack.model_validate(yaml.load(source, Loader=_StrictLoader))


@dataclass(frozen=True)
class _Pending:
    rule: TriggerRule
    evidence: Evidence
    context: TriggerContext
    created_at: float
    sequence: int


class TriggerEngine:
    def __init__(self, pack: TriggerPack, *, clock=time.monotonic, random_value=random.random):
        if not isinstance(pack, TriggerPack):
            raise TypeError("TriggerEngine requires a validated TriggerPack")
        self.pack = pack.model_copy(deep=True)
        self.clock = clock
        self.random_value = random_value
        self._queue: list[_Pending] = []
        self._seen: dict[tuple, float] = {}
        self._cooldowns: dict[tuple, float] = {}
        self._epochs: dict[tuple, int] = {}
        self._canceled_sessions: set[str] = set()
        self._sequence = 0
        self._last_clock = -math.inf
        self._disabled = False
        self._counts = dict.fromkeys(
            ["queued", "emitted", "suppressed", "stale", "deduplicated", "capacity", "canceled"], 0
        )

    @classmethod
    def from_yaml(cls, path, **kwargs):
        return cls(load_trigger_pack(path), **kwargs)

    def _count(self, name):
        self._counts[name] = min(2**63 - 1, self._counts[name] + 1)

    def _time(self):
        now = float(self.clock())
        if not math.isfinite(now) or now < self._last_clock:
            raise ValueError("trigger clock must be finite and monotonic")
        self._last_clock = now
        self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
        self._cooldowns = {key: expiry for key, expiry in self._cooldowns.items() if expiry > now}
        live = []
        for pending in self._queue:
            if now - pending.created_at <= self.pack.limits.effect_ttl_seconds:
                live.append(pending)
            else:
                self._count("stale")
        self._queue = live
        return now

    @staticmethod
    def _scene(context):
        return context.session_id, context.scene_id

    @staticmethod
    def _binding(context):
        return context.profile_id, context.session_id, context.scene_id, context.actor_id

    def _valid_context(self, context, now):
        if not isinstance(context, TriggerContext):
            raise TypeError("trigger context must be typed trusted scene state")
        if self._disabled or context.session_id in self._canceled_sessions:
            return False
        if not context.alive or context.control_policy == "protected":
            return False
        age = now - context.evidence_created_at
        if not 0 <= age <= self.pack.limits.evidence_max_age_seconds:
            return False
        scene = self._scene(context)
        if context.epoch < self._epochs.get(scene, context.epoch):
            return False
        if scene not in self._epochs and len(self._epochs) >= self.pack.limits.max_state_entries:
            self._count("capacity")
            return False
        self._epochs[scene] = context.epoch
        return True

    @staticmethod
    def _matches(rule, evidence, context):
        return (
            rule.enabled
            and evidence.event_type in rule.match.event_types
            and evidence.provenance in rule.match.provenances
            and (not rule.scope.actor_ids or context.actor_id in rule.scope.actor_ids)
            and (not rule.scope.area_ids or context.area_id in rule.scope.area_ids)
            and all(condition.matches(evidence, context) for condition in rule.conditions)
        )

    def _cooldown_keys(self, rule, context):
        base = context.profile_id, context.session_id
        keys = {("rule", *base, context.actor_id, rule.id): rule.cooldown_seconds}
        if rule.effect.type == "speak_to_player":
            keys.update(
                {
                    ("local", *base): self.pack.limits.local_remark_interval_seconds,
                    (
                        "actor",
                        *base,
                        context.actor_id,
                    ): self.pack.limits.actor_remark_cooldown_seconds,
                    ("area", *base, context.area_id): self.pack.limits.area_remark_cooldown_seconds,
                }
            )
        return keys

    def _eligible_effect(self, rule, context, now):
        if any(self._cooldowns.get(key, 0) > now for key in self._cooldown_keys(rule, context)):
            return False
        if rule.effect.type == "speak_to_player":
            return context.idle and context.player_present and not context.foreground_dialogue
        if rule.effect.type == "suggested_action":
            return (
                context.control_policy == "ambient"
                and context.idle
                and rule.effect.action.action_id in context.allowed_actions
            )
        return True

    def enqueue(self, evidence: Evidence, context: TriggerContext) -> int:
        """Queue candidate reactions. This never speaks, writes memory, or acts."""
        if not isinstance(evidence, Evidence):
            raise TypeError("triggers accept actor-local Evidence, not global observer assertions")
        if (
            evidence.event_type not in EVENT_TYPES
            or not evidence.text.strip()
            or len(evidence.text) > 1000
            or not math.isfinite(evidence.confidence)
            or not 0 <= evidence.confidence <= 1
            or not math.isfinite(evidence.distance_meters)
            or evidence.distance_meters < 0
            or (evidence.provenance == "reported" and not evidence.teller_id)
        ):
            raise ValueError("invalid grounded evidence")
        now = self._time()
        if not self._valid_context(context, now) or (
            evidence.actor_id != context.actor_id
            or evidence.runtime_instance_id != context.runtime_instance_id
        ):
            self._count("stale")
            return 0
        accepted = 0
        for rule in self.pack.rules:
            if not self._matches(rule, evidence, context):
                continue
            key = (
                context.profile_id,
                context.session_id,
                context.actor_id,
                context.timeline_branch,
                evidence.event_id,
                rule.id,
            )
            if key in self._seen:
                self._count("deduplicated")
                continue
            if len(self._seen) >= self.pack.limits.max_state_entries:
                self._count("capacity")
                continue
            self._seen[key] = now + self.pack.limits.event_dedupe_seconds
            # Generic triggers never forward rumors or act on them. Reported
            # evidence can only become owner-private memory or knowledge.
            if (
                (
                    evidence.provenance == "reported"
                    and rule.effect.type in {"speak_to_player", "suggested_action"}
                )
                or not self._eligible_effect(rule, context, now)
                or rule.probability == 0
            ):
                self._count("suppressed")
                continue
            roll = self.random_value()
            if not isinstance(roll, (int, float)) or not 0 <= roll < 1:
                raise ValueError("random_value must return a value in [0,1)")
            if roll >= rule.probability:
                self._count("suppressed")
                continue
            self._sequence += 1
            pending = _Pending(rule, evidence.model_copy(deep=True), context, now, self._sequence)
            if len(self._queue) >= self.pack.limits.max_queue:
                weakest = min(self._queue, key=lambda item: (item.rule.priority, -item.sequence))
                if weakest.rule.priority >= rule.priority:
                    self._count("capacity")
                    continue
                self._queue.remove(weakest)
                self._count("capacity")
            self._queue.append(pending)
            accepted += 1
            self._count("queued")
        return accepted

    @staticmethod
    def _render(pending):
        rule, evidence, context = pending.rule, pending.evidence, pending.context
        prefix = {
            "directly_observed": "I observed: ",
            "heard": "I heard: ",
            "reported": f"I heard a report from {evidence.teller_id}: ",
        }[evidence.provenance]
        text = None
        if rule.effect.type == "speak_to_player":
            text = rule.effect.template.replace("{evidence}", evidence.text).replace(
                "{teller}", evidence.teller_id or "an unspecified source"
            )
            if evidence.provenance == "reported":
                text = f"This is a report from {evidence.teller_id}, not my observation. " + text
        elif rule.effect.type in {"thought", "knowledge_write"}:
            text = prefix + evidence.text
        fields = context.model_dump(
            include={
                "profile_id",
                "session_id",
                "timeline_branch",
                "protagonist_id",
                "game_datetime",
                "scene_id",
                "epoch",
                "runtime_generation",
                "actor_id",
                "runtime_instance_id",
                "area_id",
            }
        )
        return TriggerEffect(
            **fields,
            rule_id=rule.id,
            type=rule.effect.type,
            priority=rule.priority,
            source_event_ids=(evidence.event_id,),
            provenance=evidence.provenance,
            confidence=evidence.confidence,
            teller_id=evidence.teller_id,
            reported_event_id=evidence.reported_event_id,
            text=text,
            action=rule.effect.action.model_copy(deep=True) if rule.effect.action else None,
            requires_authorization=rule.effect.type == "suggested_action",
        )

    def drain(self, contexts: list[TriggerContext], *, max_effects: int = 8) -> list[TriggerEffect]:
        """Revalidate queued effects against current bindings and scene state."""
        if not 1 <= max_effects <= 36 or len(contexts) > 36:
            raise ValueError("trigger drain exceeds the scene budget")
        now = self._time()
        current = {}
        for context in contexts:
            if not isinstance(context, TriggerContext):
                raise TypeError("trigger drain requires typed contexts")
            binding = self._binding(context)
            if binding in current:
                raise ValueError("duplicate trigger actor context")
            current[binding] = context
        domains = {self._scene(context) for context in contexts}
        foreground = {self._scene(context) for context in contexts if context.foreground_dialogue}
        retained, effects = [], []
        for pending in sorted(self._queue, key=lambda item: (-item.rule.priority, item.sequence)):
            prior = pending.context
            if self._scene(prior) not in domains:
                retained.append(pending)
                continue
            context = current.get(self._binding(prior))
            if (
                context is None
                or not self._valid_context(context, now)
                or now - prior.evidence_created_at > self.pack.limits.evidence_max_age_seconds
            ) or any(
                getattr(context, field) != getattr(prior, field)
                for field in (
                    "timeline_branch",
                    "protagonist_id",
                    "runtime_generation",
                    "runtime_instance_id",
                    "epoch",
                    "area_id",
                )
            ):
                self._count("stale")
                continue
            if (
                not self._matches(pending.rule, pending.evidence, context)
                or not self._eligible_effect(pending.rule, context, now)
                or (
                    pending.rule.effect.type == "speak_to_player"
                    and self._scene(context) in foreground
                )
            ):
                self._count("suppressed")
                continue
            if len(effects) >= max_effects:
                retained.append(pending)
                continue
            cooldowns = self._cooldown_keys(pending.rule, context)
            if len(self._cooldowns.keys() | cooldowns.keys()) > self.pack.limits.max_state_entries:
                self._count("capacity")
                continue
            self._cooldowns.update({key: now + duration for key, duration in cooldowns.items()})
            effects.append(self._render(pending))
            self._count("emitted")
        self._queue = retained
        return effects

    def cancel_scene(self, session_id: str, scene_id: str) -> None:
        scene = (session_id, scene_id)
        if scene not in self._epochs and len(self._epochs) >= self.pack.limits.max_state_entries:
            self._disabled = True
            self._count("capacity")
        else:
            self._epochs[scene] = self._epochs.get(scene, 0) + 1
        self._queue = [pending for pending in self._queue if self._scene(pending.context) != scene]
        self._count("canceled")

    def cancel_session(self, session_id: str) -> None:
        if len(self._canceled_sessions) >= self.pack.limits.max_state_entries:
            self._disabled = True
            self._count("capacity")
        else:
            self._canceled_sessions.add(session_id)
        self._queue = [
            pending for pending in self._queue if pending.context.session_id != session_id
        ]
        self._epochs = {key: value for key, value in self._epochs.items() if key[0] != session_id}
        self._count("canceled")

    def diagnostics(self) -> dict:
        self._time()
        return {
            "pack_id": self.pack.pack_id,
            "rules": len(self.pack.rules),
            "queued": len(self._queue),
            "dedupe_entries": len(self._seen),
            "cooldown_entries": len(self._cooldowns),
            "scene_entries": len(self._epochs),
            "canceled_sessions": len(self._canceled_sessions),
            "disabled": self._disabled,
            "counters": dict(self._counts),
            "native_perception_verified": False,
        }
