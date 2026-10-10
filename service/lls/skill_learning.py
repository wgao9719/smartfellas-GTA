"""Development-only, source-backed candidate generation without execution authority."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Literal

from pydantic import Field, model_validator

from .gateway import GatewayProvider
from .outbound import raise_if_cancelling
from .skill_store import SkillStore
from .skills import SkillModel, compile_skill, parse_definition


class SkillLearningConfig(SkillModel):
    enabled: bool = False
    release: bool = True
    context_byte_budget: int = Field(default=12000, ge=2048, le=48000)

    @model_validator(mode="after")
    def development_only(self):
        if self.enabled and self.release:
            raise ValueError("release builds refuse development skill learning")
        return self


class SkillRequest(SkillModel):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: int = Field(ge=1, le=1000000)
    action_class: Literal[
        "conversational_courtesy",
        "orientation",
        "stationary_wait",
        "following",
        "bounded_composite",
    ] = "conversational_courtesy"
    source_event_ids: list[str] = Field(min_length=1, max_length=8)
    revises: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_sources(self):
        if len(self.source_event_ids) != len(set(self.source_event_ids)) or any(
            not 1 <= len(value) <= 128 for value in self.source_event_ids
        ):
            raise ValueError("skill needs unique bounded source identifiers")
        return self


class SkillLearningUnavailable(ValueError):
    """Learning cannot proceed in this development context."""


class SkillLearner:
    def __init__(self, store, provider, *, is_current, config=None):
        if not isinstance(store, SkillStore) or not isinstance(provider, GatewayProvider):
            raise ValueError("skill learning requires sourced storage and the provider gateway")
        self.store = store
        self.provider = provider
        self.is_current = is_current
        self.config = SkillLearningConfig.model_validate(config or SkillLearningConfig())
        self._active = set()

    def _current(self, identity):
        raise_if_cancelling()
        if not self.config.enabled or self.config.release:
            raise SkillLearningUnavailable("development skill learning is disabled")
        if self.is_current(identity) is not True:
            raise SkillLearningUnavailable("skill learning identity is no longer current")

    def _snapshot(self, identity, game_datetime, request):
        """Caller holds the repository lock. No network call runs under this lock."""
        need = self.store.need_context(identity, game_datetime, request.source_event_ids)
        library = self.store.library(identity, game_datetime)
        history = self.store.history(identity, game_datetime, skill_id=request.skill_id, limit=128)
        if request.revises is not None:
            previous = next(
                (record for record in history if record["record_id"] == request.revises), None
            )
            if (
                previous is None
                or previous["kind"] not in {"candidate", "trial"}
                or previous["skill_id"] != request.skill_id
                or previous["version"] >= request.version
            ):
                raise ValueError("revision requires an earlier visible own candidate or trial")
        # need.audit_revision also detects reset or delete/reimport races before
        # an actor has its first candidate record.
        return need, library, history

    def _context(self, request, snapshot):
        need, library, history = snapshot
        context = {
            "skill_request": {
                "skill_id": request.skill_id,
                "version": request.version,
                "action_class": request.action_class,
            },
            "need": copy.deepcopy(need),
            "previous_trials": [
                {
                    "record_id": record["record_id"],
                    "skill_id": record["skill_id"],
                    "version": record["version"],
                    "result": record["result"],
                }
                for record in history
                if record["kind"] == "trial"
            ][:4],
            "available_dependencies": [
                {"skill_id": key[0], "version": key[1], "source": json.loads(source)}
                for key, source in sorted(library.items())[:8]
            ],
        }
        context["need"].pop("audit_revision", None)
        if request.revises is not None:
            previous = next(r for r in history if r["record_id"] == request.revises)
            context["previous_candidate"] = json.loads(previous["source"])
        if len(json.dumps(context, ensure_ascii=False, allow_nan=False).encode()) > (
            self.config.context_byte_budget
        ):
            raise ValueError("skill learning context exceeds the configured byte budget")
        return context

    async def generate(self, identity, game_datetime, request):
        """Return a persisted candidate, never an adopted or executed program.

        is_current checks live session, generation, actor, instance, and foreground
        interruption. The host must cancel and join this task during shutdown,
        erasure, or provider replacement, as it does for other background work.
        """
        self._current(identity)
        request = SkillRequest.model_validate(request)
        key = (identity.profile_id, identity.actor_id, identity.protagonist_id)
        if key in self._active:
            raise SkillLearningUnavailable("this actor already has a candidate request")
        self._active.add(key)
        try:
            with self.store.repo._lock:
                before = self._snapshot(identity, game_datetime, request)
                context = self._context(request, before)
            candidate = await self.provider.skill_candidate(context)
            self._current(identity)
            if candidate is None:
                return None
            definition = parse_definition(candidate)
            if (definition.skill_id, definition.version) != (request.skill_id, request.version):
                raise ValueError("candidate changed the requested skill identity")
            if definition.action_class != request.action_class:
                raise ValueError("candidate changed the requested action class")
            with self.store.repo._lock:
                after = self._snapshot(identity, game_datetime, request)
                self._current(identity)
                if before != after:
                    raise SkillLearningUnavailable(
                        "skill learning sources changed during generation"
                    )
                # Dependencies are structural code from this actor's visible
                # repertoire. Compilation grants no native capability or consent.
                offered = {
                    (item["skill_id"], item["version"])
                    for item in context["available_dependencies"]
                }
                dependencies = {key: after[1][key] for key in offered}
                compile_skill(definition, dependencies)
                return self.store.register_candidate(
                    identity,
                    game_datetime,
                    definition,
                    request.source_event_ids,
                    revises=request.revises,
                    expected_audit_revision=after[0]["audit_revision"],
                )
        except asyncio.CancelledError:
            raise
        finally:
            self._active.discard(key)
