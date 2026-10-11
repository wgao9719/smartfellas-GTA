"""Pinned service authorization outcomes, separate from adapter observations."""

import json
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class ServiceOutcome:
    profile_id: str
    branch_id: str
    actor_id: str
    protagonist_id: str
    session_id: str
    command_id: str
    event_json: str
    memory_text: str

    @property
    def event(self):
        return json.loads(self.event_json)


class OutcomeRuntime:
    def queue_service_outcome(self, session, command, status, reason):
        """Pin once before any await. Pending commands cannot leave the bounded queue."""
        existing = command.get("_service_outcome")
        if existing is not None:
            return existing
        if status not in {"expired", "canceled"}:
            raise ValueError("service outcomes only revoke action authorization")
        if len(session.outcome_pending) >= self.settings.max_commands:
            raise HTTPException(503, "action outcome queue is full")
        event = {
            "event_id": command["id"] + ":service:" + status,
            "game_datetime": session.game_datetime,
            "protagonist_id": session.request.protagonist_id,
            "event_type": "action." + status,
            "payload": {
                "action_id": command["payload"]["action_id"],
                "conversation_turn_id": command["payload"]["turn_id"],
                "command_id": command["id"],
                "status": status,
                "reason": reason[:500],
                "authority": "service",
                "native_completion_confirmed": False,
                "native_withdrawal_confirmed": False,
            },
            "provenance": "system_fact",
            "confidence": 1,
            "observer_ids": [command["actor_id"]],
        }
        outcome = ServiceOutcome(
            session.request.profile_id,
            session.branch_id,
            command["actor_id"],
            session.request.protagonist_id,
            session.id,
            command["id"],
            json.dumps(event, sort_keys=True),
            f"The service marked authorization for my {command['payload']['action_id']} "
            f"request as {status}. Native completion and withdrawal remain unconfirmed.",
        )
        command["_service_outcome"] = outcome
        session.outcome_pending[command["id"]] = outcome
        return outcome

    async def flush_outcomes(self, session):
        """Join writes before close/erasure. Failed projections retain their pinned input."""
        async with session.outcome_lock:
            # Administrative edits and erasure both flush first. Failed writes
            # prevent the mutation. Successful writes finish before it can delete
            # projections, and command markers prevent retries from restoring them.
            while session.outcome_pending:
                command_id, outcome = next(iter(session.outcome_pending.items()))
                event = outcome.event
                try:
                    await self.db(
                        "record_event", outcome.profile_id, outcome.branch_id, event
                    )
                    await self.db(
                        "add_memory",
                        outcome.profile_id,
                        outcome.branch_id,
                        outcome.actor_id,
                        outcome.protagonist_id,
                        event["game_datetime"],
                        outcome.memory_text,
                        [event["event_id"]],
                        "system_fact",
                        session_id=outcome.session_id,
                        memory_id=event["event_id"] + ":memory",
                    )
                except Exception as exc:
                    session.trace.append(
                        {"service_outcome": event["event_id"], "persistence": "failed"}
                    )
                    raise HTTPException(503, "action outcome persistence unavailable") from exc
                session.outcome_pending.pop(command_id)
