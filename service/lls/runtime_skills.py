"""Development skill orchestration through the existing command/ACK boundary."""

import asyncio
import json
import math
from dataclasses import asdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import Field, model_validator

from .actions import POLICY_VERSION
from .gateway import GatewayProvider
from .protocol import Identifier, Protagonist, StrictModel
from .skill_learning import SkillLearner, SkillLearningConfig, SkillRequest
from .skill_store import SkillStore
from .skills import PrimitiveReceipt, SkillEnvironment, SkillIdentity, SkillInterpreter

DEVELOPMENT_SKILLS_CAPABILITY = "development:skills:v1"
BRIDGE_SKILL_ACTIONS = frozenset({"face_player", "wait_here"})
SKILL_TERMINAL = {"completed", "rejected", "expired", "failed", "canceled"}


class SkillBoundRequest(StrictModel):
    session_id: Identifier
    branch_id: Identifier
    protagonist_id: Protagonist
    runtime_generation: int = Field(ge=0)
    actor_id: Identifier
    runtime_instance_id: Identifier
    game_datetime: datetime
    source_event_ids: list[Identifier] = Field(min_length=1, max_length=8)
    consent: bool = False

    @model_validator(mode="after")
    def unique_sources(self):
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("skill needs unique source identifiers")
        return self


class SkillGenerate(SkillBoundRequest):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: int = Field(ge=1, le=1000000)
    action_class: str = "conversational_courtesy"
    revises: Identifier | None = None


class SkillTrial(SkillBoundRequest):
    candidate_id: Identifier


class SkillAdopt(SkillBoundRequest):
    trial_id: Identifier


class SkillExecute(SkillBoundRequest):
    skill_id: Identifier


class SkillRuntime:
    def skill_scheduler_active(self, session):
        scheduler = getattr(self, "skill_scheduler", None)
        task = scheduler.workers.get(session.id) if scheduler is not None else None
        return task is not None and not task.done()

    def initialize_skills(self):
        self.skill_store = SkillStore(self.repo)
        self.skill_owners = {}
        self.skill_learner = None
        if self.settings.dev_skills and isinstance(self.provider, GatewayProvider):
            self.skill_learner = SkillLearner(
                self.skill_store,
                self.provider,
                is_current=self.skill_is_current,
                config=SkillLearningConfig(enabled=True, release=False),
            )

    def replace_skill_provider(self):
        self.skill_learner = None
        if self.settings.dev_skills and isinstance(self.provider, GatewayProvider):
            self.skill_learner = SkillLearner(
                self.skill_store,
                self.provider,
                is_current=self.skill_is_current,
                config=SkillLearningConfig(enabled=True, release=False),
            )

    def authorize_skill(self, session, body):
        self.ensure_current(session, body.runtime_generation)
        if (
            not self.settings.dev_skills
            or self.settings.release
            or DEVELOPMENT_SKILLS_CAPABILITY not in session.request.manifest.capability_flags
        ):
            raise HTTPException(403, "development skills are not authorized")
        if (
            body.session_id != session.id
            or body.branch_id != session.branch_id
            or body.protagonist_id != session.request.protagonist_id
            or body.game_datetime != datetime.fromisoformat(session.game_datetime)
        ):
            raise HTTPException(409, "skill session, branch, protagonist, or time mismatch")
        identity = SkillIdentity(
            session.request.profile_id,
            session.branch_id,
            session.request.protagonist_id,
            session.id,
            session.request.runtime_generation,
            body.actor_id,
            body.runtime_instance_id,
        )
        actor = session.actors.get(body.actor_id)
        if not actor or actor["runtime_instance_id"] != body.runtime_instance_id:
            raise HTTPException(409, "skill actor binding changed")
        return identity

    def skill_is_current(self, identity):
        session = self.sessions.get(identity.session_id)
        state = session.skill_run if session else None
        actor = session.actors.get(identity.actor_id) if session else None
        return bool(
            session
            and not session.closed
            and state
            and not state["canceled"]
            and state["identity"] == identity
            and state["epoch"] == session.epoch
            and state["provider"] is self.provider
            and (self.skill_scheduler is None or self.skill_scheduler.run_is_current(session))
            and self.settings.dev_skills
            and not self.settings.release
            and DEVELOPMENT_SKILLS_CAPABILITY in session.request.manifest.capability_flags
            and session.request.profile_id == identity.profile_id
            and session.branch_id == identity.branch_id
            and session.request.protagonist_id == identity.protagonist_id
            and session.request.runtime_generation == identity.runtime_generation
            and datetime.fromisoformat(session.game_datetime) >= state["start_time"]
            and actor
            and actor["runtime_instance_id"] == identity.runtime_instance_id
            and self.conversation_policy.check(actor, session.world).allowed
            and session.world.get("protagonist_id") == identity.protagonist_id
            and not (session.task and not session.task.done())
            and session.speech_state == "idle"
            and not (session.social_task and not session.social_task.done())
            and not any(not s["canceled"] for s in session.social_scenes.values())
        )

    def skill_environment(self, identity):
        if not self.skill_is_current(identity):
            raise HTTPException(409, "skill identity or world is no longer current")
        session = self.sessions[identity.session_id]
        state = session.skill_run
        # A reset/source deletion must also stop later operations in this execution.
        current = self.skill_store.need_context(identity, session.game_datetime, state["need_ids"])
        if current["audit_revision"] != state["audit_revision"]:
            raise HTTPException(409, "skill sources changed during execution")
        program = state.get("program")
        if program:
            with self.repo._lock:
                if program["kind"] == "selected":
                    selected = self.skill_store.select(
                        identity, session.game_datetime, program["skill_id"]
                    )
                    if selected is None or selected["record_id"] != program["record_id"]:
                        raise HTTPException(409, "selected skill was retired or replaced")
                    compiled = selected["compiled"]
                else:
                    _, stamp = self.skill_store._scope(identity, session.game_datetime)
                    row = self.skill_store._owned(
                        identity, stamp, program["record_id"], {"candidate"}
                    )
                    compiled = self.skill_store._compiled(identity, stamp, row)
                if compiled.fingerprint != program["fingerprint"]:
                    raise HTTPException(409, "skill code or dependencies changed")
        actor = session.actors[identity.actor_id]
        return SkillEnvironment(
            identity,
            dict(actor),
            dict(session.world),
            tuple(a for a in actor["allowed_actions"] if a in BRIDGE_SKILL_ACTIONS),
            state["consent"],
        )

    @staticmethod
    def validate_bridge_skill(compiled):
        actions = [
            json.loads(raw) for raw in compiled.operations if json.loads(raw)["op"] == "action"
        ]
        if len(actions) > 4:
            raise HTTPException(409, "development bridge permits at most four primitive actions")
        for action in actions:
            proposal = action["proposal"]
            if proposal["action_id"] not in BRIDGE_SKILL_ACTIONS:
                raise HTTPException(409, "skill primitive has no supported development bridge")
            if proposal["arguments"].get("duration_seconds") is None:
                raise HTTPException(409, "bridge skills require explicit primitive durations")

    async def preflight_skill(self, session, candidate_id):
        """Persist simulated interpreter evidence before authorizing a bridge trial."""
        identity = session.skill_run["identity"]
        self.skill_environment(identity)

        async def execute(identity, operation_id, proposal, deadline):
            self.skill_environment(identity)
            return PrimitiveReceipt(
                operation_id=operation_id, status="completed", distance_meters=0
            )

        async def cancel(identity, operation_id):
            pass

        return await self.skill_store.record_trial(
            candidate_id,
            identity,
            session.game_datetime,
            interpreter=SkillInterpreter(enabled=True, clock=self.clock),
            environment=self.skill_environment,
            execute=execute,
            cancel=cancel,
        )

    def require_skill_preflight(self, identity, game_datetime, program):
        with self.repo._lock:
            _, stamp = self.skill_store._scope(identity, game_datetime)
            candidate = self.skill_store._owned(
                identity, stamp, program["record_id"], {"candidate"}
            )
            compiled = self.skill_store._compiled(identity, stamp, candidate)
            trial = self.skill_store._owned(
                identity, stamp, program.get("preflight_id"), {"trial"}
            )
            result = self.skill_store._record(trial)["result"]
            if (
                trial["parent_id"] != program["record_id"]
                or trial["fingerprint"] != compiled.fingerprint
                or program["fingerprint"] != compiled.fingerprint
                or result.get("fingerprint") != compiled.fingerprint
                or result.get("verification") != "simulated"
                or result.get("status") != "completed"
            ):
                raise HTTPException(409, "bridge trial requires a matching passing preflight")

    async def skill_call(self, session, body, operation, *, preflight_id=None):
        async with session.lock:
            identity = self.authorize_skill(session, body)
            owner = (identity.profile_id, identity.actor_id)
            if owner in self.skill_owners or (session.skill_task and not session.skill_task.done()):
                raise HTTPException(409, "a skill operation already owns this actor or session")
            if any(
                c["kind"] in {"action.execute", "dialogue.chunk"}
                and c["state"] not in SKILL_TERMINAL
                for c in session.commands.values()
            ):
                raise HTTPException(409, "skill operation conflicts with pending bridge work")
            need = self.skill_store.need_context(
                identity, session.game_datetime, body.source_event_ids
            )
            state = {
                "run_id": self.id_factory("skill_run"),
                "identity": identity,
                "epoch": session.epoch,
                "provider": self.provider,
                "canceled": False,
                "start_time": body.game_datetime,
                "need_ids": body.source_event_ids,
                "audit_revision": need["audit_revision"],
                "consent": body.consent,
                "commands": {},
                "source_event_ids": [],
                "cleanup_requested": False,
                "preflight_id": preflight_id,
            }
            session.skill_run = state
            self.skill_environment(identity)
            self.skill_owners[owner] = state["run_id"]
            task = asyncio.create_task(self.run_skill_operation(session, state, body, operation))
            session.skill_task = task
        try:
            return await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            raise HTTPException(409, "skill operation was canceled") from None
        finally:
            if session.skill_task is task:
                session.skill_task = None
            if self.skill_owners.get(owner) == state["run_id"]:
                self.skill_owners.pop(owner, None)

    async def run_skill_operation(self, session, state, body, operation):
        identity = state["identity"]
        if operation == "generate":
            if self.skill_learner is None:
                raise HTTPException(409, "skill learning requires the configured provider gateway")
            request = SkillRequest.model_validate(
                {
                    name: getattr(body, name)
                    for name in (
                        "skill_id",
                        "version",
                        "action_class",
                        "source_event_ids",
                        "revises",
                    )
                }
            )
            result = await self.skill_learner.generate(identity, body.game_datetime, request)
        elif operation == "adopt":
            self.skill_environment(identity)
            self.require_bridge_trial(identity, session.game_datetime, body.trial_id)
            result = self.skill_store.adopt(
                identity,
                session.game_datetime,
                body.trial_id,
                body.source_event_ids,
            )
        else:
            if operation == "trial":
                with self.repo._lock:
                    _, stamp = self.skill_store._scope(identity, body.game_datetime)
                    row = self.skill_store._owned(identity, stamp, body.candidate_id, {"candidate"})
                    compiled = self.skill_store._compiled(identity, stamp, row)
                state["program"] = {
                    "kind": "candidate",
                    "record_id": body.candidate_id,
                    "fingerprint": compiled.fingerprint,
                }
            else:
                selected = self.skill_store.select(identity, session.game_datetime, body.skill_id)
                if selected is None:
                    raise HTTPException(409, "actor has no current adopted skill")
                self.require_bridge_trial(identity, session.game_datetime, selected["parent_id"])
                compiled = selected["compiled"]
                state["program"] = {
                    "kind": "selected",
                    "record_id": selected["record_id"],
                    "skill_id": body.skill_id,
                    "fingerprint": compiled.fingerprint,
                }
            self.validate_bridge_skill(compiled)
            if operation == "trial":
                preflight_id = state["preflight_id"]
                if preflight_id is None:
                    simulated = await self.preflight_skill(session, body.candidate_id)
                    if simulated["result"]["status"] != "completed":
                        return {
                            "run_id": state["run_id"],
                            "operation": operation,
                            "result": simulated,
                            "game_verified": False,
                            "native_cleanup_confirmed": False,
                        }
                    preflight_id = simulated["record_id"]
                state["program"]["preflight_id"] = preflight_id
                self.require_skill_preflight(identity, session.game_datetime, state["program"])
            interpreter = SkillInterpreter(enabled=True, clock=self.clock)
            kwargs = dict(
                environment=self.skill_environment,
                execute=self.execute_skill_operation,
                cancel=self.cancel_skill_operation,
            )
            if operation == "trial":
                result = await self.skill_store.record_trial(
                    body.candidate_id,
                    identity,
                    body.game_datetime,
                    interpreter=interpreter,
                    record_id=self.id_factory("skill_trial"),
                    completion_evidence=lambda: self.skill_completion_evidence(session, state),
                    **kwargs,
                )
            else:
                outcome = await interpreter.run(
                    compiled,
                    identity,
                    library=self.skill_store.library(identity, session.game_datetime),
                    **kwargs,
                )
                evidence = self.skill_completion_evidence(session, state)
                result = {
                    **asdict(outcome),
                    "verification": "bridge_acknowledged" if evidence else "no_bridge_receipt",
                    "source_event_ids": state["source_event_ids"],
                }
        return {
            "run_id": state["run_id"],
            "operation": operation,
            "result": result,
            "game_verified": False,
            "native_cleanup_confirmed": False,
        }

    def require_bridge_trial(self, identity, game_datetime, trial_id):
        with self.repo._lock:
            _, stamp = self.skill_store._scope(identity, game_datetime)
            trial = self.skill_store._owned(identity, stamp, trial_id, {"trial"})
            result = self.skill_store._record(trial)["result"]
            if result.get("verification") != "bridge_acknowledged":
                raise HTTPException(
                    409, "development bridge adoption requires acknowledged trial evidence"
                )

    def skill_completion_evidence(self, session, state):
        self.skill_environment(state["identity"])
        if not state["source_event_ids"]:
            return None
        return {
            "game_datetime": session.game_datetime,
            "source_event_ids": list(state["source_event_ids"]),
            "verification": "bridge_acknowledged",
            "game_verified": False,
        }

    async def execute_skill_operation(self, identity, operation_id, proposal, deadline):
        session = self.sessions[identity.session_id]
        async with session.lock:
            environment = self.skill_environment(identity)
            program = session.skill_run["program"]
            if program["kind"] == "candidate":
                self.require_skill_preflight(identity, session.game_datetime, program)
            decision = self.policy.check(
                proposal,
                environment.actor,
                environment.world,
                environment.capabilities,
                environment.consent,
            )
            if not decision.allowed:
                raise HTTPException(409, decision.reason)
            remaining = deadline - self.clock()
            if remaining <= 0 or proposal.arguments.duration_seconds > remaining:
                raise HTTPException(409, "primitive cannot fit the remaining skill deadline")
            if any(
                c["kind"] == "action.execute"
                and c["actor_id"] == identity.actor_id
                and c["state"] not in SKILL_TERMINAL
                for c in session.commands.values()
            ):
                raise HTTPException(409, "actor already has an active command")
            state = session.skill_run
            if "deadline" not in state:
                state["deadline"] = deadline
                state["expires_at"] = self.utc_now() + timedelta(seconds=remaining)
            if deadline != state["deadline"]:
                raise HTTPException(409, "skill deadline cannot change between operations")
            remaining = min(remaining, (state["expires_at"] - self.utc_now()).total_seconds())
            if remaining <= 0 or proposal.arguments.duration_seconds > remaining:
                raise HTTPException(409, "primitive cannot fit the original skill authorization")
            command = self.command(
                session,
                "action.execute",
                identity.actor_id,
                {
                    "action_id": proposal.action_id,
                    "arguments": proposal.arguments.model_dump(exclude_none=True),
                    "policy_version": POLICY_VERSION,
                    "scene_id": state["run_id"],
                    "consent": environment.consent,
                    "turn_id": operation_id,
                },
                seconds=remaining,
            )
            command["expires_at"] = min(
                datetime.fromisoformat(command["expires_at"]), state["expires_at"]
            ).isoformat()
            command["_skill_origin"] = "skill"
            pending = {
                "operation_id": operation_id,
                "command": command,
                "future": asyncio.get_running_loop().create_future(),
                "identity": identity,
                "deadline": deadline,
                "run": state,
            }
            command["_skill"] = pending
            state["commands"][operation_id] = pending
        return await pending["future"]

    def validate_skill_ack(self, session, command, ack):
        pending = command.get("_skill")
        if pending is None:
            return
        try:
            environment = self.skill_environment(pending["identity"])
        except (HTTPException, ValueError):
            self.cancel_skills(session)
            raise
        if command["runtime_instance_id"] != environment.actor["runtime_instance_id"]:
            raise HTTPException(409, "skill receipt actor instance changed")
        if self.clock() >= pending["deadline"]:
            raise HTTPException(409, "skill receipt arrived after its deadline")
        if ack.status == "started":
            if (
                ack.reason != "task_dispatched"
                or (ack.observed or {}).get("task_dispatched") is not True
            ):
                raise HTTPException(422, "skill start requires task dispatch evidence")
        if ack.status != "completed":
            return
        observed = ack.observed or {}

        def number(name):
            value = observed.get(name)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise HTTPException(422, "skill completion requires finite " + name)
            return value

        distance = number("distance_meters")
        if distance > 100000:
            raise HTTPException(422, "skill distance measurement exceeds receipt bounds")
        action = command["payload"]["action_id"]
        if action == "face_player" and number("heading_error_degrees") > 20:
            raise HTTPException(422, "skill orientation was not observed")
        if action == "wait_here":
            duration = command["payload"]["arguments"]["duration_seconds"]
            displacement = number("displacement_meters")
            if (
                number("elapsed_ms") < duration * 1000
                or displacement > 1.5
                or displacement > distance + 0.0001
            ):
                raise HTTPException(422, "skill wait completion was not observed")

    def validate_skill_ack_publication(self, session, command):
        pending = command.get("_skill")
        if pending is None:
            return
        state = pending["run"]
        identity = pending["identity"]
        try:
            if (
                session.skill_run is not state
                or self.skill_owners.get((identity.profile_id, identity.actor_id))
                != state["run_id"]
                or state["commands"].get(pending["operation_id"]) is not pending
                or session.commands.get(command["id"]) is not command
                or session.skill_task is None
                or session.skill_task.done()
                or session.skill_task.cancelling()
                or pending["future"].done()
                or command["state"] in SKILL_TERMINAL
            ):
                raise HTTPException(409, "skill receipt authorization changed during persistence")
            self.skill_environment(identity)
            if (
                self.clock() >= pending["deadline"]
                or self.utc_now() >= state["expires_at"]
                or self.utc_now() >= datetime.fromisoformat(command["expires_at"])
            ):
                raise HTTPException(409, "skill receipt expired during persistence")
        except (HTTPException, ValueError):
            # The interpreter can time out while the ACK awaits a database write,
            # even when the injected monotonic clock has not advanced. Never
            # restore a revoked command or cancel a successor's worker here.
            self.revoke_skill_commands(session, state)
            raise

    def finish_skill_ack(self, session, command, ack):
        pending = command.get("_skill")
        if pending is None:
            return
        state = pending["run"]
        event_id = command["id"] + ":" + ack.status
        if event_id not in state["source_event_ids"]:
            state["source_event_ids"].append(event_id)
        if ack.status not in SKILL_TERMINAL or pending["future"].done():
            return
        status = {"rejected": "failed", "expired": "failed"}.get(ack.status, ack.status)
        distance = (ack.observed or {}).get("distance_meters")
        if (
            type(distance) not in {int, float}
            or not math.isfinite(distance)
            or not 0 <= distance <= 100000
        ):
            pending["future"].set_exception(
                ValueError("failed primitive has no valid distance receipt")
            )
            return
        pending["future"].set_result(
            PrimitiveReceipt(
                operation_id=pending["operation_id"],
                status=status,
                distance_meters=distance,
            )
        )

    def revoke_skill_commands(self, session, state):
        # No awaits or lock acquisition: lifecycle callers already hold session.lock.
        for pending in state["commands"].values():
            command = pending["command"]
            if command["state"] not in SKILL_TERMINAL:
                command["state"] = "canceled"
                self.record_outcome(session, command, "canceled", "skill authorization withdrawn")
            if not pending["future"].done():
                pending["future"].cancel()
        if state["commands"] and not state["cleanup_requested"] and not session.closed:
            state["cleanup_requested"] = True
            try:
                self.command(session, "scene.cancel", None, {"scene_id": state["run_id"]})
            except HTTPException:
                # Existing command deadlines still revoke adapter authorization.
                session.trace.append({"skill_run": state["run_id"], "cleanup": "queue_full"})
        session.changed.set()

    async def cancel_skill_operation(self, identity, operation_id):
        session = self.sessions.get(identity.session_id)
        state = session.skill_run if session else None
        if state and state["identity"] == identity and operation_id in state["commands"]:
            self.revoke_skill_commands(session, state)

    def cancel_skills(self, session):
        state = session.skill_run
        if state:
            state["canceled"] = True
            self.revoke_skill_commands(session, state)
        if session.skill_task is not None and not session.skill_task.done():
            session.skill_task.cancel()
            if session.skill_task not in session.skill_cleanup:
                session.skill_cleanup.append(session.skill_task)

    async def stop_session_skills(self, session, *, erase=False):
        self.cancel_skills(session)
        for task in session.skill_cleanup:
            if task is not asyncio.current_task():
                await self.thread_join_task(task)
        session.skill_cleanup.clear()
        if session.skill_run:
            identity = session.skill_run["identity"]
            owner = (identity.profile_id, identity.actor_id)
            if self.skill_owners.get(owner) == session.skill_run["run_id"]:
                self.skill_owners.pop(owner, None)
        session.skill_task = None
        if erase:
            session.skill_run = None
