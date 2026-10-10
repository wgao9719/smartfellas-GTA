"""Actor-private social projections built only from decisions and delivered words."""

import hashlib
import json

from .context import build_context
from .social_adaptation import SocialAdaptation, decision_memory_text
from .storage import _date


class SocialStore:
    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def event_id(*parts):
        return "social_" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def _source_deleted(self, profile_id, event_id):
        return self.repo.connection.execute(
            "SELECT 1 FROM scrub_audit WHERE profile_id=? "
            "AND operation='delete_event' AND object_id=? LIMIT 1",
            (profile_id, event_id),
        ).fetchone() is not None

    def _projection_suppressed(self, profile_id, owner, event_id):
        """Call only under repo._lock, then retain the lock through both writes."""
        return self.repo.connection.execute(
            "SELECT 1 FROM scrub_audit WHERE profile_id=? AND ("
            "(operation='delete_event' AND object_id=?) OR "
            "(operation='delete_memory' AND object_id=?) OR "
            "(operation='forget_social_source' AND object_id=json_array(?,?))) LIMIT 1",
            (profile_id, event_id, event_id + ":memory", owner, event_id),
        ).fetchone() is not None

    def decision(self, start, owner, decision, event_id, game_datetime):
        if owner not in start.participant_bindings or decision not in {
            "accept", "refuse", "continue", "withdraw"
        }:
            raise ValueError("social decision requires its actual participant owner")
        event = {
            "event_id": event_id,
            "event_type": "social." + decision,
            "game_datetime": game_datetime.isoformat(),
            "protagonist_id": start.protagonist_id,
            "observer_ids": [owner],
            "provenance": "system_fact",
            "confidence": 1,
            "payload": {
                "scene_id": start.scene_id,
                "session_id": start.session_id,
                "decision_owner": owner,
                "initiator_id": start.initiator_id,
                "participant_ids": list(start.participant_bindings),
                "decision": decision,
                "decision_phase": (
                    "invitation" if decision in {"accept", "refuse"} else "participation"
                ),
                "decision_valid": True,
                "decision_scope": "scene",
                "development_attestation": True,
                "visibility_scope": "actor",
                "scope_id": owner,
            },
        }
        with self.repo._lock:
            if self._projection_suppressed(start.profile_id, owner, event_id):
                return
            self.repo.record_event(start.profile_id, start.branch_id, event)
            self.repo.add_memory(
                start.profile_id,
                start.branch_id,
                owner,
                start.protagonist_id,
                game_datetime.isoformat(),
                decision_memory_text(decision),
                [event_id],
                "system_fact",
                session_id=start.session_id,
                memory_id=event_id + ":memory",
            )

    def retelling_source(self, start, actor_id, event_id, game_datetime):
        """Return only a source the speaker actually received and still remembers."""
        with self.repo._lock:
            protagonist = "player:" + start.protagonist_id
            stamp = _date(game_datetime.isoformat())
            source = self.repo._sources(
                start.profile_id,
                start.branch_id,
                stamp,
                [event_id],
                "reported",
                protagonist,
            )[0]
            self.repo._require_observer(actor_id, [source], "reported")
            lineage = self.repo._lineage(start.profile_id, start.branch_id, stamp)
            rows = self.repo.connection.execute(
                "SELECT m.* FROM memories m JOIN memory_sources s ON s.memory_id=m.memory_id "
                "WHERE m.profile_id=? AND m.actor_id=? AND s.event_id=? "
                "AND (m.protagonist_id IS NULL OR m.protagonist_id=?)",
                (start.profile_id, actor_id, event_id, protagonist),
            ).fetchall()
            remembered = [
                row
                for row in rows
                if row["branch_id"] in lineage
                and row["game_datetime"] <= lineage[row["branch_id"]]
                and row["session_id"] in (None, start.session_id)
                and (not row["expires_at"] or row["expires_at"] > stamp)
                and row["visibility_scope"] == "private"
            ]
            if not remembered:
                raise ValueError("speaker has no retained memory of that source")
            payload = source.get("payload", {})
            if actor_id not in source.get("observer_ids", payload.get("observer_ids", [])):
                raise ValueError("speaker did not receive those source words")
            if payload.get("visibility_scope") == "actor" and payload.get("scope_id") != actor_id:
                raise ValueError("source words belong to another actor")
            text = payload.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 1000:
                raise ValueError("retelling requires exact bounded source words")
            lineage = payload.get("retelling_lineage", [])
            if not isinstance(lineage, list) or len(lineage) >= 8:
                raise ValueError("retelling lineage is unavailable or exceeds the hop limit")
            if any(not isinstance(value, str) or len(value) > 128 for value in lineage):
                raise ValueError("invalid retelling lineage")
            return {
                "event_id": event_id,
                "text": text,
                "teller_id": payload.get("teller_id"),
                "confidence": min(
                    0.7, source["confidence"], max(r["confidence"] for r in remembered)
                ),
                "lineage": [*lineage, event_id],
            }

    def context(
        self, start, actor_id, environment, *, phase, heard, retelling, byte_budget,
        memories=None, memory_index=None,
    ):
        actor = self.repo.get_actor(start.profile_id, actor_id)
        stamp = environment.game_datetime.isoformat()
        if memories is None:
            memories = self.repo.retrieve_memories(
                start.profile_id, start.branch_id, actor_id, start.protagonist_id, stamp,
                "conversation acquaintances social", token_budget=384,
            )
        elif memory_index is not None:
            memories = memory_index.revalidate_memories(
                start.profile_id, start.branch_id, actor_id, start.protagonist_id, stamp,
                "conversation acquaintances social", memories=memories,
                session_id=start.session_id,
            )
        knowledge = self.repo.retrieve_knowledge(
            start.profile_id,
            start.branch_id,
            actor_id,
            start.protagonist_id,
            stamp,
            group_ids=tuple(actor.get("group_ids", [])[:16]),
            region_id=start.area_id,
            token_budget=256,
        )
        relationship = self.repo.get_relationship(
            start.profile_id,
            start.branch_id,
            actor_id,
            start.protagonist_id,
            stamp,
        )
        history = SocialAdaptation(self.repo).read(
            start.profile_id, start.branch_id, start.protagonist_id, stamp,
            [actor_id], session_id=start.session_id,
        ).get(actor_id, {})
        contacts = {
            other: {
                "familiarity": value["familiarity"],
                "confirmed_scene_count": value["confirmed_scene_count"],
                "last_contact_at": value["last_contact_at"],
                "source_event_ids": value["source_event_ids"][:4],
            }
            for other, value in history.get("peers", {}).items()
            if other in start.participant_bindings and other != actor_id
        }
        # A callback uses only this actor's retained hearing of a current partner.
        # Retellings cannot become personal shared experiences or a new topic fact.
        topics = [
            quote
            for other, value in history.get("peers", {}).items()
            if other in contacts and value["confirmed_scene_count"] >= 2
            for quote in value["recent_quotes"]
            if not quote["is_retelling"]
            and (quote["session_id"], quote["scene_id"]) != (start.session_id, start.scene_id)
            and len(quote["text"]) <= 240
        ]
        topics.sort(key=lambda quote: (quote["game_datetime"], quote["event_id"]), reverse=True)
        topic = topics[0] if topics else None
        social = {
            "phase": phase,
            "scene_id": start.scene_id,
            "decision_owner": actor_id,
            "initiator_id": start.initiator_id,
            "participant_ids": list(start.participant_bindings),
            "heard": list(heard[-4:]),
            "retell_text": retelling["text"] if retelling else None,
            "retell_source_id": retelling["event_id"] if retelling else None,
            "experience": {
                "contacts": contacts,
                "own_last_boundary_at": history.get("last_boundary_at"),
                "own_decision_source_event_ids": history.get(
                    "own_decision_source_event_ids", []
                )[:4],
            },
            "topic_continuation": topic if phase == "turn" and not retelling else None,
        }
        prompt = {
            "invitation": 'Decide only for yourself. Return exactly {"decision":"accept"} '
            'or {"decision":"refuse"}. You may refuse this short conversation.',
            "participation": 'Decide only for yourself. Return exactly {"decision":"continue"} '
            'or {"decision":"withdraw"}. You may leave at any time.',
            "turn": "Speak briefly to the other NPCs using only your own context. "
            "Do not decide another person's acceptance, friendship, or forgiveness. "
            "Familiarity describes only your own confirmed hearing, not mutual friendship. "
            "A topic_continuation is something you heard its teller say. You may ask about it "
            "without claiming the statement is true or that others know it. "
            "If retell_text is present, reproduce those exact words and nothing else.",
        }[phase]
        while len(json.dumps(social, ensure_ascii=False).encode()) > byte_budget - 3072:
            if social["heard"]:
                social["heard"].pop(0)
            elif social["topic_continuation"] is not None:
                social["topic_continuation"] = None
            elif social["experience"]["contacts"]:
                social["experience"]["contacts"].pop(next(iter(social["experience"]["contacts"])))
            elif social["experience"]["own_decision_source_event_ids"]:
                social["experience"]["own_decision_source_event_ids"].clear()
            else:
                raise ValueError("social metadata exceeds context budget")
        reserved = len(json.dumps(social, ensure_ascii=False).encode()) + 64
        context, _ = build_context(
            actor=actor,
            snapshot={"area_id": start.area_id},
            world={"online": False, "mission_active": False},
            memories=memories,
            knowledge=knowledge,
            relationship=relationship,
            input_text=prompt,
            eligible=[],
            protagonist_id=start.protagonist_id,
            game_datetime=stamp,
            byte_budget=byte_budget - reserved,
        )
        context["social"] = social
        if len(json.dumps(context, ensure_ascii=False).encode()) > byte_budget:
            raise ValueError("social context exceeds its byte budget")
        return context

    def delivered(self, start, utterance, receipt, evidence, game_datetime):
        """Persist quoted words only. Never copy an underlying event's hidden payload."""
        with self.repo._lock:
            return self._delivered_locked(start, utterance, receipt, evidence, game_datetime)

    def _delivered_locked(self, start, utterance, receipt, evidence, game_datetime):
        speaker_event = self.event_id(start.session_id, receipt.event_id, utterance.actor_id)
        # Deleting shared receipt proof invalidates every replay. A speaker's
        # private reset/deletion below affects only that speaker's own projection.
        if self._source_deleted(start.profile_id, speaker_event):
            return {}
        retelling = (
            self.retelling_source(
                start, utterance.actor_id, utterance.retell_event_id, game_datetime
            )
            if utterance.retell_event_id
            else None
        )
        if retelling and retelling["text"] != receipt.text:
            raise ValueError("retelling words changed before delivery")
        common = {
            "game_datetime": game_datetime.isoformat(),
            "protagonist_id": start.protagonist_id,
            "event_type": "social.quote_delivered",
            "confidence": 1,
        }
        payload = {
            "text": receipt.text,
            "scene_id": start.scene_id,
            "session_id": start.session_id,
            "utterance_id": utterance.utterance_id,
            "delivery_receipt_id": receipt.event_id,
            "teller_id": utterance.actor_id,
            "development_attestation": True,
            "retelling_lineage": retelling["lineage"] if retelling else [],
            "reported_event_id": utterance.retell_event_id,
            "sampled_at": receipt.sampled_at.isoformat(),
        }
        speaker_suppressed = self._projection_suppressed(
            start.profile_id, utterance.actor_id, speaker_event
        )
        if speaker_suppressed and self.repo.connection.execute(
            "SELECT 1 FROM events WHERE profile_id=? AND event_id=?",
            (start.profile_id, speaker_event),
        ).fetchone() is None:
            return {}
        # Validate retained canonical proof even when its owner's memory is
        # suppressed. A changed replay must not seed a contradictory listener fact.
        self.repo.record_event(
            start.profile_id,
            start.branch_id,
            {
                **common,
                "event_id": speaker_event,
                "provenance": "directly_observed",
                "observer_ids": [utterance.actor_id],
                "payload": {
                    **payload, "visibility_scope": "actor", "scope_id": utterance.actor_id
                },
            },
        )
        if not speaker_suppressed:
            self.repo.add_memory(
                start.profile_id,
                start.branch_id,
                utterance.actor_id,
                start.protagonist_id,
                game_datetime.isoformat(),
                "I said: " + receipt.text,
                [speaker_event],
                "directly_observed",
                memory_id=speaker_event + ":memory",
            )
        heard = {}
        for item in evidence:
            if item.actor_id == utterance.actor_id:
                continue
            identifier = self.event_id(start.session_id, receipt.event_id, item.actor_id)
            if self._projection_suppressed(start.profile_id, item.actor_id, identifier):
                continue
            confidence = min(item.confidence, retelling["confidence"] if retelling else 0.7)
            self.repo.record_event(
                start.profile_id,
                start.branch_id,
                {
                    **common,
                    "event_id": identifier,
                    "provenance": "reported",
                    "observer_ids": [item.actor_id],
                    "confidence": confidence,
                    "payload": {
                        **payload,
                        "visibility_scope": "actor",
                        "scope_id": item.actor_id,
                        "heard_delivery_event_id": speaker_event,
                    },
                },
            )
            self.repo.add_memory(
                start.profile_id,
                start.branch_id,
                item.actor_id,
                start.protagonist_id,
                game_datetime.isoformat(),
                f"{utterance.actor_id} told me: {receipt.text}",
                [identifier],
                "reported",
                confidence=confidence,
                memory_id=identifier + ":memory",
            )
            heard[item.actor_id] = {
                "event_id": identifier,
                "text": receipt.text,
                "teller_id": utterance.actor_id,
                "provenance": "reported",
                "confidence": confidence,
                "game_datetime": game_datetime.isoformat(),
            }
        return heard
