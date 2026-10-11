"""Background consolidation of persistent actors' firsthand lines into episode memories.

Lines stay canonical. A summary cites every event its lines cite, so erasing any line
or event also erases the summary. Retrieval hides lines whose summary it selected.
The model only paraphrases. Importance stays within a bounded shift of the lines' score.
Pending lines live in storage, so work interrupted by shutdown resumes in a later session.
"""

import asyncio
import json
import math

from pydantic import Field, model_validator

from .outbound import raise_if_cancelling
from .protocol import StrictModel
from .providers import decode_envelope
from .storage import MAX_SUMMARY_LINES

MAX_SUMMARY_RESPONSE_BYTES = 4096
FOREGROUND_COMMANDS = {"action.execute", "dialogue.chunk"}
TERMINAL = {"completed", "rejected", "expired", "failed", "canceled"}


class MemorySummary(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    importance: float = Field(ge=0, le=1)


def parse_summary(content):
    """Reject duplicate keys, extra fields, and out-of-range values."""
    return MemorySummary.model_validate(decode_envelope(content))


class MemoryConsolidationConfig(StrictModel):
    enabled: bool = False
    min_batch: int = Field(default=3, ge=2, le=MAX_SUMMARY_LINES)
    max_batch: int = Field(default=12, ge=2, le=MAX_SUMMARY_LINES)
    # Game time. Lines further apart than this belong to separate episodes.
    episode_gap_minutes: float = Field(default=30, ge=1, le=1440)
    lookback_days: float = Field(default=14, ge=1, le=365)
    poll_seconds: float = Field(default=15, ge=1, le=600)
    unavailable_backoff_seconds: float = Field(default=120, ge=1, le=3600)
    timeout_seconds: float = Field(default=60, ge=5, le=300)
    max_importance_shift: float = Field(default=0.25, ge=0, le=0.5)

    @model_validator(mode="after")
    def ordered_batches(self):
        if self.min_batch > self.max_batch:
            raise ValueError("min_batch cannot exceed max_batch")
        return self


class ConsolidationSuperseded(ValueError):
    """The session closed or its epoch changed while a summary was pending."""


class MemoryConsolidator:
    def __init__(self, runtime, config=None):
        self.runtime = runtime
        self.config = MemoryConsolidationConfig.model_validate(
            (config or MemoryConsolidationConfig()).model_dump()
        )
        self.workers = {}
        self.states = {}
        self.next_poll = {}

    @staticmethod
    def _idle(session):
        return not (
            session.closed
            or (session.task and not session.task.done())
            or session.speech_state != "idle"
            or (session.social_task and not session.social_task.done())
            or (session.skill_task and not session.skill_task.done())
            or any(
                command["kind"] in FOREGROUND_COMMANDS and command["state"] not in TERMINAL
                for command in session.commands.values()
            )
        )

    def tick(self):
        """Start at most one summary per idle session. Foreground work always goes first."""
        if not self.config.enabled:
            return
        now = self.runtime.clock()
        for session in list(self.runtime.sessions.values()):
            worker = self.workers.get(session.id)
            if (worker is not None and not worker.done()) or not session.game_datetime:
                continue
            if now < self.next_poll.get(session.id, -math.inf) or not self._idle(session):
                continue
            self.next_poll[session.id] = now + self.config.poll_seconds
            task = asyncio.create_task(self._run(session, session.epoch))
            self.workers[session.id] = task
            task.add_done_callback(
                lambda finished: None if finished.cancelled() else finished.exception()
            )

    def _current(self, session, epoch):
        raise_if_cancelling()
        if session.closed or session.epoch != epoch:
            raise ConsolidationSuperseded("summary session is no longer current")

    async def _run(self, session, epoch):
        status, actor_id = "failed", None
        self.states[session.id] = {"status": "running", "actor_id": None}
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                status, actor_id = await self._cycle(session, epoch)
        except asyncio.CancelledError:
            status = "canceled"
            raise
        except TimeoutError:
            status = "timed_out"
        except ConsolidationSuperseded:
            status = "canceled"
        except ValueError:
            # Rejected or changed lines stay unsummarized. Never record their text.
            status = "rejected"
        finally:
            if status in {"unavailable", "failed", "timed_out"}:
                self.next_poll[session.id] = (
                    self.runtime.clock() + self.config.unavailable_backoff_seconds
                )
            self.states[session.id] = {"status": status, "actor_id": actor_id}

    async def _cycle(self, session, epoch):
        runtime, config = self.runtime, self.config
        profile_id, branch_id = session.request.profile_id, session.branch_id
        protagonist_id = session.request.protagonist_id
        self._current(session, epoch)
        batch = await runtime.db(
            "consolidation_batch",
            profile_id,
            branch_id,
            protagonist_id,
            session.game_datetime,
            min_batch=config.min_batch,
            max_batch=config.max_batch,
            episode_gap_minutes=config.episode_gap_minutes,
            lookback_days=config.lookback_days,
            # Keep the model context within its budget alongside the character fields.
            max_text_bytes=runtime.settings.context_byte_budget - 2048,
        )
        if batch is None:
            return "idle", None
        actor_id, lines = batch["actor_id"], batch["memories"]
        actor = await runtime.db("get_actor", profile_id, actor_id)
        if actor is None:
            return "rejected", actor_id
        base = max(line["importance"] for line in lines)
        biography = actor.get("persona", {}).get("biography", "")
        context = {
            "character": {
                "display_name": actor.get("display_name", actor_id),
                "biography": biography[:1000] if isinstance(biography, str) else "",
            },
            "protagonist_id": protagonist_id,
            "base_importance": base,
            "memories": [
                {
                    "index": index,
                    "game_datetime": line["game_datetime"],
                    "provenance": line["provenance"],
                    "text": line["text"],
                }
                for index, line in enumerate(lines, start=1)
            ],
        }
        self._current(session, epoch)
        reply = await runtime.provider.memory_summary(context)
        self._current(session, epoch)
        if reply is None:
            return "unavailable", actor_id
        summary = parse_summary(json.dumps(reply))
        shift = config.max_importance_shift
        importance = min(1.0, max(0.0, base - shift, min(base + shift, summary.importance)))
        await runtime.db(
            "add_summary",
            profile_id,
            branch_id,
            actor_id,
            [line["memory_id"] for line in lines],
            summary.text,
            importance,
            expected_texts=[line["text"] for line in lines],
        )
        return "summarized", actor_id

    def cancel(self, session):
        task = self.workers.get(session.id)
        if task is not None and not task.done():
            task.cancel()

    async def join(self, session, *, erase=False):
        self.cancel(session)
        task = self.workers.pop(session.id, None)
        if task is not None and task is not asyncio.current_task():
            await self.runtime.thread_join_task(task)
        if erase:
            self.states.pop(session.id, None)
            self.next_poll.pop(session.id, None)

    def forget(self, session_id):
        self.workers.pop(session_id, None)
        self.states.pop(session_id, None)
        self.next_poll.pop(session_id, None)

    def diagnostics(self, session_id):
        task = self.workers.get(session_id)
        return {
            "enabled": self.config.enabled,
            **self.states.get(session_id, {"status": "idle", "actor_id": None}),
            "worker_active": task is not None and not task.done(),
        }
