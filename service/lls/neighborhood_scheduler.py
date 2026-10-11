"""Bounded development advancement of already initialized virtual neighborhoods."""

import asyncio
import threading
from collections import deque
from copy import copy
from dataclasses import dataclass, field

from pydantic import Field

from .neighborhood import AdvanceBudget, NeighborhoodScope
from .neighborhood_store import NeighborhoodConflict, NeighborhoodStore, instant
from .protocol import StrictModel

CAPABILITY = "development:neighborhood:v1"
TERMINAL = {"completed", "failed", "rejected", "expired", "canceled"}


class NeighborhoodSchedulerConfig(StrictModel):
    interval_seconds: float = Field(default=2, ge=1, le=60)
    max_events_per_step: int = Field(default=64, ge=8, le=256)
    max_events_per_minute: int = Field(default=512, ge=64, le=4096)
    max_events_per_scope: int = Field(default=4096, ge=64, le=65536)
    max_steps_per_scope: int = Field(default=128, ge=1, le=1024)
    max_days_per_step: int = Field(default=1, ge=1, le=3)
    worker_timeout_seconds: float = Field(default=10, ge=1, le=30)
    blocked_retry_seconds: float = Field(default=30, ge=5, le=300)


@dataclass
class _Progress:
    attempts: int = 0
    reserved_events: int = 0
    confirmed_events: int = 0
    recent: deque = field(default_factory=deque)
    last_attempt: float = -float("inf")
    order: int = 0
    configured: bool | None = None
    target: str | None = None
    backlog: int | None = None
    frontier: str | None = None
    blocked: int | None = None
    status: str = "idle"
    reason: str = "not_sampled"


class _GuardedStore(NeighborhoodStore):
    def __init__(self, repository, guard):
        super().__init__(repository)
        self.guard = guard

    def append(self, scope, events, *, precondition=None):
        def current(repository):
            self.guard()
            if precondition is not None:
                precondition(repository)
            self.guard()

        return super().append(scope, events, precondition=current)


class NeighborhoodScheduler:
    def __init__(self, bridge, config=None):
        self.bridge = bridge
        self.runtime = bridge.runtime
        self.config = NeighborhoodSchedulerConfig.model_validate(
            (config or NeighborhoodSchedulerConfig()).model_dump()
        )
        self.states = {}
        self.workers = {}
        self.tokens = {}
        self.worker_keys = {}
        self.sequence = 0

    def _key(self, session):
        return (
            session.request.profile_id,
            session.branch_id,
            session.request.protagonist_id,
            self.bridge.engine.pack.neighborhood_id,
        )

    def _authorized(self, session):
        settings = self.runtime.settings
        return bool(
            getattr(settings, "dev_neighborhood_autonomy", False)
            and settings.dev_neighborhood
            and not settings.release
            and self.bridge.engine is not None
            and not session.closed
            and CAPABILITY in session.request.manifest.capability_flags
        )

    def _foreground(self, session):
        skill_scheduler = getattr(self.runtime, "skill_scheduler", None)
        skill_worker = skill_scheduler.workers.get(session.id) if skill_scheduler else None
        return bool(
            (skill_worker and not skill_worker.done())
            or any(
                task and not task.done()
                for task in (
                    session.task,
                    session.skill_task,
                    session.social_task,
                )
            )
            or session.speech_state != "idle"
            or any(not scene["canceled"] for scene in session.social_scenes.values())
            or any(
                command["kind"] in {"dialogue.chunk", "action.execute"}
                and command["state"] not in TERMINAL
                for command in session.commands.values()
            )
        )

    def _eligible(self, session):
        if not self._authorized(session):
            return "not_authorized"
        world = session.world
        if (
            not world
            or not 0 <= self.runtime.clock() - world.get("_sampled_at", -float("inf")) <= 5
            or world.get("protagonist_id") != session.request.protagonist_id
        ):
            return "world_unavailable"
        if any(
            world.get(flag, True)
            for flag in (
                "online",
                "mission_active",
                "cutscene_active",
                "random_event_active",
                "paused",
            )
        ):
            return "world_ineligible"
        if self._foreground(session):
            return "foreground_priority"
        return None

    def diagnostics(self, session_id=None):
        session = self.runtime.sessions.get(session_id) if session_id else None
        state = self.states.get(self._key(session)) if session and self.bridge.engine else None
        return {
            "enabled": bool(getattr(self.runtime.settings, "dev_neighborhood_autonomy", False)),
            "worker_active": any(not task.done() for task in self.workers.values()),
            "scope_count": len(self.states),
            "status": state.status if state else "idle",
            "reason": state.reason if state else "not_sampled",
            "configured": state.configured if state else None,
            "target_game_datetime": state.target if state else None,
            "backlog_actors": state.backlog if state else None,
            "frontier": state.frontier if state else None,
            "blocked_actors": state.blocked if state else None,
            "attempts": state.attempts if state else 0,
            "reserved_events": state.reserved_events if state else 0,
            "confirmed_events_written": state.confirmed_events if state else 0,
            "limits": self.config.model_dump(),
            "automatic_initialization": False,
            "native_verified": False,
        }

    def observe(self, scope, result, *, operation):
        key = tuple(
            getattr(scope, field)
            for field in (
                "profile_id",
                "branch_id",
                "protagonist_id",
                "neighborhood_id",
            )
        )
        state = self.states.get(key)
        if state is not None:
            state.configured = result["configured"]
            state.backlog = result["backlog_actors"]
            state.frontier = result["frontier"]
            if operation == "initialize":
                # Explicit initialization can restore eligibility without refunding budgets.
                state.reason = "explicit_state_changed"

    def tick(self):
        """Select at most one scope without DB work or awaited replay."""
        if any(not task.done() for task in self.workers.values()):
            return
        groups = {}
        for session in self.runtime.sessions.values():
            if self._authorized(session):
                groups.setdefault(self._key(session), []).append(session)
        choices = []
        now = self.runtime.clock()
        for key, sessions in groups.items():
            if key not in self.states and len(self.states) >= 128:
                continue
            state = self.states.setdefault(key, _Progress())
            if any(self._foreground(session) for session in sessions):
                state.status, state.reason = "suspended", "foreground_priority"
                continue
            eligible = [session for session in sessions if self._eligible(session) is None]
            if not eligible:
                state.status, state.reason = "suspended", self._eligible(sessions[0])
                continue
            # Duplicate sessions share one deterministic target and all accounting.
            session = min(eligible, key=lambda s: (-instant(s.game_datetime).timestamp(), s.id))
            target = instant(session.game_datetime).isoformat()
            if state.configured is False:
                state.status, state.reason = "waiting", "explicit_initialization_required"
                continue
            if state.backlog == 0 and state.target == target:
                state.status, state.reason = "caught_up", "current_game_time_reached"
                continue
            interval = (
                self.config.blocked_retry_seconds
                if state.reason in {"actors_blocked", "advancement_rejected", "worker_time_limit"}
                and state.target == target
                else self.config.interval_seconds
            )
            if now - state.last_attempt < interval:
                continue
            while state.recent and now - state.recent[0][0] >= 60:
                state.recent.popleft()
            cost = self.config.max_events_per_step
            if state.attempts >= self.config.max_steps_per_scope or (
                state.reserved_events + cost > self.config.max_events_per_scope
            ):
                state.status, state.reason = "limited", "scope_budget_exhausted"
                continue
            if sum(cost for _, cost in state.recent) + cost > self.config.max_events_per_minute:
                state.status, state.reason = "limited", "minute_budget_exhausted"
                continue
            choices.append((state.order, key, session, state, target))
        if not choices:
            return
        _, key, session, state, target = min(choices, key=lambda item: (item[0], item[1]))
        self.sequence += 1
        state.order = self.sequence
        state.last_attempt = now
        state.attempts += 1
        state.reserved_events += self.config.max_events_per_step
        state.recent.append((now, self.config.max_events_per_step))
        state.target = target
        state.status, state.reason = "running", "bounded_advancement"
        token = threading.Event()
        self.tokens[session.id] = token
        self.worker_keys[session.id] = key
        self.workers[session.id] = asyncio.create_task(
            self._work(session, key, state, target, token)
        )

    def _advance(self, session, scope, target, token, epoch, generation, pack_hash):
        engine = copy(self.bridge.engine)
        baseline = None
        scope_key = tuple(
            getattr(scope, name)
            for name in (
                "profile_id",
                "branch_id",
                "protagonist_id",
                "neighborhood_id",
            )
        )

        def guard():
            if (
                token.is_set()
                or self.runtime.sessions.get(session.id) is not session
                or self._eligible(session) is not None
                or session.epoch != epoch
                or session.request.runtime_generation != generation
                or self._key(session) != scope_key
                or any(
                    self._authorized(other)
                    and self._key(other) == scope_key
                    and self._foreground(other)
                    for other in list(self.runtime.sessions.values())
                )
                or instant(session.game_datetime) < instant(target)
                or self.bridge.engine.pack_hash != pack_hash
            ):
                raise NeighborhoodConflict("automatic neighborhood authority changed")
            if baseline is not None:
                configured = engine._read(scope, target)[0]
                if not configured or configured["event_id"] != baseline:
                    raise NeighborhoodConflict("automatic neighborhood baseline changed")

        engine.store = _GuardedStore(self.runtime.repo, guard)
        guard()
        configured = engine._read(scope, target)[0]
        if configured is None:
            return {**engine.snapshot(scope, target), "events_written": 0, "blocked_actor_ids": []}
        baseline = configured["event_id"]
        guard()
        return engine.advance(
            scope,
            target,
            budget=AdvanceBudget(
                max_events=self.config.max_events_per_step,
                max_days=self.config.max_days_per_step,
                background_events=self.config.max_events_per_step,
                named_events=0,
                near_events=0,
            ),
        )

    async def _work(self, session, key, state, target, token):
        expired = threading.Event()
        current = asyncio.current_task()

        def timeout():
            expired.set()
            token.set()
            current.cancel()

        timer = asyncio.get_running_loop().call_later(self.config.worker_timeout_seconds, timeout)
        try:
            async with session.lock:
                if self._eligible(session) is not None or token.is_set():
                    raise NeighborhoodConflict("automatic neighborhood authority changed")
                scope = NeighborhoodScope(
                    **dict(
                        zip(
                            (
                                "profile_id",
                                "branch_id",
                                "protagonist_id",
                                "neighborhood_id",
                            ),
                            key,
                            strict=True,
                        )
                    )
                )
                epoch = session.epoch
                generation = session.request.runtime_generation
                pack_hash = self.bridge.engine.pack_hash
            # Lifecycle callers join under session.lock. Never reacquire it here.
            async with self.bridge.lock:
                result = await self.runtime.thread_job(
                    self._advance,
                    session,
                    scope,
                    target,
                    token,
                    epoch,
                    generation,
                    pack_hash,
                )
            state.configured = result["configured"]
            state.backlog = result["backlog_actors"]
            state.frontier = result["frontier"]
            state.blocked = len(result["blocked_actor_ids"])
            state.confirmed_events += result["events_written"]
            if not state.configured:
                state.status, state.reason = "waiting", "explicit_initialization_required"
            elif result["caught_up"]:
                state.status, state.reason = "caught_up", "current_game_time_reached"
            elif result["events_written"] == 0:
                state.status, state.reason = "blocked", "actors_blocked"
            else:
                state.status, state.reason = "pending", "backlog_remaining"
        except asyncio.CancelledError:
            token.set()
            if expired.is_set():
                state.status, state.reason = "limited", "worker_time_limit"
            else:
                state.status, state.reason = "canceled", "runtime_interrupted"
                raise
        except Exception:
            state.status, state.reason = "blocked", "advancement_rejected"
        finally:
            timer.cancel()
            if self.workers.get(session.id) is asyncio.current_task():
                self.workers.pop(session.id, None)
                self.tokens.pop(session.id, None)
                self.worker_keys.pop(session.id, None)

    def cancel(self, session):
        key = self._key(session) if self.bridge.engine else None
        # Cancel work for the same scope even when a duplicate session owns it.
        for session_id, worker in list(self.workers.items()):
            if session_id == session.id or self.worker_keys.get(session_id) == key:
                self.tokens[session_id].set()
                if worker is not asyncio.current_task() and not worker.done():
                    worker.cancel()

    async def join(self, session, *, erase=False):
        key = self._key(session) if self.bridge.engine else None
        self.cancel(session)
        for session_id, worker in list(self.workers.items()):
            if session_id == session.id or self.worker_keys.get(session_id) == key:
                if worker is not asyncio.current_task():
                    await self.runtime.thread_join_task(worker)
        if erase and key in self.states:
            state = self.states[key]
            state.configured = state.backlog = state.frontier = state.blocked = None
            state.status, state.reason = "canceled", "state_erased"

    def forget(self, session_id):
        # Scope budgets intentionally survive session closure and duplicate owners.
        pass
