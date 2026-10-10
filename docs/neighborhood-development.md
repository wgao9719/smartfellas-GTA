# Development neighborhood simulation

`service/lls/neighborhood.py` provides a development-only foundation for AUTO-11 and AUTO-12.
It supports 30–100 named persistent actors in one authored neighborhood pack.
It does not establish completion of AUTO-11 or AUTO-14.

## Authored virtual activity

A `NeighborhoodPack` defines 2–16 virtual places and 1–12 daily routines per actor.
Routines have fixed times, destinations, and durations. They cannot overlap or cross midnight.
Virtual travel also requires an authored origin. Coordinates must be finite.

The engine records a plan when a routine starts. The plan does not change the actor's virtual location.
A matching outcome can change virtual location or energy when the routine ends.
Travel consumes bounded energy. Rest restores bounded energy. Idle activity has no additional effect.

`seek_work` and `seek_company` express intentions. Their outcomes remain `unfulfilled`.
They do not establish employment, completed work, consent, friendship, or a shared activity.
The engine creates no native jobs, services, money, inventory, social memories, or relationship changes.

Every neighborhood event identifies its simulation domain and sets `native_verified=False`.
The fixture coordinates are authored examples. They are not verified GTA locations or navigation destinations.

## Python API

The engine is disabled by default. Release configuration refuses an enabled simulation.
Authenticated development routes now expose initialization, bounded advancement, and inspection.
An optional development scheduler advances explicitly initialized state. It makes no GTA spawn calls.

```python
from service.lls.neighborhood import (
    AdvanceBudget, NeighborhoodConfig, NeighborhoodEngine,
    NeighborhoodScope, demo_pack,
)
from service.lls.storage import Repository

with Repository("development.sqlite3") as repository:
    start = "2025-01-01T00:00:00Z"
    session = repository.start_session("demo", "demo-session", start)
    scope = NeighborhoodScope(
        profile_id="demo",
        branch_id=session["branch_id"],
        protagonist_id="franklin",
        neighborhood_id="authored_demo",
    )
    engine = NeighborhoodEngine(
        repository, demo_pack(48),
        config=NeighborhoodConfig(enabled=True, release=False),
    )
    engine.initialize(scope, start)
    result = engine.advance(
        scope, "2025-01-04T00:00:00Z",
        budget=AdvanceBudget(max_days=3, background_events=256),
    )
```

`initialize()` records the authored configuration and initial virtual states.
`advance()` performs one bounded step. Call it again to continue a reported backlog.
`snapshot()` reconstructs visible actor state at a requested game time.
`ledger()` returns source-backed plans and outcomes for each actor's current baseline.

`SimulationTiers` assigns actors to background, named, or near accounting tiers.
All three tiers currently use the same finite virtual operations.
One call permits 1–256 events and at most three game days per actor.
Each tier also has its own event allowance. A zero allowance intentionally suspends that tier.

The engine processes older actor cursors first and keeps each timestamp's activity effects together.
It shortens advancement when the remaining allowance cannot cover the requested horizon.
A positive allowance that cannot fit the next activity boundary and checkpoint raises `NeighborhoodConflict`.
This avoids silently returning an unusable budget forever.

Results report event counts, blocked actors, backlog, and the earliest complete actor frontier.
The frontier is unknown when any actor's state is suppressed.
The engine rejects rewind on the same branch. A separate branch can inherit only visible ancestral events.

## Authenticated development routes

The service loads one authored JSON pack at startup when these settings are explicit:

```text
LLS_RELEASE=0
LLS_DEV_NEIGHBORHOOD=1
LLS_NEIGHBORHOOD_PACK=/absolute/path/to/authored-neighborhood.json
```

The pack must satisfy `NeighborhoodPack` and fit within 256 KiB.
The service pins its validated content hash until restart.
The game session must advertise `development:neighborhood:v1` and supply its session bearer token.
Release adapters do not advertise this capability.

| Route | Operation |
|---|---|
| `POST /v1/bridge/neighborhood/initialize` | Establish the authored virtual baseline explicitly. |
| `POST /v1/bridge/neighborhood/advance` | Perform one bounded advancement to the session's current game time. |
| `POST /v1/bridge/neighborhood/snapshot` | Inspect virtual state at the session's current game time. |

Every request contains `session_id`, `runtime_generation`, `protagonist_id`, `game_datetime`, and `neighborhood_id`.
The service derives the profile and branch from the authenticated session.
The advance request can also contain an `AdvanceBudget` object under `budget`.
Clients cannot provide arbitrary target times, output claims, actor tiers, or native spawn instructions.

Initialization and advancement require a world snapshot no older than five seconds.
Online play, missions, cutscenes, random events, and pause suspend these mutations.
Inspection remains available while mutations are suspended.
Without the separate automatic setting below, changing game time alone does not advance the simulation.

The service serializes requests with session lifecycle changes.
Canceled database work finishes before the service permits actor erasure or session closure.
Actor erasure suppresses its virtual state until a later explicit initialization.

Bridge and dashboard diagnostics show the neighborhood identity, actor count, and pinned configuration hash.
Responses retain `native_verified=false` and `game_verified=false`.
The `automatic_advancement` flag reports whether the separate scheduler is enabled.
The runtime routes expose no embodiment reservation or spawning operation.

## Automatic development advancement

Set `LLS_DEV_NEIGHBORHOOD_AUTONOMY=1` to enable automatic advancement in addition to the existing development settings.
The scheduler is off by default. Release builds reject it.
Each participating session must still advertise `development:neighborhood:v1`.

The scheduler never calls `initialize()`.
An authenticated caller must explicitly establish an authored baseline before automatic advancement can begin.
The scheduler never restores forgotten actors, deleted source events, or suppressed virtual state.
Restart does not grant initialization authority.

Each maintenance pass selects at most one worker without awaiting historical replay.
The worker advances only to its selected session's captured game time.
It requires a fresh eligible world and yields priority to foreground dialogue, speech, skills, social scenes, and pending action commands.

Sessions with the same profile, branch, protagonist, and neighborhood share one budget.
The scheduler selects the newest eligible game time within that group, then uses session ID to resolve ties.
Different groups receive turns according to their last selection order.
A duplicate session with foreground work suspends automatic progress for that entire group.

`Settings.neighborhood_scheduler` accepts these programmatic limits:

| Setting | Default | Allowed range |
|---|---:|---:|
| `interval_seconds` | 2 | 1–60 |
| `max_events_per_step` | 64 | 8–256 |
| `max_events_per_minute` | 512 | 64–4096 |
| `max_events_per_scope` | 4096 | 64–65536 |
| `max_steps_per_scope` | 128 | 1–1024 |
| `max_days_per_step` | 1 | 1–3 |
| `worker_timeout_seconds` | 10 | 1–30 |
| `blocked_retry_seconds` | 30 | 5–300 |

One worker runs across the runtime. At most 128 distinct groups retain scheduler accounting.
Each attempt reserves the entire event allowance, including refusal, cancellation, or an absent baseline.
The scheduler does not refund unused reservations.
Minute allowances use a rolling 60-second window. Group allowances last until service restart and survive duplicate-session changes.
Manual advancement remains an explicit operation with its own request budget.

The worker releases the session lock before replay.
A thread-safe cancellation token and current runtime authority checks guard the atomic event transaction.
The worker also retains the engine's source, reset, actor binding, cursor, and embodiment reservation checks.
Manual and automatic operations share the bridge lock.

Cancellation revokes the token before joining database work.
Cleanup never reacquires the session lock because erasure and shutdown can hold that lock while joining.
A worker timeout prevents a later guarded append but cannot forcibly terminate a running database thread.
The service joins that thread before erasure continues.

Session diagnostics report the sampled target, backlog, frontier, blocked actor count, attempt count, reservations, and confirmed writes.
An unknown backlog remains `null` until a successful sample supplies it.
`explicit_initialization_required`, `foreground_priority`, and budget reasons explain suspended work.
The scheduler makes zero model calls and no native commands.

## Persistence and erasure

`NeighborhoodStore` uses canonical repository events and `Repository.record_events_atomic()`.
It performs source validation and cursor checks inside the write transaction.
Concurrent actor changes roll back the complete pending batch.

Events and replay honor profile, branch ancestry, protagonist, and game time.
The stored pack hash prevents configuration drift during replay.
Canonical event IDs include the neighborhood. Operational reservation exclusivity spans neighborhoods within a profile.

Actor resets and source deletion suppress affected virtual state and activity history.
Scrub markers preserve that suppression after restart and direct canonical reimport.
Replay rejects forged outcome effects and checkpoints that skip required activity.
An explicit later `initialize()` can establish a new authored baseline. Restart alone cannot do so.

## Embodiment reservations

`reserve_embodiment(scope, at, request)` reserves an authored actor for one session and runtime generation.
`EmbodimentRequest` includes the actor, session, runtime generation, player position, and lease duration.
The caller must supply trusted runtime identity. The bridge does not expose reservations.

The actor must have caught up to the requested game time and have no active plan.
Its registry policy must permit ambient control. The player must be within 20 meters of its authored virtual location.
An open session must match the branch. Any live native binding blocks the reservation.

Reservation creation checks exclusivity inside the write transaction.
An existing lease blocks another owner across branches, protagonists, sessions, and neighborhoods in the same profile.
The exact owner can retrieve its lease again without extending its expiry.
Leases use real UTC time and expire within 30 seconds.

`release_embodiment()` requires the exact reservation ID, session, generation, branch, and protagonist.
Releasing an expired lease cannot release a successor lease.
Imported reservation records must preserve their virtual source, owner, bounded expiry, and false native flags.

Reservations do not create `actor_instances` rows. They set `native_spawned=False`.
They establish neither spawn success nor a verified native position.
The engine suspends virtual advancement for actors with active reservations or live native bindings.

## Reproducible verification

Run the temporary-database fixture:

```sh
uv run --frozen python tools/neighborhood_demo.py
```

The fixture compares a three-day catch-up with six-hour incremental steps for 48 actors.
It reopens both databases and compares final virtual state and semantic activity ledgers.
Checkpoint histories can differ. Each run produces 2,304 plan/outcome records with zero remaining backlog.
The fixture makes zero model calls and zero native operations.

Run the adversarial tests:

```sh
uv run pytest -q tests/test_neighborhood.py -W error
```

Tests cover authored bounds, budgets, rewind, erasure, forged evidence, reservation ownership, expiry, and concurrent acquisition.

The 21 bridge tests cover authentication, development gates, session identity, clock bounds, unsafe worlds, bounded advancement, and joined erasure.
These tests use the real service with in-process HTTP transport and temporary databases.

Run the separate loopback HTTP check:

```sh
uv run --frozen python tests/live_neighborhood_check.py
```

It starts the normal service with an isolated 48-actor pack and temporary database.
Ten bounded calls advance three virtual game days with zero remaining backlog.
A service restart preserves the configuration hash and virtual state digest.
The check produces no model requests or native commands.

Run the separate automatic advancement HTTP check:

```sh
uv run --frozen python tests/live_neighborhood_autonomy_check.py
```

This check first verifies that maintenance cannot initialize the authored pack.
After explicit initialization, fresh world observations trigger automatic advancement without any `/advance` requests.
The verified run advanced 30 actors across seven game hours in three automatic attempts, with 120 confirmed event writes and zero remaining backlog.
A service restart preserved the virtual state digest and authored initialization.
The check produced zero model requests and native operations. Its game and native verification flags remained false.

## Remaining acceptance gaps

AUTO-11 still needs integrated relationship evolution, sourced rumor propagation, and verified native embodiment with state reconciliation.
Finite work and social intentions do not supply those missing systems.
AUTO-12 has local simulation evidence here. It has no GTA acceptance evidence.

AUTO-14 still needs a zone scheduler, distinct fidelity implementations, aggregate-to-named reconciliation, and named-to-embodied reconciliation.
It also needs representative city-scale measurements of CPU, memory, storage, latency, and provider cost.
Canonical replay currently reads historical events. The three-day fixture does not establish bounded long-run replay cost.

This module does not claim that every pedestrian continuously reasons or that GTA NPCs execute these virtual routines.
