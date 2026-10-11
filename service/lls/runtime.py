"""Session-scoped orchestration and bounded, acknowledged command delivery."""

import asyncio
import hashlib
import json
import re
import secrets
import time
from collections import OrderedDict, deque
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .action_registry import ACTION_IDS
from .actions import POLICY_VERSION, ActionPolicy, ConversationPolicy
from .bridge_limits import MAX_BODY_BYTES
from .context import build_context
from .display import DISPLAY_MAX_CHARS, DISPLAY_RULE, display_text
from .memory_index import MemoryIndex
from .neighborhood_bridge import NeighborhoodBridge
from .outbound import AIControlOwner
from .packs import install_pilot_pack
from .perception import DeliveredUtterance, PerceptionLimits, witnesses_for
from .perception_bridge import DEVELOPMENT_PERCEPTION_CAPABILITY
from .protocol import Command
from .providers import create_provider, effective_provider_settings, persist_provider_configuration
from .roles import ROLE_POLICIES, TABLE_VERSION, ownership_from_facts, revalidate
from .runtime_outcomes import OutcomeRuntime
from .runtime_pin import PinRuntime
from .runtime_skills import SkillRuntime
from .runtime_social import SocialRuntime
from .runtime_speech import SpeechRuntime
from .skill_scheduler import SkillScheduler
from .triggers import TriggerContext

TERMINAL = {"completed", "rejected", "expired", "failed", "canceled"}


def now():
    return datetime.now(timezone.utc)


def key(prefix):
    return prefix + "_" + uuid4().hex


async def sentence_chunks(provider, context, max_chars):
    """Bound token streams and remove one leading emotion tag before delivery."""
    buffer = ""
    received = 0
    leading = True
    async with aclosing(provider.dialogue(context)) as stream:
        async for raw in stream:
            fragment = raw[: max_chars - received]
            if not fragment:
                break
            received += len(fragment)
            buffer += fragment
            if leading:
                buffer = buffer.lstrip()
                if buffer.startswith(("[", "<")) and not re.match(
                    r"^[\[<][^\]>]{1,32}[\]>]", buffer
                ):
                    if len(buffer) < 34:
                        continue
                buffer = re.sub(r"^[\[<][^\]>]{1,32}[\]>]\s*", "", buffer, count=1)
                leading = False
            while match := re.search(r"[.!?](?:\s|$)", buffer):
                end = match.end()
                sentence, buffer = buffer[:end], buffer[end:]
                if sentence.strip():
                    yield sentence
            if len(buffer) >= 400:
                split = buffer.rfind(" ", 0, 400)
                split = split if split > 0 else 400
                yield buffer[:split] + " "
                buffer = buffer[split:].lstrip()
    if buffer.strip():
        yield buffer


@dataclass
class Session:
    id: str
    token: str
    request: object
    branch_id: str
    actors: dict = field(default_factory=dict)
    actor_seq: dict = field(default_factory=dict)
    last_action: dict = field(default_factory=dict)
    action_outcomes: deque = field(default_factory=lambda: deque(maxlen=32))
    outcome_pending: OrderedDict = field(default_factory=OrderedDict)
    outcome_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    delivered_utterances: OrderedDict = field(default_factory=OrderedDict)
    player_speech: OrderedDict = field(default_factory=OrderedDict)
    perception_seen: OrderedDict = field(default_factory=OrderedDict)
    trigger_contexts: OrderedDict = field(default_factory=OrderedDict)
    delivery_turns: OrderedDict = field(default_factory=OrderedDict)
    speech_identity: object = None
    speech_state: str = "idle"
    speech_cleanup: list = field(default_factory=list)
    speech_playback: dict | None = None
    speech_whisper: bool = False
    voice_task: asyncio.Task | None = None
    social_task: asyncio.Task | None = None
    social_cleanup: list = field(default_factory=list)
    social_scenes: OrderedDict = field(default_factory=OrderedDict)
    skill_task: asyncio.Task | None = None
    skill_cleanup: list = field(default_factory=list)
    skill_run: dict | None = None
    pins: OrderedDict = field(default_factory=OrderedDict)
    last_pin_at: float = float("-inf")
    game_datetime: str = ""
    last_activity: float = field(default_factory=time.monotonic)
    world: dict = field(default_factory=dict)
    world_seq: int = 0
    seen: OrderedDict = field(default_factory=OrderedDict)
    sequences: dict = field(default_factory=dict)
    last_seq: int = 0
    command_seq: int = 0
    commands: OrderedDict = field(default_factory=OrderedDict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    cancel_seq: int = 0
    epoch: int = 0
    closed: bool = False
    cleanup_pending: bool = False
    turns: deque = field(default_factory=deque)
    heartbeat: dict = field(default_factory=dict)
    trace: deque = field(default_factory=lambda: deque(maxlen=200))


class Runtime(SpeechRuntime, SocialRuntime, SkillRuntime, OutcomeRuntime, PinRuntime):
    def __init__(
        self,
        settings,
        repository,
        provider=None,
        *,
        clock=time.monotonic,
        utc_now=now,
        id_factory=key,
        trigger_engine=None,
        speech_factory=None,
        provider_factory=None,
        social_factory=None,
    ):
        self.ai_owner = AIControlOwner(repository)
        try:
            self.settings = effective_provider_settings(settings, repository)
            self.repo = repository
            self.sessions = {}
            self.cleanup_pending = {}
            self.cleanup_recovery_pending = True
            self.cleanup_recovery_error = None
            self.selected_branches = {}
            self.profile_warning = None
            self.profile_locks = {}
            self.open_lock = asyncio.Lock()
            self.configuration_lock = asyncio.Lock()
            self.clock = clock
            self.utc_now = utc_now
            self.id_factory = id_factory
            self.trigger_engine = trigger_engine
            self.policy = ActionPolicy(clock=clock)
            self.conversation_policy = ConversationPolicy(clock=clock)
            self.provider_factory = provider_factory or create_provider
            self.provider = provider or self.provider_factory(
                self.settings,
                repository,
                use_saved=False,
                clock=clock,
                utc_now=utc_now,
                owner=self.ai_owner,
            )
            self.initialize_speech(speech_factory)
            self.initialize_social(social_factory)
            self.initialize_skills()
            self.skill_scheduler = (
                SkillScheduler(self, config=self.settings.skill_scheduler)
                if self.settings.dev_skill_autonomy else None
            )
            self._skills_first = True
            self.memory_index = MemoryIndex(
                self.repo, self.settings, gate=getattr(self.provider, "gate", None)
            )
            if self.social is not None:
                self.social.memory_index = self.memory_index
            self.neighborhood = NeighborhoodBridge(self)
        except BaseException:
            self.ai_owner.close()
            raise

    @asynccontextmanager
    async def configuration_sessions(self):
        async with self.configuration_lock, self.open_lock, AsyncExitStack() as locks:
            sessions = sorted(self.sessions.values(), key=lambda session: session.id)
            for session in sessions:
                await locks.enter_async_context(session.lock)
            yield sessions

    async def quiesce_provider_sessions(self, sessions):
        for session in sessions:
            self.cancel(session)
            if not session.closed:
                self.command(session, "scene.cancel", None, {"scene_id": None})
        for session in sessions:
            await self.stop_session_work(session)
            if session.task is not None:
                await asyncio.gather(session.task, return_exceptions=True)

    async def configure_provider(self, settings):
        from .gateway import CONFIGURATION_FIELDS
        from .settings import Settings

        candidate = Settings.model_validate(
            settings.model_dump() if isinstance(settings, Settings) else settings
        )
        current_values, candidate_values = self.settings.model_dump(), candidate.model_dump()
        if any(
            current_values[k] != candidate_values[k]
            for k in current_values
            if k not in CONFIGURATION_FIELDS
        ):
            raise ValueError("provider configuration cannot change service or speech settings")
        replacement = self.provider_factory(
            candidate,
            self.repo,
            use_saved=False,
            clock=self.clock,
            utc_now=self.utc_now,
            owner=self.ai_owner,
        )
        try:
            async with self.configuration_sessions() as sessions:
                await self.quiesce_provider_sessions(sessions)
                close = getattr(self.provider, "aclose", None)
                if close is not None:
                    await close()
                await self.thread_job(persist_provider_configuration, self.repo, candidate)
                self.settings = candidate
                self.provider = replacement
                self.memory_index = MemoryIndex(
                    self.repo, self.settings, gate=getattr(replacement, "gate", None)
                )
                self.replace_skill_provider()
                self.configure_social_fake(replacement)
                if self.social is not None:
                    self.social.replace_provider(replacement)
                    self.social.memory_index = self.memory_index
        except BaseException:
            close = getattr(replacement, "aclose", None)
            if close is not None:
                await close()
            raise
        return self.provider.safe_status()

    async def set_cloud_consent(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("cloud consent must be explicit true or false")
        async with self.configuration_sessions() as sessions:
            if not enabled:
                await self.quiesce_provider_sessions(sessions)
            change = getattr(self.provider, "set_cloud_consent", None)
            if change is not None:
                await change(enabled)
            self.settings.cloud_opt_in = enabled
            await self.thread_job(persist_provider_configuration, self.repo, self.settings)
        status = getattr(self.provider, "safe_status", None)
        return status() if status else {"cloud_consent": enabled, "provider": self.provider.name}

    async def db(self, method, *args, **kwargs):
        return await self.thread_job(getattr(self.repo, method), *args, **kwargs)

    async def thread_job(self, function, *args, **kwargs):
        # A canceled asyncio task does not stop an executor thread. Join the DB
        # operation before exposing cancellation so erasure cannot overtake it.
        job = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            while not job.done():
                try:
                    await asyncio.shield(job)
                except (asyncio.CancelledError, Exception):
                    continue
            # Retrieve exceptions to avoid leaving an unobserved failed task.
            if not job.cancelled():
                job.exception()
            raise

    def profile_lock(self, profile_id):
        return self.profile_locks.setdefault(profile_id, asyncio.Lock())

    async def open(self, request):
        await self.expire_sessions()
        async with self.open_lock:
            if (
                not (request.profile_id or "").strip()
                or not (request.save_slot or "").strip()
                or not request.profile_confirmed
            ):
                self.profile_warning = (
                    "Confirm the profile and save slot for this load before attaching memories. "
                    "A different or unknown save needs explicit selection or a new profile."
                )
                raise HTTPException(409, self.profile_warning)
            async with self.profile_lock(request.profile_id):
                return await self._open_locked(request)

    async def _open_locked(self, request):
        if self.cleanup_recovery_pending or any(
            profile == request.profile_id for profile, _ in self.cleanup_pending
        ):
            raise HTTPException(503, "session cleanup pending; retry after database recovery")
        if len([s for s in self.sessions.values() if not s.closed]) >= self.settings.max_sessions:
            raise HTTPException(429, "session limit reached")
        if request.manifest.service_host_mode == "dev_lan" and not self.settings.dev_lan:
            raise HTTPException(403, "dev-LAN unavailable")
        try:
            await self.db("create_profile", request.profile_id, save_slot=request.save_slot)
        except ValueError:
            self.profile_warning = (
                "This profile belongs to another save slot. Select the matching profile "
                "or a new profile before attaching memories; the association was not changed."
            )
            raise HTTPException(409, self.profile_warning) from None
        session_id = self.id_factory("ses")
        branch = await self.db(
            "start_session",
            request.profile_id,
            session_id,
            request.game_datetime.isoformat(),
            parent_branch_id=request.parent_branch_id
            or self.selected_branches.get(request.profile_id),
        )
        self.selected_branches.pop(request.profile_id, None)
        if isinstance(branch, dict):
            branch = branch["branch_id"]
        await self.thread_job(install_pilot_pack, self.repo, request.profile_id)
        supported_flags = {
            "typed_input", "subtitles", "actor_pin", "role_classifier_v1",
            "role_table_" + TABLE_VERSION,
        }
        if self.settings.speech.enabled:
            supported_flags.add("ptt_control")
        for enabled, feature in (
            (self.settings.dev_perception, "development:perception:v1"),
            (self.settings.dev_social, "development:social:v1"),
            (self.settings.dev_skills, "development:skills:v1"),
            (self.settings.dev_neighborhood, "development:neighborhood:v1"),
        ):
            if enabled and not self.settings.release:
                supported_flags.add(feature)
        for actor in await self.db("list_actors", request.profile_id):
            if (
                actor.get("actor_kind") == "anchored_service"
                and actor.get("control_policy") == "service_limited"
                and actor.get("service_integration_id")
            ):
                supported_flags.add("service:" + actor["service_integration_id"])
        # Existing authorization paths consume only the negotiated intersection.
        request = request.model_copy(deep=True)
        request.capabilities = list(
            dict.fromkeys(
                capability for capability in request.capabilities if capability in ACTION_IDS
            )
        )
        request.manifest.capability_flags = list(
            dict.fromkeys(
                flag for flag in request.manifest.capability_flags if flag in supported_flags
            )
        )
        session = Session(session_id, secrets.token_urlsafe(32), request, branch)
        session.last_activity = self.clock()
        session.game_datetime = request.game_datetime.isoformat()
        self.sessions[session.id] = session
        self.profile_warning = None
        # Closed sessions cannot accumulate forever.
        for sid in list(self.sessions):
            if (
                len(self.sessions) > self.settings.max_sessions * 2
                and self.sessions[sid].closed and not self.sessions[sid].cleanup_pending
            ):
                del self.sessions[sid]
        return session

    def authenticate(self, token, session_id=None):
        for session in self.sessions.values():
            if secrets.compare_digest(session.token, token) and (
                session_id is None or session.id == session_id
            ):
                if session.closed:
                    raise HTTPException(410, "session closed")
                session.last_activity = self.clock()
                return session
        raise HTTPException(401, "invalid session token")

    def ensure_current(self, session, generation):
        if session.closed:
            raise HTTPException(410, "session closed")
        if generation != session.request.runtime_generation:
            raise HTTPException(409, "stale runtime generation")

    def command(self, session, kind, actor_id, payload, *, seconds=None):
        seconds = seconds if seconds is not None else (120 if kind == "dialogue.chunk" else 15)
        self.expire_commands(session)
        while len(session.commands) >= self.settings.max_commands:
            terminal = next(
                (
                    cid for cid, c in session.commands.items()
                    if c["state"] in TERMINAL and cid not in session.outcome_pending
                ),
                None,
            )
            if terminal is None:
                raise HTTPException(
                    503, "command queue full; acknowledge or cancel pending commands"
                )
            del session.commands[terminal]
        presented = {"dialogue.chunk": "speaker", "status": "message"}.get(kind)
        if presented and isinstance(payload.get(presented), str):
            payload = {**payload, presented: display_text(payload[presented])}
        command = {
            "schema_version": 1,
            "id": self.id_factory("cmd"),
            "seq": session.command_seq + 1,
            "kind": kind,
            "session_id": session.id,
            "actor_id": actor_id,
            "runtime_generation": session.request.runtime_generation,
            "runtime_instance_id": session.actors.get(actor_id, {}).get("runtime_instance_id"),
            "expires_at": (self.utc_now() + timedelta(seconds=seconds)).isoformat(),
            "idempotency_key": self.id_factory("effect"),
            "payload": payload,
        }
        Command.model_validate(command)
        response = JSONResponse({"commands": [command], "cursor": command["seq"]})
        if len(response.body) > MAX_BODY_BYTES:
            raise HTTPException(413, "single command exceeds bridge body limit")
        session.command_seq = command["seq"]
        command.update(
            state="queued",
            _game_datetime=session.game_datetime,
            _created_at_utc=self.utc_now(),
            _record_delivery=self.provider.name != "disabled",
        )
        turn = session.delivery_turns.get(payload.get("turn_id"))
        if kind == "dialogue.chunk" and turn is not None:
            command["_conversation_turn"] = payload["turn_id"]
            turn["chunks"][command["id"]] = {
                "nonempty": bool(payload["text"]),
                "complete": False,
            }
            if payload["final"]:
                turn["final_command_id"] = command["id"]
        session.commands[command["id"]] = command
        session.changed.set()
        return command

    def record_outcome(self, session, command, status, reason, *, service=True):
        if command["kind"] != "action.execute":
            return
        outcome = self.queue_service_outcome(session, command, status, reason) if service else None
        event = outcome.event if outcome else None
        event_id = event["event_id"] if event else command["id"] + ":" + status
        if any(o["event_id"] == event_id for o in session.action_outcomes):
            return
        session.action_outcomes.append(
            {
                "event_id": event_id,
                "actor_id": outcome.actor_id if outcome else command["actor_id"],
                "protagonist_id": (
                    outcome.protagonist_id if outcome else session.request.protagonist_id
                ),
                "session_id": outcome.session_id if outcome else session.id,
                "action_id": (
                    event["payload"]["action_id"] if event else command["payload"]["action_id"]
                ),
                "status": event["payload"]["status"] if event else status,
                "reason": event["payload"]["reason"] if event else reason[:500],
                "provenance": "system_fact",
                **(
                    {
                        "authority": "service",
                        "native_completion_confirmed": False,
                        "native_withdrawal_confirmed": False,
                    }
                    if outcome else {}
                ),
                "game_datetime": (
                    event["game_datetime"] if event else command.get("_skill_ack_times", {}).get(
                        status, command["_game_datetime"]
                    )
                ),
            }
        )

    def expire_commands(self, session):
        expired_pins = []
        for command in session.commands.values():
            if (
                command["state"] not in TERMINAL
                and datetime.fromisoformat(command["expires_at"]) <= self.utc_now()
            ):
                command["state"] = "expired"
                self.invalidate_delivery_turn(session, command)
                self.record_outcome(
                    session, command, "expired", "authorization expired before completion"
                )
                if command["kind"] == "actor.pin":
                    expired_pins.append(command)
        # Settling queues a status command, so it runs after iteration.
        for command in expired_pins:
            self.settle_pin(session, command)

    def cancel(self, session, scene_id=None):
        session.epoch += 1
        self.cancel_speech(session)
        self.cancel_social(session)
        self.cancel_skills(session)
        if self.skill_scheduler is not None:
            self.skill_scheduler.cancel(session)
        if self.neighborhood.scheduler is not None:
            self.neighborhood.scheduler.cancel(session)
        if session.task and not session.task.done():
            session.task.cancel()
        for turn in session.delivery_turns.values():
            if scene_id is None or turn["scene_id"] == scene_id:
                turn["invalid"] = True
        for scene, actor_id in list(session.trigger_contexts):
            if scene_id is None or scene == scene_id:
                session.trigger_contexts.pop((scene, actor_id), None)
                if self.trigger_engine:
                    self.trigger_engine.cancel_scene(session.id, scene)
        for command in session.commands.values():
            if command["state"] not in TERMINAL and command["kind"] in {
                "dialogue.chunk",
                "action.execute",
            }:
                if scene_id is None or command["payload"].get("scene_id") == scene_id:
                    command["state"] = "canceled"
                    self.record_outcome(session, command, "canceled", "scene canceled")
        session.changed.set()

    @staticmethod
    def invalidate_delivery_turn(session, command):
        turn = session.delivery_turns.get(command.get("_conversation_turn"))
        if turn is not None:
            turn["invalid"] = True

    async def record_conversation_completion(self, session, command, ack):
        """One fixed relationship delta after a complete, acknowledged reply."""
        turn_id = command.get("_conversation_turn")
        turn = session.delivery_turns.get(turn_id)
        if turn is None or turn["completed"] or turn["invalid"]:
            return
        if ack.status in TERMINAL and ack.status != "completed":
            turn["invalid"] = True
            return
        if ack.status != "completed":
            return
        actual = (ack.observed or {}).get("text", "")
        if actual != command["payload"]["text"]:
            turn["invalid"] = True
            return
        turn["chunks"][command["id"]]["complete"] = True
        actor = session.actors.get(turn["actor_id"])
        if (
            not turn["final_command_id"]
            or not any(chunk["nonempty"] for chunk in turn["chunks"].values())
            or not all(chunk["complete"] for chunk in turn["chunks"].values())
            or turn["epoch"] != session.epoch
            or not actor
            or actor["runtime_instance_id"] != turn["runtime_instance_id"]
            or turn["branch_id"] != session.branch_id
            or turn["protagonist_id"] != session.request.protagonist_id
        ):
            return
        event_id = turn_id + ":conversation_completed"
        # Event persistence can succeed before the relationship projection fails.
        # An acknowledgment retry must preserve the canonical event's original time.
        completion_game_datetime = turn.setdefault(
            "completion_game_datetime", session.game_datetime
        )
        await self.db(
            "record_event",
            session.request.profile_id,
            session.branch_id,
            {
                "event_id": event_id,
                "game_datetime": completion_game_datetime,
                "protagonist_id": turn["protagonist_id"],
                "event_type": "conversation_completed",
                "payload": {
                    "turn_id": turn_id,
                    "input_event_id": turn["input_event_id"],
                    "delivery_event_ids": [
                        cid + ":delivered"
                        for cid, chunk in turn["chunks"].items()
                        if chunk["nonempty"]
                    ],
                },
                "observer_ids": [turn["actor_id"]],
                "provenance": "directly_observed",
                "confidence": 1,
            },
        )
        await self.db(
            "apply_relationship_event",
            session.request.profile_id,
            session.branch_id,
            turn["actor_id"],
            turn["protagonist_id"],
            event_id,
            session_id=session.id,
        )
        turn["completed"] = True
        session.trace.append({"turn_id": turn_id, "relationship_event_id": event_id})

    def foreground_dialogue(self, session):
        return bool(
            (session.task and not session.task.done())
            or (session.skill_task and not session.skill_task.done())
            or any(
                c["kind"] == "dialogue.chunk" and c["state"] not in TERMINAL
                for c in session.commands.values()
            )
        )

    def trigger_context(self, session, scene_id, actor_id, created_at, role):
        actor = session.actors.get(actor_id)
        if not actor or not actor.get("area_id"):
            return None
        available = self.conversation_policy.check(actor, session.world).allowed
        return TriggerContext(
            profile_id=session.request.profile_id,
            session_id=session.id,
            timeline_branch=session.branch_id,
            protagonist_id=session.request.protagonist_id,
            game_datetime=datetime.fromisoformat(session.game_datetime),
            scene_id=scene_id,
            epoch=session.epoch,
            runtime_generation=session.request.runtime_generation,
            area_id=actor["area_id"],
            actor_id=actor_id,
            runtime_instance_id=actor["runtime_instance_id"],
            role=role,
            control_policy=actor["control_policy"],
            evidence_created_at=created_at,
            foreground_dialogue=self.foreground_dialogue(session),
            player_present=actor.get("distance", 1000) <= 20,
            alive=available,
            idle=available and not actor.get("busy", True),
            allowed_actions=tuple(
                self.policy.eligible(actor, session.world, actor.get("allowed_actions", []))
            ),
        )

    async def perceive(self, session, batch):
        """Accept explicitly enabled development attestations, never native proof."""
        async with session.lock:
            self.ensure_current(session, batch.runtime_generation)
            if (
                not self.settings.dev_perception
                or self.settings.release
                or (
                    DEVELOPMENT_PERCEPTION_CAPABILITY
                    not in session.request.manifest.capability_flags
                )
            ):
                raise HTTPException(403, "development perception is not authorized")
            if (
                batch.session_id != session.id
                or batch.protagonist_id != session.request.protagonist_id
            ):
                raise HTTPException(409, "perception session or protagonist mismatch")
            digest = hashlib.sha256(batch.model_dump_json().encode()).hexdigest()
            prior = session.perception_seen.get(batch.incident.event_id)
            if prior:
                if prior["digest"] != digest:
                    raise HTTPException(409, "perception incident id reused with different content")
                return {**prior["result"], "duplicate": True}
            if batch.seq <= session.cancel_seq or batch.seq in session.sequences:
                raise HTTPException(409, "perception sequence is canceled or already used")
            if batch.seq <= session.last_seq - 128:
                raise HTTPException(409, "perception sequence is outside the reordering window")
            if batch.game_datetime != datetime.fromisoformat(session.game_datetime):
                raise HTTPException(409, "perception game time must match the current snapshot")
            wall_now = self.utc_now()
            limits = PerceptionLimits()
            if (
                not 0
                <= (wall_now - batch.incident.sampled_at).total_seconds()
                <= limits.max_age_seconds
            ):
                raise HTTPException(409, "perception incident time is stale or future")
            if (
                not session.world
                or not 0 <= self.clock() - session.world.get("_sampled_at", 0) <= 5
            ):
                raise HTTPException(409, "perception requires a fresh world snapshot")
            if any(
                session.world.get(flag, True)
                for flag in (
                    "online",
                    "mission_active",
                    "cutscene_active",
                    "random_event_active",
                    "paused",
                )
            ):
                raise HTTPException(409, "perception world is protected")
            for witness in batch.witnesses:
                actor = session.actors.get(witness.actor_id)
                if (
                    not actor
                    or actor["runtime_instance_id"] != witness.runtime_instance_id
                    or not 0 <= self.clock() - actor.get("_sampled_at", 0) <= 5
                    or not 0
                    <= (wall_now - witness.sampled_at).total_seconds()
                    <= limits.max_age_seconds
                    or actor["position"] != witness.position.model_dump()
                    or actor.get("area_id") != witness.area_id
                    or actor.get("interior_id") != witness.interior_id
                    or actor["exists"] != witness.exists
                    or actor["alive"] != witness.alive
                ):
                    raise HTTPException(
                        409, "perception witness does not match its current binding"
                    )
            source_id = batch.incident.source_actor_id
            if source_id:
                source = session.actors.get(source_id)
                if (
                    not source
                    or not 0 <= self.clock() - source.get("_sampled_at", 0) <= 5
                    or not source["exists"]
                    or not source["alive"]
                    or source["position"] != batch.incident.position.model_dump()
                    or source.get("area_id") != batch.incident.area_id
                    or source.get("interior_id") != batch.incident.interior_id
                ):
                    raise HTTPException(409, "perception source does not match its current binding")
            incident, candidates, overheard = batch.incident, batch.witnesses, None
            if incident.event_type == "player.spoke":
                # Only an accepted utterance supplies the words and the volume. Its
                # addressee already remembers the conversation and never overhears it.
                spoken = session.player_speech.get(batch.scene_id)
                if (
                    incident.modality != "audible"
                    or spoken is None
                    or spoken["epoch"] != session.epoch
                    or not 0 <= (wall_now - spoken["accepted_at"]).total_seconds() <= 30
                    or spoken["whisper"] != incident.whisper
                ):
                    raise HTTPException(409, "player speech requires a current accepted utterance")
                incident = incident.model_copy(update={"text": spoken["text"]})
                candidates = [w for w in candidates if w.actor_id != spoken["actor_id"]]
                overheard = (
                    "I overheard the player whisper: "
                    if incident.whisper
                    else "I overheard the player say: "
                )
            bindings = {
                actor: state["runtime_instance_id"] for actor, state in session.actors.items()
            }
            evidence = witnesses_for(
                incident,
                candidates,
                wall_now,
                bindings,
                limits,
                delivered_utterances=session.delivered_utterances,
            )
            if batch.incident.modality == "report" and not evidence:
                raise HTTPException(409, "report requires a current delivered quote and listener")
            accepted_ids = []
            for item in evidence:
                canonical_id = (
                    "perceived_"
                    + hashlib.sha256(
                        json.dumps([session.id, item.event_id, item.actor_id]).encode()
                    ).hexdigest()
                )
                item = item.model_copy(update={"event_id": canonical_id})
                await self.db(
                    "record_event",
                    session.request.profile_id,
                    session.branch_id,
                    {
                        "event_id": canonical_id,
                        "game_datetime": session.game_datetime,
                        "protagonist_id": session.request.protagonist_id,
                        "event_type": item.event_type,
                        "payload": {
                            "text": item.text,
                            "source_incident_id": batch.incident.event_id,
                            "teller_id": item.teller_id,
                            "reported_event_id": item.reported_event_id,
                            "reported_lineage_verified": False,
                            "development_attestation": True,
                            "visibility_scope": "actor",
                            "scope_id": item.actor_id,
                        },
                        "observer_ids": [item.actor_id],
                        "provenance": item.provenance,
                        "confidence": item.confidence,
                    },
                )
                prefix = overheard or {
                    "directly_observed": "I observed: ",
                    "heard": "I heard: ",
                    "reported": f"I heard a report from {item.teller_id}: ",
                }[item.provenance]
                await self.db(
                    "add_memory",
                    session.request.profile_id,
                    session.branch_id,
                    item.actor_id,
                    session.request.protagonist_id,
                    session.game_datetime,
                    prefix + item.text,
                    [canonical_id],
                    item.provenance,
                    confidence=item.confidence,
                    session_id=session.id,
                    memory_id=canonical_id + ":memory",
                )
                accepted_ids.append(canonical_id)
                if self.trigger_engine:
                    actor = await self.db(
                        "get_actor",
                        session.request.profile_id,
                        item.actor_id,
                        session_id=session.id,
                    )
                    role = actor.get("role", "local")
                    if not isinstance(role, str) or not 1 <= len(role) <= 128:
                        role = "local"
                    created_at = self.clock()
                    context = self.trigger_context(
                        session, batch.scene_id, item.actor_id, created_at, role
                    )
                    if context:
                        while len(session.trigger_contexts) >= 36:
                            old_key, _ = session.trigger_contexts.popitem(last=False)
                            self.trigger_engine.cancel_scene(session.id, old_key[0])
                        session.trigger_contexts[(batch.scene_id, item.actor_id)] = (
                            created_at,
                            role,
                        )
                        self.trigger_engine.enqueue(item, context)
            session.last_seq = max(session.last_seq, batch.seq)
            session.sequences[batch.seq] = batch.incident.event_id
            session.sequences = {
                n: eid for n, eid in session.sequences.items() if n > session.last_seq - 128
            }
            result = {
                "incident_id": batch.incident.event_id,
                "accepted_event_ids": accepted_ids,
                "duplicate": False,
            }
            session.perception_seen[batch.incident.event_id] = {"digest": digest, "result": result}
            while len(session.perception_seen) > 2048:
                session.perception_seen.popitem(last=False)
            session.trace.append(
                {
                    "incident_id": batch.incident.event_id,
                    "accepted": len(accepted_ids),
                    "development_perception": True,
                }
            )
            await self._drain_triggers_locked(session)
            return result

    async def flush_triggers(self, session):
        async with session.lock:
            await self._drain_triggers_locked(session)

    async def _drain_triggers_locked(self, session):
        if not self.trigger_engine or session.closed:
            return
        self.expire_commands(session)
        contexts = []
        for (scene_id, actor_id), (created_at, role) in list(session.trigger_contexts.items()):
            context = self.trigger_context(session, scene_id, actor_id, created_at, role)
            if context:
                contexts.append(context)
            if self.clock() - created_at > 5 or not context:
                session.trigger_contexts.pop((scene_id, actor_id), None)
        for effect in self.trigger_engine.drain(contexts, max_effects=8):
            actor = session.actors.get(effect.actor_id)
            if (
                not actor
                or effect.epoch != session.epoch
                or actor["runtime_instance_id"] != effect.runtime_instance_id
                or effect.timeline_branch != session.branch_id
                or effect.protagonist_id != session.request.protagonist_id
            ):
                continue
            if effect.type in {"thought", "knowledge_write"}:
                if effect.type == "thought":
                    await self.db(
                        "add_memory",
                        effect.profile_id,
                        effect.timeline_branch,
                        effect.actor_id,
                        effect.protagonist_id,
                        effect.game_datetime.isoformat(),
                        effect.text,
                        list(effect.source_event_ids),
                        effect.provenance,
                        confidence=effect.confidence,
                        session_id=session.id,
                    )
                else:
                    await self.db(
                        "add_knowledge",
                        effect.profile_id,
                        effect.timeline_branch,
                        effect.game_datetime.isoformat(),
                        effect.text,
                        list(effect.source_event_ids),
                        effect.provenance,
                        owner_id=effect.actor_id,
                        scope="actor",
                        protagonist_id=effect.protagonist_id,
                        confidence=effect.confidence,
                        session_id=session.id,
                    )
            elif effect.type == "speak_to_player":
                if (
                    self.foreground_dialogue(session)
                    or not self.conversation_policy.check(actor, session.world).allowed
                ):
                    continue
                text = display_text(effect.text, DISPLAY_MAX_CHARS)
                if not text.strip():
                    continue
                turn_id = self.id_factory("trigger")
                # Authored speech is still only a queued command until its delivery acknowledgment.
                command = self.command(
                    session,
                    "dialogue.chunk",
                    effect.actor_id,
                    {
                        "text": text,
                        "speaker": actor["name"],
                        "scene_id": effect.scene_id,
                        "turn_id": turn_id,
                        "chunk_index": 0,
                        "final": True,
                    },
                )
                command["_autonomous"] = True
            else:
                decision = self.policy.check(
                    effect.action, actor, session.world, actor.get("allowed_actions", []), False
                )
                if not decision.allowed or any(
                    c["kind"] == "action.execute"
                    and c["actor_id"] == effect.actor_id
                    and c["state"] not in TERMINAL
                    for c in session.commands.values()
                ):
                    continue
                action_key = effect.actor_id, effect.action.action_id
                if self.clock() - session.last_action.get(action_key, -float("inf")) < 3:
                    continue
                session.last_action[action_key] = self.clock()
                command = self.command(
                    session,
                    "action.execute",
                    effect.actor_id,
                    {
                        "action_id": effect.action.action_id,
                        "arguments": effect.action.arguments.model_dump(exclude_none=True),
                        "policy_version": POLICY_VERSION,
                        "scene_id": effect.scene_id,
                        "consent": False,
                        "turn_id": self.id_factory("trigger"),
                    },
                )
                command["_autonomous"] = True
            session.trace.append(
                {
                    "trigger_rule_id": effect.rule_id,
                    "actor_id": effect.actor_id,
                    "effect_type": effect.type,
                    "source_event_ids": list(effect.source_event_ids),
                }
            )

    async def event(self, session, event):
        async with session.lock:
            self.ensure_current(session, event.runtime_generation)
            if (
                event.session_id != session.id
                or event.protagonist_id != session.request.protagonist_id
            ):
                raise HTTPException(409, "session or protagonist mismatch")
            digest = hashlib.sha256(event.model_dump_json().encode()).hexdigest()
            if event.event_id in session.seen:
                if session.seen[event.event_id] != digest:
                    raise HTTPException(409, "event id reused with different content")
                return {"event_id": event.event_id, "duplicate": True}
            if event.seq in session.sequences:
                raise HTTPException(409, "sequence already used")
            if event.seq <= session.last_seq - 128:
                raise HTTPException(409, "event outside reordering window")
            if event.seq > session.last_seq:
                if event.game_datetime.isoformat() < session.game_datetime:
                    raise HTTPException(409, "save time moved backwards; open a new session")
                session.game_datetime = event.game_datetime.isoformat()
            await self.process(session, event)
            session.seen[event.event_id] = digest
            session.sequences[event.seq] = event.event_id
            session.last_seq = max(session.last_seq, event.seq)
            while len(session.seen) > 2048:
                session.seen.popitem(last=False)
            session.sequences = {
                n: eid for n, eid in session.sequences.items() if n > session.last_seq - 128
            }
            return {"event_id": event.event_id, "duplicate": False}

    async def save_event(
        self, session, event, *, observers=None, provenance="system_fact", text=None
    ):
        value = {
            "event_id": event.event_id,
            "game_datetime": event.game_datetime.isoformat(),
            "protagonist_id": event.protagonist_id,
            "event_type": event.kind,
            "payload": event.payload,
            "provenance": provenance,
            "confidence": event.payload.get("confidence", 1),
            "observer_ids": observers or [],
        }
        if text:
            value["payload"] = {**event.payload, "text": text}
        await self.db("record_event", session.request.profile_id, session.branch_id, value)

    async def process(self, session, event):
        payload = event.payload
        actor_id = event.actor_id
        if event.kind == "actor.snapshot":
            if event.seq <= session.actor_seq.get(actor_id, 0):
                return
            if self.pin_detached(session, actor_id):
                # A saved identity whose pin failed or was forgotten has no embodiment here.
                session.actor_seq[actor_id] = event.seq
                return
            if actor_id not in session.actors and len(session.actors) >= self.settings.max_actors:
                raise HTTPException(429, "actor limit reached")
            prior = session.actors.get(actor_id)
            if prior and prior["runtime_instance_id"] != payload["runtime_instance_id"]:
                self.cancel(session)
                await self.stop_session_work(session)
                if prior["actor_kind"] == "ambient_ephemeral":
                    session.actors.pop(actor_id, None)
                    raise HTTPException(409, "new ambient instance requires a new actor identity")
            actor = await self.db(
                "get_actor", session.request.profile_id, actor_id, session_id=session.id
            )
            if actor is None:
                if payload["actor_kind"] != "ambient_ephemeral":
                    raise HTTPException(
                        409, "persistent actors require an installed character pack"
                    )
                actor = await self.db(
                    "register_actor",
                    session.request.profile_id,
                    {
                        "actor_id": actor_id,
                        "actor_kind": "ambient_ephemeral",
                        "display_name": payload["name"],
                        "persona": {
                            "biography": "A local pedestrian. No past experiences established."
                        },
                        "control_policy": payload["control_policy"],
                        "allowed_actions": session.request.capabilities,
                        "session_id": session.id,
                    },
                )
            await self.db(
                "bind_instance",
                session.request.profile_id,
                actor_id,
                session.id,
                payload["runtime_handle"],
                event.runtime_generation,
            )
            payload = dict(payload)
            stored_kind = actor.get("actor_kind")
            policies = {actor.get("control_policy", "ambient"), payload["control_policy"]}
            if stored_kind != "anchored_service":
                # FR-ACT-02: recompute role and ownership from facts; a label cannot widen policy.
                payload["role_class"], payload["role_reason"] = revalidate(payload)
                policies.add(ROLE_POLICIES[payload["role_class"]])
                if ownership_from_facts(payload["ped_facts"])[0] not in {"ambient", "mod_owned"}:
                    policies.add("protected")
            if (
                payload["ownership"] == "mod_owned"
                and "ambient_ephemeral" in {stored_kind, payload["actor_kind"]}
            ) or (stored_kind == "ambient_ephemeral" and payload["actor_kind"] != stored_kind):
                policies.add("protected")
            if "protected" in policies:
                payload["control_policy"] = "protected"
            elif "service_limited" in policies:
                payload["control_policy"] = "service_limited"
            payload["allowed_actions"] = [
                a for a in session.request.capabilities if a in actor.get("allowed_actions", [])
            ]
            integration = actor.get("service_integration_id")
            payload["_service_authorized"] = bool(
                actor.get("actor_kind") == "anchored_service"
                and actor.get("control_policy") == "service_limited"
                and integration
                and integration == payload.get("service_integration_id")
                and "service:" + integration in session.request.manifest.capability_flags
            )
            payload["_sampled_at"] = self.clock()
            session.actors[actor_id] = payload
            session.actor_seq[actor_id] = event.seq
            if not self.conversation_policy.check(payload, session.world).allowed:
                self.cancel(session)
                await self.stop_session_work(session)
        elif event.kind == "actor.removed":
            if event.seq > session.actor_seq.get(actor_id, 0):
                session.actors.pop(actor_id, None)
                session.actor_seq[actor_id] = event.seq
                # The adapter dropped this binding; a recycled handle needs a fresh identity.
                await self.db(
                    "release_instance", session.request.profile_id, session.id, actor_id=actor_id
                )
                self.cancel(session)
                await self.stop_session_work(session)
        elif event.kind == "world.snapshot":
            if event.seq <= session.world_seq:
                return
            session.world = {**payload, "_sampled_at": self.clock()}
            session.world_seq = event.seq
            if payload["protagonist_id"] != session.request.protagonist_id or any(
                payload[x]
                for x in (
                    "online",
                    "mission_active",
                    "cutscene_active",
                    "random_event_active",
                    "paused",
                )
            ):
                self.cancel(session)
                await self.stop_session_work(session)
        elif event.kind == "scene.cancel":
            session.cancel_seq = max(session.cancel_seq, event.seq)
            self.cancel(session, payload["scene_id"])
            await self.stop_session_work(session)
            self.command(session, "scene.cancel", None, payload)
        elif event.kind.startswith("voice."):
            await self.voice_control(session, event)
        elif event.kind == "world.observed":
            # Native perception attestations are not implemented yet. This route
            # accepts explicitly unverified manual observations, never eyewitness truth.
            if payload["provenance"] != "unverified":
                raise HTTPException(422, "grounded perception attestations are not implemented")
            await self.save_event(session, event, observers=[], provenance="unverified")
        elif event.kind == "player.utterance_text":
            await self.accept_utterance_locked(session, event)
        elif event.kind == "actor.pin_requested":
            await self.pin_requested_locked(session, event)
        if session.social_scenes and event.kind in {"actor.snapshot", "world.snapshot"}:
            await self.refresh_social_locked(session)

    async def accept_utterance_locked(
        self,
        session,
        event,
        *,
        voice_action_eligible=False,
        voice_identity=None,
    ):
        actor_id, payload = event.actor_id, event.payload
        if event.seq <= session.cancel_seq:
            raise HTTPException(409, "utterance predates cancellation")
        actor = session.actors.get(actor_id)
        if not self.conversation_policy.check(actor, session.world).allowed:
            raise HTTPException(409, "target unavailable in current world state")
        if payload["whisper"] and self.whisper_too_far(
            session, actor_id, payload["scene_id"], voice_identity
        ):
            return False
        if (
            self.skill_scheduler_active(session)
            or session.skill_task is not None
            or session.social_task is not None
            or any(not state["canceled"] for state in session.social_scenes.values())
        ):
            self.cancel(session)
            await self.stop_session_work(session)
            self.command(session, "scene.cancel", None, {"scene_id": None})
        if voice_identity is None and session.speech_state in {
            "opening_mic",
            "recording",
            "stopping_mic",
            "transcribing",
        }:
            self.cancel(session)
            await self.stop_session_work(session)
            self.command(session, "scene.cancel", None, {"scene_id": None})
        timestamp = self.clock()
        while session.turns and session.turns[0] < timestamp - 60:
            session.turns.popleft()
        if len(session.turns) >= self.settings.max_turns_per_minute:
            raise HTTPException(429, "scene model budget exhausted")
        if session.task and not session.task.done():
            raise HTTPException(409, "one foreground turn is already active")
        self.expire_commands(session)
        autonomous_scenes = {
            c["payload"]["scene_id"]
            for c in session.commands.values()
            if c.get("_autonomous") and c["state"] not in TERMINAL
        }
        for scene_id in autonomous_scenes:
            self.cancel(session, scene_id)
            self.command(session, "scene.cancel", None, {"scene_id": scene_id})
        if any(
            c["kind"] == "dialogue.chunk" and c["state"] not in TERMINAL
            for c in session.commands.values()
        ):
            raise HTTPException(409, "previous speech has not finished delivery")
        session.turns.append(timestamp)
        await self.save_event(session, event, observers=[actor_id], provenance="heard")
        await self.db(
            "add_memory",
            session.request.profile_id,
            session.branch_id,
            actor_id,
            event.protagonist_id,
            event.game_datetime.isoformat(),
            ("The player whispered: " if payload["whisper"] else "The player said: ")
            + payload["text"],
            [event.event_id],
            "heard",
            session_id=session.id,
        )
        # Development perception binds overhearing to this accepted text and volume.
        session.player_speech[payload["scene_id"]] = {
            "event_id": event.event_id,
            "actor_id": actor_id,
            "text": payload["text"],
            "whisper": payload["whisper"],
            "epoch": session.epoch,
            "accepted_at": self.utc_now(),
        }
        session.player_speech.move_to_end(payload["scene_id"])
        while len(session.player_speech) > 64:
            session.player_speech.popitem(last=False)
        session.task = asyncio.create_task(
            self.turn(
                session,
                event,
                session.epoch,
                action_eligible=payload["input_mode"] != "voice" or voice_action_eligible,
            )
        )
        return True

    async def turn(self, session, event, epoch, *, action_eligible=True):
        turn_id = self.id_factory("turn")
        actor_id = event.actor_id
        scene_id = event.payload["scene_id"]
        started = self.clock()
        try:
            actor = await self.db(
                "get_actor", session.request.profile_id, actor_id, session_id=session.id
            )
            memories = await self.memory_index.retrieve_memories(
                session.request.profile_id,
                session.branch_id,
                actor_id,
                event.protagonist_id,
                event.game_datetime.isoformat(),
                event.payload["text"],
                token_budget=self.settings.memory_token_budget,
                session_id=session.id,
            )
            relationship = await self.db(
                "get_relationship",
                session.request.profile_id,
                session.branch_id,
                actor_id,
                event.protagonist_id,
                event.game_datetime.isoformat(),
                session_id=session.id,
            )
            groups = tuple(g for g in actor.get("group_ids", [])[:16] if isinstance(g, str))
            knowledge = await self.db(
                "retrieve_knowledge",
                session.request.profile_id,
                session.branch_id,
                actor_id,
                event.protagonist_id,
                event.game_datetime.isoformat(),
                group_ids=groups,
                region_id=session.actors[actor_id].get("area_id"),
                token_budget=self.settings.knowledge_token_budget,
                session_id=session.id,
            )
            eligible = (
                self.policy.eligible(
                    session.actors[actor_id],
                    session.world,
                    session.actors[actor_id].get("allowed_actions", []),
                    event.payload["consent"],
                )
                if action_eligible
                else []
            )
            memories = self.memory_index.revalidate_memories(
                session.request.profile_id, session.branch_id, actor_id,
                event.protagonist_id, event.game_datetime.isoformat(), event.payload["text"],
                memories=memories, session_id=session.id,
            )
            memories = [m for m in memories if event.event_id not in m.get("source_event_ids", [])]
            context, context_trace = build_context(
                actor=actor,
                snapshot=session.actors[actor_id],
                world=session.world,
                memories=memories,
                knowledge=knowledge,
                relationship=relationship,
                input_text=event.payload["text"],
                input_volume="whisper" if event.payload["whisper"] else "normal",
                eligible=eligible,
                protagonist_id=event.protagonist_id,
                game_datetime=event.game_datetime.isoformat(),
                byte_budget=self.settings.context_byte_budget,
                action_outcomes=[
                    o
                    for o in session.action_outcomes
                    if o["actor_id"] == actor_id
                    and o["session_id"] == session.id
                    and o["protagonist_id"] == event.protagonist_id
                ][-8:],
            )
            session.trace.append({"turn_id": turn_id, "actor_id": actor_id, **context_trace})
            if self.provider.name != "disabled":
                session.delivery_turns[turn_id] = {
                    "actor_id": actor_id,
                    "runtime_instance_id": session.actors[actor_id]["runtime_instance_id"],
                    "protagonist_id": event.protagonist_id,
                    "branch_id": session.branch_id,
                    "scene_id": scene_id,
                    "epoch": epoch,
                    "input_event_id": event.event_id,
                    "chunks": {},
                    "final_command_id": None,
                    "invalid": False,
                    "completed": False,
                }
                while len(session.delivery_turns) > 32:
                    session.delivery_turns.popitem(last=False)
            text = ""
            index = 0
            delivery_seconds = 0.0
            async with (
                asyncio.timeout(30),
                aclosing(
                    sentence_chunks(self.provider, context, self.settings.max_reply_chars)
                ) as chunks,
            ):
                async for chunk in chunks:
                    if session.closed or session.epoch != epoch:
                        return
                    chunk = chunk[: self.settings.max_reply_chars - len(text)]
                    if not chunk:
                        break
                    # Subtitles, speech, receipts, and memories all use the displayed text.
                    chunk = display_text(chunk)
                    if not chunk.strip():
                        continue
                    duration = max(3, min(15, len(chunk) / 12))
                    if index >= 20 or delivery_seconds + duration > 100:
                        break
                    delivery_seconds += duration
                    text += chunk
                    await self.deliver_dialogue_chunk(
                        session,
                        actor_id,
                        {
                            "text": chunk,
                            "speaker": actor["display_name"],
                            "scene_id": scene_id,
                            "turn_id": turn_id,
                            "chunk_index": index,
                            "final": False,
                        },
                    )
                    index += 1
            if not text.strip():
                text = "I'm not sure what to say right now."
                await self.deliver_dialogue_chunk(
                    session,
                    actor_id,
                    {
                        "text": text,
                        "speaker": actor["display_name"],
                        "scene_id": scene_id,
                        "turn_id": turn_id,
                        "chunk_index": index,
                        "final": False,
                    },
                )
                index += 1
            await self.deliver_dialogue_chunk(
                session,
                actor_id,
                {
                    "text": "",
                    "speaker": actor["display_name"],
                    "scene_id": scene_id,
                    "turn_id": turn_id,
                    "chunk_index": index,
                    "final": True,
                },
            )
            try:
                async with asyncio.timeout(20):
                    proposal = (
                        await self.provider.action(context, text, eligible) if eligible else None
                    )
            except Exception:
                proposal = None
            if session.closed or session.epoch != epoch:
                return
            if proposal:
                decision = self.policy.check(
                    proposal,
                    session.actors.get(actor_id),
                    session.world,
                    session.actors.get(actor_id, {}).get("allowed_actions", []),
                    event.payload["consent"],
                )
                session.trace.append({"turn_id": turn_id, "policy": decision.reason})
                if decision.allowed:
                    action_key = (actor_id, proposal.action_id)
                    if self.clock() - session.last_action.get(action_key, -float("inf")) < 3:
                        return
                    if any(
                        c["kind"] == "action.execute"
                        and c["actor_id"] == actor_id
                        and c["state"] not in TERMINAL
                        for c in session.commands.values()
                    ):
                        return
                    session.last_action[action_key] = self.clock()
                    action_command = self.command(
                        session,
                        "action.execute",
                        actor_id,
                        {
                            "action_id": proposal.action_id,
                            "arguments": proposal.arguments.model_dump(exclude_none=True),
                            "policy_version": POLICY_VERSION,
                            "scene_id": scene_id,
                            "consent": event.payload["consent"],
                            "turn_id": turn_id,
                        },
                    )
                    action_command["_skill_origin"] = "external"
                    action_command["_skill_source_event_id"] = event.event_id
            session.trace.append(
                {
                    "turn_id": turn_id,
                    "actor_id": actor_id,
                    "provider": self.provider.name,
                    "duration_ms": round((self.clock() - started) * 1000),
                    "status": "generated",
                }
            )
        except asyncio.CancelledError:
            if turn_id in session.delivery_turns:
                session.delivery_turns[turn_id]["invalid"] = True
            session.trace.append({"turn_id": turn_id, "status": "canceled"})
        except Exception as exc:
            if turn_id in session.delivery_turns:
                session.delivery_turns[turn_id]["invalid"] = True
            session.trace.append(
                {"turn_id": turn_id, "status": "failed", "error_type": type(exc).__name__}
            )
            try:
                self.command(
                    session,
                    "status",
                    actor_id,
                    {
                        "state": "unavailable",
                        "message": "The NPC service could not finish this turn.",
                    },
                )
            except HTTPException:
                pass

    async def acknowledge(self, session, command_id, ack, *, internal_audio=False):
        async with session.lock:
            result = await self.acknowledge_locked(
                session,
                command_id,
                ack,
                internal_audio=internal_audio,
            )
            await self.flush_outcomes(session)
            return result

    async def acknowledge_locked(self, session, command_id, ack, *, internal_audio=False):
        if internal_audio and session.closed and session.cleanup_pending:
            # Closing still settles trusted render receipts before purging ambient rows.
            if ack.runtime_generation != session.request.runtime_generation:
                raise HTTPException(409, "stale runtime generation")
        else:
            self.ensure_current(session, ack.runtime_generation)
        command = session.commands.get(command_id)
        if command is None:
            raise HTTPException(404, "unknown command")
        if command["kind"] == "actor.pin":
            return await self.acknowledge_pin_locked(session, command, ack)
        if command.get("_audio_pending") and not internal_audio:
            observed = ack.observed or {}
            actual = observed.get("text", "")
            if (
                not isinstance(actual, str)
                or not command["payload"]["text"].startswith(actual)
                or (actual and observed.get("delivery") not in {"subtitle", "simulated"})
            ):
                raise HTTPException(422, "audio subtitles require matching render evidence")
            prior = command.get("_subtitle_ack")
            if prior is None or len(actual) > len((prior.observed or {}).get("text", "")):
                command["_subtitle_ack"] = ack
            return {"id": command_id, "status": ack.status, "duplicate": prior is not None}
        if command.get("_audio_completed") and not internal_audio:
            actual = (ack.observed or {}).get("text", "")
            if not isinstance(actual, str) or not command["payload"]["text"].startswith(actual):
                raise HTTPException(422, "delivered text does not match command")
            command["state"] = ack.status
            return {"id": command_id, "status": ack.status, "duplicate": True}
        terminal_state = command["state"] if command["state"] in TERMINAL else None
        partial_delivery = (
            terminal_state in {"canceled", "failed", "expired"}
            and command["kind"] == "dialogue.chunk"
            and bool((ack.observed or {}).get("text"))
            and not command.get("_delivery_recorded")
        )
        if terminal_state and not partial_delivery:
            return {"id": command_id, "status": terminal_state, "duplicate": True}
        if not partial_delivery and datetime.fromisoformat(command["expires_at"]) <= self.utc_now():
            command["state"] = "expired"
            self.invalidate_delivery_turn(session, command)
            self.record_outcome(
                session, command, "expired", "authorization expired before completion"
            )
            return {"id": command_id, "status": "expired", "duplicate": False}
        if ack.status == "started" and command["state"] == "started":
            return {"id": command_id, "status": "started", "duplicate": True}
        self.validate_skill_ack(session, command, ack)
        if (
            command["kind"] == "action.execute"
            and ack.status == "completed"
            and command["state"] != "started"
        ):
            raise HTTPException(409, "action completion requires a started acknowledgment")
        if command["kind"] == "dialogue.chunk" and (ack.status == "completed" or partial_delivery):
            delivered = command["payload"]["text"]
            observed = ack.observed or {}
            if delivered and observed.get("delivery") not in {
                "subtitle",
                "speech",
                "simulated",
            }:
                raise HTTPException(422, "dialogue completion requires delivery evidence")
            actual = observed.get("text", "")
            if not isinstance(actual, str) or not delivered.startswith(actual):
                raise HTTPException(422, "delivered text does not match command")
            if (
                actual
                and not command.get("_delivery_recorded")
                and (command.get("_record_delivery") or command.get("_autonomous"))
            ):
                delivered_event = {
                    "event_id": command_id + ":delivered",
                    "game_datetime": command["_game_datetime"],
                    "protagonist_id": session.request.protagonist_id,
                    "event_type": "dialogue.delivered",
                    "payload": {
                        "text": actual,
                        "turn_id": command["payload"]["turn_id"],
                        "speaker": command["payload"]["speaker"],
                        "display_rule": DISPLAY_RULE,
                    },
                    "provenance": "directly_observed",
                    "confidence": 1,
                    "observer_ids": [command["actor_id"]],
                }
                await self.db(
                    "record_event",
                    session.request.profile_id,
                    session.branch_id,
                    delivered_event,
                )
                await self.db(
                    "add_memory",
                    session.request.profile_id,
                    session.branch_id,
                    command["actor_id"],
                    session.request.protagonist_id,
                    command["_game_datetime"],
                    "I said: " + actual,
                    [delivered_event["event_id"]],
                    "directly_observed",
                    session_id=session.id,
                    memory_id=command_id + ":memory",
                )
                if len(actual) <= 1000:
                    receipt = DeliveredUtterance(
                        event_id=delivered_event["event_id"],
                        actor_id=command["actor_id"],
                        runtime_instance_id=command["runtime_instance_id"],
                        text=actual,
                        # Until adapters attest the actual delivery time,
                        # queue time is the conservative earliest bound.
                        # Delayed acknowledgments cannot refresh a quote.
                        sampled_at=command["_created_at_utc"],
                    )
                    session.delivered_utterances[receipt.event_id] = receipt
                    while len(session.delivered_utterances) > 64:
                        session.delivered_utterances.popitem(last=False)
            command["_delivery_recorded"] = True
        if command["kind"] == "action.execute":
            action_stamp = command["_game_datetime"]
            if command.get("_skill"):
                action_stamp = command.setdefault("_skill_ack_times", {}).setdefault(
                    ack.status, session.game_datetime
                )
            event = {
                "event_id": command_id + ":" + ack.status,
                "game_datetime": action_stamp,
                "protagonist_id": session.request.protagonist_id,
                "event_type": "action." + ack.status,
                "payload": {
                    "action_id": command["payload"]["action_id"],
                    "conversation_turn_id": command["payload"]["turn_id"],
                    "reason": ack.reason,
                    "observed": ack.observed,
                },
                "provenance": "system_fact",
                "confidence": 1,
                "observer_ids": [command["actor_id"]],
            }
            await self.db("record_event", session.request.profile_id, session.branch_id, event)
            self.record_outcome(session, command, ack.status, ack.reason, service=False)
            outcome_text = (
                f"The game reported my {command['payload']['action_id']} request "
                f"as {ack.status}. "
                + (
                    "Completion was confirmed."
                    if ack.status == "completed"
                    else "This does not confirm completion."
                )
            )
            await self.db(
                "add_memory",
                session.request.profile_id,
                session.branch_id,
                command["actor_id"],
                session.request.protagonist_id,
                action_stamp,
                outcome_text,
                [event["event_id"]],
                "system_fact",
                session_id=session.id,
                memory_id=event["event_id"] + ":memory",
            )
        if command["kind"] == "dialogue.chunk":
            await self.record_conversation_completion(session, command, ack)
        self.validate_skill_ack_publication(session, command)
        if command["kind"] == "action.execute":
            # Joined projection writes may outlive service authorization. Retain
            # the adapter evidence, but never restore canceled/expired commands.
            self.expire_commands(session)
            if command["state"] in TERMINAL:
                return {"id": command_id, "status": command["state"], "duplicate": False}
        command["state"] = terminal_state or ack.status
        if (
            internal_audio
            and ack.status == "completed"
            and (ack.observed or {}).get("delivery") == "speech"
        ):
            command["_audio_completed"] = True
            rendered = command.pop("_subtitle_ack", None)
            command["state"] = rendered.status if rendered else "queued"
        command["reason"] = ack.reason
        self.finish_skill_ack(session, command, ack)
        if self.skill_scheduler is not None:
            self.skill_scheduler.observe(session, command, ack)
        session.changed.set()
        return {"id": command_id, "status": command["state"], "duplicate": False}

    async def close_session(self, session):
        async with session.lock:
            await self.close_session_locked(session)

    async def close_session_locked(self, session):
        """Close after the caller acquires session.lock, including admin transactions."""
        if session.closed and not session.cleanup_pending:
            return
        profile = session.request.profile_id
        cleanup_key = (profile, session.id)
        self.cleanup_pending.setdefault(cleanup_key, None)
        session.cleanup_pending = True
        # Revoke authorization immediately; closed does not imply the purge committed.
        session.closed = True
        try:
            self.cancel(session)
            if session.task:
                await asyncio.gather(session.task, return_exceptions=True)
            await self.stop_session_work(session)
            await self.speech.forget(session_id=session.id)
            if self.social is not None:
                await self.social.forget_session(session.id)
            if self.social_scheduler is not None:
                self.social_scheduler.forget(session.id)
            if self.neighborhood.scheduler is not None:
                self.neighborhood.scheduler.forget(session.id)
            session.social_scenes.clear()
            if self.trigger_engine:
                self.trigger_engine.cancel_session(session.id)
            await self.db("begin_session_cleanup", profile, session.id)
            await self.db("end_session", profile, session.id)
        except Exception as exc:
            self.cleanup_pending[cleanup_key] = type(exc).__name__
            raise HTTPException(503, "session cleanup pending; automatic retry scheduled") from exc
        session.cleanup_pending = False
        self.cleanup_pending.pop(cleanup_key, None)
        session.actors.clear()
        session.commands.clear()
        session.action_outcomes.clear()
        session.perception_seen.clear()
        session.delivered_utterances.clear()
        session.player_speech.clear()
        session.trigger_contexts.clear()
        session.delivery_turns.clear()
        session.changed.set()

    def cleanup_diagnostics(self):
        pending = [
            {"profile_id": profile, "session_id": sid, "error_type": error}
            for (profile, sid), error in sorted(self.cleanup_pending.items())
        ]
        return {
            "status": "pending" if pending or self.cleanup_recovery_pending else "complete",
            "message": (
                "Session cleanup pending. Session-only history may remain on disk; "
                "cleanup retries automatically each maintenance tick and at startup."
                if pending or self.cleanup_recovery_pending else "No session cleanup pending."
            ),
            "recovery_pending": self.cleanup_recovery_pending,
            "recovery_error_type": self.cleanup_recovery_error,
            "sessions": pending,
        }

    async def expire_sessions(self):
        async with self.open_lock:
            if self.cleanup_recovery_pending:
                try:
                    unfinished = await self.db("unfinished_sessions")
                except Exception as exc:
                    self.cleanup_recovery_error = type(exc).__name__
                else:
                    # A crash may precede the pending marker write. All unfinished
                    # sessions belong to the previous service owner at startup.
                    for row in unfinished:
                        self.cleanup_pending.setdefault(
                            (row["profile_id"], row["session_id"]), None
                        )
                    self.cleanup_recovery_pending = False
                    self.cleanup_recovery_error = None
            for profile, sid in list(self.cleanup_pending):
                if sid in self.sessions:
                    continue
                try:
                    await self.db("begin_session_cleanup", profile, sid)
                    await self.db("end_session", profile, sid)
                except Exception as exc:
                    self.cleanup_pending[(profile, sid)] = type(exc).__name__
                else:
                    self.cleanup_pending.pop((profile, sid), None)
        for session in list(self.sessions.values()):
            if (
                session.cleanup_pending or (
                    not session.closed
                    and self.clock() - session.last_activity > self.settings.session_timeout_seconds
                )
            ):
                try:
                    await self.close_session(session)
                except HTTPException:
                    # One unavailable purge must not stop maintenance or other cleanups.
                    pass
        for sid in list(self.sessions):
            if (
                len(self.sessions) > self.settings.max_sessions * 2
                and self.sessions[sid].closed and not self.sessions[sid].cleanup_pending
            ):
                del self.sessions[sid]

    async def maintain(self):
        while True:
            await asyncio.sleep(1)
            self.refresh_speech_status()
            if self.neighborhood.scheduler is not None:
                self.neighborhood.scheduler.tick()
            if self.skill_scheduler is not None and self._skills_first:
                self.skill_scheduler.tick()
            for session in list(self.sessions.values()):
                async with session.lock:
                    if not session.closed:
                        self.expire_commands(session)
                        try:
                            await self.flush_outcomes(session)
                        except HTTPException:
                            # Keep failed records bounded and retry on the next pass.
                            pass
                if not session.closed and session.social_scenes:
                    await self.refresh_social(session)
                if not session.closed and self.social_scheduler is not None:
                    await self.social_scheduler.tick(session)
            if self.skill_scheduler is not None and not self._skills_first:
                self.skill_scheduler.tick()
            self._skills_first = not self._skills_first
            await self.expire_sessions()

    async def shutdown(self):
        for session in list(self.sessions.values()):
            try:
                await self.close_session(session)
            except HTTPException:
                # The durable unfinished row survives shutdown for startup recovery.
                pass
        await self.speech.close()
        if self.social is not None:
            await self.social.aclose()
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()
        self.ai_owner.close()
