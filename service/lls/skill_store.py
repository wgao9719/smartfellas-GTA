"""Source-backed skill versions and actor-private, timeline-aware repertoires.

Shared code contains only compiler-validated structural JSON. Discovery, trials,
and adoption remain private to one persistent actor. The first retention policy
accepts only completed trials and does not infer native verification from them.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .action_registry import REGISTRY_SHA256
from .protocol import Identifier, Protagonist, StrictModel
from .skills import (
    MAX_EXPANDED_STEPS,
    PrimitiveReceipt,
    SkillIdentity,
    SkillInterpreter,
    SkillResult,
    compile_skill,
    parse_definition,
)
from .storage import _date, _json, _now, _protagonist

PERSISTENT_KINDS = {"curated_persistent", "spawned_persistent", "anchored_service"}
MAX_TRIAL_SOURCES = 16 + MAX_EXPANDED_STEPS * 2


class _Identity(StrictModel):
    profile_id: Identifier
    branch_id: Identifier
    protagonist_id: Protagonist
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    actor_id: Identifier
    runtime_instance_id: Identifier


class _CompletionEvidence(StrictModel):
    game_datetime: datetime
    source_event_ids: list[Identifier] = Field(min_length=1, max_length=MAX_EXPANDED_STEPS * 2)
    verification: Literal["bridge_acknowledged"]
    game_verified: Literal[False] = False


def _stamp(value):
    return _date(value.isoformat() if isinstance(value, datetime) else value)


class SkillStore:
    def __init__(self, repository, *, registry_fingerprint=REGISTRY_SHA256, id_factory=None):
        self.repo = repository
        self.registry_fingerprint = registry_fingerprint
        self.id_factory = id_factory or (lambda prefix: prefix + "_" + uuid4().hex)
        self._active_trials = set()
        self._active_records = set()

    @contextmanager
    def _transaction(self):
        with self.repo._lock:
            if self.repo.connection.in_transaction:
                raise ValueError("skill operation cannot nest inside a write transaction")
            self.repo.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.repo.connection.commit()
            except BaseException:
                self.repo.connection.rollback()
                raise

    def _scope(self, identity, game_datetime):
        if type(identity) is not SkillIdentity:
            raise TypeError("skill storage requires a SkillIdentity")
        identity = SkillIdentity(**_Identity.model_validate(asdict(identity)).model_dump())
        stamp = _stamp(game_datetime)
        actor = self.repo._actor_session(
            identity.profile_id, identity.actor_id, identity.session_id
        )
        if actor["actor_kind"] not in PERSISTENT_KINDS:
            raise ValueError("retained skills require a persistent actor")
        self.repo._branch(identity.profile_id, identity.branch_id)
        return identity, stamp

    def _sources(self, identity, stamp, source_ids, *, limit=16):
        if (
            not isinstance(source_ids, (list, tuple))
            or not 1 <= len(source_ids) <= limit
            or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in source_ids)
        ):
            raise ValueError(f"skill provenance requires 1 to {limit} bounded source identifiers")
        sources = self.repo._sources(
            identity.profile_id,
            identity.branch_id,
            stamp,
            list(source_ids),
            "reported",
            _protagonist(identity.protagonist_id),
        )
        for source in sources:
            payload = source.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("skill source payload must be structured")
            observers = source.get("observer_ids", payload.get("observer_ids", []))
            if not isinstance(observers, list) or identity.actor_id not in observers:
                raise ValueError("actor did not observe or receive the skill source")
            if source["provenance"] not in {
                "directly_observed",
                "heard",
                "reported",
                "system_fact",
            }:
                raise ValueError("unverified skill sources cannot support learning")
            if (
                payload.get("visibility_scope") == "actor"
                and payload.get("scope_id") != identity.actor_id
            ):
                raise ValueError("skill source belongs to another actor")
            forgotten = self.repo.connection.execute(
                "SELECT 1 FROM scrub_audit WHERE profile_id=? AND operation='forget_skill_source' "
                "AND object_id=? LIMIT 1",
                (identity.profile_id, _json([identity.actor_id, source["event_id"]])),
            ).fetchone()
            if forgotten:
                raise ValueError("skill source was explicitly forgotten")
        return sources

    def _visible_rows(self, identity, stamp):
        lineage = self.repo._lineage(identity.profile_id, identity.branch_id, stamp)
        rows = self.repo.connection.execute(
            "SELECT * FROM skill_records WHERE profile_id=? AND actor_id=? AND protagonist_id=? "
            "ORDER BY game_datetime,sequence",
            (identity.profile_id, identity.actor_id, _protagonist(identity.protagonist_id)),
        ).fetchall()
        visible = {}
        for row in rows:
            if row["branch_id"] not in lineage or row["game_datetime"] > lineage[row["branch_id"]]:
                continue
            data = json.loads(row["data"])
            source_ids = data["source_event_ids"]
            links = self.repo.connection.execute(
                "SELECT event_id FROM skill_record_sources WHERE profile_id=? AND record_id=?",
                (identity.profile_id, row["record_id"]),
            ).fetchall()
            if set(source_ids) != {link[0] for link in links}:
                continue
            try:
                self._sources(
                    identity,
                    min(stamp, row["game_datetime"]),
                    source_ids,
                    limit=MAX_TRIAL_SOURCES,
                )
            except ValueError:
                continue
            visible[row["record_id"]] = row
        # A missing or rewound parent cannot confer validation or adoption.
        while True:
            invalid = [
                key
                for key, row in visible.items()
                if row["parent_id"] is not None and row["parent_id"] not in visible
            ]
            if not invalid:
                return visible
            for key in invalid:
                del visible[key]

    def _owned(self, identity, stamp, record_id, kinds=None):
        row = self._visible_rows(identity, stamp).get(record_id)
        if row is None or (kinds is not None and row["kind"] not in kinds):
            raise ValueError("skill record is absent from this actor's visible timeline")
        return row

    def _version(self, profile_id, skill_id, version):
        row = self.repo.connection.execute(
            "SELECT * FROM skill_versions WHERE profile_id=? AND skill_id=? AND version=?",
            (profile_id, skill_id, version),
        ).fetchone()
        if row is None:
            raise ValueError("skill implementation is unavailable")
        return row

    def _library(self, identity, stamp):
        rows = self._visible_rows(identity, stamp)
        latest = {}
        for row in rows.values():
            if row["kind"] in {"adopt", "retire"}:
                latest[(row["skill_id"], row["version"])] = row
        candidates = {
            key: row
            for key, row in rows.items()
            if row["kind"] == "candidate"
            and latest.get((row["skill_id"], row["version"]), {"kind": None})["kind"] != "retire"
        }
        while True:
            invalid = [
                key
                for key, row in candidates.items()
                if any(
                    dep not in candidates for dep in json.loads(row["data"])["dependency_records"]
                )
            ]
            if not invalid:
                break
            for key in invalid:
                del candidates[key]
        library, owners = {}, {}
        for key, row in candidates.items():
            version = self._version(identity.profile_id, row["skill_id"], row["version"])
            label = (row["skill_id"], row["version"])
            library[label] = version["source"]
            owners[label] = key
        return library, owners

    def library(self, identity, game_datetime):
        with self.repo._lock:
            identity, stamp = self._scope(identity, game_datetime)
            return self._library(identity, stamp)[0]

    def _compiled(self, identity, stamp, row):
        version = self._version(identity.profile_id, row["skill_id"], row["version"])
        compiled = compile_skill(
            version["source"],
            self._library(identity, stamp)[0],
            registry_fingerprint=self.registry_fingerprint,
        )
        if (
            compiled.fingerprint != row["fingerprint"]
            or compiled.fingerprint != version["fingerprint"]
            or compiled.registry_fingerprint != version["registry_fingerprint"]
            or _json(compiled.dependencies) != version["dependencies"]
        ):
            raise ValueError("skill or dependencies require revalidation")
        return compiled

    def _record(self, row):
        data = json.loads(row["data"])
        version = self._version(row["profile_id"], row["skill_id"], row["version"])
        return {
            **{key: row[key] for key in row.keys() if key not in {"sequence", "data"}},
            **data,
            "source": version["source"],
            "protagonist_id": row["protagonist_id"].removeprefix("player:"),
        }

    def _insert(
        self,
        identity,
        stamp,
        kind,
        compiled,
        sources,
        *,
        parent_id=None,
        record_id=None,
        metadata=None,
        retired_version=None,
    ):
        if retired_version is None:
            definition = parse_definition(compiled.source)
            skill_id, version, fingerprint = (
                definition.skill_id,
                definition.version,
                compiled.fingerprint,
            )
        else:
            skill_id, version, fingerprint = (
                retired_version[key] for key in ("skill_id", "version", "fingerprint")
            )
        record_id = record_id or self.id_factory("skill_" + kind)
        if not isinstance(record_id, str) or not 1 <= len(record_id) <= 128:
            raise ValueError("invalid skill record identifier")
        data = {"source_event_ids": list(sources), "result": None, **(metadata or {})}
        existing = self.repo.connection.execute(
            "SELECT 1 FROM skill_records WHERE profile_id=? AND record_id=?",
            (identity.profile_id, record_id),
        ).fetchone()
        if existing:
            raise ValueError("skill record identifier was already used")
        count = self.repo.connection.execute(
            "SELECT COUNT(*) FROM skill_records WHERE profile_id=?",
            (identity.profile_id,),
        ).fetchone()[0]
        if count >= 4096:
            raise ValueError("profile skill history capacity reached")
        self.repo._touch(identity.profile_id, identity.branch_id, stamp)
        self.repo.connection.execute(
            "INSERT INTO skill_records(profile_id,record_id,actor_id,protagonist_id,branch_id,"
            "game_datetime,session_id,runtime_generation,runtime_instance_id,kind,skill_id,version,"
            "fingerprint,parent_id,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity.profile_id,
                record_id,
                identity.actor_id,
                _protagonist(identity.protagonist_id),
                identity.branch_id,
                stamp,
                identity.session_id,
                identity.runtime_generation,
                identity.runtime_instance_id,
                kind,
                skill_id,
                version,
                fingerprint,
                parent_id,
                _json(data),
            ),
        )
        self.repo.connection.executemany(
            "INSERT INTO skill_record_sources VALUES(?,?,?)",
            [(identity.profile_id, record_id, source_id) for source_id in sources],
        )
        return self._record(
            self.repo.connection.execute(
                "SELECT * FROM skill_records WHERE profile_id=? AND record_id=?",
                (identity.profile_id, record_id),
            ).fetchone()
        )

    def register_candidate(
        self,
        identity,
        game_datetime,
        source,
        source_event_ids,
        *,
        revises=None,
        record_id=None,
        expected_audit_revision=None,
    ):
        with self._transaction():
            identity, stamp = self._scope(identity, game_datetime)
            if expected_audit_revision is not None:
                revision = self.repo.connection.execute(
                    "SELECT COALESCE(MAX(audit_id),0) FROM scrub_audit WHERE profile_id=?",
                    (identity.profile_id,),
                ).fetchone()[0]
                if type(expected_audit_revision) is not int or revision != expected_audit_revision:
                    raise ValueError("skill source audit changed before candidate persistence")
            self._sources(identity, stamp, source_event_ids)
            library, owners = self._library(identity, stamp)
            compiled = compile_skill(
                source, library, registry_fingerprint=self.registry_fingerprint
            )
            definition = parse_definition(compiled.source)
            if revises is not None:
                previous = self._owned(identity, stamp, revises, {"candidate", "trial"})
                if (
                    previous["skill_id"] != definition.skill_id
                    or previous["version"] >= definition.version
                ):
                    raise ValueError("revision requires a later version of the same skill")
            version = self.repo.connection.execute(
                "SELECT * FROM skill_versions WHERE profile_id=? AND skill_id=? AND version=?",
                (identity.profile_id, definition.skill_id, definition.version),
            ).fetchone()
            if version:
                if (
                    version["source"] != compiled.source
                    or version["fingerprint"] != compiled.fingerprint
                ):
                    raise ValueError("skill ID and version are immutable")
            else:
                count = self.repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_versions WHERE profile_id=?",
                    (identity.profile_id,),
                ).fetchone()[0]
                if count >= 512:
                    raise ValueError("shared skill version capacity reached")
                self.repo.connection.execute(
                    "INSERT INTO skill_versions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        identity.profile_id,
                        definition.skill_id,
                        definition.version,
                        compiled.source,
                        compiled.fingerprint,
                        compiled.registry_fingerprint,
                        _json(compiled.dependencies),
                        _now(),
                    ),
                )
            dependencies = [owners[tuple(json.loads(label))] for label, _ in compiled.dependencies]
            return self._insert(
                identity,
                stamp,
                "candidate",
                compiled,
                source_event_ids,
                parent_id=revises,
                record_id=record_id,
                metadata={"dependency_records": dependencies, "revises": revises},
            )

    @staticmethod
    def _trial_result(result, compiled):
        if type(result) is not SkillResult or result.fingerprint != compiled.fingerprint:
            raise ValueError("trial result did not come from the bound interpreter program")
        actions = sum(json.loads(op)["op"] == "action" for op in compiled.operations)
        if (
            result.status
            not in {"completed", "refused", "failed", "canceled", "interrupted", "rejected"}
            or not isinstance(result.completed_operations, int)
            or not 0 <= result.completed_operations <= actions
            or (result.status == "completed" and result.completed_operations != actions)
            or result.game_verified is not False
            or not math.isfinite(result.distance_meters)
            or result.distance_meters < 0
            or not math.isfinite(result.elapsed_seconds)
            or result.elapsed_seconds < 0
            or len(result.reason) > 160
        ):
            raise ValueError("invalid or unsupported trial verification result")
        return {**asdict(result), "verification": "simulated"}

    def _completion_sources(self, identity, started, evidence, executed, outcome):
        """Validate canonical receipts for operations actually invoked by this interpreter."""
        evidence = _CompletionEvidence.model_validate(evidence)
        terminal = _stamp(evidence.game_datetime)
        if terminal < started:
            raise ValueError("trial terminal time precedes execution")
        source_ids = list(dict.fromkeys(evidence.source_event_ids))
        sources = self._sources(identity, terminal, source_ids, limit=MAX_EXPANDED_STEPS * 2)
        receipts = {}
        for source in sources:
            payload = source["payload"]
            operation_id = payload.get("conversation_turn_id")
            operation = executed.get(operation_id)
            status = source.get("event_type", "").removeprefix("action.")
            command_id, separator, suffix = source["event_id"].rpartition(":")
            branch = self.repo.connection.execute(
                "SELECT branch_id FROM events WHERE profile_id=? AND event_id=?",
                (identity.profile_id, source["event_id"]),
            ).fetchone()[0]
            if (
                operation is None
                or source.get("event_type") != "action." + status
                or status
                not in {"started", "completed", "failed", "canceled", "rejected", "expired"}
                or not separator
                or not command_id
                or suffix != status
                or source["provenance"] != "system_fact"
                or source["protagonist_id"] != _protagonist(identity.protagonist_id)
                or source.get("observer_ids") != [identity.actor_id]
                or branch != identity.branch_id
                or source["game_datetime"] < started
                or payload.get("action_id") != operation["action_id"]
            ):
                raise ValueError("trial bridge source does not match its executed operation")
            receipt = receipts.setdefault(operation_id, {"command_id": command_id, "events": {}})
            if receipt["command_id"] != command_id or status in receipt["events"]:
                raise ValueError("trial operation has conflicting bridge receipts")
            receipt["events"][status] = source["game_datetime"]
        completed = 0
        for operation_id, receipt in receipts.items():
            events = receipt["events"]
            terminal_statuses = set(events) - {"started"}
            if len(terminal_statuses) > 1:
                raise ValueError("trial operation has multiple terminal bridge receipts")
            if terminal_statuses:
                status = next(iter(terminal_statuses))
                if "started" in events and events[status] < events["started"]:
                    raise ValueError("terminal bridge receipt precedes its start")
                if status == "completed":
                    if "started" not in events:
                        raise ValueError("completed bridge receipt requires its start")
                    completed += 1
                actual = executed[operation_id]["status"]
                normalized = "failed" if status in {"rejected", "expired"} else status
                if actual is not None and actual != normalized:
                    raise ValueError("bridge receipt disagrees with interpreter receipt")
        if (
            outcome["completed_operations"] is not None
            and completed < outcome["completed_operations"]
        ) or (
            outcome["status"] == "completed"
            and (completed != outcome["completed_operations"] or set(receipts) != set(executed))
        ):
            raise ValueError("completed trial lacks acknowledged operation completion")
        return terminal, source_ids

    async def record_trial(
        self,
        candidate_id,
        identity,
        game_datetime,
        *,
        interpreter,
        environment,
        execute,
        cancel,
        record_id=None,
        completion_evidence=None,
    ):
        """Execute the actual interpreter and persist only its joined, bound outcome.

        This method never accepts a model-authored result. A cancellation remains
        cancellation after cleanup. Partial counts are unknown when execution raises.

        completion_evidence is a trusted synchronous, read-only runtime callback.
        It runs after interpreter cleanup, inside the final write transaction. It
        returns terminal game_datetime and canonical ACK source_event_ids with
        verification=bridge_acknowledged, or None when no ACK exists. It must
        recheck the live binding and raise to suppress stale persistence.
        """
        if type(interpreter) is not SkillInterpreter:
            raise TypeError("trials require the actual SkillInterpreter")
        if completion_evidence is not None and (
            not callable(completion_evidence) or inspect.iscoroutinefunction(completion_evidence)
        ):
            raise TypeError("completion evidence requires a synchronous runtime callback")
        record_id = record_id or self.id_factory("skill_trial")
        if not isinstance(record_id, str) or not 1 <= len(record_id) <= 128:
            raise ValueError("invalid skill record identifier")
        with self.repo._lock:
            identity, stamp = self._scope(identity, game_datetime)
            if self.repo.connection.execute(
                "SELECT 1 FROM skill_records WHERE profile_id=? AND record_id=?",
                (identity.profile_id, record_id),
            ).fetchone():
                raise ValueError("skill record identifier was already used")
            candidate = self._owned(identity, stamp, candidate_id, {"candidate"})
            compiled = self._compiled(identity, stamp, candidate)
            sources = json.loads(candidate["data"])["source_event_ids"]
            library = self._library(identity, stamp)[0]
            audit_revision = self.repo.connection.execute(
                "SELECT COALESCE(MAX(audit_id),0) FROM scrub_audit WHERE profile_id=?",
                (identity.profile_id,),
            ).fetchone()[0]
        actor_key = (identity.profile_id, identity.actor_id)
        record_key = (identity.profile_id, record_id)
        if actor_key in self._active_trials:
            raise ValueError("actor already has an active skill trial")
        if record_key in self._active_records:
            raise ValueError("skill record identifier is already reserved")
        self._active_trials.add(actor_key)
        self._active_records.add(record_key)
        canceled = False
        executed = {}

        async def execute_bound(current, operation_id, proposal, deadline):
            executed[operation_id] = {"action_id": proposal.action_id, "status": None}
            result = await execute(current, operation_id, proposal, deadline)
            receipt = PrimitiveReceipt.model_validate(result)
            if receipt.operation_id == operation_id:
                executed[operation_id]["status"] = receipt.status
            return result

        try:
            try:
                result = await SkillInterpreter.run(
                    interpreter,
                    compiled,
                    identity,
                    environment=environment,
                    execute=execute_bound,
                    cancel=cancel,
                    library=library,
                    registry_fingerprint=self.registry_fingerprint,
                )
                outcome = self._trial_result(result, compiled)
            except asyncio.CancelledError:
                canceled = True
                outcome = {
                    "status": "canceled",
                    "reason": "interpreter_canceled",
                    "fingerprint": compiled.fingerprint,
                    "completed_operations": None,
                    "distance_meters": None,
                    "elapsed_seconds": None,
                    "game_verified": False,
                    "verification": "simulated",
                }
            except Exception:
                outcome = {
                    "status": "failed",
                    "reason": "interpreter_or_executor_failed",
                    "fingerprint": compiled.fingerprint,
                    "completed_operations": None,
                    "distance_meters": None,
                    "elapsed_seconds": None,
                    "game_verified": False,
                    "verification": "simulated",
                }
            try:
                with self._transaction():
                    # Reset, source deletion, or rewind while awaiting execution cannot
                    # resurrect the candidate when the interpreter eventually returns.
                    terminal_sources = []
                    terminal = stamp
                    if completion_evidence is not None:
                        evidence = completion_evidence()
                        if inspect.isawaitable(evidence):
                            if inspect.iscoroutine(evidence):
                                evidence.close()
                            raise TypeError("completion evidence callback must be synchronous")
                        if evidence is not None:
                            if environment(identity).identity != identity:
                                raise ValueError("trial actor binding changed before persistence")
                            terminal, terminal_sources = self._completion_sources(
                                identity, stamp, evidence, executed, outcome
                            )
                            outcome["verification"] = "bridge_acknowledged"
                        elif outcome["status"] == "completed" and executed:
                            raise ValueError(
                                "completed bridge trial requires acknowledgment evidence"
                            )
                    identity, terminal = self._scope(identity, terminal)
                    candidate = self._owned(identity, terminal, candidate_id, {"candidate"})
                    sources = list(dict.fromkeys([*sources, *terminal_sources]))
                    self._sources(identity, terminal, sources, limit=MAX_TRIAL_SOURCES)
                    if self._compiled(identity, terminal, candidate) != compiled:
                        raise ValueError("trial program changed while execution was pending")
                    revision = self.repo.connection.execute(
                        "SELECT COALESCE(MAX(audit_id),0) FROM scrub_audit WHERE profile_id=?",
                        (identity.profile_id,),
                    ).fetchone()[0]
                    if revision != audit_revision:
                        raise ValueError("skill source audit changed during trial execution")
                    saved = self._insert(
                        identity,
                        terminal,
                        "trial",
                        compiled,
                        sources,
                        parent_id=candidate_id,
                        record_id=record_id,
                        metadata={"result": outcome, "terminal_source_event_ids": terminal_sources},
                    )
            except Exception:
                if canceled:
                    raise asyncio.CancelledError from None
                raise
            if canceled:
                raise asyncio.CancelledError
            return saved
        finally:
            self._active_trials.discard(actor_key)
            self._active_records.discard(record_key)

    def adopt(self, identity, game_datetime, trial_id, source_event_ids, *, record_id=None):
        with self._transaction():
            identity, stamp = self._scope(identity, game_datetime)
            self._sources(identity, stamp, source_event_ids)
            trial = self._owned(identity, stamp, trial_id, {"trial"})
            outcome = json.loads(trial["data"])["result"]
            if outcome["status"] != "completed":
                raise ValueError("initial adoption requires a completed own trial")
            compiled = self._compiled(identity, stamp, trial)
            self._trial_result(
                SkillResult(**{k: v for k, v in outcome.items() if k != "verification"}), compiled
            )
            return self._insert(
                identity,
                stamp,
                "adopt",
                compiled,
                source_event_ids,
                parent_id=trial_id,
                record_id=record_id,
            )

    def select(self, identity, game_datetime, skill_id=None, *, action_class=None):
        """Select a visible retained revision. Selection does not grant permission to run."""
        with self.repo._lock:
            identity, stamp = self._scope(identity, game_datetime)
            latest = {}
            for row in self._visible_rows(identity, stamp).values():
                if row["kind"] in {"adopt", "retire"}:
                    latest[row["skill_id"]] = row
            for row in sorted(
                latest.values(), key=lambda r: (r["game_datetime"], r["sequence"]), reverse=True
            ):
                if row["kind"] != "adopt" or (skill_id is not None and row["skill_id"] != skill_id):
                    continue
                try:
                    compiled = self._compiled(identity, stamp, row)
                except ValueError:
                    continue
                if (
                    action_class is not None
                    and parse_definition(compiled.source).action_class != action_class
                ):
                    continue
                return {**self._record(row), "compiled": compiled}
            return None

    def retire(self, identity, game_datetime, skill_id, source_event_ids, *, record_id=None):
        with self._transaction():
            identity, stamp = self._scope(identity, game_datetime)
            self._sources(identity, stamp, source_event_ids)
            records = [
                row
                for row in self._visible_rows(identity, stamp).values()
                if row["kind"] in {"adopt", "retire"} and row["skill_id"] == skill_id
            ]
            current = records[-1] if records else None
            if current is None or current["kind"] != "adopt":
                raise ValueError("actor has no available adopted skill to retire")
            return self._insert(
                identity,
                stamp,
                "retire",
                None,
                source_event_ids,
                parent_id=current["record_id"],
                record_id=record_id,
                retired_version=current,
            )

    def history(self, identity, game_datetime, *, skill_id=None, limit=32):
        if not isinstance(limit, int) or not 1 <= limit <= 128:
            raise ValueError("invalid skill history limit")
        with self.repo._lock:
            identity, stamp = self._scope(identity, game_datetime)
            return [
                self._record(row)
                for row in reversed(list(self._visible_rows(identity, stamp).values()))
                if skill_id is None or row["skill_id"] == skill_id
            ][:limit]

    def need_context(self, identity, game_datetime, source_event_ids):
        """Build a private bounded prompt excerpt, never a shared code annotation."""
        with self.repo._lock:
            identity, stamp = self._scope(identity, game_datetime)
            sources = self._sources(identity, stamp, source_event_ids)
            excerpts = []
            for source in sources:
                text = source.get("payload", {}).get("text", "")
                excerpt = {
                    **{
                        key: source[key]
                        for key in (
                            "event_id",
                            "event_type",
                            "game_datetime",
                            "provenance",
                            "confidence",
                        )
                    },
                    "text": text[:500] if isinstance(text, str) else "",
                }
                payload = source.get("payload", {})
                if source["event_type"].startswith("action.") and payload.get("action_id") in {
                    "face_player",
                    "wait_here",
                }:
                    observed = payload.get("observed")
                    metrics = {}
                    for key in (
                        "distance_meters",
                        "heading_error_degrees",
                        "elapsed_ms",
                        "displacement_meters",
                    ):
                        value = observed.get(key) if isinstance(observed, dict) else None
                        if type(value) in {int, float} and math.isfinite(value) and value >= 0:
                            metrics[key] = value
                    reason = payload.get("reason", "")
                    excerpt["action_evidence"] = {
                        "action_id": payload["action_id"],
                        "status": source["event_type"].removeprefix("action."),
                        "reason": reason[:120] if isinstance(reason, str) else "",
                        "observed": metrics,
                    }
                excerpts.append(excerpt)
            trials = [
                {
                    key: record[key]
                    for key in (
                        "record_id",
                        "skill_id",
                        "version",
                        "fingerprint",
                        "game_datetime",
                        "source_event_ids",
                        "result",
                    )
                }
                for record in self.history(identity, stamp, limit=32)
                if record["kind"] == "trial"
            ][:8]
            return {
                "actor_id": identity.actor_id,
                "protagonist_id": identity.protagonist_id,
                "game_datetime": stamp,
                "source_events": excerpts,
                "prior_trials": trials,
                "audit_revision": self.repo.connection.execute(
                    "SELECT COALESCE(MAX(audit_id),0) FROM scrub_audit WHERE profile_id=?",
                    (identity.profile_id,),
                ).fetchone()[0],
            }
