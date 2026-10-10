"""Read social preferences from retained private sources, without global relationships.

Own explicit decisions express a temporary scene boundary. Exact confirmed hearing
supports familiarity with the teller, never trust in the content or its subjects.
No derived counters are stored, so corrections, deletion, rewind, and decay apply
on every read. Callers must keep each returned owner's history private.
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta

from .storage import _date, _protagonist

MAX_OWNERS = 128
MAX_ANCHORS_PER_OWNER = 128
# 128 owners with 100 recent decision anchors each use about 3.2M steps.
# Keep a fixed ceiling even when retained history exceeds the returned limits.
SQL_STEP_BUDGET = 4_000_000
HISTORY_DAYS = 56
CONTACT_HALF_LIFE_DAYS = 7
BOUNDARY_HALF_LIFE_DAYS = 1
BOUNDARY_WINDOW_SECONDS = 3600
DECISIONS = {"accept", "refuse", "continue", "withdraw"}
NEGATIVE_DECISIONS = {"refuse", "withdraw"}


def decision_memory_text(decision):
    if decision not in DECISIONS:
        raise ValueError("unsupported social decision")
    return f"I chose to {decision} this social conversation."


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 128


def _unit(value):
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1


def _empty():
    return {
        "last_boundary_at": None,
        "recent_boundary_count": 0,
        "boundary_weight": 0,
        "own_decision_source_event_ids": [],
        "peers": {},
    }


class SocialAdaptation:
    def __init__(self, repository, *, sql_step_budget=SQL_STEP_BUDGET):
        if type(sql_step_budget) is not int or sql_step_budget < 1000:
            raise ValueError("social query budget requires at least 1000 SQLite steps")
        self.repo = repository
        self.sql_step_budget = sql_step_budget

    def read(
        self, profile_id, branch_id, protagonist_id, game_datetime, actor_ids, *, session_id=None
    ):
        owners = tuple(dict.fromkeys(actor_ids))
        if (
            isinstance(actor_ids, str)
            or not 1 <= len(owners) <= MAX_OWNERS
            or any(not _identifier(owner) for owner in owners)
        ):
            raise ValueError("social history requires 1 to 128 distinct actor identifiers")
        stamp = _date(
            game_datetime.isoformat() if isinstance(game_datetime, datetime) else game_datetime
        )
        protagonist = _protagonist(protagonist_id)
        if protagonist is None:
            raise ValueError("social history requires a specific protagonist")
        current = datetime.fromisoformat(stamp)
        cutoff = _date((current - timedelta(days=HISTORY_DAYS)).isoformat())
        result = {owner: _empty() for owner in owners}
        with self.repo._lock:
            lineage = self.repo._lineage(profile_id, branch_id, stamp)
            branch_filter = " OR ".join(
                "(m.branch_id=? AND m.game_datetime<=?)" for _ in lineage
            )
            # One bounded source read for all owners. Speaker data validates an
            # exact hearing receipt but never enters another actor's prompt.
            # CROSS JOIN preserves indexed owner-first lookup. SQLite otherwise
            # starts from every event in the profile, including unrelated actors.
            sql = (
                "WITH candidates AS (SELECT m.actor_id,m.memory_id,m.text,m.provenance,"
                "m.confidence AS memory_confidence,m.decay_days,e.event_id,e.data,"
                "e.game_datetime,e.branch_id,e.rowid AS source_order,"
                "speaker.data AS speaker_data,speaker.branch_id AS speaker_branch,"
                "speaker.game_datetime AS speaker_time,"
                "ROW_NUMBER() OVER (PARTITION BY m.actor_id,"
                "(json_extract(e.data,'$.event_type')='social.quote_delivered') "
                "ORDER BY e.game_datetime DESC,e.rowid DESC) AS source_rank "
                "FROM memories m INDEXED BY memory_actor "
                "CROSS JOIN actors a ON a.profile_id=m.profile_id "
                "AND a.actor_id=m.actor_id "
                "CROSS JOIN memory_sources s "
                "ON s.profile_id=m.profile_id AND s.memory_id=m.memory_id "
                "CROSS JOIN events e ON e.profile_id=s.profile_id AND e.event_id=s.event_id "
                "LEFT JOIN events speaker ON speaker.profile_id=e.profile_id "
                "AND speaker.event_id=json_extract(e.data,'$.payload.heard_delivery_event_id') "
                "WHERE m.profile_id=? AND m.actor_id IN ("
                + ",".join("?" for _ in owners)
                + ") AND m.protagonist_id=? AND e.protagonist_id=m.protagonist_id "
                "AND m.visibility_scope='private' AND m.branch_id=e.branch_id "
                "AND m.game_datetime=e.game_datetime AND m.game_datetime>=? "
                "AND (" + branch_filter + ") "
                "AND (m.session_id IS NULL OR m.session_id=?) "
                "AND (a.actor_kind!='ambient_ephemeral' OR a.session_id=?) "
                "AND (m.expires_at IS NULL OR m.expires_at>?) "
                "AND m.memory_id=e.event_id||':memory' "
                "AND (SELECT COUNT(*) FROM memory_sources ms WHERE ms.memory_id=m.memory_id)=1 "
                "AND json_extract(e.data,'$.event_type') IN "
                "('social.accept','social.refuse','social.continue','social.withdraw',"
                "'social.quote_delivered') "
                "AND ((m.provenance='reported' "
                "AND json_extract(e.data,'$.provenance')='reported' "
                "AND json_extract(e.data,'$.event_type')='social.quote_delivered' "
                "AND speaker.event_id IS NOT NULL) "
                "OR (m.provenance='system_fact' "
                "AND json_extract(e.data,'$.provenance')='system_fact' "
                "AND json_extract(e.data,'$.event_type')!='social.quote_delivered')) "
                "AND NOT EXISTS (SELECT 1 FROM scrub_audit scrub "
                "WHERE scrub.profile_id=m.profile_id "
                "AND ((scrub.operation='delete_memory' AND scrub.object_id=m.memory_id) "
                "OR (scrub.operation='delete_event' "
                "AND scrub.object_id IN (e.event_id,speaker.event_id)) "
                "OR (scrub.operation='forget_social_source' "
                "AND scrub.object_id=json_array(m.actor_id,e.event_id)))) "
                ") SELECT * FROM candidates WHERE source_rank<=? "
                "ORDER BY game_datetime,source_order"
            )
            parameters = (
                    profile_id, *owners, protagonist, cutoff,
                    *(item for pair in lineage.items() for item in pair),
                    session_id, session_id, stamp, MAX_ANCHORS_PER_OWNER // 2,
            )
            # LIMIT bounds returned anchors, not all work needed by a window
            # query. A connection-local VM budget also bounds dense-history scans.
            steps = 0

            def budget_exhausted():
                nonlocal steps
                steps += 1000
                return steps >= self.sql_step_budget

            self.repo.connection.set_progress_handler(budget_exhausted, 1000)
            try:
                rows = self.repo.connection.execute(sql, parameters).fetchall()
            except sqlite3.OperationalError as exc:
                if steps >= self.sql_step_budget:
                    raise ValueError("social history query exceeded its work budget") from exc
                raise
            finally:
                self.repo.connection.set_progress_handler(None, 0)
            decisions = {owner: [] for owner in owners}
            contacts = {owner: {} for owner in owners}
            for row in rows:
                source = json.loads(row["data"])
                payload = source.get("payload", {})
                owner = row["actor_id"]
                if not self._private_source(source, payload, owner):
                    continue
                age = (current - datetime.fromisoformat(row["game_datetime"])).total_seconds()
                if age < 0 or not _unit(row["memory_confidence"]):
                    continue
                if source.get("event_type") == "social.quote_delivered":
                    contact = self._contact(row, source, payload, age)
                    if contact:
                        peer, item = contact
                        contacts[owner].setdefault(peer, []).append(item)
                elif self._decision(row, source, payload):
                    decisions[owner].append((row, payload, age))
            for owner in owners:
                self._boundaries(result[owner], decisions[owner])
                result[owner]["peers"] = {
                    peer: self._familiarity(items) for peer, items in contacts[owner].items()
                }
        return result

    @staticmethod
    def _private_source(source, payload, owner):
        return bool(
            isinstance(payload, dict)
            and source.get("observer_ids") == [owner]
            and _unit(source.get("confidence"))
            and payload.get("development_attestation") is True
            and payload.get("visibility_scope") == "actor"
            and payload.get("scope_id") == owner
            and _identifier(payload.get("scene_id"))
        )

    @staticmethod
    def _decision(row, source, payload):
        choice = payload.get("decision")
        participants = payload.get("participant_ids")
        return bool(
            isinstance(choice, str) and choice in DECISIONS
            and source.get("event_type") == "social." + choice
            and source.get("provenance") == row["provenance"] == "system_fact"
            and payload.get("decision_owner") == row["actor_id"]
            and payload.get("decision_valid") is True
            and payload.get("decision_scope") == "scene"
            and payload.get("decision_phase") == (
                "invitation" if choice in {"accept", "refuse"} else "participation"
            )
            and isinstance(participants, list)
            and 2 <= len(participants) <= 3
            and all(_identifier(actor) for actor in participants)
            and len(set(participants)) == len(participants)
            and row["actor_id"] in participants
            and payload.get("initiator_id") in participants
            and _identifier(payload.get("session_id"))
            and row["text"] == decision_memory_text(choice)
            and min(source["confidence"], row["memory_confidence"]) >= 0.5
        )

    @staticmethod
    def _contact(row, source, payload, age):
        peer, text = payload.get("teller_id"), payload.get("text")
        if (
            not _identifier(peer) or peer == row["actor_id"]
            or not isinstance(text, str) or not 1 <= len(text) <= 1000
            or source.get("provenance") != "reported" or row["provenance"] != "reported"
            or row["text"] != f"{peer} told me: {text}"
            or not row["speaker_data"]
            or row["speaker_branch"] != row["branch_id"]
            or row["speaker_time"] != row["game_datetime"]
        ):
            return None
        speaker = json.loads(row["speaker_data"])
        spoken = speaker.get("payload", {})
        if (
            not SocialAdaptation._private_source(speaker, spoken, peer)
            or speaker.get("event_type") != "social.quote_delivered"
            or speaker.get("provenance") != "directly_observed"
            or speaker.get("protagonist_id") != source.get("protagonist_id")
            or any(
                spoken.get(field) != payload.get(field)
                for field in (
                    "text", "scene_id", "utterance_id", "delivery_receipt_id", "teller_id",
                    "sampled_at", "reported_event_id", "retelling_lineage", "session_id",
                )
            )
            or not all(_identifier(payload.get(field)) for field in (
                "utterance_id", "delivery_receipt_id", "heard_delivery_event_id"
            ))
        ):
            return None
        half_life = CONTACT_HALF_LIFE_DAYS
        if row["decay_days"] is not None:
            if not math.isfinite(row["decay_days"]) or row["decay_days"] <= 0:
                return None
            half_life = min(half_life, row["decay_days"])
        weight = min(source["confidence"], row["memory_confidence"], speaker["confidence"])
        weight *= 2 ** (-age / (86400 * half_life))
        return peer, {
            "text": text,
            "event_id": row["event_id"],
            "memory_id": row["memory_id"],
            "teller_id": peer,
            "scene_id": payload["scene_id"],
            "session_id": payload.get("session_id"),
            "game_datetime": row["game_datetime"],
            "confidence": weight,
            "is_retelling": bool(
                payload.get("reported_event_id") or payload.get("retelling_lineage")
            ),
        }

    @staticmethod
    def _boundaries(result, decisions):
        if not decisions:
            return
        last_row, last, age = decisions[-1]
        result["own_decision_source_event_ids"] = [last_row["event_id"]]
        if last["decision"] not in NEGATIVE_DECISIONS:
            return
        result["last_boundary_at"] = last_row["game_datetime"]
        result["boundary_weight"] = round(
            last_row["memory_confidence"] * 2 ** (-age / (86400 * BOUNDARY_HALF_LIFE_DAYS)), 6
        )
        scenes = set()
        for _, payload, decision_age in reversed(decisions):
            if payload["decision"] not in NEGATIVE_DECISIONS:
                break
            if decision_age <= BOUNDARY_WINDOW_SECONDS:
                scenes.add((payload["session_id"], payload["scene_id"]))
        result["recent_boundary_count"] = min(3, len(scenes))

    @staticmethod
    def _familiarity(items):
        encounters = {}
        for item in items:
            scene = item["session_id"], item["scene_id"]
            encounters[scene] = max(encounters.get(scene, 0), item["confidence"])
        return {
            "familiarity": round(min(1, 0.1 * sum(encounters.values())), 6),
            "confirmed_scene_count": sum(weight >= 0.5 for weight in encounters.values()),
            "last_contact_at": items[-1]["game_datetime"],
            "source_event_ids": [item["event_id"] for item in items[-8:]],
            "recent_quotes": [
                {**item, "confidence": round(item["confidence"], 6)}
                for item in reversed(items) if item["confidence"] >= 0.5
            ][:2],
        }
