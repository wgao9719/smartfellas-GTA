# Declarative skill language and persistence

`service/lls/skills.py` compiles finite JSON programs.
The interpreter can request existing action primitives through an injected executor.
It cannot execute Python, shell commands, file access, network access, or arbitrary GTA natives.
Compilation does not grant an actor permission to execute an action.

## Program format

This example requests orientation followed by a short wait:

```json
{
  "schema_version": 1,
  "skill_id": "face_then_wait",
  "version": 1,
  "action_class": "conversational_courtesy",
  "max_duration_seconds": 4.0,
  "max_distance_meters": 1.0,
  "steps": [
    {
      "op": "action",
      "proposal": {
        "action_id": "face_player",
        "arguments": {"duration_seconds": 1.0}
      }
    },
    {
      "op": "action",
      "proposal": {
        "action_id": "wait_here",
        "arguments": {"duration_seconds": 1.0}
      }
    }
  ]
}
```

An `action` step contains an existing action proposal.
A `require` step checks `consent`, `can_move`, `mod_following`, or `observed_threat`.
A `call` step identifies another skill by `skill_id` and an exact `version`.
Called skills must exist in the actor's visible library.
Shared code cannot contain narrative `reason_for_player` text.

| Limit | Value |
|---|---|
| Source bytes | 16 KiB |
| Direct steps | 16 |
| Expanded operations | 32 |
| Call depth | Four |
| Maximum program duration | 30 seconds |
| Maximum program distance | 30 meters |

The compiler rejects recursion, duplicate JSON keys, missing dependencies, and composition beyond declared limits.
The interpreter recompiles dependencies before execution and compares every compiled field.
It rechecks current identity, policy, capabilities, and consent before each operation.
Each returned primitive receipt must match its operation identity.
Cleanup belongs to the current execution and joins before cancellation completes.

The standalone interpreter also recognizes `follow_player` and `stop_following`.
It refuses following inside a called skill.
The [development skill bridge](skill-runtime.md) currently supports orientation and waiting only.

## Private retained skills

`SkillStore` stores immutable structural code by profile, skill identity, and version.
Candidates, trial outcomes, adoption, and retirement belong to one persistent actor.
Sharing structural code does not share private context or automatically grant adoption.

Every record follows profile, actor, protagonist, branch ancestry, and game-time visibility.
Canonical source events support each candidate and outcome.
Source deletion removes dependent records. Actor reset suppresses forgotten sources after reopen and reimport.
The final trial transaction rechecks binding, sources, dependencies, and deletion history.

`SkillLearner` sends bounded actor-visible source excerpts and private trial history through the background provider gateway.
It compiles and registers a candidate. It does not execute or adopt that candidate.
Requested skill identity, version, and action class constrain the generated result.

## Reproducible demonstrations

Run the standalone authored trial demonstration:

```sh
uv run --frozen python tools/trial_skill.py --demo
```

It demonstrates refusal followed by a successful revision with simulated primitive receipts.
It does not generate candidates through a model or persist a repertoire.

Run the persistent learning demonstration:

```sh
uv run --frozen python tools/learn_skill.py --demo
```

It uses an explicit fake candidate provider with the real gateway, interpreter, and store.
The provider reads a refused trial and returns a revised candidate.
The actor adopts the completed revision and selects it after database reopen.
A second actor has no adopted repertoire.

Both demonstrations retain `game_verified=false` and make zero cloud requests.
Standalone simulated trials do not qualify for adoption through the development bridge.
