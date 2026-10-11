"""Profile-isolated, branch-aware SQLite storage.

Events are the canonical input. Memories, knowledge and relationship changes are
separate sourced projections. There is deliberately no untracked text cache.
Game dates must include a full calendar date, not just an hour or game tick.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import salience

PROVENANCES = {"directly_observed", "heard", "reported", "system_fact", "unverified"}
# Summaries combine only firsthand lines, weakest first. Hearsay keeps its exact line.
FIRSTHAND = ("heard", "directly_observed", "system_fact")
MAX_SUMMARY_LINES = 32
ACTOR_KINDS = {
    "curated_persistent",
    "spawned_persistent",
    "ambient_ephemeral",
    "anchored_service",
    "protagonist",
}
PROTAGONISTS = {
    "michael",
    "franklin",
    "trevor",
    "player:michael",
    "player:franklin",
    "player:trevor",
}
RELATIONSHIP_FIELDS = ("familiarity", "trust", "affinity", "fear", "respect")
QUERY_STOP_WORDS = set(
    (
        "a an and are as at be been by can did do does for from had has have how i in is it "
        "me my of on or our said say that the their them there they this to us was we were "
        "what when where which who why will with would you your recall remember about also right"
    ).split()
)
# These small changes affect conversation only, never native GTA faction state.
RELATIONSHIP_RULES = {
    "conversation_completed": {"familiarity": 0.02},
    "courtesy_observed": {"trust": 0.01, "affinity": 0.02, "respect": 0.01},
    "boundary_respected": {"trust": 0.02, "affinity": 0.01},
    "conversation_interrupted": {"affinity": -0.01},
    "threat_observed": {"trust": -0.03, "fear": 0.05, "affinity": -0.02},
    # Declining an invitation is an ordinary boundary, not an offense.
    "invitation_declined": {},
}
RELATIONSHIP_POLICY_VERSION = "1"
# Decay changes a conversational score, not the underlying sourced event.
# Important observations retain their event and provenance until explicit deletion.
RELATIONSHIP_HALF_LIFE_DAYS = {"conversation_interrupted": {"affinity": 1.0}}


def _relationship_baseline(actor: dict) -> dict[str, float]:
    initial = actor.get(
        "starting_relationship", actor.get("persona", {}).get("starting_relationship", {})
    )
    if not isinstance(initial, dict) or not set(initial) <= set(RELATIONSHIP_FIELDS):
        raise ValueError(
            "starting_relationship must contain only conversational relationship fields"
        )
    values = {}
    for field in RELATIONSHIP_FIELDS:
        value = initial.get(field, 0)
        minimum = 0 if field in {"familiarity", "fear"} else -1
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= 1
        ):
            raise ValueError(f"starting relationship {field} must be between {minimum} and 1")
        values[field] = float(value)
    return values


class TimelineAmbiguityError(ValueError):
    """A game date matches more than one saved history; request a branch choice."""

    def __init__(self, candidates: list[str]):
        self.candidates = candidates
        super().__init__("save timeline is ambiguous; select an explicit parent_branch_id")


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _date(value: str) -> str:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
        raise ValueError("game_datetime requires a full ISO calendar date and time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        stamp.replace(tzinfo=stamp.tzinfo or timezone.utc)
        .astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unit(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def _protagonist(value: str | None) -> str | None:
    if value is None:
        return None
    # Names are accepted for convenient fixture/adapter integration; IDs remain
    # distinct. No protagonist is a wildcard for the other protagonists.
    if value in PROTAGONISTS:
        return "player:" + value.removeprefix("player:")
    raise ValueError("unknown protagonist")


class Repository:
    """One connection guarded across FastAPI worker threads, with WAL on disk."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise RuntimeError("database schema is newer than this service")
        if version == 4:
            return
        if version == 3:
            # Social windows index only decision- and hearing-shaped memories, so action
            # outcomes and reports never lengthen a walk. Erasure markers and source events
            # gain lookups. The source index leads with event_id: on default statistics a
            # profile-first index wins profile+memory_id joins and scans the whole profile.
            # Derivations hold only identifiers: this actor's memories citing the derived
            # event repeat or answer this actor's memories citing the source event.
            self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE INDEX IF NOT EXISTS scrub_lookup ON scrub_audit(profile_id,operation,object_id);
            CREATE INDEX IF NOT EXISTS memory_social_window
              ON memories(profile_id,actor_id,protagonist_id,provenance,game_datetime)
              WHERE provenance='system_fact' AND substr(text,1,11)='I chose to '
              OR provenance='reported' AND instr(text,' told me: ')>1;
            CREATE INDEX IF NOT EXISTS memory_source_event ON memory_sources(event_id,profile_id);
            CREATE TABLE IF NOT EXISTS memory_derivations (
              profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
              actor_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
              derived_event_id TEXT NOT NULL,
              PRIMARY KEY(profile_id,actor_id,source_event_id,derived_event_id)
            );
            -- Recover what earlier speaker events prove: the speaker's two copies of each
            -- line, and lines retold from a source. Topic and heard-line carriers before
            -- v4 left no record and cannot be recovered.
            WITH spoken AS (
              SELECT profile_id,event_id,json_extract(data,'$.payload.teller_id') AS actor_id,
                json_extract(data,'$.payload.delivery_receipt_id') AS receipt_id,
                json_extract(data,'$.payload.reported_event_id') AS source_id
              FROM events WHERE json_extract(data,'$.event_type')='social.quote_delivered'
              AND json_extract(data,'$.provenance')='directly_observed'
              AND json_extract(data,'$.payload.teller_id') IS NOT NULL
            )
            INSERT OR IGNORE INTO memory_derivations
            SELECT profile_id,actor_id,receipt_id,event_id FROM spoken WHERE receipt_id IS NOT NULL
            UNION ALL SELECT profile_id,actor_id,event_id,receipt_id FROM spoken
              WHERE receipt_id IS NOT NULL
            UNION ALL SELECT profile_id,actor_id,source_id,receipt_id FROM spoken
              WHERE source_id IS NOT NULL AND receipt_id IS NOT NULL
            UNION ALL SELECT profile_id,actor_id,source_id,event_id FROM spoken
              WHERE source_id IS NOT NULL;
            PRAGMA user_version=4;
            COMMIT;
            """)
            return
        if version == 2:
            self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS skill_versions (
              profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
              skill_id TEXT NOT NULL, version INTEGER NOT NULL, source TEXT NOT NULL,
              fingerprint TEXT NOT NULL, registry_fingerprint TEXT NOT NULL,
              dependencies TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(profile_id,skill_id,version)
            );
            CREATE TABLE IF NOT EXISTS skill_records (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,
              record_id TEXT NOT NULL, actor_id TEXT NOT NULL, protagonist_id TEXT NOT NULL,
              branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
              session_id TEXT NOT NULL, runtime_generation INTEGER NOT NULL,
              runtime_instance_id TEXT NOT NULL, kind TEXT NOT NULL,
              skill_id TEXT NOT NULL, version INTEGER NOT NULL,
              fingerprint TEXT NOT NULL, parent_id TEXT, data TEXT NOT NULL,
              UNIQUE(profile_id,record_id),
              FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
              FOREIGN KEY(profile_id,branch_id) REFERENCES branches,
              FOREIGN KEY(profile_id,skill_id,version) REFERENCES skill_versions,
              FOREIGN KEY(profile_id,parent_id) REFERENCES skill_records(profile_id,record_id)
                ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS skill_actor_history
              ON skill_records(profile_id,actor_id,protagonist_id,game_datetime);
            CREATE TABLE IF NOT EXISTS skill_record_sources (
              profile_id TEXT NOT NULL, record_id TEXT NOT NULL, event_id TEXT NOT NULL,
              PRIMARY KEY(profile_id,record_id,event_id),
              FOREIGN KEY(profile_id,record_id) REFERENCES skill_records(profile_id,record_id)
                ON DELETE CASCADE,
              FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
            );
            PRAGMA user_version=3;
            COMMIT;
            """)
            self._migrate()
            return
        if version == 1:
            self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE ai_requests (
              request_id TEXT PRIMARY KEY, day TEXT NOT NULL, budget_class TEXT NOT NULL,
              provider TEXT NOT NULL, model TEXT NOT NULL, reserved_micro_usd INTEGER NOT NULL,
              charged_micro_usd INTEGER NOT NULL, state TEXT NOT NULL,
              input_tokens INTEGER, output_tokens INTEGER, price_json TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX ai_requests_day_class ON ai_requests(day,budget_class);
            CREATE TABLE service_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=2;
            COMMIT;
            """)
            self._migrate()
            return
        self.connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE profiles (
          profile_id TEXT PRIMARY KEY, save_slot TEXT NOT NULL, settings TEXT NOT NULL,
          created_at TEXT NOT NULL, active_branch TEXT
        );
        CREATE TABLE branches (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          branch_id TEXT NOT NULL, parent_id TEXT, fork_time TEXT NOT NULL,
          latest_time TEXT NOT NULL, PRIMARY KEY(profile_id,branch_id)
        );
        CREATE TABLE game_sessions (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          session_id TEXT NOT NULL, branch_id TEXT NOT NULL, started_at TEXT NOT NULL,
          ended_at TEXT, PRIMARY KEY(profile_id,session_id)
        );
        CREATE TABLE actors (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          actor_id TEXT NOT NULL, actor_kind TEXT NOT NULL, session_id TEXT,
          data TEXT NOT NULL, PRIMARY KEY(profile_id,actor_id)
        );
        CREATE TABLE actor_instances (
          profile_id TEXT NOT NULL, session_id TEXT NOT NULL, handle INTEGER NOT NULL,
          generation INTEGER NOT NULL, actor_id TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL, PRIMARY KEY(profile_id,session_id,handle,generation),
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE
        );
        CREATE TABLE events (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          event_id TEXT NOT NULL, branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
          protagonist_id TEXT, data TEXT NOT NULL, PRIMARY KEY(profile_id,event_id),
          FOREIGN KEY(profile_id,branch_id) REFERENCES branches
        );
        CREATE TABLE memories (
          memory_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, actor_id TEXT NOT NULL,
          protagonist_id TEXT, branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
          text TEXT NOT NULL, provenance TEXT NOT NULL, importance REAL NOT NULL,
          confidence REAL NOT NULL, visibility_scope TEXT NOT NULL, session_id TEXT,
          expires_at TEXT, decay_days REAL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
          FOREIGN KEY(profile_id,branch_id) REFERENCES branches
        );
        CREATE INDEX memory_actor ON memories(profile_id,actor_id,game_datetime);
        CREATE VIRTUAL TABLE memory_fts USING fts5(text, content='memories',
          content_rowid='rowid', tokenize='porter unicode61');
        CREATE TRIGGER memory_insert AFTER INSERT ON memories BEGIN
          INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text);
        END;
        CREATE TRIGGER memory_delete AFTER DELETE ON memories BEGIN
          INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text);
        END;
        CREATE TRIGGER memory_update AFTER UPDATE OF text ON memories BEGIN
          INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text);
          INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text);
        END;
        CREATE TABLE memory_sources (
          memory_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          profile_id TEXT NOT NULL, event_id TEXT NOT NULL,
          PRIMARY KEY(memory_id,event_id),
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );
        CREATE TABLE memory_vectors (
          memory_id TEXT PRIMARY KEY REFERENCES memories ON DELETE CASCADE,
          model TEXT NOT NULL, vector BLOB NOT NULL
        );
        CREATE TABLE summary_sources (
          summary_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          memory_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          PRIMARY KEY(summary_id,memory_id)
        );
        CREATE TABLE relationship_updates (
          profile_id TEXT NOT NULL, actor_id TEXT NOT NULL, protagonist_id TEXT NOT NULL,
          branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL, event_id TEXT NOT NULL,
          reason TEXT NOT NULL, deltas TEXT NOT NULL, session_id TEXT,
          PRIMARY KEY(profile_id,actor_id,protagonist_id,event_id),
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );
        CREATE TABLE knowledge_cards (
          card_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          owner_id TEXT, scope TEXT NOT NULL, scope_id TEXT, protagonist_id TEXT,
          branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL, expires_at TEXT,
          text TEXT NOT NULL, provenance TEXT NOT NULL, confidence REAL NOT NULL,
          session_id TEXT, FOREIGN KEY(profile_id,branch_id) REFERENCES branches,
          FOREIGN KEY(profile_id,owner_id) REFERENCES actors ON DELETE CASCADE
        );
        CREATE TABLE knowledge_sources (
          card_id TEXT NOT NULL REFERENCES knowledge_cards ON DELETE CASCADE,
          profile_id TEXT NOT NULL, event_id TEXT NOT NULL,
          PRIMARY KEY(card_id,event_id),
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );
        CREATE TABLE scrub_audit (
          audit_id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL,
          operation TEXT NOT NULL, object_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        PRAGMA user_version=1;
        COMMIT;
        """)
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def create_profile(
        self, profile_id: str, save_slot: str = "", settings: dict | None = None
    ) -> dict:
        if not profile_id:
            raise ValueError("profile_id is required")
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT * FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if existing and save_slot and existing["save_slot"] != save_slot:
                raise ValueError("profile already belongs to another save association")
            self.connection.execute(
                "INSERT OR IGNORE INTO profiles VALUES(?,?,?,?,NULL)",
                (profile_id, save_slot, _json(settings or {}), _now()),
            )
            return dict(
                self.connection.execute(
                    "SELECT * FROM profiles WHERE profile_id=?", (profile_id,)
                ).fetchone()
            )

    def _branch(self, profile_id: str, branch_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM branches WHERE profile_id=? AND branch_id=?", (profile_id, branch_id)
        ).fetchone()
        if row is None:
            raise ValueError("unknown timeline branch for this profile")
        return row

    def open_timeline(
        self, profile_id: str, game_datetime: str, *, parent_branch_id: str | None = None
    ) -> str:
        stamp = _date(game_datetime)
        with self._lock, self.connection:
            self.create_profile(profile_id)
            profile = self.connection.execute(
                "SELECT active_branch FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            current = (
                self._branch(profile_id, parent_branch_id or profile[0])
                if (parent_branch_id or profile[0])
                else None
            )
            if current is not None and parent_branch_id is None and stamp >= current["latest_time"]:
                candidates = self.connection.execute(
                    "SELECT branch_id FROM branches WHERE profile_id=? AND branch_id!=? "
                    "AND fork_time<=? AND latest_time>=?",
                    (profile_id, current["branch_id"], stamp, stamp),
                ).fetchall()
                if candidates:
                    raise TimelineAmbiguityError(
                        [current["branch_id"], *(row[0] for row in candidates)]
                    )
            if current is not None and stamp >= current["latest_time"]:
                self._touch(profile_id, current["branch_id"], stamp)
                self.connection.execute(
                    "UPDATE profiles SET active_branch=? WHERE profile_id=?",
                    (current["branch_id"], profile_id),
                )
                return current["branch_id"]
            branch_id = str(uuid4())
            self.connection.execute(
                "INSERT INTO branches VALUES(?,?,?,?,?)",
                (profile_id, branch_id, current["branch_id"] if current else None, stamp, stamp),
            )
            self.connection.execute(
                "UPDATE profiles SET active_branch=? WHERE profile_id=?", (branch_id, profile_id)
            )
            return branch_id

    def _touch(self, profile_id: str, branch_id: str, stamp: str) -> None:
        branch = self._branch(profile_id, branch_id)
        if stamp < branch["fork_time"]:
            raise ValueError("cannot write before the timeline branch starts")
        self.connection.execute(
            "UPDATE branches SET latest_time=MAX(latest_time,?) WHERE profile_id=? AND branch_id=?",
            (stamp, profile_id, branch_id),
        )

    def _lineage(self, profile_id: str, branch_id: str, stamp: str) -> dict[str, str]:
        visible: dict[str, str] = {}
        while branch_id:
            branch = self._branch(profile_id, branch_id)
            if branch_id in visible:
                raise RuntimeError("timeline ancestry cycle")
            visible[branch_id] = stamp
            stamp = min(stamp, branch["fork_time"])
            branch_id = branch["parent_id"]
        return visible

    def start_session(
        self,
        profile_id: str,
        session_id: str,
        game_datetime: str,
        *,
        parent_branch_id: str | None = None,
    ) -> dict:
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT * FROM game_sessions WHERE profile_id=? AND session_id=?",
                (profile_id, session_id),
            ).fetchone()
            if existing:
                if existing["ended_at"]:
                    raise ValueError("cannot reuse an ended gameplay session")
                return dict(existing)
            branch_id = self.open_timeline(
                profile_id, game_datetime, parent_branch_id=parent_branch_id
            )
            self.connection.execute(
                "INSERT INTO game_sessions VALUES(?,?,?,?,NULL)",
                (profile_id, session_id, branch_id, _date(game_datetime)),
            )
            return {"profile_id": profile_id, "session_id": session_id, "branch_id": branch_id}

    def end_session(self, profile_id: str, session_id: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM actors WHERE profile_id=? AND session_id=? "
                "AND actor_kind='ambient_ephemeral'",
                (profile_id, session_id),
            )
            self.connection.execute(
                "DELETE FROM actor_instances WHERE profile_id=? AND session_id=?",
                (profile_id, session_id),
            )
            self.connection.execute(
                "UPDATE game_sessions SET ended_at=? WHERE profile_id=? AND session_id=?",
                (_now(), profile_id, session_id),
            )

    def register_actor(self, profile_id: str, actor: dict) -> dict:
        data = dict(actor)
        actor_id = data.get("id", data.get("actor_id"))
        kind = data.get("actor_kind", "curated_persistent")
        if not actor_id or kind not in ACTOR_KINDS:
            raise ValueError("valid actor id and actor_kind required")
        if data.get("profile_id", profile_id) != profile_id:
            raise ValueError("actor profile mismatch")
        session_id = data.get("session_id") if kind == "ambient_ephemeral" else None
        if kind == "ambient_ephemeral" and not session_id:
            raise ValueError("ambient actors require a gameplay session_id")
        data.update(
            id=actor_id,
            actor_id=actor_id,
            profile_id=profile_id,
            actor_kind=kind,
            session_id=session_id,
        )
        data.setdefault("identity_confidence", 1.0 if kind != "ambient_ephemeral" else 0.5)
        _unit(data["identity_confidence"], "identity_confidence")
        _relationship_baseline(data)
        with self._lock, self.connection:
            self.create_profile(profile_id)
            existing = self.get_actor(profile_id, actor_id)
            if existing and (
                existing["actor_kind"] != kind or existing.get("session_id") != session_id
            ):
                raise ValueError("an actor identity cannot change kind or gameplay session")
            self.connection.execute(
                "INSERT INTO actors VALUES(?,?,?,?,?) ON CONFLICT(profile_id,actor_id) "
                "DO UPDATE SET data=excluded.data",
                (profile_id, actor_id, kind, session_id, _json(data)),
            )
        return data

    def get_actor(
        self, profile_id: str, actor_id: str, *, session_id: str | None = None
    ) -> dict | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT data,session_id FROM actors WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            ).fetchone()
            if row is None or (
                session_id is not None and row["session_id"] not in (None, session_id)
            ):
                return None
            return json.loads(row["data"])

    def list_actors(self, profile_id: str, *, session_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT data,session_id FROM actors WHERE profile_id=? ORDER BY actor_id",
                (profile_id,),
            ).fetchall()
            return [
                json.loads(row["data"])
                for row in rows
                if row["session_id"] is None or row["session_id"] == session_id
            ]

    def bind_instance(
        self,
        profile_id: str,
        actor_id: str,
        session_id: str,
        handle: int,
        generation: int,
        confidence: float = 1.0,
    ) -> None:
        with self._lock, self.connection:
            actor = self._actor_session(profile_id, actor_id, session_id)
            ambient = actor["actor_kind"] == "ambient_ephemeral"
            previous = self.connection.execute(
                "SELECT handle,generation,status FROM actor_instances "
                "WHERE profile_id=? AND session_id=? AND actor_id=?",
                (profile_id, session_id, actor_id),
            ).fetchall()
            if ambient and any(
                row["handle"] != handle or row["generation"] != generation for row in previous
            ):
                raise ValueError("a different ambient entity generation needs a new actor identity")
            # An ambient identity embodies one entity once. Its released row can be
            # replaced by a recycled handle's new identity, so the marker lives on the actor.
            bound = actor.get("runtime_binding")
            if ambient and bound is not None and (
                bound != [handle, generation] or not any(r["status"] == "live" for r in previous)
            ):
                raise ValueError("a released ambient entity needs a new actor identity")
            existing = self.connection.execute(
                "SELECT actor_id FROM actor_instances WHERE profile_id=? AND session_id=? "
                "AND handle=? AND generation=? AND status='live'",
                (profile_id, session_id, handle, generation),
            ).fetchone()
            if existing and existing[0] != actor_id:
                raise ValueError("runtime handle generation already belongs to another actor")
            # A handle can have only one live generation, and one persistent
            # character can have only one live engine incarnation per session.
            self.connection.execute(
                "UPDATE actor_instances SET status='released' WHERE profile_id=? "
                "AND session_id=? AND (handle=? OR actor_id=?)",
                (profile_id, session_id, handle, actor_id),
            )
            self.connection.execute(
                "INSERT OR REPLACE INTO actor_instances VALUES(?,?,?,?,?,?,?)",
                (
                    profile_id,
                    session_id,
                    handle,
                    generation,
                    actor_id,
                    _unit(confidence, "confidence"),
                    "live",
                ),
            )
            if ambient and bound is None:
                actor["runtime_binding"] = [handle, generation]
                self.connection.execute(
                    "UPDATE actors SET data=? WHERE profile_id=? AND actor_id=?",
                    (_json(actor), profile_id, actor_id),
                )

    def resolve_instance(
        self, profile_id: str, session_id: str, handle: int, generation: int
    ) -> dict | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM actor_instances WHERE profile_id=? AND session_id=? "
                "AND handle=? AND generation=? AND status='live'",
                (profile_id, session_id, handle, generation),
            ).fetchone()
            return dict(row) if row else None

    def release_instance(
        self,
        profile_id: str,
        session_id: str,
        handle: int | None = None,
        generation: int | None = None,
        *,
        actor_id: str | None = None,
    ) -> int:
        """Release live bindings by entity tuple, by actor, or both. Return the count."""
        if (handle is None) != (generation is None) or (handle is None and actor_id is None):
            raise ValueError("release requires a runtime handle generation or an actor")
        clauses = ["profile_id=?", "session_id=?", "status='live'"]
        values = [profile_id, session_id]
        if handle is not None:
            clauses.append("handle=? AND generation=?")
            values += [handle, generation]
        if actor_id is not None:
            clauses.append("actor_id=?")
            values.append(actor_id)
        with self._lock, self.connection:
            return self.connection.execute(
                "UPDATE actor_instances SET status='released' WHERE " + " AND ".join(clauses),
                values,
            ).rowcount

    def adopt_actor(
        self, profile_id: str, actor_id: str, session_id: str, *, display_name: str | None = None
    ) -> dict:
        """Copy a persona into a new identity without private experiences.

        The player-facing pin is pin_actor, which keeps the id and the experiences.
        """
        with self._lock:
            actor = self._actor_session(profile_id, actor_id, session_id)
            if actor["actor_kind"] != "ambient_ephemeral":
                raise ValueError("only ambient actors can be adopted")
            actor.update(
                id=str(uuid4()),
                actor_kind="spawned_persistent",
                session_id=None,
                adopted_from=actor_id,
            )
            actor.pop("runtime_binding", None)
            actor["actor_id"] = actor["id"]
            if display_name:
                actor["display_name"] = display_name
            return self.register_actor(profile_id, actor)

    def pin_actor(
        self,
        profile_id: str,
        actor_id: str,
        session_id: str,
        *,
        display_name: str,
        pinned_at_game_datetime: str,
        branch_id: str,
    ) -> dict:
        """Promote a session pedestrian in place, keeping its id and its own experiences.

        The actor's own memories, relationship updates, and owned knowledge leave the
        session scope that end_session deletes. Other actors' rows are untouched. A
        repeated pin from the same session returns the same durable actor.
        """
        stamp = _date(pinned_at_game_datetime)
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 48:
            raise ValueError("a pinned actor needs a display name of 1 to 48 characters")
        with self._lock:
            if self.connection.in_transaction:
                raise ValueError("pinning cannot nest inside another transaction")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT actor_kind,session_id,data FROM actors WHERE profile_id=? "
                    "AND actor_id=?",
                    (profile_id, actor_id),
                ).fetchone()
                if row is None:
                    raise ValueError("unknown actor in this profile")
                data = json.loads(row["data"])
                if not (
                    row["actor_kind"] == "spawned_persistent"
                    and data.get("pinned_from_session") == session_id
                ):
                    if row["actor_kind"] != "ambient_ephemeral" or row["session_id"] != session_id:
                        raise ValueError("only a pedestrian from this session can be pinned")
                    self._branch(profile_id, branch_id)
                    # A persistent character may embody new entities, unlike an ambient one.
                    data.pop("runtime_binding", None)
                    data.update(
                        actor_kind="spawned_persistent",
                        session_id=None,
                        display_name=display_name,
                        pinned_from_session=session_id,
                        pinned_at={"game_datetime": stamp, "branch_id": branch_id},
                    )
                    self.connection.execute(
                        "UPDATE actors SET actor_kind='spawned_persistent',session_id=NULL,data=? "
                        "WHERE profile_id=? AND actor_id=?",
                        (_json(data), profile_id, actor_id),
                    )
                    # memory_fts, memory_sources and memory_vectors follow their memory rows.
                    for table, owner in (
                        ("memories", "actor_id"),
                        ("relationship_updates", "actor_id"),
                        ("knowledge_cards", "owner_id"),
                    ):
                        self.connection.execute(
                            f"UPDATE {table} SET session_id=NULL WHERE profile_id=? "
                            f"AND {owner}=? AND session_id=?",
                            (profile_id, actor_id, session_id),
                        )
                    self._audit(profile_id, "pin_actor", actor_id)
                self.connection.commit()
                return data
            except BaseException:
                self.connection.rollback()
                raise

    def forget_pinned_actor(self, profile_id: str, actor_id: str) -> None:
        """Scrub and delete a pinned pedestrian. Authored characters cannot be forgotten."""
        with self._lock:
            actor = self.get_actor(profile_id, actor_id)
            if actor is None:
                raise ValueError("unknown actor in this profile")
            if actor["actor_kind"] != "spawned_persistent" or not actor.get("pinned_from_session"):
                raise ValueError("only pinned pedestrians can be forgotten")
            self.reset_actor(profile_id, actor_id)
            with self.connection:
                self.connection.execute(
                    "DELETE FROM actors WHERE profile_id=? AND actor_id=?", (profile_id, actor_id)
                )
                self._audit(profile_id, "forget_pinned_actor", actor_id)

    def _actor_session(self, profile_id: str, actor_id: str, session_id: str | None) -> dict:
        actor = self.get_actor(profile_id, actor_id)
        if actor is None:
            raise ValueError("unknown actor in this profile")
        if actor["actor_kind"] == "ambient_ephemeral" and actor.get("session_id") != session_id:
            raise ValueError("ambient identity is isolated to its gameplay session")
        return actor

    def record_event(self, profile_id: str, branch_id: str, event: dict) -> bool:
        with self._lock, self.connection:
            return self._record_event(profile_id, branch_id, event)

    def record_events_atomic(
        self, profile_id: str, branch_id: str, events: list[dict], *, precondition=None
    ) -> list[bool]:
        """Insert a bounded canonical batch under one cross-connection write lock.

        The optional trusted synchronous precondition receives this Repository
        before insertion. It must perform reads only and raise to reject a batch.
        This method owns its transaction and cannot nest inside another write.
        """
        if not isinstance(events, list) or not 1 <= len(events) <= 256:
            raise ValueError("canonical event batch requires 1 to 256 events")
        with self._lock:
            if self.connection.in_transaction:
                raise ValueError("atomic event batch cannot nest inside a transaction")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self._branch(profile_id, branch_id)
                if precondition is not None:
                    precondition(self)
                result = [self._record_event(profile_id, branch_id, event) for event in events]
                self.connection.commit()
                return result
            except BaseException:
                self.connection.rollback()
                raise

    def _record_event(self, profile_id: str, branch_id: str, event: dict) -> bool:
        data = dict(event)
        event_id = data.get("event_id", data.get("id"))
        if not event_id:
            raise ValueError("event_id is required for deduplication")
        stamp = _date(data["game_datetime"])
        provenance = data.get("provenance", "unverified")
        if provenance not in PROVENANCES:
            raise ValueError("unknown provenance")
        protagonist = _protagonist(data.get("protagonist_id", data.get("active_protagonist")))
        data.update(
            event_id=event_id,
            game_datetime=stamp,
            provenance=provenance,
            protagonist_id=protagonist,
            confidence=_unit(data.get("confidence", 1.0), "confidence"),
        )
        self._branch(profile_id, branch_id)
        old = self.connection.execute(
            "SELECT data,branch_id FROM events WHERE profile_id=? AND event_id=?",
            (profile_id, event_id),
        ).fetchone()
        if old:
            if old["data"] != _json(data) or old["branch_id"] != branch_id:
                raise ValueError("event ID collision: canonical events are immutable")
            return False
        self._touch(profile_id, branch_id, stamp)
        self.connection.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)",
            (profile_id, event_id, branch_id, stamp, protagonist, _json(data)),
        )
        return True

    def _sources(
        self,
        profile_id: str,
        branch_id: str,
        stamp: str,
        source_event_ids: list[str],
        provenance: str,
        protagonist_id: str | None,
    ) -> list[dict]:
        if not source_event_ids or len(source_event_ids) != len(set(source_event_ids)):
            raise ValueError("at least one unique source event is required")
        if provenance not in PROVENANCES:
            raise ValueError("unknown provenance")
        lineage = self._lineage(profile_id, branch_id, stamp)
        sources = []
        for event_id in source_event_ids:
            row = self.connection.execute(
                "SELECT * FROM events WHERE profile_id=? AND event_id=?", (profile_id, event_id)
            ).fetchone()
            if (
                row is None
                or row["branch_id"] not in lineage
                or row["game_datetime"] > lineage[row["branch_id"]]
            ):
                raise ValueError("source event is absent from this timeline")
            if row["protagonist_id"] is not None and row["protagonist_id"] != protagonist_id:
                raise ValueError("source event belongs to another protagonist")
            data = json.loads(row["data"])
            if provenance in {"directly_observed", "system_fact"} and data["provenance"] not in {
                "directly_observed",
                "system_fact",
            }:
                raise ValueError("hearsay cannot become an observed or verified fact")
            if provenance == "system_fact" and data["provenance"] != "system_fact":
                raise ValueError("an observation cannot become a system fact")
            if provenance == "heard" and data["provenance"] == "reported":
                raise ValueError("reported knowledge cannot become directly heard knowledge")
            if provenance != "unverified" and data["provenance"] == "unverified":
                raise ValueError("unverified source cannot become verified knowledge")
            sources.append(data)
        return sources

    @staticmethod
    def _require_observer(actor_id: str, sources: list[dict], provenance: str) -> None:
        for source in sources:
            observers = source.get(
                "observer_ids", source.get("payload", {}).get("observer_ids", [])
            )
            public_fact = (
                source["provenance"] == "system_fact"
                and source.get("payload", {}).get("visibility_scope") == "public"
                and source.get("payload", {}).get("scope_id") is None
                and provenance not in {"directly_observed", "heard"}
            )
            if actor_id not in observers and not public_fact:
                raise ValueError("actor did not observe or receive the source event")

    def add_memory(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str | None,
        game_datetime: str,
        text: str,
        source_event_ids: list[str],
        provenance: str,
        importance: float | None = None,
        confidence: float = 1.0,
        *,
        session_id: str | None = None,
        visibility_scope: str = "private",
        expires_at: str | None = None,
        decay_days: float | None = None,
        memory_id: str | None = None,
    ) -> dict:
        with self._lock, self.connection:
            return self._add_memory(
                profile_id,
                branch_id,
                actor_id,
                protagonist_id,
                game_datetime,
                text,
                source_event_ids,
                provenance,
                importance,
                confidence,
                session_id=session_id,
                visibility_scope=visibility_scope,
                expires_at=expires_at,
                decay_days=decay_days,
                memory_id=memory_id,
            )

    def _add_memory(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str | None,
        game_datetime: str,
        text: str,
        source_event_ids: list[str],
        provenance: str,
        importance: float | None = None,
        confidence: float = 1.0,
        *,
        session_id: str | None = None,
        visibility_scope: str = "private",
        expires_at: str | None = None,
        decay_days: float | None = None,
        memory_id: str | None = None,
    ) -> dict:
        """Caller holds the repository lock and owns the write transaction."""
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        if not text.strip() or visibility_scope != "private":
            raise ValueError("episodic memories require text and private visibility")
        if importance is not None:
            importance = _unit(importance, "importance")
        confidence = _unit(confidence, "confidence")
        expiry = _date(expires_at) if expires_at else None
        if expiry is not None and expiry <= stamp:
            raise ValueError("memory expiry must follow its creation")
        if decay_days is not None and (not math.isfinite(decay_days) or decay_days <= 0):
            raise ValueError("decay_days must be positive")
        fingerprint = hashlib.sha256(
            _json(
                [" ".join(text.casefold().split()), sorted(source_event_ids), protagonist]
            ).encode()
        ).hexdigest()
        actor = self._actor_session(profile_id, actor_id, session_id)
        session = session_id if actor["actor_kind"] == "ambient_ephemeral" else None
        sources = self._sources(
            profile_id, branch_id, stamp, source_event_ids, provenance, protagonist
        )
        self._require_observer(actor_id, sources, provenance)
        confidence = min(confidence, *(source["confidence"] for source in sources))
        lineage = self._lineage(profile_id, branch_id, stamp)
        old = self.connection.execute(
            "SELECT * FROM memories WHERE profile_id=? AND actor_id=? AND fingerprint=?",
            (profile_id, actor_id, fingerprint),
        ).fetchall()
        for row in old:
            if (
                row["branch_id"] in lineage
                and row["game_datetime"] <= lineage[row["branch_id"]]
            ):
                return self._memory_dict(row)
        if importance is None:
            importance = self._salience(profile_id, actor_id, text.strip(), sources, lineage)
        identifier = memory_id or str(uuid4())
        self._touch(profile_id, branch_id, stamp)
        self.connection.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                profile_id,
                actor_id,
                protagonist,
                branch_id,
                stamp,
                text.strip(),
                provenance,
                importance,
                confidence,
                visibility_scope,
                session,
                expiry,
                decay_days,
                fingerprint,
                _now(),
            ),
        )
        self.connection.executemany(
            "INSERT INTO memory_sources VALUES(?,?,?)",
            [(identifier, profile_id, event_id) for event_id in source_event_ids],
        )
        return self.get_memory(profile_id, identifier)

    def _salience(
        self,
        profile_id: str,
        actor_id: str,
        text: str,
        sources: list[dict],
        lineage: dict[str, str],
    ) -> float:
        # Only identical lines already visible in this timeline count as repeats.
        repeats = sum(
            1
            for row in self.connection.execute(
                "SELECT branch_id,game_datetime FROM memories "
                "WHERE profile_id=? AND actor_id=? AND text=?",
                (profile_id, actor_id, text),
            )
            if row["branch_id"] in lineage and row["game_datetime"] <= lineage[row["branch_id"]]
        )
        return salience.score(sources, repeats=repeats)

    def _memory_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data.pop("fingerprint", None)
        data["source_event_ids"] = [
            r[0]
            for r in self.connection.execute(
                "SELECT event_id FROM memory_sources WHERE memory_id=? ORDER BY event_id",
                (row["memory_id"],),
            )
        ]
        data["text_first_person"] = data["text"]
        return data

    def get_memory(self, profile_id: str, memory_id: str) -> dict | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM memories WHERE profile_id=? AND memory_id=?", (profile_id, memory_id)
            ).fetchone()
            return self._memory_dict(row) if row else None

    @staticmethod
    def token_cost(text: str) -> int:
        # A UTF-8 byte is a conservative upper bound for byte-level tokenizers.
        # Include record metadata allowance; callers may supply a smaller budget.
        return len(text.encode("utf-8")) + 48

    def retrieve_memories(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str | None,
        game_datetime: str,
        query: str = "",
        limit: int = 8,
        *,
        token_budget: int = 512,
        session_id: str | None = None,
        collapse_summaries: bool = True,
    ) -> list[dict]:
        """Rank visible memories. A selected summary hides the lines it consolidates."""
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        if limit < 0 or token_budget < 0:
            raise ValueError("retrieval limits cannot be negative")
        with self._lock:
            try:
                self._actor_session(profile_id, actor_id, session_id)
            except ValueError:
                return []
            lineage = self._lineage(profile_id, branch_id, stamp)
            rows = self.connection.execute(
                "SELECT * FROM memories WHERE profile_id=? AND actor_id=? "
                "AND (protagonist_id IS NULL OR protagonist_id=?)",
                (profile_id, actor_id, protagonist),
            ).fetchall()
            words = [
                word
                for word in dict.fromkeys(re.findall(r"\w+", query.casefold()))
                if word not in QUERY_STOP_WORDS
            ][:32]
            lexical = {}
            if words:
                match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
                hits = self.connection.execute(
                    "SELECT m.memory_id,bm25(memory_fts) AS rank FROM memory_fts "
                    "JOIN memories m ON m.rowid=memory_fts.rowid "
                    "WHERE memory_fts MATCH ? AND m.profile_id=? AND m.actor_id=?",
                    (match, profile_id, actor_id),
                ).fetchall()
                lexical = {row["memory_id"]: -row["rank"] for row in hits}
            ranked = []
            current = datetime.fromisoformat(stamp)
            for row in rows:
                if (
                    row["branch_id"] not in lineage
                    or row["game_datetime"] > lineage[row["branch_id"]]
                ):
                    continue
                if row["session_id"] is not None and row["session_id"] != session_id:
                    continue
                if row["expires_at"] and row["expires_at"] <= stamp:
                    continue
                age_days = max(
                    0,
                    (current - datetime.fromisoformat(row["game_datetime"])).total_seconds()
                    / 86400,
                )
                decay = math.exp(
                    -age_days / (row["decay_days"] or salience.decay_days(row["importance"]))
                )
                relevance = (
                    1 + math.log1p(lexical[row["memory_id"]]) if row["memory_id"] in lexical else 0
                )
                score = relevance * 4 + row["importance"] * 2 + row["confidence"] + decay
                ranked.append((score, row))
            ranked.sort(key=lambda entry: (-entry[0], entry[1]["memory_id"]))
            covers = self.summary_cover(profile_id, actor_id) if collapse_summaries else {}
            result, used, seen, covered = [], 0, set(), set()
            for score, row in ranked:
                key = " ".join(row["text"].casefold().split())
                cost = self.token_cost(row["text"])
                if key in seen or row["memory_id"] in covered or used + cost > token_budget:
                    continue
                if len(result) >= limit:
                    break
                data = self._memory_dict(row)
                data.update(retrieval_score=score, token_cost=cost)
                result.append(data)
                seen.add(key)
                covered.update(covers.get(row["memory_id"], ()))
                used += cost
            return result

    def summary_cover(self, profile_id: str, actor_id: str) -> dict[str, set[str]]:
        """Map each of this actor's summaries to the memories it consolidates."""
        covers: dict[str, set[str]] = {}
        with self._lock:
            for row in self.connection.execute(
                "SELECT s.summary_id,s.memory_id FROM summary_sources s "
                "JOIN memories m ON m.memory_id=s.summary_id "
                "WHERE m.profile_id=? AND m.actor_id=?",
                (profile_id, actor_id),
            ):
                covers.setdefault(row["summary_id"], set()).add(row["memory_id"])
        return covers

    def consolidation_batch(
        self,
        profile_id: str,
        branch_id: str,
        protagonist_id: str,
        game_datetime: str,
        *,
        min_batch: int,
        max_batch: int,
        episode_gap_minutes: float,
        lookback_days: float,
        max_text_bytes: int,
    ) -> dict | None:
        """Return the oldest settled episode of unsummarized firsthand lines, if any.

        Only persistent actors qualify. An episode is a run of one actor's lines with
        no gap longer than episode_gap_minutes. The newest run waits for that gap to
        pass after its last line, unless it already holds max_batch lines. A batch
        takes the episode's oldest lines that fit max_text_bytes once serialized.
        """
        if not 2 <= min_batch <= max_batch <= MAX_SUMMARY_LINES:
            raise ValueError("summary batches require 2 to 32 lines")
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        current = datetime.fromisoformat(stamp)
        earliest = (current - timedelta(days=lookback_days)).isoformat()
        gap = timedelta(minutes=episode_gap_minutes)
        with self._lock:
            lineage = self._lineage(profile_id, branch_id, stamp)

            def visible(row):
                return (
                    row["branch_id"] in lineage
                    and row["game_datetime"] <= lineage[row["branch_id"]]
                )

            covered = {
                row["memory_id"]
                for row in self.connection.execute(
                    "SELECT s.memory_id,m.branch_id,m.game_datetime FROM summary_sources s "
                    "JOIN memories m ON m.memory_id=s.summary_id WHERE m.profile_id=?",
                    (profile_id,),
                )
                if visible(row)
            }
            rows = self.connection.execute(
                "SELECT m.* FROM memories m JOIN actors a "
                "ON a.profile_id=m.profile_id AND a.actor_id=m.actor_id "
                "WHERE m.profile_id=? AND m.protagonist_id=? AND m.session_id IS NULL "
                "AND a.actor_kind NOT IN ('ambient_ephemeral','protagonist') "
                "AND m.provenance IN ('system_fact','directly_observed','heard') "
                "AND m.game_datetime>=? AND m.game_datetime<=? "
                "AND NOT EXISTS (SELECT 1 FROM summary_sources s WHERE s.summary_id=m.memory_id) "
                "ORDER BY m.actor_id,m.game_datetime,m.rowid",
                (profile_id, protagonist, earliest, stamp),
            ).fetchall()
            episodes: list[list[sqlite3.Row]] = []
            for row in rows:
                if (
                    not visible(row)
                    or row["memory_id"] in covered
                    or (row["expires_at"] and row["expires_at"] <= stamp)
                ):
                    continue
                last = episodes[-1][-1] if episodes else None
                if (
                    last is None
                    or last["actor_id"] != row["actor_id"]
                    or datetime.fromisoformat(row["game_datetime"])
                    - datetime.fromisoformat(last["game_datetime"])
                    > gap
                ):
                    episodes.append([])
                episodes[-1].append(row)
            for index, episode in enumerate(episodes):
                following = episodes[index + 1] if index + 1 < len(episodes) else None
                settled = (
                    following is not None and following[0]["actor_id"] == episode[0]["actor_id"]
                ) or (
                    current - datetime.fromisoformat(episode[-1]["game_datetime"]) > gap
                )
                if len(episode) < max_batch and not (settled and len(episode) >= min_batch):
                    continue
                taken, size = [], 0
                for row in episode[:max_batch]:
                    # Leave room for each line's index, time, and provenance.
                    size += len(_json(row["text"]).encode("utf-8")) + 128
                    if size > max_text_bytes:
                        break
                    taken.append(row)
                if len(taken) >= min_batch:
                    return {
                        "actor_id": episode[0]["actor_id"],
                        "memories": [self._memory_dict(row) for row in taken],
                    }
            return None

    def add_summary(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        memory_ids: list[str],
        text: str,
        importance: float,
        *,
        expected_texts: list[str] | None = None,
    ) -> dict:
        """Store one paraphrase of an actor's own firsthand lines with their sources.

        The summary cites every event its lines cite and keeps the weakest provenance
        and lowest confidence among them. Erasing any line or event erases it.
        expected_texts rejects a summary of lines edited after it was requested.
        """
        if not 2 <= len(memory_ids) <= MAX_SUMMARY_LINES or len(set(memory_ids)) != len(
            memory_ids
        ):
            raise ValueError("a summary consolidates 2 to 32 distinct memories")
        with self._lock, self.connection:
            lines = [self.get_memory(profile_id, memory_id) for memory_id in memory_ids]
            if any(line is None or line["actor_id"] != actor_id for line in lines):
                raise ValueError("a summary consolidates only this actor's memories")
            if expected_texts is not None and [line["text"] for line in lines] != expected_texts:
                raise ValueError("summarized memories changed during generation")
            protagonists = {line["protagonist_id"] for line in lines}
            sessions = {line["session_id"] for line in lines}
            if len(protagonists) != 1 or None in protagonists or len(sessions) != 1:
                raise ValueError("a summary stays within one protagonist and session")
            if any(line["provenance"] not in FIRSTHAND for line in lines):
                raise ValueError("only firsthand memories can be summarized")
            stamp = max(line["game_datetime"] for line in lines)
            lineage = self._lineage(profile_id, branch_id, stamp)
            if any(
                line["branch_id"] not in lineage
                or line["game_datetime"] > lineage[line["branch_id"]]
                or (line["expires_at"] and line["expires_at"] <= stamp)
                for line in lines
            ):
                raise ValueError("summarized memories are absent from this timeline")
            placeholders = ",".join("?" for _ in memory_ids)
            if self.connection.execute(
                f"SELECT 1 FROM summary_sources WHERE summary_id IN ({placeholders}) LIMIT 1",
                memory_ids,
            ).fetchone():
                raise ValueError("summaries cannot be summarized again")
            for row in self.connection.execute(
                "SELECT m.branch_id,m.game_datetime FROM summary_sources s "
                "JOIN memories m ON m.memory_id=s.summary_id "
                f"WHERE m.profile_id=? AND s.memory_id IN ({placeholders})",
                (profile_id, *memory_ids),
            ):
                if row["branch_id"] in lineage and (
                    row["game_datetime"] <= lineage[row["branch_id"]]
                ):
                    raise ValueError("a memory already belongs to a visible summary")
            events = sorted({event for line in lines for event in line["source_event_ids"]})
            if len(events) > 64:
                raise ValueError("a summary cites at most 64 source events")
            provenance = min((line["provenance"] for line in lines), key=FIRSTHAND.index)
            summary = self._add_memory(
                profile_id,
                branch_id,
                actor_id,
                lines[0]["protagonist_id"],
                stamp,
                text,
                events,
                provenance,
                importance,
                min(line["confidence"] for line in lines),
                session_id=lines[0]["session_id"],
                memory_id="summary_"
                + hashlib.sha256(
                    _json([profile_id, branch_id, sorted(memory_ids)]).encode()
                ).hexdigest(),
            )
            self.connection.executemany(
                "INSERT OR IGNORE INTO summary_sources VALUES(?,?)",
                [(summary["memory_id"], memory_id) for memory_id in memory_ids],
            )
            return summary

    def update_memory(
        self,
        profile_id: str,
        memory_id: str,
        *,
        text: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        with self._lock, self.connection:
            old = self.get_memory(profile_id, memory_id)
            if old is None:
                raise ValueError("unknown memory")
            body = old["text"] if text is None else text.strip()
            if not body:
                raise ValueError("memory cannot be empty")
            sources = self._sources(
                profile_id,
                old["branch_id"],
                old["game_datetime"],
                old["source_event_ids"],
                old["provenance"],
                old["protagonist_id"],
            )
            capped_confidence = min(
                _unit(old["confidence"] if confidence is None else confidence, "confidence"),
                *(source["confidence"] for source in sources),
            )
            fingerprint = hashlib.sha256(
                _json(
                    [
                        " ".join(body.casefold().split()),
                        sorted(old["source_event_ids"]),
                        old["protagonist_id"],
                    ]
                ).encode()
            ).hexdigest()
            self.connection.execute(
                "UPDATE memories SET text=?,importance=?,confidence=?,fingerprint=? "
                "WHERE profile_id=? AND memory_id=?",
                (
                    body,
                    _unit(old["importance"] if importance is None else importance, "importance"),
                    capped_confidence,
                    fingerprint,
                    profile_id,
                    memory_id,
                ),
            )
            if (body, capped_confidence) != (old["text"], old["confidence"]):
                # A corrected line makes every paraphrase of it stale. The line becomes
                # eligible for a fresh summary.
                for (summary_id,) in self.connection.execute(
                    "SELECT summary_id FROM summary_sources WHERE memory_id=?", (memory_id,)
                ).fetchall():
                    self._delete_memory(profile_id, summary_id)
            return self.get_memory(profile_id, memory_id)

    def _audit(self, profile_id: str, operation: str, object_id: str) -> None:
        self.connection.execute(
            "INSERT INTO scrub_audit(profile_id,operation,object_id,created_at) VALUES(?,?,?,?)",
            (profile_id, operation, object_id, _now()),
        )

    def link_memory_derivations(
        self, profile_id: str, actor_id: str, links: list[tuple[str, str]]
    ) -> None:
        """Record that this actor's memories citing each derived event repeat or answer
        this actor's memories citing its source event. Rows hold identifiers only.
        """
        pairs = list(dict.fromkeys((source, derived) for source, derived in links))
        if not 1 <= len(pairs) <= 64 or any(
            not isinstance(source, str) or not isinstance(derived, str)
            or not source or not derived or source == derived
            for source, derived in pairs
        ):
            raise ValueError("memory derivations require 1 to 64 distinct event identifier pairs")
        with self._lock, self.connection:
            if self.connection.execute(
                "SELECT 1 FROM actors WHERE profile_id=? AND actor_id=?", (profile_id, actor_id)
            ).fetchone() is None:
                raise ValueError("unknown actor in this profile")
            self.connection.executemany(
                "INSERT OR IGNORE INTO memory_derivations VALUES(?,?,?,?)",
                [(profile_id, actor_id, source, derived) for source, derived in pairs],
            )

    def _memory_closure(self, profile_id: str, memory_id: str) -> list[str]:
        """Return a memory, its summaries, and its owner's derived lines, transitively.

        Derivations stay within one actor. Other actors' memories of hearing a derived
        line are their own experiences, and canonical events remain their proof.
        A summary cites its lines' events but derives nothing, so erasing it alone
        leaves those lines.
        """
        closure, pending, seen = [memory_id], [memory_id], {memory_id}
        while pending:
            for (found,) in self.connection.execute(
                "SELECT summary_id FROM summary_sources WHERE memory_id=:memory "
                "UNION SELECT later.memory_id FROM memories own "
                "CROSS JOIN memory_sources cited ON cited.memory_id=own.memory_id "
                "CROSS JOIN memory_derivations link ON link.profile_id=own.profile_id "
                "AND link.actor_id=own.actor_id AND link.source_event_id=cited.event_id "
                "CROSS JOIN memory_sources derived ON derived.event_id=link.derived_event_id "
                "AND derived.profile_id=link.profile_id "
                "CROSS JOIN memories later ON later.memory_id=derived.memory_id "
                "AND later.profile_id=link.profile_id AND later.actor_id=link.actor_id "
                "WHERE own.profile_id=:profile AND own.memory_id=:memory "
                "AND NOT EXISTS (SELECT 1 FROM summary_sources own_summary "
                "WHERE own_summary.summary_id=own.memory_id)",
                {"profile": profile_id, "memory": pending.pop()},
            ).fetchall():
                if found not in seen:
                    seen.add(found)
                    closure.append(found)
                    pending.append(found)
        return closure

    def _delete_memory(self, profile_id: str, memory_id: str) -> bool:
        if self.get_memory(profile_id, memory_id) is None:
            return False
        for identifier in self._memory_closure(profile_id, memory_id):
            if self.connection.execute(
                "DELETE FROM memories WHERE profile_id=? AND memory_id=?", (profile_id, identifier)
            ).rowcount:
                self._audit(profile_id, "delete_memory", identifier)
        return True

    def delete_memory(self, profile_id: str, memory_id: str) -> bool:
        """Delete a memory with its summaries and this actor's lines derived from it."""
        with self._lock, self.connection:
            return self._delete_memory(profile_id, memory_id)

    def memory_dependents(self, profile_id: str, memory_id: str) -> list[str]:
        """List the other memories that deleting this memory removes, changing nothing."""
        with self._lock:
            if self.get_memory(profile_id, memory_id) is None:
                return []
            return sorted(self._memory_closure(profile_id, memory_id)[1:])

    def delete_event(self, profile_id: str, event_id: str) -> bool:
        """Delete canonical content and projections, retaining content-free reset markers.

        Reimporting this source identifier must not undo an explicit actor reset.
        """
        with self._lock, self.connection:
            skill_rows = self.connection.execute(
                "SELECT r.actor_id,r.record_id FROM skill_records r "
                "JOIN skill_record_sources s ON s.profile_id=r.profile_id "
                "AND s.record_id=r.record_id WHERE s.profile_id=? AND s.event_id=?",
                (profile_id, event_id),
            ).fetchall()
            for row in skill_rows:
                self._audit(profile_id, "forget_skill_source", _json([row["actor_id"], event_id]))
                self.connection.execute(
                    "DELETE FROM skill_records WHERE profile_id=? AND record_id=?",
                    (profile_id, row["record_id"]),
                )
            for row in self.connection.execute(
                "SELECT memory_id FROM memory_sources WHERE profile_id=? AND event_id=?",
                (profile_id, event_id),
            ).fetchall():
                self._delete_memory(profile_id, row[0])
            self.connection.execute(
                "DELETE FROM knowledge_cards WHERE card_id IN (SELECT card_id "
                "FROM knowledge_sources WHERE profile_id=? AND event_id=?)",
                (profile_id, event_id),
            )
            deleted = (
                self.connection.execute(
                    "DELETE FROM events WHERE profile_id=? AND event_id=?", (profile_id, event_id)
                ).rowcount
                > 0
            )
            if deleted:
                self._audit(profile_id, "delete_event", event_id)
            return deleted

    def apply_relationship_event(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str,
        event_id: str,
        *,
        session_id: str | None = None,
    ) -> bool:
        """Project a canonical local event using fixed conversational rules.

        The projection belongs to its source event's branch and game date.
        Applying an already visible ancestor source lazily does not invent an
        earlier event on the current branch or duplicate an inherited update.
        Narrative payloads and model score suggestions cannot supply deltas.
        Profile-scoped reset markers prevent replay across every branch, even
        after the retained canonical source is deleted and reimported.
        """
        protagonist = _protagonist(protagonist_id)
        if protagonist is None:
            raise ValueError("relationship updates require a specific protagonist")
        with self._lock, self.connection:
            actor = self._actor_session(profile_id, actor_id, session_id)
            event_row = self.connection.execute(
                "SELECT * FROM events WHERE profile_id=? AND event_id=?", (profile_id, event_id)
            ).fetchone()
            if event_row is None:
                raise ValueError("relationship change requires a canonical event")
            if event_row["protagonist_id"] != protagonist:
                raise ValueError("relationship source must identify the same protagonist")
            forgotten = self.connection.execute(
                "SELECT 1 FROM scrub_audit WHERE profile_id=? AND operation=? "
                "AND object_id=? LIMIT 1",
                (
                    profile_id,
                    "forget_relationship_source",
                    _json([actor_id, protagonist, event_id]),
                ),
            ).fetchone()
            if forgotten:
                return False
            data = json.loads(event_row["data"])
            self._sources(
                profile_id,
                branch_id,
                event_row["game_datetime"],
                [event_id],
                "directly_observed",
                protagonist,
            )
            event_type = data.get("event_type", data.get("type"))
            observers = data.get("observer_ids", data.get("payload", {}).get("observer_ids", []))
            if (
                event_type not in RELATIONSHIP_RULES
                or actor_id not in observers
                or data["confidence"] < 0.7
            ):
                return False
            if event_type == "threat_observed" and actor.get("flags", {}).get("no_crime_reactions"):
                return False
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO relationship_updates VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    profile_id,
                    actor_id,
                    protagonist,
                    event_row["branch_id"],
                    event_row["game_datetime"],
                    event_id,
                    event_type,
                    _json(RELATIONSHIP_RULES[event_type]),
                    session_id if actor["actor_kind"] == "ambient_ephemeral" else None,
                ),
            )
            return cursor.rowcount > 0

    def get_relationship(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str,
        game_datetime: str,
        *,
        session_id: str | None = None,
    ) -> dict:
        """Return bounded conversational state, never native faction allegiance."""
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        if protagonist is None:
            raise ValueError("relationship retrieval requires a specific protagonist")
        with self._lock:
            actor = self._actor_session(profile_id, actor_id, session_id)
            values = _relationship_baseline(actor)
            source_ids = []
            history = self.relationship_history(
                profile_id, branch_id, actor_id, protagonist, stamp, session_id=session_id
            )
            for update in history:
                for field, delta in update["effective_deltas"].items():
                    values[field] = max(
                        0 if field in {"familiarity", "fear"} else -1, min(1, values[field] + delta)
                    )
                source_ids.append(update["event_id"])
            return {
                **values,
                "actor_id": actor_id,
                "protagonist_id": protagonist,
                "source_event_ids": source_ids,
                "policy_version": RELATIONSHIP_POLICY_VERSION,
                "scope": "conversation_only",
            }

    def relationship_history(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str,
        game_datetime: str,
        *,
        session_id: str | None = None,
    ) -> list[dict]:
        """Audit fixed deltas with their original sources and current decay.

        Read through canonical events instead of trusting a freeform stored
        delta. Any inconsistent projection fails closed and requires repair.
        """
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        if protagonist is None:
            raise ValueError("relationship history requires a specific protagonist")
        with self._lock:
            self._actor_session(profile_id, actor_id, session_id)
            lineage = self._lineage(profile_id, branch_id, stamp)
            rows = self.connection.execute(
                "SELECT r.*,e.data AS source_data,e.branch_id AS source_branch,"
                "e.game_datetime AS source_time,e.protagonist_id AS source_protagonist "
                "FROM relationship_updates r JOIN events e "
                "ON e.profile_id=r.profile_id AND e.event_id=r.event_id "
                "WHERE r.profile_id=? AND r.actor_id=? AND r.protagonist_id=? "
                "ORDER BY r.game_datetime,r.event_id",
                (profile_id, actor_id, protagonist),
            ).fetchall()
            history = []
            current = datetime.fromisoformat(stamp)
            for row in rows:
                if (
                    row["branch_id"] not in lineage
                    or row["game_datetime"] > lineage[row["branch_id"]]
                    or row["session_id"] not in (None, session_id)
                ):
                    continue
                source = json.loads(row["source_data"])
                reason = row["reason"]
                rule = RELATIONSHIP_RULES.get(reason)
                observers = source.get(
                    "observer_ids", source.get("payload", {}).get("observer_ids", [])
                )
                if (
                    rule is None
                    or json.loads(row["deltas"]) != rule
                    or reason != source.get("event_type", source.get("type"))
                    or row["source_branch"] != row["branch_id"]
                    or row["source_time"] != row["game_datetime"]
                    or row["source_protagonist"] != protagonist
                    or actor_id not in observers
                    or source["provenance"] not in {"system_fact", "directly_observed"}
                    or source["confidence"] < 0.7
                ):
                    raise ValueError("relationship projection does not match its grounded policy")
                age_days = max(
                    0,
                    (current - datetime.fromisoformat(row["game_datetime"])).total_seconds()
                    / 86400,
                )
                decay = RELATIONSHIP_HALF_LIFE_DAYS.get(reason, {})
                effective = {
                    field: delta * (2 ** (-age_days / decay[field]) if field in decay else 1)
                    for field, delta in rule.items()
                }
                history.append(
                    {
                        "event_id": row["event_id"],
                        "source_event_ids": [row["event_id"]],
                        "branch_id": row["branch_id"],
                        "game_datetime": row["game_datetime"],
                        "actor_id": actor_id,
                        "protagonist_id": protagonist,
                        "reason": reason,
                        "provenance": source["provenance"],
                        "confidence": source["confidence"],
                        "deltas": dict(rule),
                        "effective_deltas": effective,
                        "half_life_days": dict(decay),
                        "policy_version": RELATIONSHIP_POLICY_VERSION,
                    }
                )
            return history

    def add_knowledge(
        self,
        profile_id: str,
        branch_id: str,
        game_datetime: str,
        text: str,
        source_event_ids: list[str],
        provenance: str,
        *,
        owner_id: str | None = None,
        scope: str = "actor",
        scope_id: str | None = None,
        protagonist_id: str | None = None,
        confidence: float = 1.0,
        expires_at: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        if not text.strip() or scope not in {"actor", "group", "region", "public"}:
            raise ValueError("valid knowledge text and scope required")
        if scope == "actor" and not owner_id:
            raise ValueError("actor knowledge needs an owner")
        if scope in {"group", "region"} and not scope_id:
            raise ValueError("scoped knowledge needs a scope_id")
        expiry = _date(expires_at) if expires_at else None
        if expiry is not None and expiry <= stamp:
            raise ValueError("knowledge expiry must follow its creation")
        with self._lock, self.connection:
            if owner_id:
                actor = self._actor_session(profile_id, owner_id, session_id)
                session_id = session_id if actor["actor_kind"] == "ambient_ephemeral" else None
            elif session_id:
                raise ValueError("session knowledge requires an owner")
            sources = self._sources(
                profile_id, branch_id, stamp, source_event_ids, provenance, protagonist
            )
            if owner_id:
                self._require_observer(owner_id, sources, provenance)
            if scope != "actor" and any(
                source.get("payload", {}).get("visibility_scope") != scope
                or source.get("payload", {}).get("scope_id") != scope_id
                for source in sources
            ):
                raise ValueError("private source events cannot become shared knowledge")
            confidence = min(
                _unit(confidence, "confidence"), *(source["confidence"] for source in sources)
            )
            self._touch(profile_id, branch_id, stamp)
            identifier = str(uuid4())
            self.connection.execute(
                "INSERT INTO knowledge_cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    profile_id,
                    owner_id,
                    scope,
                    scope_id,
                    protagonist,
                    branch_id,
                    stamp,
                    expiry,
                    text.strip(),
                    provenance,
                    _unit(confidence, "confidence"),
                    session_id,
                ),
            )
            self.connection.executemany(
                "INSERT INTO knowledge_sources VALUES(?,?,?)",
                [(identifier, profile_id, event_id) for event_id in source_event_ids],
            )
            return self._knowledge_dict(
                self.connection.execute(
                    "SELECT * FROM knowledge_cards WHERE card_id=?", (identifier,)
                ).fetchone()
            )

    def _knowledge_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["source_event_ids"] = [
            r[0]
            for r in self.connection.execute(
                "SELECT event_id FROM knowledge_sources WHERE card_id=? ORDER BY event_id",
                (row["card_id"],),
            )
        ]
        return data

    def retrieve_knowledge(
        self,
        profile_id: str,
        branch_id: str,
        actor_id: str,
        protagonist_id: str | None,
        game_datetime: str,
        *,
        group_ids: tuple[str, ...] = (),
        region_id: str | None = None,
        session_id: str | None = None,
        token_budget: int = 512,
    ) -> list[dict]:
        stamp, protagonist = _date(game_datetime), _protagonist(protagonist_id)
        with self._lock:
            self._actor_session(profile_id, actor_id, session_id)
            lineage = self._lineage(profile_id, branch_id, stamp)
            rows = self.connection.execute(
                "SELECT * FROM knowledge_cards WHERE profile_id=? "
                "AND (protagonist_id IS NULL OR protagonist_id=?) "
                "ORDER BY confidence DESC,game_datetime DESC,card_id",
                (profile_id, protagonist),
            ).fetchall()
            result, used = [], 0
            for row in rows:
                allowed = (
                    row["scope"] == "public"
                    or (row["scope"] == "actor" and row["owner_id"] == actor_id)
                    or (row["scope"] == "group" and row["scope_id"] in group_ids)
                    or (row["scope"] == "region" and row["scope_id"] == region_id)
                )
                if (
                    not allowed
                    or row["branch_id"] not in lineage
                    or row["game_datetime"] > lineage[row["branch_id"]]
                    or row["session_id"] not in (None, session_id)
                    or (row["expires_at"] and row["expires_at"] <= stamp)
                ):
                    continue
                cost = self.token_cost(row["text"])
                if used + cost <= token_budget:
                    result.append(self._knowledge_dict(row))
                    used += cost
            return result

    def delete_knowledge(self, profile_id: str, card_id: str) -> bool:
        with self._lock, self.connection:
            changed = (
                self.connection.execute(
                    "DELETE FROM knowledge_cards WHERE profile_id=? AND card_id=?",
                    (profile_id, card_id),
                ).rowcount
                > 0
            )
            if changed:
                self._audit(profile_id, "delete_knowledge", card_id)
            return changed

    def reset_actor(self, profile_id: str, actor_id: str) -> None:
        """Forget this actor's derived state across all branches in one profile.

        Preserve shared canonical events for other actors. Retain only profile,
        actor, protagonist, and source identifiers as replay suppression metadata.
        Reset markers contain no dialogue or event payload and survive reopening.
        """
        with self._lock, self.connection:
            # Reset also suppresses raw observations that have not yet generated
            # a candidate. Shared canonical evidence remains available to others.
            for row in self.connection.execute(
                "SELECT DISTINCT e.event_id FROM events e, "
                "json_each(CASE WHEN json_type(e.data,'$.observer_ids')='array' "
                "THEN json_extract(e.data,'$.observer_ids') "
                "WHEN json_type(e.data,'$.payload.observer_ids')='array' "
                "THEN json_extract(e.data,'$.payload.observer_ids') ELSE '[]' END) observers "
                "WHERE e.profile_id=? AND observers.value=?",
                (profile_id, actor_id),
            ).fetchall():
                self._audit(profile_id, "forget_skill_source", _json([actor_id, row[0]]))
                self._audit(profile_id, "forget_social_source", _json([actor_id, row[0]]))
            for row in self.connection.execute(
                "SELECT DISTINCT s.event_id FROM skill_record_sources s "
                "JOIN skill_records r ON r.profile_id=s.profile_id AND r.record_id=s.record_id "
                "WHERE r.profile_id=? AND r.actor_id=?",
                (profile_id, actor_id),
            ).fetchall():
                self._audit(profile_id, "forget_skill_source", _json([actor_id, row[0]]))
            self.connection.execute(
                "DELETE FROM skill_records WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            )
            # Keep only source identifiers as replay suppression metadata.
            # A retained canonical event must not resurrect a reset relationship.
            for row in self.connection.execute(
                "SELECT protagonist_id,event_id FROM relationship_updates "
                "WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            ).fetchall():
                self._audit(
                    profile_id,
                    "forget_relationship_source",
                    _json([actor_id, row["protagonist_id"], row["event_id"]]),
                )
            for row in self.connection.execute(
                "SELECT memory_id FROM memories WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            ).fetchall():
                self._delete_memory(profile_id, row[0])
            self.connection.execute(
                "DELETE FROM memory_derivations WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            )
            self.connection.execute(
                "DELETE FROM relationship_updates WHERE profile_id=? AND actor_id=?",
                (profile_id, actor_id),
            )
            self.connection.execute(
                "DELETE FROM knowledge_cards WHERE profile_id=? AND owner_id=?",
                (profile_id, actor_id),
            )
            self._audit(profile_id, "reset_actor", actor_id)

    def delete_profile(self, profile_id: str) -> bool:
        """Remove profile content while retaining content-free scrub audit identifiers."""
        with self._lock, self.connection:
            # Delete projections before their branch FK targets.
            self.connection.execute("DELETE FROM skill_records WHERE profile_id=?", (profile_id,))
            self.connection.execute("DELETE FROM skill_versions WHERE profile_id=?", (profile_id,))
            self.connection.execute("DELETE FROM memories WHERE profile_id=?", (profile_id,))
            self.connection.execute(
                "DELETE FROM memory_derivations WHERE profile_id=?", (profile_id,)
            )
            self.connection.execute("DELETE FROM knowledge_cards WHERE profile_id=?", (profile_id,))
            self.connection.execute("DELETE FROM events WHERE profile_id=?", (profile_id,))
            deleted = (
                self.connection.execute(
                    "DELETE FROM profiles WHERE profile_id=?", (profile_id,)
                ).rowcount
                > 0
            )
            if deleted:
                self._audit(profile_id, "delete_profile", profile_id)
            return deleted

    def export_profile(self, profile_id: str) -> dict:
        with self._lock:
            profile = self.connection.execute(
                "SELECT * FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if profile is None:
                raise ValueError("unknown profile")
            return {
                "schema_version": 1,
                "profile": dict(profile),
                "actors": [
                    json.loads(r[0])
                    for r in self.connection.execute(
                        "SELECT data FROM actors WHERE profile_id=?", (profile_id,)
                    )
                ],
                "branches": [
                    dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM branches WHERE profile_id=?", (profile_id,)
                    )
                ],
                "events": [
                    json.loads(r[0])
                    for r in self.connection.execute(
                        "SELECT data FROM events WHERE profile_id=?", (profile_id,)
                    )
                ],
                "memories": [
                    self._memory_dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM memories WHERE profile_id=?", (profile_id,)
                    )
                ],
                "knowledge_cards": [
                    self._knowledge_dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM knowledge_cards WHERE profile_id=?", (profile_id,)
                    )
                ],
                "relationships": [
                    dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM relationship_updates WHERE profile_id=?", (profile_id,)
                    )
                ],
                "skill_versions": [
                    dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM skill_versions WHERE profile_id=?", (profile_id,)
                    )
                ],
                "skill_records": [
                    dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM skill_records WHERE profile_id=?", (profile_id,)
                    )
                ],
                "skill_sources": [
                    dict(r)
                    for r in self.connection.execute(
                        "SELECT * FROM skill_record_sources WHERE profile_id=?", (profile_id,)
                    )
                ],
            }

    def list_profiles(self) -> list[dict]:
        """Administrative metadata only; this does not assemble NPC context."""
        with self._lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    "SELECT profile_id,save_slot,created_at,active_branch "
                    "FROM profiles ORDER BY profile_id"
                )
            ]

    def list_memories(
        self, profile_id: str, actor_id: str | None = None, *, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """Administrative history across branches. Never use this in NPC prompts."""
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid administrative history window")
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM memories WHERE profile_id=? AND (? IS NULL OR actor_id=?) "
                "ORDER BY game_datetime DESC,memory_id LIMIT ? OFFSET ?",
                (profile_id, actor_id, actor_id, limit, offset),
            )
            return [self._memory_dict(row) for row in rows]

    def list_branches(self, profile_id: str) -> list[dict]:
        with self._lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM branches WHERE profile_id=? ORDER BY fork_time,branch_id",
                    (profile_id,),
                )
            ]

    def backup(self, destination: str | Path) -> None:
        if self.path != ":memory:" and Path(destination).resolve() == Path(self.path).resolve():
            raise ValueError("backup destination cannot be the live database")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(destination)) as target:
            self.connection.backup(target)
