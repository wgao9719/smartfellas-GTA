"""Isolated replay of sanitized bridge events and recorded model responses.

Replay uses the real service runtime with a virtual clock and a scripted model.
It never loads environment configuration, calls a model, or touches live data.
The sanitized flag is the author's assertion, not an automatic privacy scan.
"""

import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import Field, model_validator

from .app import create_app
from .protocol import ActionProposal, Event, SessionOpen, StrictModel
from .settings import Settings

MAX_BUNDLE_BYTES = 1024 * 1024
TURN_TIMEOUT_SECONDS = 5


class RecordedTurn(StrictModel):
    chunks: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=20
    )
    action: ActionProposal | None = None


class EventStep(StrictModel):
    type: Literal["event"]
    event: Event


class WaitStep(StrictModel):
    type: Literal["wait_turn"]


class AdvanceStep(StrictModel):
    type: Literal["advance"]
    seconds: float = Field(ge=0, le=600)


class Delivery(StrictModel):
    delivery: Literal["simulated"] = "simulated"
    text: str = Field(default="", max_length=4000)


class AckStep(StrictModel):
    type: Literal["ack"]
    command_seq: int = Field(ge=1, le=2048)
    status: Literal["started", "completed", "rejected", "expired", "failed", "canceled"]
    reason: str = Field(default="", max_length=500)
    observed: Delivery | None = None


class CloseStep(StrictModel):
    type: Literal["close"]


ReplayStep = EventStep | WaitStep | AdvanceStep | AckStep | CloseStep


class ReplayBundle(StrictModel):
    schema_version: Literal[1] = 1
    sanitized: Literal[True]
    # This clock controls service authorization expiry, not actual asyncio waits.
    start_utc: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session: SessionOpen
    turns: list[RecordedTurn] = Field(default_factory=list, max_length=64)
    steps: list[ReplayStep] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def isolated_inputs(self):
        if self.start_utc.tzinfo is None or self.start_utc.utcoffset().total_seconds() != 0:
            raise ValueError("replay clock requires UTC")
        if self.session.parent_branch_id is not None:
            raise ValueError("replay opens a fresh profile without external branches")
        if self.session.manifest.service_host_mode != "local":
            raise ValueError("replay requires local service mode")
        try:
            self.start_utc + timedelta(
                seconds=120 + sum(s.seconds for s in self.steps if isinstance(s, AdvanceStep))
            )
        except OverflowError as exc:
            raise ValueError("replay clock exceeds the datetime range") from exc
        for index, step in enumerate(self.steps):
            if isinstance(step, EventStep) and step.event.kind == "player.utterance_text":
                if index + 1 == len(self.steps) or not isinstance(self.steps[index + 1], WaitStep):
                    raise ValueError("recorded turns require an immediate wait_turn step")
        return self


def load_bundle(path: str | Path) -> ReplayBundle:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("replay bundle exceeds 1 MiB")

    def unique_keys(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("duplicate JSON keys are not allowed")
            value[name] = item
        return value

    return ReplayBundle.model_validate(json.loads(raw, object_pairs_hook=unique_keys))


class ReplayClock:
    def __init__(self, start):
        self.start = start
        # A nonzero origin also exercises runtime cooldowns correctly.
        self.elapsed = 1000.0
        self.serial = 0

    def monotonic(self):
        return self.elapsed

    def utc_now(self):
        return self.start + timedelta(seconds=self.elapsed - 1000)

    def key(self, prefix):
        self.serial += 1
        return f"{prefix}_replay_{self.serial:04}"


class RecordedProvider:
    name = "recorded_replay"

    def __init__(self, turns):
        self.turns = turns
        self.index = 0
        self.current = None
        self.missing_responses = 0

    async def dialogue(self, context):
        if self.index >= len(self.turns):
            self.missing_responses += 1
            raise ValueError("replay has no recorded response for this turn")
        self.current = self.turns[self.index]
        self.index += 1
        for chunk in self.current.chunks:
            yield chunk
            await asyncio.sleep(0)

    async def action(self, context, utterance, eligible):
        # The real runtime policy decides whether this recorded proposal is allowed.
        return self.current.action if self.current else None


def command_summary(command):
    payload = command["payload"]
    result = {
        "seq": command["seq"],
        "kind": command["kind"],
        "actor_id": command["actor_id"],
        "state": command["state"],
    }
    for field in ("action_id", "chunk_index", "final", "state"):
        if field in payload:
            result["payload_" + field] = payload[field]
    if "text" in payload:
        result["text_sha256"] = hashlib.sha256(payload["text"].encode()).hexdigest()
        result["text_characters"] = len(payload["text"])
    return result


async def run_replay(bundle: ReplayBundle) -> dict:
    """Return deterministic metadata, never raw dialogue or session credentials."""
    if not isinstance(bundle, ReplayBundle):
        raise TypeError("replay requires a validated ReplayBundle")
    clock = ReplayClock(bundle.start_utc)
    provider = RecordedProvider(bundle.turns)
    with tempfile.TemporaryDirectory(prefix="lls-replay-") as folder:
        app = create_app(
            Settings(database_path=str(Path(folder) / "replay.sqlite3"), provider="disabled"),
            provider=provider,
            runtime_options={
                "clock": clock.monotonic,
                "utc_now": clock.utc_now,
                "id_factory": clock.key,
            },
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
                base_url="http://127.0.0.1:8765",
            ) as client:
                opened = await client.post(
                    "/v1/bridge/sessions", json=bundle.session.model_dump(mode="json")
                )
                opened.raise_for_status()
                sid = opened.json()["session_id"]
                client.headers["Authorization"] = "Bearer " + opened.json()["token"]
                runtime = app.state.runtime
                session = runtime.sessions[sid]
                steps = []
                # Retain references so cleanup state changes remain reviewable.
                commands = {}
                action_outcomes = {}
                timed_out_turns = 0
                for index, step in enumerate(bundle.steps):
                    response = None
                    result = {"index": index, "type": step.type}
                    if isinstance(step, EventStep):
                        event = step.event.model_dump(mode="json")
                        event["session_id"] = sid
                        # Event timestamps belong to the recorded virtual clock.
                        response = await client.post(
                            "/v1/bridge/events:batch",
                            json={"session_id": sid, "events": [event]},
                        )
                    elif isinstance(step, WaitStep):
                        result["status"] = "settled"
                        if session.task:
                            _, pending = await asyncio.wait(
                                {session.task}, timeout=TURN_TIMEOUT_SECONDS
                            )
                            if pending:
                                timed_out_turns += 1
                                result["status"] = "timed_out"
                                runtime.cancel(session)
                                await asyncio.gather(session.task, return_exceptions=True)
                    elif isinstance(step, AdvanceStep):
                        clock.elapsed += step.seconds
                        runtime.expire_commands(session)
                        for command in session.commands.values():
                            commands[command["seq"]] = command
                        await runtime.expire_sessions()
                        result["status"] = "advanced"
                    elif isinstance(step, AckStep):
                        if step.observed is not None:
                            result["submitted_text_sha256"] = hashlib.sha256(
                                step.observed.text.encode()
                            ).hexdigest()
                            result["submitted_text_characters"] = len(step.observed.text)
                        command = next(
                            (c for c in session.commands.values() if c["seq"] == step.command_seq),
                            None,
                        )
                        if command is None:
                            result.update(status="unknown_command", http_status=404)
                        else:
                            response = await client.post(
                                f"/v1/bridge/commands/{command['id']}/ack",
                                json={
                                    "session_id": sid,
                                    "runtime_generation": bundle.session.runtime_generation,
                                    "status": step.status,
                                    "reason": step.reason,
                                    "observed": (
                                        step.observed.model_dump() if step.observed else None
                                    ),
                                },
                            )
                    elif isinstance(step, CloseStep):
                        response = await client.post(
                            f"/v1/bridge/sessions/{sid}/close",
                            json={"runtime_generation": bundle.session.runtime_generation},
                        )
                    if response is not None:
                        result["http_status"] = response.status_code
                        if response.is_success:
                            body = response.json()
                            result["status"] = body.get("status", "accepted")
                            if "duplicate" in body:
                                result["duplicate"] = body["duplicate"]
                            if "results" in body:
                                result["duplicates"] = [r["duplicate"] for r in body["results"]]
                        else:
                            result["status"] = "rejected"
                    for command in session.commands.values():
                        commands[command["seq"]] = command
                    # Session close clears transient outcome ledgers. Retained
                    # commands still show their actual terminal state changes.
                    for command in commands.values():
                        state = command["state"]
                        if command["kind"] == "action.execute" and state != "queued":
                            action_outcomes.setdefault(
                                (command["seq"], state),
                                {
                                    "actor_id": command["actor_id"],
                                    "action_id": command["payload"]["action_id"],
                                    "status": state,
                                    "source": "command_state",
                                },
                            )
                    steps.append(result)
                # A pending model task means the trace lacks its explicit wait/cancel.
                unsettled = bool(session.task and not session.task.done())
                return {
                    "mode": "isolated_replay",
                    "game_verified": False,
                    "cloud_calls": 0,
                    "native_calls": 0,
                    "recorded_turns_used": provider.index,
                    "missing_responses": provider.missing_responses,
                    "timed_out_turns": timed_out_turns,
                    "unsettled_turn": unsettled,
                    "session_closed": session.closed,
                    "steps": steps,
                    "commands": [command_summary(commands[n]) for n in sorted(commands)],
                    "action_outcomes": list(action_outcomes.values()),
                    "policy_decisions": [row["policy"] for row in session.trace if "policy" in row],
                }
