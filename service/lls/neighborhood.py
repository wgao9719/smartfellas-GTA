"""Finite, deterministic development simulation. Nothing here executes GTA behavior."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil, dist
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .neighborhood_store import NeighborhoodConflict, NeighborhoodStore, instant
from .protocol import Identifier, Position, Protagonist, StrictModel
from .storage import _date, _protagonist


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Place(StrictModel):
    location_id: Identifier
    label: str = Field(min_length=1, max_length=128)
    position: Position
    domain: Literal["authored_virtual"] = "authored_virtual"


class Routine(StrictModel):
    routine_id: Identifier
    start_minute: int = Field(ge=0, lt=1440)
    duration_minutes: int = Field(ge=1, le=480)
    activity: Literal["travel", "rest", "idle", "seek_company", "seek_work"]
    location_id: Identifier
    from_location_id: Identifier | None = None

    @model_validator(mode="after")
    def finite_interval(self):
        if self.start_minute + self.duration_minutes > 1440:
            raise ValueError("a routine cannot cross midnight")
        if (self.activity == "travel") != (self.from_location_id is not None):
            raise ValueError("only virtual travel requires an authored origin")
        return self


class Neighbor(StrictModel):
    actor_id: Identifier
    display_name: str = Field(min_length=1, max_length=128)
    initial_location_id: Identifier
    routines: list[Routine] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def nonoverlapping_routines(self):
        ordered = sorted(self.routines, key=lambda r: r.start_minute)
        if len({r.routine_id for r in ordered}) != len(ordered):
            raise ValueError("duplicate routine identity")
        if any(
            a.start_minute + a.duration_minutes > b.start_minute
            for a, b in zip(ordered, ordered[1:])
        ):
            raise ValueError("authored routines overlap")
        return self


class NeighborhoodPack(StrictModel):
    neighborhood_id: Identifier
    version: Identifier
    places: list[Place] = Field(min_length=2, max_length=16)
    actors: list[Neighbor] = Field(min_length=30, max_length=100)

    @model_validator(mode="after")
    def finite_authored_world(self):
        locations = {p.location_id for p in self.places}
        if len(locations) != len(self.places):
            raise ValueError("duplicate virtual location")
        if len({a.actor_id for a in self.actors}) != len(self.actors):
            raise ValueError("duplicate persistent actor")
        if len({a.display_name.strip().casefold() for a in self.actors}) != len(self.actors):
            raise ValueError("persistent actors require distinct names")
        for actor in self.actors:
            if not actor.display_name.strip() or actor.initial_location_id not in locations:
                raise ValueError("invalid authored identity or initial location")
            for routine in actor.routines:
                if routine.location_id not in locations or (
                    routine.from_location_id is not None
                    and routine.from_location_id not in locations
                ):
                    raise ValueError("routine refers to an unknown virtual location")
        return self


class NeighborhoodScope(StrictModel):
    profile_id: Identifier
    branch_id: Identifier
    protagonist_id: Protagonist
    neighborhood_id: Identifier


class NeighborhoodConfig(StrictModel):
    enabled: bool = False
    release: bool = True

    @model_validator(mode="after")
    def development_only(self):
        if self.enabled and self.release:
            raise ValueError("release builds refuse development neighborhood simulation")
        return self


class AdvanceBudget(StrictModel):
    max_events: int = Field(default=256, ge=1, le=256)
    max_days: int = Field(default=1, ge=1, le=3)
    background_events: int = Field(default=128, ge=0, le=256)
    named_events: int = Field(default=96, ge=0, le=256)
    near_events: int = Field(default=32, ge=0, le=256)


class SimulationTiers(StrictModel):
    named_actor_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    near_actor_ids: list[Identifier] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def disjoint_tiers(self):
        combined = self.named_actor_ids + self.near_actor_ids
        if len(combined) != len(set(combined)):
            raise ValueError("an actor can have only one simulation tier")
        return self


class EmbodimentRequest(StrictModel):
    actor_id: Identifier
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    player_position: Position
    lease_seconds: int = Field(default=15, ge=1, le=30)


class NeighborhoodEngine:
    def __init__(self, repository, pack, *, config=None, utc_now=None):
        self.repo = repository
        self.store = NeighborhoodStore(repository)
        self.pack = NeighborhoodPack.model_validate(pack.model_dump())
        self.config = NeighborhoodConfig.model_validate(
            (config or NeighborhoodConfig()).model_dump()
        )
        self.pack_hash = digest(self.pack.model_dump(mode="json"))
        self.actors = {a.actor_id: a for a in self.pack.actors}
        self.places = {p.location_id: p for p in self.pack.places}
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))

    def _enabled(self, scope):
        if not self.config.enabled or self.config.release:
            raise NeighborhoodConflict("development neighborhood simulation is disabled")
        if scope.neighborhood_id != self.pack.neighborhood_id:
            raise NeighborhoodConflict("neighborhood scope does not match the authored pack")

    def _event(self, scope, kind, at, identity, payload, *, actor=None, sources=(), order=0):
        return {
            "event_id": "neighborhood_"
            + digest(
                [
                    scope.profile_id,
                    scope.branch_id,
                    scope.protagonist_id,
                    scope.neighborhood_id,
                    kind,
                    identity,
                ]
            ),
            "event_type": "neighborhood." + kind,
            "game_datetime": _date(instant(at).isoformat()),
            "protagonist_id": _protagonist(scope.protagonist_id),
            "provenance": "system_fact",
            "confidence": 1,
            "observer_ids": [actor] if actor else [],
            "payload": {
                "neighborhood_id": scope.neighborhood_id,
                "branch_id": scope.branch_id,
                "config_hash": self.pack_hash,
                "domain": "development_simulation",
                "native_verified": False,
                "actor_id": actor,
                "order": order,
                "source_event_ids": list(sources),
                **payload,
            },
        }

    def _audit_epoch(self, audit, actor_id=None):
        return max(audit["profile_epoch"], audit["reset_epochs"].get(actor_id, 0))

    def _read(self, scope, at):
        self._enabled(scope)
        events = self.store.events(scope, at)
        audit = self.store.audit(scope.profile_id)
        valid, states, accepted, configured = set(), {}, [], None
        for event in events:
            payload, kind = event["payload"], event["event_type"].removeprefix("neighborhood.")
            actor_id = payload.get("actor_id")
            if event["event_id"] in audit["deleted_event_ids"]:
                continue
            if payload.get("reset_epoch", 0) != self._audit_epoch(audit, actor_id):
                continue
            if payload.get("config_hash") != self.pack_hash:
                raise NeighborhoodConflict("the authored neighborhood configuration changed")
            if (
                payload.get("domain") != "development_simulation"
                or payload.get("native_verified") is not False
                or (kind != "configured" and not payload.get("source_event_ids"))
            ):
                raise NeighborhoodConflict("simulation event lacks its domain or sources")
            if kind == "configured":
                if digest(payload["pack"]) != self.pack_hash:
                    raise NeighborhoodConflict("authored configuration content changed")
                configured = event
                valid.add(event["event_id"])
                continue
            if any(source not in valid for source in payload.get("source_event_ids", [])):
                if (
                    actor_id in states
                    and payload.get("baseline_id") == states[actor_id]["baseline_id"]
                ):
                    states[actor_id]["suppressed"] = True
                continue
            if kind.startswith("embodiment_"):
                self.store._validate_reservation(event, {}, scope.profile_id)
                valid.add(event["event_id"])
                continue
            if actor_id not in self.actors:
                raise NeighborhoodConflict("simulation event names an unauthored actor")
            if kind == "initialized":
                if configured is None or payload["source_event_ids"] != [configured["event_id"]]:
                    raise NeighborhoodConflict("initial state requires the authored configuration")
                if actor_id in states and payload["generation"] < states[actor_id]["generation"]:
                    continue
                authored = self.actors[actor_id]
                if (
                    payload["location_id"] != authored.initial_location_id
                    or payload["energy"] != 50
                ):
                    raise NeighborhoodConflict("invalid authored initial simulation state")
                states[actor_id] = {
                    "actor_id": actor_id,
                    "location_id": payload["location_id"],
                    "energy": 50,
                    "cursor": event["game_datetime"],
                    "started": False,
                    "active_plan": None,
                    "initialized_at": event["game_datetime"],
                    "baseline_id": event["event_id"],
                    "last_state_event_id": event["event_id"],
                    "cursor_event_id": event["event_id"],
                    "reset_epoch": payload["reset_epoch"],
                    "generation": payload["generation"],
                    "suppressed": False,
                }
            else:
                state = states.get(actor_id)
                if not state or payload.get("baseline_id") != state["baseline_id"]:
                    continue
                if state["suppressed"]:
                    continue
                if kind == "plan":
                    self._apply_plan(state, event)
                elif kind == "outcome":
                    self._apply_outcome(state, event)
                elif kind == "checkpoint":
                    checkpoint_time = instant(event["game_datetime"])
                    if checkpoint_time - instant(state["cursor"]) > timedelta(days=3):
                        raise NeighborhoodConflict(
                            "checkpoint exceeds the bounded simulation horizon"
                        )
                    expected = self._prepare_actor(scope, configured, state, checkpoint_time)
                    if len(expected) != 1:
                        raise NeighborhoodConflict("checkpoint skips required authored activity")
                    if payload["state"] != self._physical(state):
                        raise NeighborhoodConflict(
                            "checkpoint does not follow implemented outcomes"
                        )
                    state["cursor_event_id"] = event["event_id"]
                else:
                    raise NeighborhoodConflict("unknown neighborhood event type")
                state["cursor"] = event["game_datetime"]
                state["started"] = True
            valid.add(event["event_id"])
            accepted.append(event)
        return configured, states, accepted, audit

    @staticmethod
    def _physical(state):
        return {
            "location_id": state["location_id"],
            "energy": state["energy"],
            "active_occurrence": (state["active_plan"] or {}).get("occurrence_id"),
        }

    def _routine(self, actor_id, routine_id):
        return next((r for r in self.actors[actor_id].routines if r.routine_id == routine_id), None)

    def _apply_plan(self, state, event):
        payload = event["payload"]
        routine = self._routine(state["actor_id"], payload["routine_id"])
        start = instant(payload["starts_at"])
        if (
            routine is None
            or state["last_state_event_id"] not in payload["source_event_ids"]
            or start.hour * 60 + start.minute != routine.start_minute
            or start.second
            or start.microsecond
            or payload["activity"] != routine.activity
            or payload["location_id"] != routine.location_id
            or payload["from_location_id"] != routine.from_location_id
            or instant(payload["ends_at"]) != start + timedelta(minutes=routine.duration_minutes)
            or instant(event["game_datetime"]) != start
        ):
            raise NeighborhoodConflict("plan does not match a finite authored routine")
        expected = routine.from_location_id if routine.activity == "travel" else routine.location_id
        status = (
            "planned"
            if state["location_id"] == expected and not state["active_plan"]
            else "blocked"
        )
        if payload["status"] != status:
            raise NeighborhoodConflict("plan eligibility does not match simulated state")
        if status == "planned":
            state["active_plan"] = {**payload, "event_id": event["event_id"]}

    def _outcome(self, state):
        plan = state["active_plan"]
        minutes = int((instant(plan["ends_at"]) - instant(plan["starts_at"])).total_seconds() // 60)
        activity = plan["activity"]
        delta = (
            min(100 - state["energy"], minutes // 15)
            if activity == "rest"
            else (-min(state["energy"], ceil(minutes / 10)) if activity == "travel" else 0)
        )
        return {
            "status": "unfulfilled"
            if activity in {"seek_company", "seek_work"}
            else "completed_simulation",
            "effects": {
                "location_id": plan["location_id"]
                if activity == "travel"
                else state["location_id"],
                "energy_delta": delta,
            },
        }

    def _apply_outcome(self, state, event):
        plan, payload = state["active_plan"], event["payload"]
        if (
            not plan
            or plan["event_id"] not in payload["source_event_ids"]
            or state["last_state_event_id"] not in payload["source_event_ids"]
            or payload["plan_event_id"] != plan["event_id"]
            or payload["occurrence_id"] != plan["occurrence_id"]
            or instant(event["game_datetime"]) != instant(plan["ends_at"])
        ):
            raise NeighborhoodConflict("outcome has no matching executed simulation plan")
        expected = self._outcome(state)
        if payload["status"] != expected["status"] or payload["effects"] != expected["effects"]:
            raise NeighborhoodConflict("outcome exceeds the implemented simulation")
        state["location_id"] = expected["effects"]["location_id"]
        state["energy"] += expected["effects"]["energy_delta"]
        state["active_plan"] = None
        state["last_state_event_id"] = event["event_id"]

    def initialize(self, scope, at):
        self._enabled(scope)
        at = instant(at)
        configured, states, _, audit = self._read(scope, at)
        events = []
        if configured is None:
            configured = self._event(
                scope,
                "configured",
                at,
                [self.pack_hash, at.isoformat()],
                {
                    "pack": self.pack.model_dump(mode="json"),
                    "reset_epoch": audit["profile_epoch"],
                },
                order=-100,
            )
            events.append(configured)
        generation = max([audit["profile_epoch"], *audit["reset_epochs"].values(), 0])
        for actor in self.pack.actors:
            if actor.actor_id in states and not states[actor.actor_id]["suppressed"]:
                continue
            existing = self.repo.get_actor(scope.profile_id, actor.actor_id)
            if existing is None:
                self.repo.register_actor(
                    scope.profile_id,
                    {
                        "id": actor.actor_id,
                        "actor_kind": "curated_persistent",
                        "display_name": actor.display_name,
                        "control_policy": "ambient",
                        "allowed_actions": [],
                        "persona": {"biography": "Authored virtual simulation participant."},
                    },
                )
            elif (
                existing["actor_kind"] not in {"curated_persistent", "spawned_persistent"}
                or existing["display_name"] != actor.display_name
            ):
                raise NeighborhoodConflict("authored actor identity conflicts with the registry")
            events.append(
                self._event(
                    scope,
                    "initialized",
                    at,
                    [actor.actor_id, at.isoformat(), generation],
                    {
                        "location_id": actor.initial_location_id,
                        "energy": 50,
                        "generation": generation,
                        "reset_epoch": self._audit_epoch(audit, actor.actor_id),
                    },
                    actor=actor.actor_id,
                    sources=[configured["event_id"]],
                    order=-50,
                )
            )
        if events:
            self.store.append(scope, events)
        return self.snapshot(scope, at)

    def _prepare_actor(self, scope, configured, original, until):
        state = deepcopy(original)
        cursor, baseline = instant(state["cursor"]), instant(state["initialized_at"])
        boundaries = []
        day = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        while day <= until:
            for routine in self.actors[state["actor_id"]].routines:
                start = day + timedelta(minutes=routine.start_minute)
                end = start + timedelta(minutes=routine.duration_minutes)
                if start >= baseline:
                    boundaries.extend([(start, 1, routine), (end, 0, routine)])
            day += timedelta(days=1)
        events = []
        for stamp, phase, routine in sorted(
            boundaries, key=lambda item: (item[0], item[1], item[2].routine_id)
        ):
            if stamp > until or stamp < cursor or (stamp == cursor and state["started"]):
                continue
            start = stamp if phase == 1 else stamp - timedelta(minutes=routine.duration_minutes)
            occurrence = digest(
                [
                    self.pack_hash,
                    state["actor_id"],
                    state["reset_epoch"],
                    state["initialized_at"],
                    routine.routine_id,
                    start.isoformat(),
                ]
            )
            common = {
                "occurrence_id": occurrence,
                "routine_id": routine.routine_id,
                "baseline_id": state["baseline_id"],
                "reset_epoch": state["reset_epoch"],
            }
            if phase == 1:
                expected_location = (
                    routine.from_location_id
                    if routine.activity == "travel"
                    else routine.location_id
                )
                event = self._event(
                    scope,
                    "plan",
                    stamp,
                    [occurrence, state["baseline_id"]],
                    {
                        **common,
                        "activity": routine.activity,
                        "location_id": routine.location_id,
                        "from_location_id": routine.from_location_id,
                        "starts_at": start.isoformat(),
                        "ends_at": (
                            start + timedelta(minutes=routine.duration_minutes)
                        ).isoformat(),
                        "status": "planned"
                        if state["location_id"] == expected_location and not state["active_plan"]
                        else "blocked",
                    },
                    actor=state["actor_id"],
                    sources=[configured["event_id"], state["last_state_event_id"]],
                    order=10,
                )
                self._apply_plan(state, event)
            else:
                if not state["active_plan"] or state["active_plan"]["occurrence_id"] != occurrence:
                    continue
                event = self._event(
                    scope,
                    "outcome",
                    stamp,
                    [occurrence, state["baseline_id"]],
                    {
                        **common,
                        "plan_event_id": state["active_plan"]["event_id"],
                        **self._outcome(state),
                    },
                    actor=state["actor_id"],
                    sources=[state["active_plan"]["event_id"], state["last_state_event_id"]],
                    order=0,
                )
                self._apply_outcome(state, event)
            events.append(event)
        sources = list(
            dict.fromkeys([original["cursor_event_id"], *[e["event_id"] for e in events]])
        )
        events.append(
            self._event(
                scope,
                "checkpoint",
                until,
                [state["baseline_id"], until.isoformat()],
                {
                    "baseline_id": state["baseline_id"],
                    "reset_epoch": state["reset_epoch"],
                    "state": self._physical(state),
                },
                actor=state["actor_id"],
                sources=sources,
                order=100,
            )
        )
        return events

    def advance(self, scope, target, *, tiers=None, budget=None):
        self._enabled(scope)
        target = instant(target)
        tiers = SimulationTiers.model_validate((tiers or SimulationTiers()).model_dump())
        budget = AdvanceBudget.model_validate((budget or AdvanceBudget()).model_dump())
        if set(tiers.named_actor_ids + tiers.near_actor_ids) - self.actors.keys():
            raise NeighborhoodConflict("simulation tier contains an unknown actor")
        configured, states, _, _ = self._read(scope, datetime.max.replace(tzinfo=timezone.utc))
        if configured is None:
            raise NeighborhoodConflict("initialize the authored simulation before advancing")
        if any(instant(s["cursor"]) > target for s in states.values()):
            raise NeighborhoodConflict("rewind requires a separate timeline branch")
        if states and (target - min(instant(s["cursor"]) for s in states.values())).days > 366:
            raise NeighborhoodConflict("catch-up request exceeds one game year")
        used = {"background": 0, "named": 0, "near": 0}
        blocked = sorted(self.actors.keys() - states.keys())
        pending, expected_cursors = [], {}
        for actor_id, state in sorted(
            states.items(), key=lambda item: (item[1]["cursor"], item[0])
        ):
            if state["suppressed"]:
                blocked.append(actor_id)
                continue
            if instant(state["cursor"]) == target and state["started"]:
                continue
            actor = self.repo.get_actor(scope.profile_id, actor_id)
            if (
                actor is None
                or actor.get("control_policy", "ambient") != "ambient"
                or self.store.live_binding(scope.profile_id, actor_id)
                or self._active_reservations(scope, actor_id)
            ):
                blocked.append(actor_id)
                continue
            tier = (
                "near"
                if actor_id in tiers.near_actor_ids
                else ("named" if actor_id in tiers.named_actor_ids else "background")
            )
            until = min(target, instant(state["cursor"]) + timedelta(days=budget.max_days))
            events = self._prepare_actor(scope, configured, state, until)
            tier_limit = getattr(budget, tier + "_events")
            capacity = min(budget.max_events - sum(used.values()), tier_limit - used[tier])
            if capacity <= 0:
                continue
            if len(events) > capacity:
                # Keep all effects at a timestamp together, plus their checkpoint.
                groups = {}
                for event in events[:-1]:
                    stamp = event["game_datetime"]
                    groups[stamp] = groups.get(stamp, 0) + 1
                selected, count = None, 1
                for stamp, size in groups.items():
                    if count + size > capacity:
                        break
                    selected, count = instant(stamp), count + size
                if selected is None:
                    if capacity == min(budget.max_events, tier_limit):
                        raise NeighborhoodConflict(
                            "event budget cannot fit the next atomic activity boundary"
                        )
                    continue
                events = self._prepare_actor(scope, configured, state, selected)

            pending.extend(events)
            expected_cursors[actor_id] = state["cursor_event_id"]
            used[tier] += len(events)

        if pending:

            def current(repository):
                _, fresh, _, _ = self._read(scope, datetime.max.replace(tzinfo=timezone.utc))
                for actor_id, expected in expected_cursors.items():
                    actor = repository.get_actor(scope.profile_id, actor_id)
                    if (
                        fresh.get(actor_id, {}).get("cursor_event_id") != expected
                        or not actor
                        or actor.get("control_policy", "ambient") != "ambient"
                        or self.store.live_binding(scope.profile_id, actor_id)
                        or self._active_reservations(scope, actor_id)
                    ):
                        raise NeighborhoodConflict(
                            "simulation actor changed before atomic advancement"
                        )

            self.store.append(scope, pending, precondition=current)
        result = self.snapshot(scope, target)
        result.update(
            events_written=sum(used.values()),
            tier_events=used,
            blocked_actor_ids=blocked,
            requested_time=target.isoformat(),
            model_calls=0,
        )
        return result

    def snapshot(self, scope, at):
        configured, states, _, _ = self._read(scope, at)
        target = instant(at)
        actors = []
        for actor in self.pack.actors:
            state = states.get(actor.actor_id)
            if state is None or state["suppressed"]:
                actors.append(
                    {
                        "actor_id": actor.actor_id,
                        "display_name": actor.display_name,
                        "status": "suppressed",
                        "location_id": None,
                        "energy": None,
                        "processed_through": None,
                        "active_plan": None,
                    }
                )
                continue
            active = state["active_plan"]
            actors.append(
                {
                    "actor_id": actor.actor_id,
                    "display_name": actor.display_name,
                    "status": "virtual",
                    "location_id": state["location_id"],
                    "energy": state["energy"],
                    "processed_through": instant(state["cursor"]).isoformat(),
                    "active_plan": (
                        {
                            "occurrence_id": active["occurrence_id"],
                            "activity": active["activity"],
                            "status": "planned",
                            "ends_at": active["ends_at"],
                        }
                        if active
                        else None
                    ),
                }
            )
        cursors = [instant(a["processed_through"]) for a in actors if a["processed_through"]]
        backlog = sum(
            a["processed_through"] is None
            or instant(a["processed_through"]) < target
            or not states[a["actor_id"]]["started"]
            for a in actors
        )
        return {
            "neighborhood_id": scope.neighborhood_id,
            "config_hash": self.pack_hash,
            "configured": configured is not None,
            "actors": actors,
            "frontier": min(cursors).isoformat() if len(cursors) == len(actors) else None,
            "backlog_actors": backlog,
            "caught_up": backlog == 0,
            "domain": "development_simulation",
            "native_verified": False,
            "state_digest": digest(actors),
        }

    def ledger(self, scope, at):
        _, states, events, _ = self._read(scope, at)
        return [
            event
            for event in events
            if event["event_type"] in {"neighborhood.plan", "neighborhood.outcome"}
            and (state := states.get(event["payload"]["actor_id"]))
            and not state["suppressed"]
            and event["payload"].get("baseline_id") == state["baseline_id"]
        ]

    def _active_reservations(self, scope, actor_id):
        events = self.store.reservations(scope.profile_id)
        audit = self.store.audit(scope.profile_id)
        released = {
            e["payload"].get("reservation_id")
            for e in events
            if e["event_type"] == "neighborhood.embodiment_released"
        }
        now = instant(self.utc_now())
        return [
            e
            for e in events
            if e["event_type"] == "neighborhood.embodiment_reserved"
            and e["payload"].get("actor_id") == actor_id
            and e["event_id"] not in released
            and e["payload"].get("reset_epoch") == self._audit_epoch(audit, actor_id)
            and instant(e["payload"]["expires_at_utc"]) > now
        ]

    def _session(self, scope, session_id):
        row = self.repo.connection.execute(
            "SELECT branch_id,ended_at FROM game_sessions WHERE profile_id=? AND session_id=?",
            (scope.profile_id, session_id),
        ).fetchone()
        if not row or row["ended_at"] or row["branch_id"] != scope.branch_id:
            raise NeighborhoodConflict("reservation requires an open session on this branch")

    def reserve_embodiment(self, scope, at, request):
        self._enabled(scope)
        at = instant(at)
        request = EmbodimentRequest.model_validate(request.model_dump())
        actor_id = request.actor_id
        if actor_id not in self.actors:
            raise NeighborhoodConflict("reservation actor is not authored")
        self._session(scope, request.session_id)
        if self.store.live_binding(scope.profile_id, actor_id):
            raise NeighborhoodConflict("reservation actor already has a live native binding")
        _, states, _, _ = self._read(scope, datetime.max.replace(tzinfo=timezone.utc))
        state = states.get(actor_id)
        actor = self.repo.get_actor(scope.profile_id, actor_id)
        if (
            not state
            or state["suppressed"]
            or not state["started"]
            or instant(state["cursor"]) != at
            or state["active_plan"]
            or not actor
            or actor.get("control_policy", "ambient") != "ambient"
        ):
            raise NeighborhoodConflict("reservation requires a caught-up idle virtual actor")
        place = self.places[state["location_id"]]
        if (
            dist(
                place.position.model_dump().values(), request.player_position.model_dump().values()
            )
            > 20
        ):
            raise NeighborhoodConflict("reservation actor is outside the near-player radius")
        owner = (
            scope.branch_id,
            _protagonist(scope.protagonist_id),
            request.session_id,
            request.runtime_generation,
        )
        active = self._active_reservations(scope, actor_id)
        if active:
            previous = active[0]
            p = previous["payload"]
            if (
                len(active) == 1
                and (
                    p["branch_id"],
                    previous["protagonist_id"],
                    p["session_id"],
                    p["runtime_generation"],
                )
                == owner
                and p["neighborhood_id"] == scope.neighborhood_id
            ):
                return previous
            raise NeighborhoodConflict("persistent actor already has an embodiment reservation")
        now = instant(self.utc_now())
        event = self._event(
            scope,
            "embodiment_reserved",
            at,
            [actor_id, *owner, now.isoformat(), str(uuid4())],
            {
                "baseline_id": state["baseline_id"],
                "reset_epoch": state["reset_epoch"],
                "session_id": request.session_id,
                "runtime_generation": request.runtime_generation,
                "location_id": state["location_id"],
                "status": "reserved",
                "created_at_utc": now.isoformat(),
                "expires_at_utc": (now + timedelta(seconds=request.lease_seconds)).isoformat(),
                "native_spawned": False,
            },
            actor=actor_id,
            sources=[state["cursor_event_id"]],
            order=200,
        )

        def current(repository):
            self._session(scope, request.session_id)
            _, fresh, _, _ = self._read(scope, datetime.max.replace(tzinfo=timezone.utc))
            current_actor = repository.get_actor(scope.profile_id, actor_id)
            if (
                fresh.get(actor_id, {}).get("cursor_event_id") != state["cursor_event_id"]
                or not current_actor
                or current_actor.get("control_policy", "ambient") != "ambient"
                or self.store.live_binding(scope.profile_id, actor_id)
                or self._active_reservations(scope, actor_id)
                or instant(self.utc_now()) >= instant(event["payload"]["expires_at_utc"])
            ):
                raise NeighborhoodConflict("embodiment reservation changed before atomic write")

        self.store.append(scope, [event], precondition=current)
        return event

    def release_embodiment(self, scope, at, reservation_id, *, session_id, runtime_generation):
        self._enabled(scope)
        candidates = self.store.reservations(scope.profile_id, scope.neighborhood_id)
        reserved = next((e for e in candidates if e["event_id"] == reservation_id), None)
        if not reserved or reserved["event_type"] != "neighborhood.embodiment_reserved":
            raise NeighborhoodConflict("unknown embodiment reservation")
        p = reserved["payload"]
        if (
            p["branch_id"] != scope.branch_id
            or reserved["protagonist_id"] != _protagonist(scope.protagonist_id)
            or p["session_id"] != session_id
            or p["runtime_generation"] != runtime_generation
        ):
            raise NeighborhoodConflict("only the exact reservation owner can release it")
        if instant(at) < instant(reserved["game_datetime"]):
            raise NeighborhoodConflict("release cannot precede its reservation")
        if any(e["payload"].get("reservation_id") == reservation_id for e in candidates):
            return False
        event = self._event(
            scope,
            "embodiment_released",
            at,
            reservation_id,
            {
                "reservation_id": reservation_id,
                "reset_epoch": p["reset_epoch"],
                "baseline_id": p["baseline_id"],
                "session_id": session_id,
                "runtime_generation": runtime_generation,
                "native_spawned": False,
            },
            actor=p["actor_id"],
            sources=[reservation_id],
            order=210,
        )
        return self.store.append(scope, [event])[0]


def demo_pack(count=48):
    """Reproducible authored fixture, with no claimed GTA locations or services."""
    first = ("Avery", "Bea", "Cora", "Dara", "Eli", "Finn", "Gia", "Hugo", "Inez", "Jules")
    last = ("Alden", "Brooks", "Chen", "Diaz", "Ellis", "Frost", "Green", "Hale", "Ivers", "Jones")
    routines = [
        Routine(
            routine_id="outbound",
            start_minute=360,
            duration_minutes=30,
            activity="travel",
            from_location_id="courtyard",
            location_id="plaza",
        ),
        Routine(
            routine_id="morning_rest",
            start_minute=390,
            duration_minutes=90,
            activity="rest",
            location_id="plaza",
        ),
        Routine(
            routine_id="work_intention",
            start_minute=480,
            duration_minutes=60,
            activity="seek_work",
            location_id="plaza",
        ),
        Routine(
            routine_id="uphill",
            start_minute=540,
            duration_minutes=30,
            activity="travel",
            from_location_id="plaza",
            location_id="overlook",
        ),
        Routine(
            routine_id="wait",
            start_minute=570,
            duration_minutes=60,
            activity="idle",
            location_id="overlook",
        ),
        Routine(
            routine_id="social_intention",
            start_minute=630,
            duration_minutes=30,
            activity="seek_company",
            location_id="overlook",
        ),
        Routine(
            routine_id="return",
            start_minute=660,
            duration_minutes=30,
            activity="travel",
            from_location_id="overlook",
            location_id="courtyard",
        ),
        Routine(
            routine_id="afternoon_rest",
            start_minute=720,
            duration_minutes=240,
            activity="rest",
            location_id="courtyard",
        ),
    ]
    return NeighborhoodPack(
        neighborhood_id="authored_demo",
        version="v1",
        places=[
            Place(location_id=key, label=key.title(), position=Position(x=x, y=0, z=0))
            for key, x in [("courtyard", 0), ("plaza", 40), ("overlook", 80)]
        ],
        actors=[
            Neighbor(
                actor_id=f"neighbor-{i + 1:03}",
                display_name=f"{first[i // 10]} {last[i % 10]}",
                initial_location_id="courtyard",
                routines=routines,
            )
            for i in range(count)
        ],
    )
