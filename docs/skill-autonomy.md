# Development automatic skill practice

The skill scheduler completes an experience-driven learning cycle without calls to the manual skill routes.
It derives a bounded practice opportunity from an external action outcome.
It can generate a candidate, revise a failed preflight, run an acknowledged bridge trial, adopt the result, and later select it again.

This cycle currently supports short orientation and stationary waiting procedures.
It does not establish improved conversation, relationships, native learning, or participant satisfaction.
The scheduler treats a need as a procedural hypothesis supported by recorded experiences.

## Enablement

The scheduler requires all these gates:

- `LLS_RELEASE=0`
- `LLS_DEV_SKILLS=1`
- `LLS_DEV_SKILL_AUTONOMY=1`
- The session manifest includes `development:skills:v1`.

The scheduler remains off by default. Enabling it does not change cloud consent or provider budgets.
Candidate requests use the Gateway background allowance. Its default is zero.
The adapter must already have permission for each primitive.

## External evidence and opportunities

The runtime marks ordinary foreground action commands with an internal external origin and their originating canonical player utterance.
Skill commands have a separate internal origin.
Clients cannot create scheduler authority through prose, action-class names, or diagnostic labels.

The scheduler independently verifies the canonical started receipt.
It requires `reason="task_dispatched"` and `observed.task_dispatched=true`.
It also requires finite movement measurements and primitive-specific outcome measurements.
Ordinary ACK acceptance alone does not establish a learning opportunity.

Two completed uses of the same primitive, from distinct player utterances, can establish a repetition opportunity.
A narrowly measured failure can establish one repair opportunity:

| Primitive | Required failure reason | Required measurements |
|---|---|---|
| `face_player` | `orientation_not_reached` | Finite distance and heading error above 20 degrees, at most 180 degrees |
| `wait_here` | `wait_duration_incomplete` | Positive elapsed time below the requested duration, displacement at most 1.5 meters, finite consistent distance |

The current native adapter does not produce these measured failure reasons.
The real HTTP test supplies them as explicit simulated observations.
Repeated successful native receipts can use the repetition path when the development capability is available.

Unknown engine failures, stale snapshots, missing measurements, native task refusal, cancellation, and expiry cannot create repair opportunities.
Skill-generated receipts never create another learning episode.
Cooldowns only restrict frequency. Elapsed time cannot create a need or unlock a skill.

## Complete bounded cycle

The scheduler claims the episode in SQLite before candidate generation or physical dispatch.
An episode retains its original UTC expiry and monotonic deadline through every stage.
Current actor binding, source visibility, audit revision, policy, and provider identity must remain valid.
The actor and world snapshots must be at most two seconds old.

The scheduler first checks for an actor-private retained skill with the exact observed primitive signature.
A selected procedure must include that primitive and fit the current orientation/wait limits.
The interpreter checks its guards and every primitive's current permissions.

If no retained skill matches, the scheduler generates a private candidate through the existing learner.
The compiler validates its operations and dependencies.
The actual interpreter then runs a recorded preflight with explicitly simulated zero-distance receipts.
This preflight checks declarative structure, current guards, and policy. It does not prove native feasibility.

A false candidate guard can support one revision within the same episode.
The revised candidate receives another preflight.
An unsuccessful preflight cannot authorize physical execution or adoption.
Engine failure, source invalidation, and policy withdrawal do not cause automatic model retries.

A successful preflight proceeds through the existing bridge trial.
The adapter must acknowledge every dispatched primitive with the required observations.
Only a completed trial with `verification="bridge_acknowledged"` permits adoption.
Canonical event and memory persistence precede receipt publication.
The runtime rechecks episode authority after persistence and before releasing another primitive.

A later fresh external experience can select the retained revision for one bounded practice attempt.
This selection occurs after the external outcome. It does not replace the foreground action beforehand.
The scheduler never claims that this practice repaired a relationship or satisfied the player.

A retained revision can retire after an acknowledged, narrowly measured primitive failure.
Unknown engine errors and interruption only stop that episode.
Retirement preserves earlier code and trial records. Further generation requires fresh external evidence.

## Bounds, fairness, and persistence

One skill scheduler worker runs across the runtime.
The scheduler favors actors with fewer durable attempts, then the oldest ready episode.
The runtime alternates skill and social scheduling priority.
Foreground interaction, speech, social scenes, active commands, and unsafe actor or world state prevent practice.

| Setting | Default | Scope |
|---|---:|---|
| `max_queued_per_profile` | 16 | Pending or running episodes per profile |
| `max_experiences_per_profile` | 2048 | Durable external experience identifiers per profile |
| `max_episodes_per_session` | 8 | Claimed episodes per session |
| `max_candidate_requests_per_minute` | 4 | Scheduler candidate requests across the runtime |
| `episode_timeout_seconds` | 45 | Entire episode, including queue and provider waits |
| `actor_cooldown_seconds` | 10 | Between an actor's episode attempts |
| `max_skill_duration_seconds` | 5 | Each generated or selected procedure |

Each episode permits at most two candidate requests and two bridge-trial reservations.
The current revision path consumes at most one bridge trial after preflight success.
Reservations do not refund on refusal, failure, or cancellation.
Gateway request and spending limits persist independently and count actual provider attempts, including fallback.

SQLite episode and experience records contain identifiers, counters, states, and bounded reason codes.
They do not contain dialogue excerpts or generated source code.
Duplicate ACKs, duplicate sessions, and process restart cannot replay consumed experiences.
Restart abandons unfinished episodes. It does not resume an old physical authorization.
Fresh evidence can create a new episode after abandonment or expiry.
Actor reset and source deletion invalidate original evidence, including deletion followed by reimport.

The learner receives only bounded actor-visible source excerpts and a typed whitelist of action measurements.
It does not receive arbitrary observation dictionaries.
Skill identities include the actor and protagonist scope. Reusable structural code does not transfer another actor's experience or adoption.

## Verification

Run the scheduler tests:

```sh
uv run --frozen pytest -q tests/test_skill_scheduler.py -W error
```

The tests cover revision, ACK-backed adoption, later practice, replay suppression, source erasure, queue limits, fairness, deadlines, and lifecycle joins.
They also block a completed-ACK memory write during cancellation and verify that adoption cannot follow.

Run the automatic loopback HTTP probe:

```sh
uv run --frozen python tests/live_skill_autonomy_check.py
```

The probe uses ordinary external interactions and simulated action receipts, with no manual skill-route requests.
It verifies revision, acknowledged adoption, process restart, and retained practice from a fresh external experience.
It makes no real model or native calls.

The runtime diagnostics expose identifiers, states, counters, and `selection_mode="post_outcome_bounded_practice"`.
Results retain `game_verified=false`.
Native GTA acceptance remains a separate requirement.

See [Development skill bridge](skill-runtime.md) for manual routes and receipt requirements.
