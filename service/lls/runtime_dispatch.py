"""Reauthorize queued physical actions immediately before bridge delivery."""

from fastapi import HTTPException
from pydantic import ValidationError

from .actions import POLICY_VERSION
from .protocol import ActionCommand, ActionProposal
from .roles import ROLE_POLICIES, ownership_from_facts, revalidate

TERMINAL = {"completed", "rejected", "expired", "failed", "canceled"}


class DispatchRuntime:
    def dispatch_refusal(self, session, command):
        if (
            session.closed
            or self.sessions.get(session.id) is not session
            or command["session_id"] != session.id
            or command["runtime_generation"] != session.request.runtime_generation
            or command.get("_epoch") != session.epoch
            or command.get("_branch_id") != session.branch_id
            or command.get("_protagonist_id") != session.request.protagonist_id
            or session.world.get("protagonist_id") != session.request.protagonist_id
        ):
            return "stale_action_identity"
        try:
            payload = ActionCommand.model_validate(command["payload"])
            proposal = ActionProposal(action_id=payload.action_id, arguments=payload.arguments)
        except ValidationError:
            return "invalid_action_proposal"
        if payload.policy_version != POLICY_VERSION:
            return "stale_action_policy"
        actor = session.actors.get(command["actor_id"])
        if not actor or actor["runtime_instance_id"] != command["runtime_instance_id"]:
            return "actor_binding_changed"
        # Read current authored permissions as well as the latest adapter sample.
        stored = self.repo.get_actor(
            session.request.profile_id, command["actor_id"], session_id=session.id
        )
        if not stored or stored.get("control_policy", "ambient") != "ambient":
            return "actor_policy_changed"
        role, _ = revalidate(actor)
        if ROLE_POLICIES[role] != "ambient":
            return "protected_actor_role"
        if ownership_from_facts(actor.get("ped_facts"))[0] not in {"ambient", "mod_owned"}:
            return "protected_actor_ownership"
        capabilities = [
            action for action in session.request.capabilities
            if action in actor.get("allowed_actions", [])
            and action in stored.get("allowed_actions", [])
        ]
        consent = payload.consent
        pending = command.get("_skill")
        if pending is not None:
            try:
                environment = self.skill_environment(pending["identity"])
            except (HTTPException, ValueError):
                return "skill_authorization_changed"
            if self.clock() >= pending["deadline"]:
                return "skill_authorization_expired"
            capabilities = [a for a in capabilities if a in environment.capabilities]
            consent = consent and environment.consent
        decision = self.policy.check(proposal, actor, session.world, capabilities, consent)
        return None if decision.allowed else decision.reason

    def reauthorize_commands(self, session):
        # Called under session.lock without awaits before returning delivery output.
        for command in list(session.commands.values()):
            if command["kind"] != "action.execute" or command["state"] in TERMINAL:
                continue
            reason = self.dispatch_refusal(session, command)
            if reason is None:
                continue
            command["state"] = "canceled"
            command["reason"] = reason
            self.record_outcome(session, command, "canceled", reason)
            session.trace.append({"command_id": command["id"], "dispatch_policy": reason})
            pending = command.get("_skill")
            if pending is not None:
                pending["run"]["canceled"] = True
                self.revoke_skill_commands(session, pending["run"])
            session.changed.set()
