"""Canonical, scoped development-simulation events and conditional writes."""

import json
from datetime import datetime, timedelta, timezone

from .storage import _date, _protagonist


class NeighborhoodConflict(ValueError):
    pass


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_order(event):
    payload = event["payload"]
    return (
        _date(event["game_datetime"]),
        payload.get("order", 0),
        payload.get("actor_id", ""),
        event["event_id"],
    )


class NeighborhoodStore:
    def __init__(self, repository):
        self.repo = repository

    def events(self, scope, at):
        stamp = _date(instant(at).isoformat())
        with self.repo._lock:
            lineage = self.repo._lineage(scope.profile_id, scope.branch_id, stamp)
            rows = self.repo.connection.execute(
                "SELECT branch_id,game_datetime,data FROM events WHERE profile_id=? "
                "AND protagonist_id=? AND game_datetime<=?",
                (scope.profile_id, _protagonist(scope.protagonist_id), stamp),
            ).fetchall()
            result = []
            for row in rows:
                if (
                    row["branch_id"] not in lineage
                    or row["game_datetime"] > lineage[row["branch_id"]]
                ):
                    continue
                event = json.loads(row["data"])
                if (
                    event.get("event_type", "").startswith("neighborhood.")
                    and event.get("payload", {}).get("neighborhood_id") == scope.neighborhood_id
                ):
                    if event["payload"].get("branch_id") != row["branch_id"]:
                        raise NeighborhoodConflict("neighborhood event branch metadata changed")
                    result.append(event)
            return sorted(result, key=event_order)

    def audit(self, profile_id):
        with self.repo._lock:
            rows = self.repo.connection.execute(
                "SELECT audit_id,operation,object_id FROM scrub_audit WHERE profile_id=?",
                (profile_id,),
            ).fetchall()
        resets, deleted, profile_epoch = {}, set(), 0
        for row in rows:
            if row["operation"] == "reset_actor":
                resets[row["object_id"]] = max(resets.get(row["object_id"], 0), row["audit_id"])
            elif row["operation"] == "delete_event":
                deleted.add(row["object_id"])
            elif row["operation"] == "delete_profile":
                profile_epoch = max(profile_epoch, row["audit_id"])
        return {
            "reset_epochs": resets,
            "deleted_event_ids": deleted,
            "profile_epoch": profile_epoch,
        }

    def append(self, scope, events, *, precondition=None):
        def validate(repository):
            audit = self.audit(scope.profile_id)
            accepted = {}
            for event in events:
                payload = event["payload"]
                if (
                    payload.get("neighborhood_id") != scope.neighborhood_id
                    or payload.get("branch_id") != scope.branch_id
                    or _protagonist(event.get("protagonist_id"))
                    != _protagonist(scope.protagonist_id)
                    or payload.get("domain") != "development_simulation"
                    or payload.get("native_verified") is not False
                ):
                    raise NeighborhoodConflict("neighborhood event scope is invalid")
                if event["event_id"] in audit["deleted_event_ids"]:
                    raise NeighborhoodConflict("deleted neighborhood source cannot be reimported")
                actor_id = payload.get("actor_id")
                epoch = max(audit["profile_epoch"], audit["reset_epochs"].get(actor_id, 0))
                if payload.get("reset_epoch", 0) != epoch:
                    raise NeighborhoodConflict("neighborhood actor was reset")
                sources = payload.get("source_event_ids", [])
                if len(sources) != len(set(sources)):
                    raise NeighborhoodConflict("duplicate neighborhood source")
                if event["event_type"] != "neighborhood.configured" and not sources:
                    raise NeighborhoodConflict("neighborhood outcomes require canonical sources")
                stamp = _date(event["game_datetime"])
                lineage = repository._lineage(scope.profile_id, scope.branch_id, stamp)
                for source_id in sources:
                    if source_id in audit["deleted_event_ids"]:
                        raise NeighborhoodConflict("deleted neighborhood source is unavailable")
                    source = accepted.get(source_id)
                    if source is None:
                        row = repository.connection.execute(
                            "SELECT data,branch_id,game_datetime,protagonist_id FROM events "
                            "WHERE profile_id=? AND event_id=?",
                            (scope.profile_id, source_id),
                        ).fetchone()
                        if (
                            not row
                            or row["branch_id"] not in lineage
                            or row["game_datetime"] > lineage[row["branch_id"]]
                            or row["protagonist_id"] != _protagonist(scope.protagonist_id)
                        ):
                            raise NeighborhoodConflict(
                                "neighborhood source is outside this timeline"
                            )
                        source = json.loads(row["data"])
                    if (
                        source["payload"].get("neighborhood_id") != scope.neighborhood_id
                        or _date(source["game_datetime"]) > stamp
                    ):
                        raise NeighborhoodConflict("neighborhood source scope or time is invalid")
                accepted[event["event_id"]] = event
                if event["event_type"].startswith("neighborhood.embodiment_"):
                    self._validate_reservation(event, accepted, scope.profile_id)
            if precondition is not None:
                precondition(repository)

        return self.repo.record_events_atomic(
            scope.profile_id, scope.branch_id, events, precondition=validate
        )

    def reservations(self, profile_id, neighborhood_id=None):
        with self.repo._lock:
            rows = self.repo.connection.execute(
                "SELECT data,branch_id FROM events WHERE profile_id=? "
                "AND json_extract(data,'$.event_type') IN "
                "('neighborhood.embodiment_reserved','neighborhood.embodiment_released')",
                (profile_id,),
            ).fetchall()
            deleted = self.audit(profile_id)["deleted_event_ids"]
            events = [
                event
                for row in rows
                if (event := json.loads(row["data"]))["event_id"] not in deleted
                and event.get("event_type")
                in {"neighborhood.embodiment_reserved", "neighborhood.embodiment_released"}
                and (
                    neighborhood_id is None
                    or event.get("payload", {}).get("neighborhood_id") == neighborhood_id
                )
            ]
            indexed = {event["event_id"]: event for event in events}
            for row in rows:
                event = json.loads(row["data"])
                if event["event_id"] in indexed:
                    if event["payload"].get("branch_id") != row["branch_id"]:
                        raise NeighborhoodConflict("reservation branch metadata changed")
                    self._validate_reservation(event, indexed, profile_id)
            return events

    def _validate_reservation(self, event, indexed, profile_id):
        payload = event["payload"]
        if (
            payload.get("domain") != "development_simulation"
            or payload.get("native_verified") is not False
            or payload.get("native_spawned") is not False
            or type(payload.get("runtime_generation")) is not int
            or payload["runtime_generation"] < 0
            or not payload.get("actor_id")
            or not payload.get("session_id")
        ):
            raise NeighborhoodConflict("invalid development embodiment evidence")
        if event["event_type"] == "neighborhood.embodiment_reserved":
            try:
                created = instant(payload["created_at_utc"])
                expiry = instant(payload["expires_at_utc"])
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise NeighborhoodConflict("invalid reservation expiry") from exc
            if not timedelta(0) < expiry - created <= timedelta(seconds=30):
                raise NeighborhoodConflict("reservation lease must expire within 30 seconds")
            if payload.get("status") != "reserved" or not payload.get("source_event_ids"):
                raise NeighborhoodConflict("reservation requires sourced virtual state")
            sources = payload["source_event_ids"]
            if len(sources) != 1:
                raise NeighborhoodConflict("reservation requires one virtual checkpoint")
            source = indexed.get(sources[0])
            if source is None:
                row = self.repo.connection.execute(
                    "SELECT data,branch_id FROM events WHERE profile_id=? AND event_id=?",
                    (profile_id, sources[0]),
                ).fetchone()
                source = json.loads(row["data"]) if row else None
                if row and source["payload"].get("branch_id") != row["branch_id"]:
                    raise NeighborhoodConflict("reservation source branch metadata changed")
            lineage = self.repo._lineage(
                profile_id, payload["branch_id"], _date(event["game_datetime"])
            )
            if (
                not source
                or source["event_type"] != "neighborhood.checkpoint"
                or any(
                    source["payload"].get(key) != payload.get(key)
                    for key in ("actor_id", "neighborhood_id", "baseline_id", "reset_epoch")
                )
                or _protagonist(source.get("protagonist_id"))
                != _protagonist(event.get("protagonist_id"))
                or source["payload"].get("branch_id") not in lineage
                or _date(source["game_datetime"]) > lineage[source["payload"]["branch_id"]]
                or source["payload"].get("domain") != "development_simulation"
                or source["payload"].get("native_verified") is not False
            ):
                raise NeighborhoodConflict("reservation source is outside its virtual actor scope")
            return
        reservation_id = payload.get("reservation_id")
        reserved = indexed.get(reservation_id)
        if reserved is None:
            row = self.repo.connection.execute(
                "SELECT data FROM events WHERE profile_id=? AND event_id=?",
                (profile_id, reservation_id),
            ).fetchone()
            reserved = json.loads(row[0]) if row else None
        keys = (
            "branch_id",
            "neighborhood_id",
            "actor_id",
            "session_id",
            "runtime_generation",
            "baseline_id",
            "reset_epoch",
        )
        if (
            not reserved
            or reserved["event_type"] != "neighborhood.embodiment_reserved"
            or any(payload.get(key) != reserved["payload"].get(key) for key in keys)
            or _protagonist(event.get("protagonist_id"))
            != _protagonist(reserved.get("protagonist_id"))
            or payload.get("source_event_ids") != [reservation_id]
            or instant(event["game_datetime"]) < instant(reserved["game_datetime"])
        ):
            raise NeighborhoodConflict("embodiment release does not match the exact owner")

    def live_binding(self, profile_id, actor_id):
        with self.repo._lock:
            return (
                self.repo.connection.execute(
                    "SELECT 1 FROM actor_instances WHERE profile_id=? AND actor_id=? "
                    "AND status='live'",
                    (profile_id, actor_id),
                ).fetchone()
                is not None
            )
