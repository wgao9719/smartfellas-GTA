"""Finite declarative skill programs. Compilation never grants game capabilities.

This development interpreter accepts only existing bounded action primitives.
It has no Python evaluation, imports, file access, network access, or native API.
The caller must provide the current bridge policy inputs and a validated executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .action_registry import REGISTRY_SHA256
from .actions import ActionPolicy
from .protocol import ActionProposal, Identifier, Protagonist

MAX_SOURCE_BYTES = 16384
MAX_EXPANDED_STEPS = 32
MAX_CALL_DEPTH = 4
BOUNDED_PRIMITIVES = frozenset({"face_player", "wait_here", "follow_player", "stop_following"})


class SkillModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, strict=True, revalidate_instances="always"
    )


class SkillAction(SkillModel):
    op: Literal["action"]
    proposal: ActionProposal

    @model_validator(mode="after")
    def no_narrative_payload(self):
        if self.proposal.reason_for_player:
            raise ValueError("shared skill code cannot include narrative context")
        return self


class SkillCall(SkillModel):
    op: Literal["call"]
    skill_id: Identifier
    version: int = Field(ge=1, le=1000000)


class SkillGuard(SkillModel):
    op: Literal["require"]
    condition: Literal["consent", "can_move", "mod_following", "observed_threat"]


Instruction = Annotated[SkillAction | SkillCall | SkillGuard, Field(discriminator="op")]


class SkillDefinition(SkillModel):
    schema_version: Literal[1] = 1
    skill_id: Identifier
    version: int = Field(ge=1, le=1000000)
    action_class: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    max_duration_seconds: float = Field(ge=0.1, le=30)
    max_distance_meters: float = Field(default=0, ge=0, le=30)
    steps: list[Instruction] = Field(min_length=1, max_length=16)


@dataclass(frozen=True)
class CompiledSkill:
    source: str
    fingerprint: str
    registry_fingerprint: str
    dependencies: tuple[tuple[str, str], ...]
    operations: tuple[str, ...]
    duration_seconds: float
    distance_meters: float


@dataclass(frozen=True)
class SkillIdentity:
    profile_id: str
    branch_id: str
    protagonist_id: Protagonist
    session_id: str
    runtime_generation: int
    actor_id: str
    runtime_instance_id: str


@dataclass(frozen=True)
class SkillEnvironment:
    identity: SkillIdentity
    actor: dict
    world: dict
    capabilities: tuple[str, ...]
    consent: bool = False


class PrimitiveReceipt(SkillModel):
    operation_id: Identifier
    status: Literal["completed", "refused", "failed", "canceled"]
    distance_meters: float = Field(default=0, ge=0, le=100000)


@dataclass(frozen=True)
class SkillResult:
    status: str
    reason: str
    fingerprint: str
    completed_operations: int
    distance_meters: float
    elapsed_seconds: float
    game_verified: bool = False


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_definition(source):
    if isinstance(source, SkillDefinition):
        source = source.model_dump(mode="json")
    if not isinstance(source, str):
        source = canonical(source)
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("skill source exceeds byte limit")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key in skill source")
            result[key] = value
        return result

    try:
        return SkillDefinition.model_validate(json.loads(source, object_pairs_hook=unique_object))
    except (RecursionError, ValueError) as exc:
        raise ValueError("invalid declarative skill") from exc


def compile_skill(source, library=None, *, registry_fingerprint=REGISTRY_SHA256):
    """Expand a finite call graph and sum effects across every operation."""
    library = library or {}
    root = parse_definition(source)
    operations, dependencies = [], {}
    duration = distance = 0.0

    def visit(definition, stack):
        nonlocal duration, distance
        identity = (definition.skill_id, definition.version)
        if identity in stack or len(stack) >= MAX_CALL_DEPTH:
            raise ValueError("skill recursion or call depth exceeded")
        stack = (*stack, identity)
        start_duration, start_distance = duration, distance
        for step in definition.steps:
            if isinstance(step, SkillCall):
                key = (step.skill_id, step.version)
                if key not in library:
                    raise ValueError("skill dependency is unavailable")
                child = parse_definition(library[key])
                if (child.skill_id, child.version) != key:
                    raise ValueError("skill dependency identity mismatch")
                label = canonical(list(key))
                digest = hashlib.sha256(
                    canonical(child.model_dump(mode="json")).encode()
                ).hexdigest()
                dependencies[label] = digest
                visit(child, stack)
                continue
            if isinstance(step, SkillAction):
                proposal = step.proposal
                if proposal.action_id not in BOUNDED_PRIMITIVES:
                    raise ValueError("primitive lacks a bounded skill execution contract")
                if proposal.action_id in {"face_player", "wait_here"}:
                    duration += proposal.arguments.duration_seconds or 30
                elif proposal.action_id == "follow_player":
                    if len(stack) > 1:
                        raise ValueError("composed follow requires a scoped duration contract")
                    # Follow has no independent time limit. The whole program's
                    # deadline bounds it, and the adapter must bound its distance.
                    duration += definition.max_duration_seconds
                    distance += proposal.arguments.max_follow_meters or 30
            operations.append(canonical(step.model_dump(mode="json")))
            if len(operations) > MAX_EXPANDED_STEPS:
                raise ValueError("expanded skill operation limit exceeded")
        if duration - start_duration > definition.max_duration_seconds:
            raise ValueError("composed duration exceeds skill limit")
        if distance - start_distance > definition.max_distance_meters:
            raise ValueError("composed distance exceeds skill limit")

    visit(root, ())
    if not any(json.loads(op)["op"] == "action" for op in operations):
        raise ValueError("skill requires an executable operation")
    source = canonical(root.model_dump(mode="json"))
    dependency_pairs = tuple(sorted(dependencies.items()))
    fingerprint = hashlib.sha256(
        canonical([source, dependency_pairs, registry_fingerprint]).encode()
    ).hexdigest()
    return CompiledSkill(
        source,
        fingerprint,
        registry_fingerprint,
        dependency_pairs,
        tuple(operations),
        root.max_duration_seconds,
        root.max_distance_meters,
    )


async def join_cleanup(awaitable):
    task = asyncio.ensure_future(awaitable)
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
        except BaseException:
            break
    if canceled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class SkillInterpreter:
    def __init__(self, *, enabled=False, clock=time.monotonic):
        self.enabled = enabled
        self.clock = clock
        self.policy = ActionPolicy(clock=clock)

    async def run(
        self,
        compiled,
        identity,
        *,
        environment,
        execute,
        cancel,
        library=None,
        registry_fingerprint=REGISTRY_SHA256,
    ):
        """Run through injected policy/executor boundaries, never GTA natives.

        execute(identity, operation_id, proposal, deadline) must return a receipt
        from the validated bridge. cancel(identity, operation_id) must join the
        cleanup of only this interpreter's outstanding command.
        """
        started = self.clock()
        completed, traveled = 0, 0.0
        active_operation = None
        run_id = uuid4().hex

        def result(status, reason):
            return SkillResult(
                status, reason, compiled.fingerprint, completed, traveled, self.clock() - started
            )

        if not self.enabled:
            return result("rejected", "development_skill_runtime_disabled")
        current = compile_skill(compiled.source, library, registry_fingerprint=registry_fingerprint)
        # Compare every immutable field. A forged operation tuple cannot borrow
        # the fingerprint of a valid program.
        if current != compiled:
            return result("rejected", "skill_or_dependency_requires_revalidation")
        deadline = started + compiled.duration_seconds
        try:
            async with asyncio.timeout(compiled.duration_seconds):
                for index, raw in enumerate(compiled.operations):
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    snapshot = environment(identity)
                    if snapshot.identity != identity:
                        return result("interrupted", "actor_binding_changed")
                    if self.clock() >= deadline:
                        return result("interrupted", "skill_deadline")
                    operation = json.loads(raw)
                    if operation["op"] == "require":
                        condition = operation["condition"]
                        value = (
                            snapshot.consent
                            if condition == "consent"
                            else snapshot.actor.get(condition) is True
                        )
                        if not value:
                            return result("refused", "precondition_" + condition)
                        continue
                    proposal = SkillAction.model_validate(operation).proposal
                    decision = self.policy.check(
                        proposal,
                        snapshot.actor,
                        snapshot.world,
                        snapshot.capabilities,
                        snapshot.consent,
                    )
                    if not decision.allowed:
                        return result("refused", decision.reason)
                    active_operation = "skill-" + run_id + "-" + str(index)
                    receipt = PrimitiveReceipt.model_validate(
                        await execute(identity, active_operation, proposal, deadline)
                    )
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    if receipt.operation_id != active_operation:
                        return result("failed", "primitive_receipt_mismatch")
                    traveled += receipt.distance_meters
                    if traveled > compiled.distance_meters:
                        return result("failed", "primitive_distance_limit_exceeded")
                    if receipt.status != "completed":
                        return result(receipt.status, "primitive_" + receipt.status)
                    active_operation = None
                    completed += 1
                    if environment(identity).identity != identity:
                        return result("interrupted", "actor_binding_changed")
                    if self.clock() >= deadline:
                        return result("interrupted", "skill_deadline")
                return result("completed", "all_operations_completed")
        except TimeoutError:
            return result("interrupted", "skill_deadline")
        except asyncio.CancelledError:
            if active_operation is not None:
                try:
                    await join_cleanup(cancel(identity, active_operation))
                except (Exception, asyncio.CancelledError):
                    # Cleanup failures cannot replace the original cancellation.
                    pass
                active_operation = None
            raise
        finally:
            if active_operation is not None:
                await join_cleanup(cancel(identity, active_operation))
