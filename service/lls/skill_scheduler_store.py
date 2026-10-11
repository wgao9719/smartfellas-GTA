"""Content-free durable episode claims for bounded development skill practice."""

import hashlib
import json

from .storage import _date, _protagonist


class SkillEpisodeStore:
    def __init__(self, skills):
        self.skills = skills
        self.repo = skills.repo
        with self.repo._lock, self.repo.connection:
            self.repo.connection.executescript("""
                CREATE TABLE IF NOT EXISTS skill_experiences (
                    profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
                    actor_id TEXT NOT NULL, protagonist_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
                    event_id TEXT NOT NULL, input_event_id TEXT NOT NULL,
                    action_id TEXT NOT NULL, status TEXT NOT NULL,
                    episode_id TEXT,
                    PRIMARY KEY(profile_id,actor_id,event_id)
                );
                CREATE TABLE IF NOT EXISTS skill_episodes (
                    profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
                    episode_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                    protagonist_id TEXT NOT NULL, branch_id TEXT NOT NULL,
                    game_datetime TEXT NOT NULL, session_id TEXT NOT NULL,
                    action_id TEXT NOT NULL, source_ids TEXT NOT NULL,
                    expires_at TEXT NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    candidate_requests INTEGER NOT NULL DEFAULT 0,
                    trials INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(profile_id,episode_id)
                );
            """)
            # Claimed work cannot regain authority after a process restart. Keep
            # its consumed evidence and counters, but unblock genuinely new needs.
            self.repo.connection.execute(
                "UPDATE skill_episodes SET state='abandoned',reason='scheduler_restart' "
                "WHERE state IN ('queued','running')"
            )

    def observe(
        self,
        identity,
        stamp,
        event_id,
        input_id,
        action_id,
        status,
        *,
        selected,
        now,
        expires_at,
        max_queued,
        max_records,
    ):
        """Consume each external experience once, including refused queue entries."""
        with self.skills._transaction():
            identity, stamp = self.skills._scope(identity, stamp)
            self.skills._sources(
                identity, stamp, [event_id, input_id, event_id.rsplit(":", 1)[0] + ":started"]
            )
            connection = self.repo.connection
            connection.execute(
                "UPDATE skill_episodes SET state='expired',reason='episode_deadline' "
                "WHERE profile_id=? AND state IN ('queued','running') AND expires_at<=?",
                (identity.profile_id, now),
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM skill_experiences WHERE profile_id=?",
                (identity.profile_id,),
            ).fetchone()[0]
            if count >= max_records:
                return None
            inserted = connection.execute(
                "INSERT OR IGNORE INTO skill_experiences VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                (
                    identity.profile_id,
                    identity.actor_id,
                    _protagonist(identity.protagonist_id),
                    identity.branch_id,
                    stamp,
                    event_id,
                    input_id,
                    action_id,
                    status,
                ),
            ).rowcount
            if not inserted:
                return None
            lineage = self.repo._lineage(identity.profile_id, identity.branch_id, stamp)
            rows = connection.execute(
                "SELECT * FROM skill_experiences WHERE profile_id=? AND actor_id=? "
                "AND protagonist_id=? AND action_id=? AND episode_id IS NULL "
                "ORDER BY game_datetime,event_id",
                (
                    identity.profile_id,
                    identity.actor_id,
                    _protagonist(identity.protagonist_id),
                    action_id,
                ),
            ).fetchall()
            experiences, inputs = [], set()
            for row in rows:
                if (
                    row["branch_id"] not in lineage
                    or row["game_datetime"] > lineage[row["branch_id"]]
                    or row["input_event_id"] in inputs
                ):
                    continue
                try:
                    self.skills._sources(
                        identity,
                        stamp,
                        [
                            row["event_id"],
                            row["input_event_id"],
                            row["event_id"].rsplit(":", 1)[0] + ":started",
                        ],
                    )
                except ValueError:
                    continue
                experiences.append(row)
                inputs.add(row["input_event_id"])
            current = next((row for row in experiences if row["event_id"] == event_id), None)
            if current is None or (status == "completed" and not selected and len(experiences) < 2):
                return None
            chosen = [current] if status == "failed" or selected else experiences[-2:]
            sources = list(
                dict.fromkeys(
                    source
                    for row in chosen
                    for source in (
                        row["input_event_id"],
                        row["event_id"].rsplit(":", 1)[0] + ":started",
                        row["event_id"],
                    )
                )
            )
            digest = hashlib.sha256(
                json.dumps(
                    [
                        identity.profile_id,
                        identity.branch_id,
                        identity.protagonist_id,
                        identity.actor_id,
                        action_id,
                        sources,
                    ],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:32]
            episode_id = "skill_episode_" + digest
            pending = connection.execute(
                "SELECT COUNT(*) FROM skill_episodes WHERE profile_id=? "
                "AND state IN ('queued','running')",
                (identity.profile_id,),
            ).fetchone()[0]
            same_actor = connection.execute(
                "SELECT 1 FROM skill_episodes WHERE profile_id=? AND actor_id=? "
                "AND state IN ('queued','running') LIMIT 1",
                (identity.profile_id, identity.actor_id),
            ).fetchone()
            state = "suppressed" if pending >= max_queued or same_actor else "queued"
            connection.execute(
                "INSERT INTO skill_episodes(profile_id,episode_id,actor_id,protagonist_id,"
                "branch_id,game_datetime,session_id,action_id,source_ids,expires_at,state) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity.profile_id,
                    episode_id,
                    identity.actor_id,
                    _protagonist(identity.protagonist_id),
                    identity.branch_id,
                    stamp,
                    identity.session_id,
                    action_id,
                    json.dumps(sources),
                    expires_at,
                    state,
                ),
            )
            for row in chosen:
                connection.execute(
                    "UPDATE skill_experiences SET episode_id=? WHERE profile_id=? "
                    "AND actor_id=? AND event_id=?",
                    (episode_id, identity.profile_id, identity.actor_id, row["event_id"]),
                )
            return {
                "episode_id": episode_id,
                "source_event_ids": sources,
                "action_id": action_id,
                "state": state,
            }

    def claim(self, identity, episode_id):
        with self.repo._lock, self.repo.connection:
            return (
                self.repo.connection.execute(
                    "UPDATE skill_episodes SET state='running',attempts=attempts+1 "
                    "WHERE profile_id=? AND episode_id=? AND state='queued'",
                    (identity.profile_id, episode_id),
                ).rowcount
                == 1
            )

    def reserve(self, identity, episode_id, field, limit):
        if field not in {"candidate_requests", "trials"}:
            raise ValueError("unknown episode reservation")
        with self.repo._lock, self.repo.connection:
            return (
                self.repo.connection.execute(
                    f"UPDATE skill_episodes SET {field}={field}+1 "
                    f"WHERE profile_id=? AND episode_id=? AND state='running' AND {field}<?",
                    (identity.profile_id, episode_id, limit),
                ).rowcount
                == 1
            )

    def finish(self, identity, episode_id, status, reason):
        with self.repo._lock, self.repo.connection:
            self.repo.connection.execute(
                "UPDATE skill_episodes SET state=?,reason=? WHERE profile_id=? AND episode_id=?",
                (status, reason[:120], identity.profile_id, episode_id),
            )

    def attempts(self, identity):
        with self.repo._lock:
            return self.repo.connection.execute(
                "SELECT COALESCE(SUM(attempts),0) FROM skill_episodes "
                "WHERE profile_id=? AND actor_id=?",
                (identity.profile_id, identity.actor_id),
            ).fetchone()[0]

    def next_version(self, profile_id, skill_id):
        with self.repo._lock:
            return self.repo.connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM skill_versions "
                "WHERE profile_id=? AND skill_id=?",
                (profile_id, skill_id),
            ).fetchone()[0]

    def current_sources(self, identity, stamp, sources):
        with self.repo._lock:
            stamp = _date(stamp)
            self.skills._sources(identity, stamp, sources)
            # Deletion before the first candidate must also invalidate its episode.
            deleted = self.repo.connection.execute(
                "SELECT object_id FROM scrub_audit WHERE profile_id=? AND operation='delete_event'",
                (identity.profile_id,),
            ).fetchall()
            if set(sources).intersection(row[0] for row in deleted):
                raise ValueError("episode source was deleted")
