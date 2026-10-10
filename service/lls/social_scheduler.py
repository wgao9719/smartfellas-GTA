"""Bounded development social initiation. Hearing always arrives through the bridge."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from math import dist

from pydantic import Field

from .protocol import StrictModel
from .social_adaptation import SocialAdaptation
from .social_bridge import DEVELOPMENT_SOCIAL_CAPABILITY, SocialInvite, SocialReview, SocialTurn
from .storage import _date


class SocialSchedulerConfig(StrictModel):
    max_calls_per_minute: int = Field(default=8, ge=4, le=30)
    max_calls_per_session: int = Field(default=24, ge=4, le=100)
    max_scene_attempts: int = Field(default=4, ge=1, le=16)
    max_concurrent_workers: int = Field(default=1, ge=1, le=2)
    max_turns_per_scene: int = Field(default=4, ge=2, le=6)
    actor_cooldown_seconds: float = Field(default=60, ge=10, le=600)
    pair_cooldown_seconds: float = Field(default=120, ge=30, le=1800)
    boundary_cooldown_game_seconds: float = Field(default=600, ge=30, le=3600)
    hearing_timeout_seconds: float = Field(default=8, ge=1, le=30)
    scene_timeout_seconds: float = Field(default=60, ge=5, le=120)
    worker_timeout_seconds: float = Field(default=45, ge=1, le=90)


@dataclass
class _Schedule:
    calls: int = 0
    recent_calls: deque = field(default_factory=deque)
    attempts: int = 0
    pair_attempts: dict = field(default_factory=dict)
    pair_last: dict = field(default_factory=dict)
    actor_last: dict = field(default_factory=dict)
    initiations: dict = field(default_factory=dict)
    scene_id: str | None = None
    last_scene_id: str | None = None
    scene_started: float = 0
    pending_command: str | None = None
    awaiting_since: float | None = None
    ready: bool = False
    turns: int = 0
    status: str = "idle"
    reason: str = ""
    last_wait_reason: str = ""
    abort_reason: str | None = None


class SocialScheduler:
    def __init__(self, runtime, config=None):
        self.runtime = runtime
        self.config = SocialSchedulerConfig.model_validate(
            (config or SocialSchedulerConfig()).model_dump()
        )
        self.states = {}
        self.workers = {}
        self.adaptation = SocialAdaptation(runtime.repo)

    def diagnostics(self, session_id):
        state = self.states.get(session_id)
        return {
            "enabled": self.runtime.settings.dev_social_autonomy,
            "status": state.status if state else "idle",
            "reason": state.reason if state else "",
            "last_wait_reason": state.last_wait_reason if state else "",
            "scene_id": state.scene_id if state else None,
            "last_scene_id": state.last_scene_id if state else None,
            "pending_command_id": state.pending_command if state else None,
            "reserved_model_calls": state.calls if state else 0,
            "scene_attempts": state.attempts if state else 0,
            "queued_turns": state.turns if state else 0,
            "worker_active": session_id in self.workers and not self.workers[session_id].done(),
            "limits": self.config.model_dump(),
            "native_verified": False,
            "hearing_source": "external_social_delivery_evidence",
        }

    def _authorized(self, session):
        settings = self.runtime.settings
        return (
            settings.dev_social_autonomy
            and settings.dev_social
            and not settings.release
            and not session.closed
            and DEVELOPMENT_SOCIAL_CAPABILITY in session.request.manifest.capability_flags
        )

    def _reserve(self, state, count):
        now = self.runtime.clock()
        while state.recent_calls and now - state.recent_calls[0] >= 60:
            state.recent_calls.popleft()
        if (
            state.calls + count > self.config.max_calls_per_session
            or len(state.recent_calls) + count > self.config.max_calls_per_minute
        ):
            return False
        state.calls += count
        state.recent_calls.extend([now] * count)
        return True

    def _capacity_available(self):
        return (
            sum(not task.done() for task in self.workers.values())
            < self.config.max_concurrent_workers
        )

    def expired(self, session_id, scene_id):
        state = self.states.get(session_id)
        return bool(
            state
            and state.scene_id == scene_id
            and self.runtime.clock() - state.scene_started >= self.config.scene_timeout_seconds
        )

    @staticmethod
    def _scope(session):
        return (
            session.request.profile_id,
            session.branch_id,
            session.request.protagonist_id,
            session.request.runtime_generation,
            session.game_datetime,
            session.epoch,
        )

    def _current_candidates(self, session, actors, scope, bindings):
        """Recheck live authority after storage yields, without another await."""
        world = session.world
        now = self.runtime.clock()
        if (
            not self._authorized(session)
            or self._scope(session) != scope
            or not world
            or not 0 <= now - world.get("_sampled_at", -float("inf")) <= 2
            or world.get("protagonist_id") != session.request.protagonist_id
            or any(
                world.get(flag, True)
                for flag in (
                    "online", "mission_active", "cutscene_active", "random_event_active", "paused"
                )
            )
            or any(
                task and not task.done()
                for task in (session.task, session.skill_task, session.social_task)
            )
            or self.runtime.skill_scheduler_active(session)
            or session.speech_state != "idle"
            or any(not scene["canceled"] for scene in session.social_scenes.values())
            or any(
                command["kind"] in {"dialogue.chunk", "action.execute"}
                and command["state"]
                not in {"completed", "failed", "rejected", "expired", "canceled"}
                for command in session.commands.values()
            )
        ):
            return {}
        current = {}
        for actor_id in actors:
            actor = session.actors.get(actor_id)
            if (
                actor
                and actor.get("runtime_instance_id") == bindings.get(actor_id)
                and self.runtime.conversation_policy.check(actor, world).allowed
                and 0 <= now - actor.get("_sampled_at", -float("inf")) <= 2
                and actor.get("area_id")
                and actor.get("interior_id")
                and actor.get("distance", float("inf")) <= 20
            ):
                current[actor_id] = actor
        return current

    def _pair(self, state, actors, history, game_datetime):
        now = self.runtime.clock()
        game_now = datetime.fromisoformat(_date(game_datetime))
        available = {}
        for actor_id, actor in actors.items():
            own = history.get(actor_id, {})
            boundary = own.get("last_boundary_at")
            count = max(1, min(3, own.get("recent_boundary_count", 1)))
            pause = min(3600, self.config.boundary_cooldown_game_seconds * count)
            if boundary and 0 <= (
                game_now - datetime.fromisoformat(_date(boundary))
            ).total_seconds() < pause:
                continue
            available[actor_id] = actor
        pairs = []
        for first, second in combinations(sorted(available), 2):
            a, b = available[first], available[second]
            pair = (first, second)
            if (
                a["area_id"] != b["area_id"]
                or a["interior_id"] != b["interior_id"]
                or a["runtime_instance_id"] == b["runtime_instance_id"]
                or dist(
                    tuple(a["position"][axis] for axis in ("x", "y", "z")),
                    tuple(b["position"][axis] for axis in ("x", "y", "z")),
                )
                > 10
                or now - state.pair_last.get(pair, -float("inf"))
                < self.config.pair_cooldown_seconds
                or any(
                    now - state.actor_last.get(actor, -float("inf"))
                    < self.config.actor_cooldown_seconds
                    for actor in pair
                )
            ):
                continue
            pairs.append(pair)
        if not pairs:
            return None

        def initiator(pair):
            return min(pair, key=lambda actor: (state.initiations.get(actor, 0), actor))

        def priority(pair):
            owner = initiator(pair)
            peer = next(actor for actor in pair if actor != owner)
            contact = history.get(owner, {}).get("peers", {}).get(peer, {})
            count = contact.get("confirmed_scene_count", 0)
            # Repeated heard scenes affect only this owner's partner preference.
            # Least-attempted pair fairness remains ahead of familiarity.
            familiarity = min(3, count) if count >= 2 else 0
            return (
                state.pair_attempts.get(pair, 0),
                -familiarity,
                state.pair_last.get(pair, -float("inf")),
                pair,
            )

        pair = min(
            pairs,
            key=priority,
        )
        owner = initiator(pair)
        return (owner, next(actor for actor in pair if actor != owner))

    def _envelope(self, session, scene_id):
        return {
            "session_id": session.id,
            "runtime_generation": session.request.runtime_generation,
            "protagonist_id": session.request.protagonist_id,
            "scene_id": scene_id,
            "game_datetime": datetime.fromisoformat(session.game_datetime),
        }

    async def tick(self, session):
        """Schedule one bounded worker. Provider waits never hold session.lock."""
        if not self._authorized(session):
            return
        active = self.workers.get(session.id)
        if active is not None and not active.done():
            state = self.states.get(session.id)
            if (
                state
                and state.scene_id
                and self.runtime.clock() - state.scene_started >= self.config.scene_timeout_seconds
            ):
                active.cancel()
                await self.runtime.thread_join_task(active)
                async with session.lock:
                    await self._finish_locked(session, state, "scene_time_limit")
            return
        async with session.lock:
            if (
                not self._authorized(session)
                or (session.social_task and not session.social_task.done())
                or (session.id in self.workers and not self.workers[session.id].done())
            ):
                return
            state = self.states.setdefault(session.id, _Schedule())
            now = self.runtime.clock()
            if state.scene_id:
                if state.abort_reason:
                    await self._finish_locked(session, state, state.abort_reason)
                    return
                current = session.social_scenes.get(state.scene_id)
                if not current or current["canceled"]:
                    state.scene_id = None
                    state.pending_command = None
                    state.awaiting_since = None
                    state.ready = False
                    state.status = "canceled"
                    state.reason = state.reason or "scene_unavailable"
                    return
                reason = state.abort_reason
                if now - state.scene_started >= self.config.scene_timeout_seconds:
                    reason = reason or "scene_time_limit"
                if (
                    state.awaiting_since is not None
                    and now - state.awaiting_since >= self.config.hearing_timeout_seconds
                ):
                    reason = reason or "waiting_for_hearing"
                if state.turns >= self.config.max_turns_per_scene and state.ready:
                    reason = reason or "turn_limit"
                if reason:
                    await self._finish_locked(session, state, reason)
                    return
                if not state.ready:
                    return
                if not self._capacity_available():
                    return
                if not self._reserve(state, 2):
                    await self._finish_locked(session, state, "model_call_limit")
                    return
                state.ready = False
                pair = None
            else:
                if any(not value["canceled"] for value in session.social_scenes.values()):
                    return
                if state.attempts >= self.config.max_scene_attempts:
                    state.status, state.reason = "limited", "scene_attempt_limit"
                    return
                scope = self._scope(session)
                bindings = {
                    actor_id: actor["runtime_instance_id"]
                    for actor_id, actor in session.actors.items()
                }
                actors = await self.runtime.social_scheduler_candidates(session)
                actors = self._current_candidates(session, actors, scope, bindings)
                if len(actors) < 2:
                    return
                try:
                    history = await self.runtime.thread_job(
                        self.adaptation.read,
                        session.request.profile_id,
                        session.branch_id,
                        session.request.protagonist_id,
                        session.game_datetime,
                        tuple(sorted(actors)),
                        session_id=session.id,
                    )
                except Exception:
                    state.status, state.reason = "waiting", "social_history_unavailable"
                    return
                actors = self._current_candidates(session, actors, scope, bindings)
                pair = self._pair(state, actors, history, session.game_datetime)
                if pair is None:
                    return
                # Candidate lookup yields to other sessions. Reserve global worker
                # capacity only after it returns, with no await before creation.
                if not self._capacity_available():
                    return
                # Two independent invitation decisions, one participation review,
                # and one turn. Early refusal does not refund this scheduler cap.
                if not self._reserve(state, 4):
                    state.status, state.reason = "limited", "model_call_limit"
                    return
                now = self.runtime.clock()
                state.attempts += 1
                key = tuple(sorted(pair))
                state.pair_attempts[key] = state.pair_attempts.get(key, 0) + 1
                state.pair_last[key] = now
                state.initiations[pair[0]] = state.initiations.get(pair[0], 0) + 1
                state.actor_last.update({actor: now for actor in pair})
                state.scene_id = state.last_scene_id = self.runtime.id_factory("social_auto")
                state.scene_started = now
                state.turns = 0
                state.abort_reason = None
                state.pending_command = None
                state.awaiting_since = None
                pair = {actor: actors[actor]["runtime_instance_id"] for actor in pair}
            state.status, state.reason = "running", ""
            worker = asyncio.create_task(self._work(session, state, pair))
            self.workers[session.id] = worker

    async def _work(self, session, state, participants):
        remaining = max(
            0, self.config.scene_timeout_seconds - (self.runtime.clock() - state.scene_started)
        )
        scene_deadline = remaining <= self.config.worker_timeout_seconds
        try:
            async with asyncio.timeout(min(remaining, self.config.worker_timeout_seconds)):
                if participants:
                    result = await self.runtime.social_call(
                        session,
                        SocialInvite(
                            **self._envelope(session, state.scene_id),
                            initiator_id=next(iter(participants)),
                            participants=participants,
                        ),
                        "invite",
                    )
                    if result["status"] != "active":
                        state.status, state.reason = result["status"], result["reason"]
                        state.scene_id = None
                        return
                summary = self.runtime.social.get_scene(state.scene_id)
                actor = summary["turn_owner"]
                review = await self.runtime.social_call(
                    session,
                    SocialReview(
                        **self._envelope(session, state.scene_id),
                        actor_id=actor,
                    ),
                    "review",
                )
                if review["status"] != "active":
                    state.status, state.reason = review["status"], review["reason"]
                    state.scene_id = None
                    return
                turn = await self.runtime.social_call(
                    session,
                    SocialTurn(
                        **self._envelope(session, state.scene_id),
                        actor_id=actor,
                    ),
                    "turn",
                )
                state.pending_command = turn["command_id"]
                state.awaiting_since = self.runtime.clock()
                state.turns += 1
                state.status, state.reason = "waiting", "waiting_for_hearing"
                state.last_wait_reason = "waiting_for_hearing"
        except asyncio.CancelledError:
            state.status = "canceled"
            state.reason = state.reason or "runtime_interrupted"
            raise
        except TimeoutError:
            state.status = "stopping"
            state.abort_reason = "scene_time_limit" if scene_deadline else "worker_time_limit"
        except Exception:
            state.status = "stopping"
            state.abort_reason = (
                "scene_time_limit"
                if self.expired(session.id, state.scene_id)
                else "social_operation_failed"
            )
        finally:
            # A caller may hold session.lock while joining this worker. Cleanup
            # deliberately takes no session lock and performs no new model call.
            if self.workers.get(session.id) is asyncio.current_task():
                self.workers.pop(session.id, None)

    def delivered(self, session, scene_id, command_id, result):
        state = self.states.get(session.id)
        if not state or state.scene_id != scene_id or state.pending_command != command_id:
            return
        if result.get("duplicate"):
            return
        state.pending_command = None
        state.awaiting_since = None
        if result.get("status") == "completed":
            state.status, state.reason = "completed", "engine_turn_limit"
            state.scene_id = None
            return
        next_actor = self.runtime.social.get_scene(scene_id)["turn_owner"]
        if next_actor not in result.get("heard", {}):
            state.abort_reason = "listener_not_confirmed"
            state.status, state.reason = "stopping", "listener_not_confirmed"
            return
        state.ready = True
        state.status, state.reason = "ready", "listener_confirmed"

    async def _finish_locked(self, session, state, reason):
        scene_id = state.scene_id
        if scene_id:
            current = session.social_scenes.get(scene_id)
            if current:
                current["canceled"] = True
            if any(
                scene["scene_id"] == scene_id
                for scene in self.runtime.social.scenes_for_session(session.id)
            ):
                await self.runtime.social.cancel_scene(scene_id, reason=reason)
            for command in session.commands.values():
                if command.get("_social", {}).get("scene_id") == scene_id and command[
                    "state"
                ] not in {"completed", "failed", "rejected", "expired", "canceled"}:
                    command["state"] = "canceled"
            self.runtime.command(session, "scene.cancel", None, {"scene_id": scene_id})
        state.scene_id = None
        state.pending_command = None
        state.awaiting_since = None
        state.ready = False
        state.abort_reason = None
        state.status = "completed" if reason == "turn_limit" else "canceled"
        state.reason = reason

    def cancel(self, session):
        worker = self.workers.get(session.id)
        if worker and worker is not asyncio.current_task() and not worker.done():
            worker.cancel()
        state = self.states.get(session.id)
        if state and state.scene_id:
            if self.expired(session.id, state.scene_id):
                state.reason = "scene_time_limit"
            state.scene_id = None
            state.pending_command = None
            state.awaiting_since = None
            state.ready = False
            state.status = "canceled"
            state.reason = state.reason or "runtime_interrupted"

    async def join(self, session, *, erase=False):
        self.cancel(session)
        worker = self.workers.get(session.id)
        if worker and worker is not asyncio.current_task():
            await self.runtime.thread_join_task(worker)
        if erase:
            self.states.pop(session.id, None)

    def forget(self, session_id):
        self.states.pop(session_id, None)
