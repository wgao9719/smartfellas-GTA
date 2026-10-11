# Development skill bridge

The development skill bridge connects candidate generation, bounded trials, retention, and later execution.
It uses the existing authenticated command queue and canonical action acknowledgment writer.
It does not establish native GTA acceptance.

See [Skill language](skill-language.md) for standalone limits, storage semantics, and local demo commands.
See [Automatic skill practice](skill-autonomy.md) for experience-driven scheduling and its evidence limits.

## Gates and scope

The service requires `LLS_RELEASE=0` and `LLS_DEV_SKILLS=1`.
The authenticated session manifest must advertise `development:skills:v1`.
Release configuration rejects this feature. Ordinary dialogue does not enable it.

Each request identifies the current session, branch, protagonist, actor, runtime instance, generation, and game time.
The service derives the profile from the authenticated session.
It requires existing source events that the persistent actor observed or received.
Client-authored result objects cannot establish trial success.

The runtime permits one skill worker per session and persistent actor.
It rejects conflicts with pending dialogue, physical commands, speech capture, and social scenes.
Foreground interaction cancels the skill worker through the shared lifecycle.

## Routes

All routes use the session bearer token:

| Route | Additional fields | Result |
|---|---|---|
| `POST /v1/bridge/skills/generate` | `skill_id`, `version`, optional `action_class`, optional `revises` | A persisted candidate, or no candidate |
| `POST /v1/bridge/skills/trial` | `candidate_id` | A persisted simulated refusal/failure, or a bridge trial outcome with canonical receipt sources |
| `POST /v1/bridge/skills/adopt` | `trial_id` | Actor-private adoption after a completed acknowledged trial |
| `POST /v1/bridge/skills/execute` | `skill_id` | Execution of the actor's current retained revision |

Every body also supplies these common fields:

```json
{
  "session_id": "CURRENT_SESSION",
  "branch_id": "CURRENT_BRANCH",
  "protagonist_id": "franklin",
  "runtime_generation": 1,
  "actor_id": "CURRENT_PERSISTENT_ACTOR",
  "runtime_instance_id": "CURRENT_INSTANCE",
  "game_datetime": "2025-01-01T12:00:00Z",
  "source_event_ids": ["EXISTING_ACTOR_VISIBLE_EVENT"],
  "consent": false
}
```

Generation uses `SkillLearner` and the configured provider gateway's background budget.
The runtime does not bypass cloud consent, provider limits, or candidate validation.
A revision must refer to the actor's earlier visible candidate or trial.

Every manual trial first runs the same recorded simulated preflight as automatic practice.
The actual interpreter checks the complete candidate with simulated zero-distance primitive receipts.
A later guard or policy refusal therefore prevents even an earlier action from reaching the bridge.
An unsuccessful preflight returns its persisted simulated trial for revision, without dispatching commands.

The passing preflight belongs to the candidate and carries a fingerprint of its source, transitive dependencies, and action registry.
Before bridge dispatch, the runtime requires that actor-visible passing record and matching current fingerprints.
It rechecks that authorization before each primitive; compilation alone cannot authorize a bridge trial.
Changed immutable implementations require new versions and another successful preflight.
Simulated completion does not establish native feasibility or permit bridge adoption.

Trial and execution requests remain pending while the adapter polls commands and submits acknowledgments through the existing bridge routes.
They do not receive arbitrary executor callbacks from HTTP clients.
Request task cancellation joins the worker's local cleanup.
An HTTP disconnect does not itself guarantee task cancellation.
Explicit scene cancellation, session closure, and the operation deadline provide separate stopping paths.

## Execution and receipts

The first bridge supports `face_player` and `wait_here` only.
Each action requires an explicit duration. A bridge skill permits at most four primitive actions.
The interpreter still enforces its source, dependency, operation, duration, and distance limits.
Follow and stop-following skills cannot execute through this bridge.

One monotonic deadline covers the complete skill. Queue delays consume that deadline.
Each command expires within the remaining allowance. Later actions cannot restart the deadline.
The runtime rechecks current capability, actor policy, consent, source visibility, and binding before every operation.

Every invocation gets a new execution identity and scene identity.
Each primitive gets its own operation and command identities.
The command's `turn_id` identifies the interpreter operation in canonical action evidence.

A started receipt requires `reason="task_dispatched"` and `observed.task_dispatched=true`.
A completed receipt requires a previously accepted started receipt.
It also requires finite, nonnegative `observed.distance_meters`.
The interpreter sums these measured distances against the complete skill's distance limit.
Missing movement measurements never become zero.

Orientation completion requires `heading_error_degrees` between 0 and 20.
Wait completion requires `elapsed_ms` to cover the requested duration and `displacement_meters` between 0 and 1.5.
The adapter must supply those observations. The runtime does not infer them from model text or elapsed service time.

The canonical writer persists the action event and its existing memory projection before the skill worker receives a receipt.
Skill acknowledgments use the current game time at first receipt, separately for each status.
Projection retries preserve that original receipt time.
After persistence, the runtime rechecks command authorization, worker ownership, and the original deadlines before releasing the next operation.
If cancellation occurs during persistence, the canonical observation remains evidence, but the command cannot regain completion authorization.
Duplicate acknowledgments cannot execute another step or create another memory.

The trial record uses terminal game time and references its original need and canonical action receipts.
`verification="bridge_acknowledged"` describes receipt provenance. `game_verified` remains false.
A refusal before dispatch has no bridge receipt and cannot support adoption.
Later execution also requires a retained revision backed by an acknowledged trial.

## Cancellation and lifecycle

Cancellation synchronously withdraws outstanding command authorization and queues a cancellation for the execution's own scene.
It never cancels another execution by operation identity.
Joined cleanup does not acquire `session.lock` or wait for an acknowledgment handler.
This prevents deadlocks during admin erasure, provider changes, and session shutdown.

The shared lifecycle joins skill work before reset or deletion.
Provider replacement joins the old learner before connecting the replacement.
Actor replacement, unsafe world state, source deletion, and session changes prevent stale continuation or persistence.

Local withdrawal does not prove native cleanup. Responses retain `native_cleanup_confirmed=false`.
The adapter's command expiry remains a separate cancellation boundary.

## Verification and limits

Run the authenticated runtime tests:

```sh
uv run pytest -q tests/test_runtime_skills.py -W error
```

The tests exercise the real HTTP routes, provider gateway, interpreter, storage, command queue, and acknowledgment writer.
They use a fake provider and simulated adapter receipts. They make no real model calls or native GTA calls.

Run the separate-process loopback HTTP probe:

```sh
uv run --frozen python tests/live_skill_check.py
```

The probe exercises revision after refusal, acknowledged trial adoption, service restart, retained execution, receipt rejection, and canonical memory writes.

This feature supplies development integration evidence for the skill learning cycle.
It does not prove native execution, safe GTA behavior, city autonomy, or continuous pedestrian reasoning.
Windows gameplay trials and native cancellation evidence remain separate acceptance requirements.
