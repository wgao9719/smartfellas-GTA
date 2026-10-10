"""Adversarial tests for authored simulation, replay, budgets, and reservations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest
from pydantic import ValidationError

from service.lls.neighborhood import (
    AdvanceBudget,
    EmbodimentRequest,
    NeighborhoodConfig,
    NeighborhoodEngine,
    NeighborhoodPack,
    NeighborhoodScope,
    SimulationTiers,
    demo_pack,
)
from service.lls.neighborhood_store import NeighborhoodConflict
from service.lls.protocol import Position
from service.lls.storage import Repository
from tools.neighborhood_demo import drain, run_demo

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
CONFIG = NeighborhoodConfig(enabled=True, release=False)
BUDGET = AdvanceBudget(background_events=256)


def setup(repo, *, profile="save", protagonist="franklin", now=None):
    branch = repo.start_session(profile, "session", T0.isoformat())["branch_id"]
    scope = NeighborhoodScope(
        profile_id=profile,
        branch_id=branch,
        protagonist_id=protagonist,
        neighborhood_id="authored_demo",
    )
    engine = NeighborhoodEngine(repo, demo_pack(30), config=CONFIG, utc_now=now)
    engine.initialize(scope, T0)
    return engine, scope


@pytest.fixture
def world(tmp_path):
    with Repository(tmp_path / "world.sqlite3") as repo:
        engine, scope = setup(repo)
        yield repo, engine, scope


def request(**changes):
    return EmbodimentRequest(
        **{
            "actor_id": "neighbor-001",
            "session_id": "session",
            "runtime_generation": 7,
            "player_position": Position(x=0, y=0, z=0),
            **changes,
        }
    )


def test_gates(world):
    repo, _, scope = world
    with pytest.raises(NeighborhoodConflict, match="disabled"):
        NeighborhoodEngine(repo, demo_pack(30)).snapshot(scope, T0)
    with pytest.raises(ValidationError, match="release"):
        NeighborhoodConfig(enabled=True)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(actors=p["actors"][:29]),
        lambda p: p.update(actors=p["actors"] * 4),
        lambda p: p["actors"][1].update(actor_id=p["actors"][0]["actor_id"]),
        lambda p: p["actors"][1].update(display_name=p["actors"][0]["display_name"]),
        lambda p: p["actors"][0].update(initial_location_id="missing"),
        lambda p: p["actors"][0]["routines"][0].update(activity="real_job"),
        lambda p: p["actors"][0]["routines"][1].update(start_minute=360),
        lambda p: p["actors"][0]["routines"][0].update(location_id="missing"),
        lambda p: p["places"][0]["position"].update(x=float("nan")),
        lambda p: p["places"][0]["position"].update(z=float("inf")),
    ],
)
def test_authored_pack_validation(mutation):
    pack = demo_pack(30).model_dump()
    mutation(pack)
    with pytest.raises(ValidationError):
        NeighborhoodPack.model_validate(pack)


def test_plans_are_not_outcomes(world):
    repo, engine, scope = world
    early = drain(engine, scope, T0 + timedelta(hours=6, minutes=15), budget=BUDGET)
    assert {a["location_id"] for a in early["actors"]} == {"courtyard"}
    assert all(a["active_plan"]["activity"] == "travel" for a in early["actors"])
    later = drain(engine, scope, T0 + timedelta(hours=11), budget=BUDGET)
    assert {a["location_id"] for a in later["actors"]} == {"overlook"}
    ledger = engine.ledger(scope, T0 + timedelta(hours=11))
    intentions = {
        e["event_id"]
        for e in ledger
        if e["event_type"] == "neighborhood.plan"
        and e["payload"]["activity"] in {"seek_company", "seek_work"}
    }
    outcomes = [e for e in ledger if e["payload"].get("plan_event_id") in intentions]
    assert len(outcomes) == 60
    assert all(e["payload"]["status"] == "unfulfilled" for e in outcomes)
    assert all(e["payload"]["native_verified"] is False for e in ledger)
    for table in ("memories", "actor_instances", "relationship_updates"):
        assert repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_three_day_48_person_equivalence():
    result = run_demo()
    assert result["equivalent"] and result["actors"] == 48 and result["game_days"] == 3
    assert result["model_calls"] == result["native_operations"] == 0
    assert all(r["activity_records"] == 2304 for r in result["results"])


def test_budget_and_tiers_report_truthful_backlog(world):
    _, engine, scope = world
    target = T0 + timedelta(days=3)
    with pytest.raises(NeighborhoodConflict, match="budget cannot fit"):
        engine.advance(scope, target, budget=AdvanceBudget(max_events=1))
    tiers = SimulationTiers(named_actor_ids=["neighbor-001"], near_actor_ids=["neighbor-002"])
    result = engine.advance(
        scope,
        target,
        tiers=tiers,
        budget=AdvanceBudget(max_events=20, named_events=0, near_events=0),
    )
    assert result["events_written"] == 19 and result["tier_events"]["background"] == 19
    assert result["frontier"] == T0.isoformat() and not result["caught_up"]
    assert result["actors"][0]["processed_through"] == T0.isoformat()


@pytest.mark.parametrize("fork_minutes", [375, 390])
def test_rewind_fork_retains_only_visible_outcomes(world, fork_minutes):
    repo, engine, scope = world
    target = T0 + timedelta(hours=7)
    drain(engine, scope, target, budget=BUDGET)
    with pytest.raises(NeighborhoodConflict, match="rewind"):
        engine.advance(scope, T0)
    fork_at = T0 + timedelta(minutes=fork_minutes)
    branch = repo.open_timeline(
        scope.profile_id, fork_at.isoformat(), parent_branch_id=scope.branch_id
    )
    fork = scope.model_copy(update={"branch_id": branch})
    before = engine.snapshot(fork, fork_at)
    expected = "courtyard" if fork_minutes < 390 else "plaza"
    assert {a["location_id"] for a in before["actors"]} == {expected}
    final = drain(engine, fork, target, budget=BUDGET)
    assert final["state_digest"] == engine.snapshot(scope, target)["state_digest"]


def test_profile_and_protagonist_isolation(world):
    repo, engine, scope = world
    drain(engine, scope, T0 + timedelta(hours=7), budget=BUDGET)
    other = scope.model_copy(update={"protagonist_id": "trevor"})
    assert not engine.snapshot(other, T0 + timedelta(hours=7))["configured"]
    engine.initialize(other, T0)
    assert {a["location_id"] for a in engine.snapshot(other, T0)["actors"]} == {"courtyard"}
    second, second_scope = setup(repo, profile="other")
    assert {a["location_id"] for a in second.snapshot(second_scope, T0)["actors"]} == {"courtyard"}


def test_config_drift_fails_closed(world):
    repo, _, scope = world
    pack = demo_pack(30)
    pack.actors[0].display_name = "Changed Name"
    with pytest.raises(NeighborhoodConflict, match="configuration changed"):
        NeighborhoodEngine(repo, pack, config=CONFIG).snapshot(scope, T0)


@pytest.mark.parametrize("operation", ["delete", "reset"])
def test_erasure_survives_restart_and_reimport(tmp_path, operation):
    path = tmp_path / "erasure.sqlite3"
    with Repository(path) as repo:
        engine, scope = setup(repo)
        target = T0 + timedelta(hours=7)
        drain(engine, scope, target, budget=BUDGET)
        original = next(
            e
            for e in engine.ledger(scope, target)
            if e["event_type"] == "neighborhood.plan" and e["payload"]["actor_id"] == "neighbor-001"
        )
        if operation == "delete":
            repo.delete_event(scope.profile_id, original["event_id"])
            repo.record_event(scope.profile_id, scope.branch_id, original)
        else:
            repo.reset_actor(scope.profile_id, "neighbor-001")
    with Repository(path) as repo:
        engine = NeighborhoodEngine(repo, demo_pack(30), config=CONFIG)
        snapshot = engine.snapshot(scope, target)
        assert snapshot["actors"][0]["status"] == "suppressed"
        assert snapshot["actors"][0]["location_id"] is None
        assert not any(
            e["payload"]["actor_id"] == "neighbor-001" for e in engine.ledger(scope, target)
        )
        assert snapshot["actors"][1]["status"] == "virtual"


def test_forged_outcome_rejected(world):
    repo, engine, scope = world
    drain(engine, scope, T0 + timedelta(hours=6), budget=BUDGET)
    configured, states, _, _ = engine._read(scope, T0 + timedelta(hours=6))
    event = engine._prepare_actor(
        scope, configured, states["neighbor-001"], T0 + timedelta(hours=6, minutes=30)
    )[0]
    event["payload"]["effects"]["energy_delta"] = 999
    repo.record_event(scope.profile_id, scope.branch_id, event)
    with pytest.raises(NeighborhoodConflict, match="exceeds"):
        engine.snapshot(scope, T0 + timedelta(hours=7))


def test_atomic_advance_rejects_concurrent_cursor(world, monkeypatch):
    repo, engine, scope = world
    original = engine.store.append
    injected = False

    def raced(scope, events, *, precondition=None):
        nonlocal injected
        if not injected:
            injected = True
            engine.store.append = original
            engine.advance(scope, T0 + timedelta(minutes=1), budget=AdvanceBudget(max_events=1))
        return original(scope, events, precondition=precondition)

    monkeypatch.setattr(engine.store, "append", raced)
    with pytest.raises(NeighborhoodConflict, match="changed"):
        engine.advance(scope, T0 + timedelta(hours=7), budget=BUDGET)
    assert (
        repo.connection.execute(
            "SELECT COUNT(*) FROM events WHERE "
            "json_extract(data,'$.event_type')='neighborhood.plan'"
        ).fetchone()[0]
        == 0
    )


def test_reservation_expiry_ownership_and_no_native_claims(world):
    repo, engine, scope = world
    now = [T0]
    engine.utc_now = lambda: now[0]
    drain(engine, scope, T0, budget=BUDGET)
    first = engine.reserve_embodiment(scope, T0, request())
    assert first["payload"]["native_spawned"] is False
    assert engine.reserve_embodiment(scope, T0, request()) == first
    with pytest.raises(NeighborhoodConflict, match="already has"):
        engine.reserve_embodiment(scope, T0, request(runtime_generation=8))
    with pytest.raises(NeighborhoodConflict, match="exact"):
        engine.release_embodiment(
            scope, T0, first["event_id"], session_id="session", runtime_generation=8
        )
    blocked = engine.advance(scope, T0 + timedelta(minutes=1), budget=BUDGET)
    assert "neighbor-001" in blocked["blocked_actor_ids"]
    now[0] += timedelta(seconds=16)
    second = engine.reserve_embodiment(scope, T0, request(runtime_generation=8))
    assert engine.release_embodiment(
        scope, T0, first["event_id"], session_id="session", runtime_generation=7
    )
    assert [e["event_id"] for e in engine._active_reservations(scope, "neighbor-001")] == [
        second["event_id"]
    ]
    assert repo.connection.execute("SELECT COUNT(*) FROM actor_instances").fetchone()[0] == 0


def test_reservation_rejects_live_binding_wrong_session_and_distance(world):
    repo, engine, scope = world
    drain(engine, scope, T0, budget=BUDGET)
    with pytest.raises(NeighborhoodConflict, match="near-player"):
        engine.reserve_embodiment(scope, T0, request(player_position=Position(x=21, y=0, z=0)))
    with pytest.raises(NeighborhoodConflict, match="open session"):
        engine.reserve_embodiment(scope, T0, request(session_id="absent"))
    repo.bind_instance(scope.profile_id, "neighbor-001", "session", 10, 1)
    with pytest.raises(NeighborhoodConflict, match="live native"):
        engine.reserve_embodiment(scope, T0, request())
    result = engine.advance(scope, T0 + timedelta(minutes=1), budget=BUDGET)
    assert "neighbor-001" in result["blocked_actor_ids"]


def test_reservation_race_across_connections(tmp_path):
    path = tmp_path / "race.sqlite3"
    with Repository(path) as repo:
        engine, scope = setup(repo)
        drain(engine, scope, T0, budget=BUDGET)
    barrier, results = Barrier(2), []

    def reserve(generation):
        with Repository(path) as repo:
            engine = NeighborhoodEngine(repo, demo_pack(30), config=CONFIG, utc_now=lambda: T0)
            append = engine.store.append

            def together(*args, **kwargs):
                barrier.wait(timeout=10)
                return append(*args, **kwargs)

            engine.store.append = together
            try:
                results.append(
                    engine.reserve_embodiment(scope, T0, request(runtime_generation=generation))
                )
            except NeighborhoodConflict:
                results.append(None)

    threads = [Thread(target=reserve, args=(generation,)) for generation in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert len(results) == 2 and sum(result is not None for result in results) == 1


def test_small_budget_repeated_catch_up_converges(world):
    _, engine, scope = world
    result = drain(
        engine,
        scope,
        T0 + timedelta(hours=7),
        budget=AdvanceBudget(max_events=3, background_events=3),
    )
    assert result["caught_up"]
    assert {a["location_id"] for a in result["actors"]} == {"plaza"}


def test_near_tier_three_day_catch_up_converges(world):
    _, engine, scope = world
    target = T0 + timedelta(days=3)
    tiers = SimulationTiers(near_actor_ids=["neighbor-001"])
    for _ in range(100):
        result = engine.advance(
            scope, target, tiers=tiers, budget=AdvanceBudget(max_days=3, background_events=256)
        )
        assert result["tier_events"]["near"] <= 32 and result["events_written"] <= 256
        if result["caught_up"]:
            break
        assert result["events_written"] > 0
    assert result["caught_up"]


def test_released_reservation_reacquires_at_identical_clock(world):
    _, engine, scope = world
    engine.utc_now = lambda: T0
    drain(engine, scope, T0, budget=BUDGET)
    first = engine.reserve_embodiment(scope, T0, request())
    assert engine.release_embodiment(
        scope, T0, first["event_id"], session_id="session", runtime_generation=7
    )
    second = engine.reserve_embodiment(scope, T0, request())
    assert second["event_id"] != first["event_id"]
    assert [e["event_id"] for e in engine._active_reservations(scope, "neighbor-001")] == [
        second["event_id"]
    ]


def test_forged_release_rejected_at_write_and_read(world):
    repo, engine, scope = world
    engine.utc_now = lambda: T0
    drain(engine, scope, T0, budget=BUDGET)
    first = engine.reserve_embodiment(scope, T0, request())
    event = engine._event(
        scope,
        "embodiment_released",
        T0,
        "forged",
        {
            "reservation_id": first["event_id"],
            "reset_epoch": first["payload"]["reset_epoch"],
            "baseline_id": first["payload"]["baseline_id"],
            "session_id": "attacker-session",
            "runtime_generation": 999,
            "native_spawned": False,
        },
        actor="neighbor-001",
        sources=[first["event_id"]],
        order=210,
    )
    with pytest.raises(NeighborhoodConflict, match="exact owner"):
        engine.store.append(scope, [event])
    assert len(engine._active_reservations(scope, "neighbor-001")) == 1
    repo.record_event(scope.profile_id, scope.branch_id, event)
    with pytest.raises(NeighborhoodConflict, match="exact owner"):
        engine._active_reservations(scope, "neighbor-001")


@pytest.mark.parametrize(
    "change",
    [
        {"native_spawned": True},
        {"native_verified": True},
        {"domain": "native_gta"},
        {"expires_at_utc": (T0 + timedelta(seconds=31)).isoformat()},
        {"expires_at_utc": T0.isoformat()},
    ],
)
def test_imported_reservation_rejects_native_claims_or_invalid_expiry(world, change):
    repo, engine, scope = world
    engine.utc_now = lambda: T0
    drain(engine, scope, T0, budget=BUDGET)
    first = engine.reserve_embodiment(scope, T0, request())
    forged = deepcopy(first)
    forged["event_id"] += "-forged"
    forged["payload"].update(change)
    repo.record_event(scope.profile_id, scope.branch_id, forged)
    with pytest.raises(NeighborhoodConflict):
        engine._active_reservations(scope, "neighbor-001")


def test_reservations_are_exclusive_across_protagonists_and_districts(world):
    repo, engine, scope = world
    engine.utc_now = lambda: T0
    drain(engine, scope, T0, budget=BUDGET)
    engine.reserve_embodiment(scope, T0, request())
    other_scope = scope.model_copy(update={"protagonist_id": "trevor"})
    engine.initialize(other_scope, T0)
    # The lease in Franklin's scope also blocks Trevor's virtual advancement.
    result = engine.advance(other_scope, T0, budget=BUDGET)
    assert "neighbor-001" in result["blocked_actor_ids"]
    pack = demo_pack(30)
    pack.neighborhood_id = "another_district"
    other = NeighborhoodEngine(repo, pack, config=CONFIG, utc_now=lambda: T0)
    district = scope.model_copy(update={"neighborhood_id": pack.neighborhood_id})
    other.initialize(district, T0)
    assert len(other._active_reservations(district, "neighbor-001")) == 1
    assert "neighbor-001" in other.advance(district, T0, budget=BUDGET)["blocked_actor_ids"]


def test_imported_checkpoint_cannot_skip_authored_outcomes(world):
    repo, engine, scope = world
    configured, states, _, _ = engine._read(scope, T0)
    state = states["neighbor-001"]
    event = engine._event(
        scope,
        "checkpoint",
        T0 + timedelta(hours=7),
        "forged",
        {
            "baseline_id": state["baseline_id"],
            "reset_epoch": state["reset_epoch"],
            "state": engine._physical(state),
        },
        actor="neighbor-001",
        sources=[state["cursor_event_id"]],
        order=100,
    )
    repo.record_event(scope.profile_id, scope.branch_id, event)
    with pytest.raises(NeighborhoodConflict, match="skips required"):
        engine.snapshot(scope, T0 + timedelta(hours=7))


def test_reservation_ends_at_exact_expiry_and_session_end_does_not_allow_reuse(world):
    repo, engine, scope = world
    now = [T0]
    engine.utc_now = lambda: now[0]
    drain(engine, scope, T0, budget=BUDGET)
    engine.reserve_embodiment(scope, T0, request(lease_seconds=1))
    now[0] += timedelta(seconds=1)
    assert engine._active_reservations(scope, "neighbor-001") == []
    repo.end_session(scope.profile_id, "session")
    with pytest.raises(NeighborhoodConflict, match="open session"):
        engine.reserve_embodiment(scope, T0, request())
