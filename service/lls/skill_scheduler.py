"""Experience-driven bounded practice, without new native action authority."""

import asyncio
import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import Field

from .protocol import StrictModel
from .runtime_skills import (
    DEVELOPMENT_SKILLS_CAPABILITY,
    SKILL_TERMINAL,
    SkillAdopt,
    SkillExecute,
    SkillGenerate,
    SkillTrial,
)
from .skill_scheduler_store import SkillEpisodeStore
from .skills import SkillIdentity, compile_skill


class SkillSchedulerConfig(StrictModel):
    max_queued_per_profile: int = Field(default=16, ge=1, le=64)
    max_experiences_per_profile: int = Field(default=2048, ge=16, le=8192)
    max_episodes_per_session: int = Field(default=8, ge=1, le=32)
    max_candidate_requests_per_minute: int = Field(default=4, ge=2, le=16)
    episode_timeout_seconds: float = Field(default=45, ge=2, le=90)
    actor_cooldown_seconds: float = Field(default=10, ge=0, le=300)
    max_skill_duration_seconds: float = Field(default=5, ge=1, le=10)


@dataclass
class _Episode:
    record: dict
    identity: SkillIdentity
    epoch: int
    provider: object
    deadline: float
    expires_at: datetime
    audit_revision: int
    canceled: bool = False


class SkillScheduler:
    def __init__(self, runtime, config=None):
        self.runtime = runtime
        self.config = SkillSchedulerConfig.model_validate(
            (config or SkillSchedulerConfig()).model_dump()
        )
        self.store = SkillEpisodeStore(runtime.skill_store)
        self.pending = {}
        self.active = {}
        self.workers = {}
        self.states = {}
        self.session_attempts = {}
        self.actor_last = {}
        self.recent_requests = deque()

    def _authorized(self, session):
        return bool(
            getattr(self.runtime.settings, "dev_skill_autonomy", False)
            and self.runtime.settings.dev_skills
            and not self.runtime.settings.release
            and not session.closed
            and DEVELOPMENT_SKILLS_CAPABILITY in session.request.manifest.capability_flags
        )

    @staticmethod
    def skill_id(identity, action_id):
        digest = hashlib.sha256(
            json.dumps(
                [
                    identity.profile_id,
                    identity.actor_id,
                    identity.protagonist_id,
                    action_id,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:24]
        return "auto_practice_" + digest

    def _identity(self, session, actor_id):
        actor = session.actors.get(actor_id)
        if actor is None:
            raise ValueError("actor is unavailable")
        return SkillIdentity(
            session.request.profile_id,
            session.branch_id,
            session.request.protagonist_id,
            session.id,
            session.request.runtime_generation,
            actor_id,
            actor["runtime_instance_id"],
        )

    def _eligible(self, session, identity, *, idle=True):
        runtime = self.runtime
        actor = session.actors.get(identity.actor_id)
        social_worker = (
            runtime.social_scheduler.workers.get(session.id)
            if runtime.social_scheduler is not None
            else None
        )
        if (
            not self._authorized(session)
            or session.branch_id != identity.branch_id
            or session.request.profile_id != identity.profile_id
            or session.request.protagonist_id != identity.protagonist_id
            or session.request.runtime_generation != identity.runtime_generation
            or not actor
            or actor["runtime_instance_id"] != identity.runtime_instance_id
            or not runtime.conversation_policy.check(actor, session.world).allowed
            or actor.get("control_policy") != "ambient"
            or not 0 <= runtime.clock() - actor.get("_sampled_at", -math.inf) <= 2
            or not 0 <= runtime.clock() - session.world.get("_sampled_at", -math.inf) <= 2
            or session.world.get("protagonist_id") != identity.protagonist_id
            or (session.task and not session.task.done())
            or session.speech_state != "idle"
            or (session.social_task and not session.social_task.done())
            or (social_worker is not None and not social_worker.done())
            or any(not state["canceled"] for state in session.social_scenes.values())
        ):
            return False
        if idle and (
            (session.skill_task and not session.skill_task.done())
            or (identity.profile_id, identity.actor_id) in runtime.skill_owners
            or any(
                command["kind"] in {"action.execute", "dialogue.chunk"}
                and command["state"] not in SKILL_TERMINAL
                for command in session.commands.values()
            )
        ):
            return False
        return True

    @staticmethod
    def _number(observed, key):
        value = observed.get(key)
        return (
            value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None
        )

    def _receipt(self, session, command, ack):
        """Ordinary ACK validation is weaker; independently require dispatch and metrics."""
        if (
            command.get("_skill_origin") != "external"
            or command.get("_skill") is not None
            or command["kind"] != "action.execute"
            or command["payload"]["action_id"] not in {"face_player", "wait_here"}
            or ack.status not in {"completed", "failed"}
            or command["state"] != ack.status
            or not command.get("_skill_source_event_id")
        ):
            return False
        with self.runtime.repo._lock:
            row = self.runtime.repo.connection.execute(
                "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                (session.request.profile_id, command["id"] + ":started"),
            ).fetchone()
        if row is None:
            return False
        started = json.loads(row[0])
        payload = started.get("payload", {})
        if (
            payload.get("reason") != "task_dispatched"
            or (payload.get("observed") or {}).get("task_dispatched") is not True
            or payload.get("action_id") != command["payload"]["action_id"]
            or payload.get("conversation_turn_id") != command["payload"]["turn_id"]
        ):
            return False
        observed = ack.observed or {}
        distance = self._number(observed, "distance_meters")
        if distance is None or distance > 100000:
            return False
        action = command["payload"]["action_id"]
        if action == "face_player":
            heading = self._number(observed, "heading_error_degrees")
            return bool(
                heading is not None
                and heading <= 180
                and (
                    (ack.status == "completed" and heading <= 20)
                    or (
                        ack.status == "failed"
                        and ack.reason == "orientation_not_reached"
                        and heading > 20
                    )
                )
            )
        elapsed = self._number(observed, "elapsed_ms")
        displacement = self._number(observed, "displacement_meters")
        duration = command["payload"]["arguments"].get("duration_seconds")
        if (
            type(duration) not in {int, float}
            or not math.isfinite(duration)
            or duration <= 0
            or elapsed is None
            or displacement is None
            or displacement > 1.5
            or displacement > distance + 0.0001
        ):
            return False
        return bool(
            (ack.status == "completed" and elapsed >= duration * 1000)
            or (
                ack.status == "failed"
                and ack.reason == "wait_duration_incomplete"
                and 0 < elapsed < duration * 1000
            )
        )

    def observe(self, session, command, ack):
        if not self._authorized(session) or not self._receipt(session, command, ack):
            return
        try:
            identity = self._identity(session, command["actor_id"])
            action_id = command["payload"]["action_id"]
            sources = [
                command["_skill_source_event_id"],
                command["id"] + ":started",
                command["id"] + ":" + ack.status,
            ]
            self.store.current_sources(identity, session.game_datetime, sources)
            with self.runtime.repo._lock:
                input_event = self.runtime.repo.connection.execute(
                    "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                    (identity.profile_id, sources[0]),
                ).fetchone()
            if input_event is None or json.loads(input_event[0])["event_type"] != (
                "player.utterance_text"
            ):
                return
            selected = self.runtime.skill_store.select(
                identity, session.game_datetime, self.skill_id(identity, action_id)
            )
            expires = self.runtime.utc_now() + timedelta(
                seconds=self.config.episode_timeout_seconds
            )
            record = self.store.observe(
                identity,
                session.game_datetime,
                sources[-1],
                sources[0],
                action_id,
                ack.status,
                selected=selected is not None,
                now=self.runtime.utc_now().isoformat(),
                expires_at=expires.isoformat(),
                max_queued=self.config.max_queued_per_profile,
                max_records=self.config.max_experiences_per_profile,
            )
            if record and record["state"] == "queued":
                need = self.runtime.skill_store.need_context(
                    identity, session.game_datetime, record["source_event_ids"]
                )
                self.pending[record["episode_id"]] = _Episode(
                    record,
                    identity,
                    session.epoch,
                    self.runtime.provider,
                    self.runtime.clock() + self.config.episode_timeout_seconds,
                    expires,
                    need["audit_revision"],
                )
                self.states[session.id] = {
                    "status": "queued",
                    "reason": "bounded_practice",
                    "episode_id": record["episode_id"],
                }
        except (ValueError, HTTPException):
            # An accepted action receipt remains accepted if its learning context
            # was erased, became ineligible, or reached a scheduler capacity.
            return

    def _current(self, session, episode):
        runtime = self.runtime
        if (
            episode.canceled
            or runtime.sessions.get(session.id) is not session
            or session.epoch != episode.epoch
            or runtime.provider is not episode.provider
            or runtime.clock() >= episode.deadline
            or runtime.utc_now() >= episode.expires_at
            or not self._eligible(session, episode.identity, idle=False)
        ):
            raise ValueError("skill episode authorization expired or changed")
        self.store.current_sources(
            episode.identity, session.game_datetime, episode.record["source_event_ids"]
        )
        need = runtime.skill_store.need_context(
            episode.identity, session.game_datetime, episode.record["source_event_ids"]
        )
        if need["audit_revision"] != episode.audit_revision:
            raise ValueError("skill episode source audit changed")

    def run_is_current(self, session):
        """Let learner persistence and ACK publication inherit episode authority."""
        episode = self.active.get(session.id)
        if episode is None:
            return True
        try:
            self._current(session, episode)
        except (ValueError, HTTPException):
            return False
        return True

    def _body(self, session, episode, model, **fields):
        self._current(session, episode)
        return model(
            session_id=session.id,
            branch_id=episode.identity.branch_id,
            runtime_generation=episode.identity.runtime_generation,
            protagonist_id=episode.identity.protagonist_id,
            actor_id=episode.identity.actor_id,
            runtime_instance_id=episode.identity.runtime_instance_id,
            game_datetime=datetime.fromisoformat(session.game_datetime),
            source_event_ids=episode.record["source_event_ids"],
            consent=False,
            **fields,
        )

    def tick(self):
        if any(not task.done() for task in self.workers.values()):
            return
        ready = []
        for key, episode in list(self.pending.items()):
            session = self.runtime.sessions.get(episode.identity.session_id)
            if session is None:
                self.pending.pop(key, None)
                continue
            if (
                episode.canceled
                or self.runtime.clock() >= episode.deadline
                or self.runtime.utc_now() >= episode.expires_at
            ):
                self.store.finish(episode.identity, key, "expired", "episode_deadline")
                self.pending.pop(key, None)
                continue
            if not self._eligible(session, episode.identity):
                continue
            actor = (episode.identity.profile_id, episode.identity.actor_id)
            if self.runtime.clock() - self.actor_last.get(actor, -math.inf) < (
                self.config.actor_cooldown_seconds
            ):
                continue
            if self.session_attempts.get(session.id, 0) >= self.config.max_episodes_per_session:
                self.store.finish(episode.identity, key, "limited", "session_episode_limit")
                self.pending.pop(key, None)
                continue
            ready.append(
                (self.store.attempts(episode.identity), episode.deadline, key, session, episode)
            )
        if not ready:
            return
        _, _, key, session, episode = min(ready, key=lambda item: item[:3])
        self.pending.pop(key, None)
        if not self.store.claim(episode.identity, key):
            return
        self.session_attempts[session.id] = self.session_attempts.get(session.id, 0) + 1
        self.actor_last[(episode.identity.profile_id, episode.identity.actor_id)] = (
            self.runtime.clock()
        )
        task = asyncio.create_task(self._run(session, episode))
        self.active[session.id] = episode
        self.workers[session.id] = task
        task.add_done_callback(
            lambda finished: None if finished.cancelled() else finished.exception()
        )

    def _suitable(self, compiled, episode):
        self.runtime.validate_bridge_skill(compiled)
        actions = [
            json.loads(raw)["proposal"]
            for raw in compiled.operations
            if json.loads(raw)["op"] == "action"
        ]
        if (
            compiled.duration_seconds > self.config.max_skill_duration_seconds
            or compiled.distance_meters != 0
            or not any(action["action_id"] == episode.record["action_id"] for action in actions)
        ):
            raise ValueError("candidate does not fit the observed bounded practice need")

    def _reserve_candidate(self, episode):
        now = self.runtime.clock()
        while self.recent_requests and now - self.recent_requests[0] >= 60:
            self.recent_requests.popleft()
        if len(self.recent_requests) >= self.config.max_candidate_requests_per_minute:
            raise ValueError("scheduler candidate request limit")
        if not self.store.reserve(
            episode.identity, episode.record["episode_id"], "candidate_requests", 2
        ):
            raise ValueError("episode candidate request limit")
        self.recent_requests.append(now)

    def _measured_retirement(self, session, episode, outcome):
        sources = outcome.get("source_event_ids", [])
        if not sources:
            return False
        with self.runtime.repo._lock:
            identity, stamp = self.runtime.skill_store._scope(
                episode.identity, session.game_datetime
            )
            events = self.runtime.skill_store._sources(identity, stamp, sources)
        for event in events:
            if event["event_type"] != "action.failed":
                continue
            payload = event["payload"]
            observed = payload.get("observed") or {}
            distance = self._number(observed, "distance_meters")
            heading = self._number(observed, "heading_error_degrees")
            if (
                payload.get("action_id") == "face_player"
                and payload.get("reason") == "orientation_not_reached"
                and distance is not None
                and distance <= 100000
                and heading is not None
                and 20 < heading <= 180
            ):
                return True
            command = session.commands.get(event["event_id"].rsplit(":", 1)[0])
            if command is None:
                continue
            duration = command["payload"]["arguments"].get("duration_seconds")
            elapsed = self._number(observed, "elapsed_ms")
            displacement = self._number(observed, "displacement_meters")
            if (
                payload.get("action_id") == "wait_here"
                and payload.get("reason") == "wait_duration_incomplete"
                and type(duration) in {int, float}
                and math.isfinite(duration)
                and elapsed is not None
                and 0 < elapsed < duration * 1000
                and distance is not None
                and distance <= 100000
                and displacement is not None
                and displacement <= 1.5
                and displacement <= distance + 0.0001
            ):
                return True
        return False

    async def _preflight(self, session, episode, candidate):
        """A recorded simulated trial can inform revision but cannot permit adoption."""
        runtime = self.runtime
        self._current(session, episode)
        compiled = compile_skill(
            candidate["source"],
            runtime.skill_store.library(episode.identity, session.game_datetime),
        )
        self._suitable(compiled, episode)

        return await runtime.preflight_skill(session, candidate["record_id"])

    async def _run(self, session, episode):
        status, reason = "failed", "episode_incomplete"
        self.states[session.id] = {
            "status": "running",
            "reason": "bounded_practice",
            "episode_id": episode.record["episode_id"],
        }
        try:
            remaining = min(
                episode.deadline - self.runtime.clock(),
                (episode.expires_at - self.runtime.utc_now()).total_seconds(),
            )
            async with asyncio.timeout(max(0, remaining)):
                status, reason = await self._cycle(session, episode)
        except asyncio.CancelledError:
            status, reason = "canceled", "episode_canceled"
            raise
        except TimeoutError:
            status, reason = "interrupted", "episode_deadline"
        except (HTTPException, ValueError) as exc:
            reason = str(exc.detail if isinstance(exc, HTTPException) else exc)[:120]
        finally:
            if episode.canceled:
                status, reason = "canceled", "episode_canceled"
            self.store.finish(episode.identity, episode.record["episode_id"], status, reason)
            self.states[session.id] = {
                "status": status,
                "reason": reason,
                "episode_id": episode.record["episode_id"],
            }
            if self.active.get(session.id) is episode:
                self.active.pop(session.id, None)

    async def _cycle(self, session, episode):
        runtime = self.runtime
        identity = episode.identity
        skill_id = self.skill_id(identity, episode.record["action_id"])
        self._current(session, episode)
        selected = runtime.skill_store.select(identity, session.game_datetime, skill_id)
        if selected is not None:
            self._suitable(selected["compiled"], episode)
            result = await runtime.skill_call(
                session, self._body(session, episode, SkillExecute, skill_id=skill_id), "execute"
            )
            self._current(session, episode)
            outcome = result["result"]
            if outcome["status"] == "completed":
                return "completed", "retained_bounded_practice_completed"
            # Only an actual acknowledged failure retires a retained revision.
            # Policy refusal, timeout, missing metrics, or interruption just stop.
            if (
                outcome["status"] == "failed"
                and outcome.get("verification") == "bridge_acknowledged"
                and outcome.get("reason") == "primitive_failed"
                and self._measured_retirement(session, episode, outcome)
            ):
                runtime.skill_store.retire(
                    identity, session.game_datetime, skill_id, outcome["source_event_ids"]
                )
                return "retired", "acknowledged_retained_primitive_failed"
            return "stopped", "retained_practice_" + outcome["status"]
        revises = None
        for attempt in range(2):
            self._current(session, episode)
            self._reserve_candidate(episode)
            version = self.store.next_version(identity.profile_id, skill_id)
            generated = await runtime.skill_call(
                session,
                self._body(
                    session,
                    episode,
                    SkillGenerate,
                    skill_id=skill_id,
                    version=version,
                    action_class="conversational_courtesy",
                    revises=revises,
                ),
                "generate",
            )
            self._current(session, episode)
            candidate = generated["result"]
            if candidate is None:
                return "stopped", "candidate_unavailable"
            simulated = await self._preflight(session, episode, candidate)
            self._current(session, episode)
            if simulated["result"]["status"] != "completed":
                # A deterministic false candidate guard is code feedback, unlike
                # engine failures or safety-policy withdrawal.
                if (
                    attempt == 0
                    and simulated["result"]["status"] == "refused"
                    and simulated["result"]["reason"].startswith("precondition_")
                ):
                    revises = simulated["record_id"]
                    continue
                return "stopped", "candidate_preflight_" + simulated["result"]["status"]
            if not self.store.reserve(identity, episode.record["episode_id"], "trials", 2):
                return "limited", "episode_trial_limit"
            trial_result = await runtime.skill_call(
                session,
                self._body(session, episode, SkillTrial, candidate_id=candidate["record_id"]),
                "trial",
                preflight_id=simulated["record_id"],
            )
            self._current(session, episode)
            trial = trial_result["result"]
            if (
                trial["result"]["status"] == "completed"
                and trial["result"]["verification"] == "bridge_acknowledged"
            ):
                await runtime.skill_call(
                    session,
                    self._body(session, episode, SkillAdopt, trial_id=trial["record_id"]),
                    "adopt",
                )
                self._current(session, episode)
                return "adopted", "acknowledged_trial_adopted"
            return "stopped", "bridge_trial_" + trial["result"]["status"]
        return "limited", "episode_revision_limit"

    def cancel(self, session):
        active = self.active.get(session.id)
        if active is not None:
            active.canceled = True
            self.store.finish(
                active.identity, active.record["episode_id"], "canceled", "episode_canceled"
            )
            self.states[session.id] = {
                "status": "canceled",
                "reason": "episode_canceled",
                "episode_id": active.record["episode_id"],
            }
        for key, episode in list(self.pending.items()):
            if episode.identity.session_id == session.id:
                episode.canceled = True
                self.store.finish(episode.identity, key, "canceled", "episode_canceled")
                self.pending.pop(key, None)
        task = self.workers.get(session.id)
        if task is not None and not task.done():
            task.cancel()

    async def join(self, session, *, erase=False):
        self.cancel(session)
        task = self.workers.pop(session.id, None)
        if task is not None and task is not asyncio.current_task():
            await self.runtime.thread_join_task(task)
        self.active.pop(session.id, None)
        if erase:
            self.states.pop(session.id, None)
            self.session_attempts.pop(session.id, None)

    def diagnostics(self, session_id):
        task = self.workers.get(session_id)
        return {
            "enabled": getattr(self.runtime.settings, "dev_skill_autonomy", False),
            **self.states.get(session_id, {"status": "idle", "reason": "no_external_need"}),
            "queued_episodes": sum(
                e.identity.session_id == session_id for e in self.pending.values()
            ),
            "episode_attempts": self.session_attempts.get(session_id, 0),
            "worker_active": task is not None and not task.done(),
            "selection_mode": "post_outcome_bounded_practice",
            "limits": self.config.model_dump(),
            "game_verified": False,
        }
