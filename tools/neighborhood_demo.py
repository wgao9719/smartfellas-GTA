"""Reproducible development-only 48-person, three-day simulation comparison."""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.lls.neighborhood import (  # noqa: E402
    AdvanceBudget,
    NeighborhoodConfig,
    NeighborhoodEngine,
    NeighborhoodScope,
    demo_pack,
    digest,
)
from service.lls.storage import Repository  # noqa: E402

START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=3)


def drain(engine, scope, target, *, budget):
    """Each call stays bounded. This fixture explicitly requests more calls."""
    for _ in range(1000):
        result = engine.advance(scope, target, budget=budget)
        if result["caught_up"]:
            return result
        if result["events_written"] == 0:
            raise RuntimeError("fixture cannot progress within its configured event budget")
    raise RuntimeError("fixture exceeded its bounded drain limit")


def ledger_digest(engine, scope):
    entries = []
    for event in engine.ledger(scope, END):
        payload = event["payload"]
        entries.append(
            {
                "kind": event["event_type"],
                "at": event["game_datetime"],
                "actor": payload["actor_id"],
                "occurrence": payload["occurrence_id"],
                "status": payload["status"],
                "effects": payload.get("effects"),
            }
        )
    return digest(entries), len(entries)


def run_demo():
    results = []
    budget = AdvanceBudget(max_days=3, background_events=256)
    config = NeighborhoodConfig(enabled=True, release=False)
    pack = demo_pack(48)
    with tempfile.TemporaryDirectory(prefix="lls-neighborhood-") as directory:
        for mode in ("catch_up", "incremental"):
            path = Path(directory) / (mode + ".sqlite3")
            with Repository(path) as repo:
                branch = repo.open_timeline("fixture", START.isoformat())
                scope = NeighborhoodScope(
                    profile_id="fixture",
                    branch_id=branch,
                    protagonist_id="franklin",
                    neighborhood_id=pack.neighborhood_id,
                )
                engine = NeighborhoodEngine(repo, pack, config=config)
                engine.initialize(scope, START)
                targets = (
                    [END]
                    if mode == "catch_up"
                    else [START + timedelta(hours=h) for h in range(6, 73, 6)]
                )
                for target in targets:
                    snapshot = drain(engine, scope, target, budget=budget)
                activities, count = ledger_digest(engine, scope)
                native = repo.connection.execute("SELECT COUNT(*) FROM actor_instances").fetchone()[
                    0
                ]
            with Repository(path) as reopened:
                restarted = NeighborhoodEngine(reopened, pack, config=config)
                replay = restarted.snapshot(scope, END)
                if replay["state_digest"] != snapshot["state_digest"]:
                    raise AssertionError("restart changed virtual simulation state")
                if ledger_digest(restarted, scope) != (activities, count):
                    raise AssertionError("restart changed source-backed activity ledger")
            results.append(
                {
                    "mode": mode,
                    "state_digest": snapshot["state_digest"],
                    "ledger_digest": activities,
                    "activity_records": count,
                    "backlog_actors": snapshot["backlog_actors"],
                    "restart_equivalent": True,
                    "native_bindings": native,
                }
            )
    if results[0]["state_digest"] != results[1]["state_digest"]:
        raise AssertionError("catch-up and incremental virtual states differ")
    if results[0]["ledger_digest"] != results[1]["ledger_digest"]:
        raise AssertionError("catch-up and incremental activity ledgers differ")
    return {
        "domain": "development_simulation",
        "native_verified": False,
        "actors": 48,
        "game_days": 3,
        "model_calls": 0,
        "native_operations": 0,
        "equivalent": True,
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2, sort_keys=True))
