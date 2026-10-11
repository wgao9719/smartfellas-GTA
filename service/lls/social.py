"""Development-only, recipient-owned social scenes with no native action authority."""

import asyncio
import json
import time
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import dist
from uuid import uuid4

from .display import DISPLAY_MAX_CHARS, display_text
from .gateway import GatewayProvider
from .perception import DeliveredUtterance, Incident, PerceptionLimits, Witness, witnesses_for
from .providers import FakeProvider
from .social_models import (
    SocialConfig,
    SocialDecision,
    SocialEnvironment,
    SocialStart,
    SocialUtterance,
)
from .social_storage import SocialStore
from .storage import _date


class SocialError(ValueError):
    pass


class SocialUnavailable(SocialError):
    pass


class SocialConflict(SocialError):
    pass


TERMINAL = {"refused", "withdrawn", "completed", "canceled", "expired"}
PERSISTENT = {"curated_persistent", "spawned_persistent", "anchored_service"}


class FakeSocialProvider(FakeProvider):
    """Explicit simulation using each actor's own authored preferences and opinions."""

    async def dialogue(self, context):
        social = context.get("social")
        if social is None:
            async for part in super().dialogue(context):
                yield part
            return
        persona = json.loads(context["actor"]["persona"])
        preferences = persona.get("social", {})
        if social["phase"] == "invitation":
            choice = "accept" if preferences.get("accept_invitations") is True else "refuse"
            yield json.dumps({"decision": choice})
        elif social["phase"] == "participation":
            choice = "withdraw" if preferences.get("withdraw") is True else "continue"
            yield json.dumps({"decision": choice})
        elif social["retell_text"] is not None:
            yield social["retell_text"]
        elif social.get("topic_continuation"):
            topic = social["topic_continuation"]
            yield f"{topic['teller_id']}, you said: {topic['text']} What do you think now?"
        else:
            yield str(preferences.get("opinion", "I enjoy a quiet conversation."))[:500]


@dataclass
class _Scene:
    start: SocialStart
    revalidate: object
    created_at: float
    expires_at: float
    status: str = "inviting"
    reason: str = ""
    epoch: int = 0
    turn_index: int = 0
    decisions: dict = field(default_factory=dict)
    heard: dict = field(default_factory=dict)
    pending: SocialUtterance | None = None
    delivered: dict = field(default_factory=dict)
    worker: asyncio.Task | None = None
    storage_task: asyncio.Task | None = None
    operation: asyncio.Task | None = None


class SocialEngine:
    def __init__(
        self,
        repository,
        gateway_provider,
        *,
        config=None,
        clock=time.monotonic,
        utc_now=None,
        id_factory=None,
    ):
        if not isinstance(gateway_provider, GatewayProvider):
            raise TypeError("social calls require GatewayProvider")
        self.repository = repository
        self.provider = gateway_provider
        self.config = SocialConfig.model_validate((config or SocialConfig()).model_dump())
        self.clock = clock
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (lambda prefix: prefix + "_" + uuid4().hex)
        self.store = SocialStore(repository)
        self.scenes = {}
        self.cooldowns = {}
        self.closed = False
        self.memory_index = None

    def replace_provider(self, provider):
        if any(s.operation and not s.operation.done() for s in self.scenes.values()):
            raise SocialConflict("join social work before replacing its provider")
        if not isinstance(provider, GatewayProvider):
            raise TypeError("social calls require GatewayProvider")
        self.provider = provider

    def _scene(self, scene_id):
        if scene_id not in self.scenes:
            raise SocialUnavailable("unknown social scene")
        return self.scenes[scene_id]

    def get_scene(self, scene_id):
        scene = self._scene(scene_id)
        participants = list(scene.start.participant_bindings)
        return {
            "scene_id": scene_id,
            "session_id": scene.start.session_id,
            "status": scene.status,
            "reason": scene.reason,
            "epoch": scene.epoch,
            "participant_ids": participants,
            "decisions": dict(scene.decisions),
            "turn_index": scene.turn_index,
            "turn_cap": self.config.max_turns,
            "turn_owner": (
                participants[scene.turn_index % len(participants)]
                if scene.status == "active"
                else None
            ),
            "pending_utterance_id": scene.pending.utterance_id if scene.pending else None,
            "game_verified": False,
        }

    def scenes_for_session(self, session_id):
        return [
            self.get_scene(key)
            for key, value in self.scenes.items()
            if value.start.session_id == session_id
        ]

    def _terminal(self, scene, status, reason):
        scene.status = status
        scene.reason = reason
        scene.epoch += 1
        scene.pending = None

    def _guard(self, scene):
        if self.closed or scene.status in TERMINAL:
            raise SocialUnavailable("social scene is no longer active")
        if self.clock() >= scene.expires_at:
            self._terminal(scene, "expired", "duration_limit")
            raise SocialUnavailable("social scene expired")
        try:
            source = scene.revalidate()
            env = SocialEnvironment.model_validate(source.model_dump())
            start = scene.start
            for name in (
                "profile_id",
                "branch_id",
                "session_id",
                "protagonist_id",
                "runtime_generation",
            ):
                if getattr(env, name) != getattr(start, name):
                    raise SocialUnavailable("social scope changed")
            now = self.utc_now()
            if (
                not 0
                <= (now - env.sampled_at).total_seconds()
                <= self.config.max_sample_age_seconds
            ):
                raise SocialUnavailable("world snapshot is stale")
            if (
                not env.player_present
                or env.foreground_dialogue
                or env.danger
                or env.online
                or env.mission_active
                or env.cutscene_active
                or env.random_event_active
                or env.paused
            ):
                raise SocialUnavailable("social world is unavailable")
            if _date(env.game_datetime.isoformat()) < _date(start.game_datetime.isoformat()):
                raise SocialUnavailable("social game time moved backwards")
            positions = []
            for actor_id, binding in start.participant_bindings.items():
                actor = self.repository.get_actor(start.profile_id, actor_id)
                state = env.actors.get(actor_id)
                if actor is None or actor["actor_kind"] not in PERSISTENT or state is None:
                    raise SocialUnavailable("persistent participant is absent")
                age = (now - state.sampled_at).total_seconds()
                if (
                    state.actor_id != actor_id
                    or state.runtime_instance_id != binding
                    or state.area_id != start.area_id
                    or state.interior_id != start.interior_id
                    or not state.exists
                    or not state.alive
                    or state.busy
                    or state.mission_entity
                    or state.in_combat
                    or not 0 <= age <= self.config.max_sample_age_seconds
                    or state.ownership not in {"ambient", "mod_owned"}
                    or state.control_policy not in {"ambient", "service_limited"}
                    or actor.get("control_policy", "ambient") == "protected"
                    or actor.get("flags", {}).get("no_spontaneous_speech")
                ):
                    raise SocialUnavailable("social participant is unavailable")
                if (
                    state.control_policy == "service_limited"
                    or actor.get("control_policy") == "service_limited"
                    or actor["actor_kind"] == "anchored_service"
                ) and (
                    state.control_policy != "service_limited"
                    or not state.service_integration_id
                    or state.service_integration_id != actor.get("service_integration_id")
                ):
                    raise SocialUnavailable("service participant lacks a specific integration")
                positions.append(tuple(state.position.model_dump().values()))
            if any(
                dist(a, b) > self.config.max_distance_meters for a in positions for b in positions
            ):
                raise SocialUnavailable("social participant departed")
            return env
        except Exception as exc:
            self._terminal(scene, "canceled", "eligibility_changed")
            if isinstance(exc, SocialUnavailable):
                raise
            raise SocialUnavailable("social revalidation failed") from exc

    def _claim(self, scene):
        if scene.operation and not scene.operation.done():
            raise SocialConflict("one social operation is already active")
        self._guard(scene)
        scene.operation = asyncio.current_task()

    async def _storage(self, scene, method, *args, **kwargs):
        self._guard(scene)
        job = asyncio.create_task(asyncio.to_thread(method, *args, **kwargs))
        scene.storage_task = job
        try:
            try:
                result = await asyncio.shield(job)
            except asyncio.CancelledError:
                while not job.done():
                    try:
                        await asyncio.shield(job)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if not job.cancelled():
                    job.exception()
                raise
            self._guard(scene)
            return result
        except ValueError as exc:
            if isinstance(exc, SocialError):
                raise
            raise SocialError(str(exc)) from exc
        finally:
            scene.storage_task = None

    async def _model(self, scene, actor_id, phase, retelling=None):
        env = self._guard(scene)
        epoch = scene.epoch
        memories = None
        if self.memory_index is not None:
            memories = await self.memory_index.retrieve_memories(
                scene.start.profile_id,
                scene.start.branch_id,
                actor_id,
                scene.start.protagonist_id,
                env.game_datetime.isoformat(),
                "conversation acquaintances social",
                token_budget=384,
                session_id=scene.start.session_id,
                budget_class="background",
            )
            env = self._guard(scene)
            if scene.epoch != epoch:
                raise SocialUnavailable("social retrieval became stale")
        context = await self._storage(
            scene,
            self.store.context,
            scene.start,
            actor_id,
            env,
            phase=phase,
            heard=scene.heard.get(actor_id, []),
            retelling=retelling,
            byte_budget=self.config.context_byte_budget,
            memories=memories,
            memory_index=self.memory_index,
        )

        async def consume():
            output = ""
            async with aclosing(
                self.provider.dialogue(context, budget_class="background")
            ) as stream:
                async for part in stream:
                    self._guard(scene)
                    if scene.epoch != epoch:
                        raise SocialUnavailable("social generation became stale")
                    output += part
                    if len(output) > self.config.max_text_characters:
                        raise SocialError("social model output exceeds its bound")
            return output

        worker = asyncio.create_task(consume())
        scene.worker = worker
        try:
            _, pending = await asyncio.wait({worker}, timeout=self.config.decision_timeout_seconds)
            if pending:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                raise SocialUnavailable("social model call timed out")
            result = worker.result()
            self._guard(scene)
            if scene.epoch != epoch:
                raise SocialUnavailable("social generation became stale")
            return result
        except asyncio.CancelledError:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise
        finally:
            scene.worker = None

    @staticmethod
    def _decision(text, allowed):
        def unique_keys(items):
            values = {}
            for key, value in items:
                if key in values:
                    raise ValueError("duplicate social decision key")
                values[key] = value
            return values

        try:
            values = json.loads(text, object_pairs_hook=unique_keys)
            if not isinstance(values, dict) or set(values) != {"decision"}:
                return None
            value = SocialDecision.model_validate(values).decision
            return value if value in allowed else None
        except (ValueError, TypeError, IndexError):
            return None

    async def invite(self, start, revalidate):
        if self.closed or not self.config.enabled:
            raise SocialUnavailable("development social scenes are disabled")
        start = SocialStart.model_validate(start.model_dump())
        if (
            not start.development_attestation
            or start.initiator_id not in start.participant_bindings
        ):
            raise SocialUnavailable("social invitation lacks development authority")
        if start.scene_id in self.scenes:
            raise SocialConflict("social scene identifier was already used")
        if len(self.scenes) >= 64:
            raise SocialUnavailable("retained social scene capacity reached")
        if (
            len([s for s in self.scenes.values() if s.status not in TERMINAL])
            >= self.config.max_active_scenes
        ):
            raise SocialUnavailable("social scene capacity reached")
        now = self.clock()
        self.cooldowns = {key: expiry for key, expiry in self.cooldowns.items() if now < expiry}
        if len(self.cooldowns) > 509:
            raise SocialUnavailable("social cooldown capacity reached")
        for actor_id in start.participant_bindings:
            if now < self.cooldowns.get((start.profile_id, actor_id), 0):
                raise SocialUnavailable("social participant cooldown is active")
            if any(
                s.status not in TERMINAL
                and s.start.profile_id == start.profile_id
                and actor_id in s.start.participant_bindings
                for s in self.scenes.values()
            ):
                raise SocialConflict("actor already owns another social scene")
        start.participant_bindings = {
            start.initiator_id: start.participant_bindings[start.initiator_id],
            **start.participant_bindings,
        }
        scene = _Scene(start, revalidate, now, now + self.config.max_duration_seconds)
        self.scenes[start.scene_id] = scene
        self._claim(scene)
        try:
            for actor_id in start.participant_bindings:
                self.cooldowns[(start.profile_id, actor_id)] = (
                    now + self.config.scene_cooldown_seconds
                )
            for actor_id in start.participant_bindings:
                text = await self._model(scene, actor_id, "invitation")
                decision = self._decision(text, {"accept", "refuse"})
                if decision is None:
                    # An unavailable model decision closes this attempt. It is not
                    # the actor's preference and must not become durable history.
                    scene.decisions[actor_id] = "refuse"
                    self._terminal(scene, "refused", "invalid_invitation_decision")
                    return self.get_scene(start.scene_id)
                env = self._guard(scene)
                await self._storage(
                    scene,
                    self.store.decision,
                    start,
                    actor_id,
                    decision,
                    self.id_factory("social_decision"),
                    env.game_datetime,
                )
                scene.decisions[actor_id] = decision
                if decision == "refuse":
                    self._terminal(scene, "refused", "recipient_refused")
                    return self.get_scene(start.scene_id)
            scene.status = "active"
            return self.get_scene(start.scene_id)
        except BaseException:
            if scene.status not in TERMINAL:
                self._terminal(scene, "canceled", "invitation_interrupted")
            raise
        finally:
            scene.operation = None

    async def generate_turn(self, scene_id, actor_id, *, retell_event_id=None):
        scene = self._scene(scene_id)
        self._claim(scene)
        try:
            summary = self.get_scene(scene_id)
            if scene.status != "active" or summary["turn_owner"] != actor_id:
                raise SocialConflict("actor does not own this social turn")
            if scene.pending is not None:
                raise SocialConflict("the current social utterance awaits delivery")
            env = self._guard(scene)
            retelling = None
            if retell_event_id:
                retelling = await self._storage(
                    scene,
                    self.store.retelling_source,
                    scene.start,
                    actor_id,
                    retell_event_id,
                    env.game_datetime,
                )
            # Delivery receipts must quote exactly what the subtitle shows.
            text = display_text(
                await self._model(scene, actor_id, "turn", retelling), DISPLAY_MAX_CHARS
            )
            if not text.strip() or (retelling is not None and text != retelling["text"]):
                raise SocialError("social reply did not preserve the requested exact quote")
            scene.pending = SocialUtterance(
                scene_id=scene_id,
                utterance_id=self.id_factory("social_utterance"),
                actor_id=actor_id,
                runtime_instance_id=scene.start.participant_bindings[actor_id],
                text=text,
                turn_index=scene.turn_index,
                scene_epoch=scene.epoch,
                sampled_at=self.utc_now(),
                retell_event_id=retell_event_id,
            )
            return scene.pending.model_copy(deep=True)
        finally:
            scene.operation = None

    async def review_participation(self, scene_id, actor_id):
        scene = self._scene(scene_id)
        self._claim(scene)
        try:
            if scene.status != "active" or actor_id not in scene.start.participant_bindings:
                raise SocialConflict("only participants can decide their continued participation")
            text = await self._model(scene, actor_id, "participation")
            decision = self._decision(text, {"continue", "withdraw"})
            if decision is None:
                scene.decisions[actor_id] = "withdraw"
                self._terminal(scene, "withdrawn", "invalid_participation_decision")
                return self.get_scene(scene_id)
            env = self._guard(scene)
            await self._storage(
                scene,
                self.store.decision,
                scene.start,
                actor_id,
                decision,
                self.id_factory("social_decision"),
                env.game_datetime,
            )
            scene.decisions[actor_id] = decision
            if decision == "withdraw":
                self._terminal(scene, "withdrawn", "participant_withdrew")
            return self.get_scene(scene_id)
        finally:
            scene.operation = None

    async def acknowledge_delivery(self, scene_id, utterance_id, receipt, witnesses):
        scene = self._scene(scene_id)
        receipt = DeliveredUtterance.model_validate(receipt.model_dump())
        if utterance_id in scene.delivered:
            previous = scene.delivered[utterance_id]
            if previous["receipt"] != receipt.model_dump(mode="json"):
                raise SocialConflict("delivery identifier was reused with different evidence")
            return {**deepcopy(previous["result"]), "duplicate": True}
        self._claim(scene)
        try:
            witnesses = [Witness.model_validate(w.model_dump()) for w in witnesses]
            pending = scene.pending
            if pending is None or pending.utterance_id != utterance_id:
                raise SocialConflict("unknown pending social utterance")
            if (
                pending.scene_epoch != scene.epoch
                or receipt.actor_id != pending.actor_id
                or receipt.runtime_instance_id != pending.runtime_instance_id
                or receipt.text != pending.text
                or receipt.sampled_at < pending.sampled_at
            ):
                raise SocialConflict("social delivery does not match the owned exact utterance")
            if (
                not 0
                <= (self.utc_now() - receipt.sampled_at).total_seconds()
                <= self.config.max_sample_age_seconds
            ):
                raise SocialConflict("social delivery receipt is stale or future")
            env = self._guard(scene)
            source = env.actors[pending.actor_id]
            for witness in witnesses:
                state = env.actors.get(witness.actor_id)
                if (
                    witness.actor_id not in scene.start.participant_bindings
                    or state is None
                    or witness.runtime_instance_id != state.runtime_instance_id
                    or witness.position != state.position
                    or witness.area_id != state.area_id
                    or witness.interior_id != state.interior_id
                    or witness.exists != state.exists
                    or witness.alive != state.alive
                    or witness.sampled_at < receipt.sampled_at
                ):
                    raise SocialConflict("hearing witness does not match the current participant")
            incident = Incident(
                event_id=receipt.event_id,
                event_type="actor.spoke",
                sampled_at=receipt.sampled_at,
                position=source.position,
                area_id=source.area_id,
                interior_id=source.interior_id,
                text=receipt.text,
                modality="report",
                source_actor_id=receipt.actor_id,
                confidence=0.7,
                reported_event_id=pending.retell_event_id or receipt.event_id,
            )
            try:
                evidence = witnesses_for(
                    incident,
                    witnesses,
                    self.utc_now(),
                    scene.start.participant_bindings,
                    PerceptionLimits(max_age_seconds=self.config.max_sample_age_seconds),
                    delivered_utterances={receipt.event_id: receipt},
                )
            except ValueError as exc:
                raise SocialConflict(str(exc)) from exc
            heard = await self._storage(
                scene,
                self.store.delivered,
                scene.start,
                pending,
                receipt,
                evidence,
                env.game_datetime,
            )
            for actor, item in heard.items():
                scene.heard[actor] = [*scene.heard.get(actor, []), item][-8:]
            scene.pending = None
            scene.turn_index += 1
            if scene.turn_index >= self.config.max_turns:
                self._terminal(scene, "completed", "turn_cap")
            result = {
                "utterance_id": utterance_id,
                "heard": heard,
                "duplicate": False,
                "status": scene.status,
            }
            scene.delivered[utterance_id] = {
                "receipt": receipt.model_dump(mode="json"),
                "result": result,
            }
            return deepcopy(result)
        finally:
            scene.operation = None

    async def cancel_scene(self, scene_id, reason="canceled"):
        scene = self._scene(scene_id)
        if scene.status not in TERMINAL:
            self._terminal(scene, "canceled", reason[:128])
        current = asyncio.current_task()
        tasks = {
            task
            for task in (scene.operation, scene.worker)
            if task is not None and task is not current and not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if scene.storage_task is not None:
            await asyncio.gather(scene.storage_task, return_exceptions=True)

    async def cancel_session(self, session_id, reason="session_closed"):
        for scene_id, scene in list(self.scenes.items()):
            if scene.start.session_id == session_id:
                await self.cancel_scene(scene_id, reason)

    async def forget_session(self, session_id):
        await self.cancel_session(session_id, "session_erased")
        for scene_id, scene in list(self.scenes.items()):
            if scene.start.session_id == session_id:
                scene.heard.clear()
                scene.delivered.clear()
                scene.revalidate = None
                del self.scenes[scene_id]

    async def aclose(self):
        self.closed = True
        for scene_id in list(self.scenes):
            await self.cancel_scene(scene_id, "shutdown")
