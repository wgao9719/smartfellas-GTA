# Living Los Santos

Living Los Santos is a GTA V Legacy Story Mode project for interactive, persistent, evolving NPCs in-game. Developers build on Macs. The game adapter runs on Windows with Script Hook V and ScriptHookVDotNet.

**Status: development foundation. G0 through G4 have not passed.** The Windows rig will connect through Tailscale later. No result in this repository proves GTA gameplay, Windows audio, or release readiness. The [progress and handoff note](docs/progress.md) lists open issues and next steps.

## Current implementation

- The Python service handles authenticated sessions, streaming text, action proposals, bounded commands, acknowledgments, and cancellation.
- Automatic action expiry and cancellation produce durable service facts. These facts do not confirm native completion or withdrawal.
- SQLite stores actor identities, memories, scoped knowledge, relationships, and timeline branches. Older-save branches exclude future memories.
- Optional semantic memory retrieval shares provider budgets and cloud consent. Disabled or unavailable embeddings use lexical retrieval.
- The pilot pack contains 12 original characters. Spawn locations and voice assets remain unverified placeholders.
- The C# adapter builds for `net48` on macOS. Its HTTP workers remain separate from the GTA script thread.
- The simulated C# adapter exercises the real Python HTTP service. Core tests cover stale identities, cancellation, and action restrictions.
- Standalone local speech probes support whisper.cpp, Piper, Windows SAPI, and explicit microphone capture.
- The optional speech coordinator connects push-to-talk, transcription, playback, subtitles, cancellation, and delivery receipts through the game bridge.
- Subtitles, synthesized speech, delivery receipts, and memories share one display-safe text rule. The adapter rejects unsafe dialogue instead of rewriting it.
- Whisper mode uses a hold key or a typed `/w ` prefix. Whispers reach only nearby targets, and development perception limits overhearing to the whisper radius.
- The development perception interface filters witnesses and runs optional authored triggers with actor-private evidence.
- Development social scenes support recipient-owned invitations, private contexts, bounded turns, and memories from confirmed delivered quotes.
- An optional development scheduler initiates social scenes and requests turns. It waits for separate delivery and hearing evidence.
- Retained hearing changes social partner preferences and later topics. Own refusals create temporary game-time pauses. Memory controls remove these effects.
- A development neighborhood simulation tracks 48 named characters through virtual schedules, bounded catch-up, and reservations for future embodiment.
- Authenticated development neighborhood routes expose explicit initialization, bounded advancement, and current-session inspection of an authored pack.
- Optional automatic neighborhood advancement follows eligible world snapshots within per-scope budgets. Initialization remains explicit.
- Deterministic replay uses recorded model responses and a controlled clock through the real service HTTP boundary.
- The isolated development skill interpreter validates finite action programs, versioned dependencies, cumulative limits, and per-operation permissions.
- Development skill learning connects sourced candidates, recorded trials, revisions, actor-private adoption, retirement, and selection after database reopen.
- Authenticated development skill routes connect generation, trials, adoption, and selected execution to canonical bridge acknowledgments for orientation and waiting.
- Optional automatic skill practice learns from external action receipts, revises candidates, and retains acknowledged trials for later bounded practice.
- The local dashboard supports character inspection, memory edits, export, backups, save timeline selection, and live session diagnostics.
- The dashboard can pin an eligible live pedestrian, label pinned pedestrians, and forget a pinned character with typed confirmation.
- Provider controls persist configured-price budgets, cloud consent, and an explicit local fallback. Revocation cancels active remote requests.

AI starts disabled. The fake provider requires explicit development selection. Hosted AI requires explicit cloud opt-in and model configuration. Release adapter builds advertise no native action capabilities while game validation remains pending.

The [semantic memory guide](docs/semantic-memory.md) explains optional embeddings, shared budgets, and lexical fallback.

## Run the service

Requirements: Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

1. Install the locked dependencies.

   ```sh
   uv sync --frozen --group dev
   ```

2. Run the service.

   ```sh
   uv run --frozen python -m service.lls
   ```

3. Open the local dashboard at <http://127.0.0.1:8765>.

The service uses loopback by default. Runtime data stays in the user's application-data directory. `LLS_DATABASE` selects a separate database for testing. Path settings expand `~` and environment variables and must resolve to absolute paths.

## Select an AI provider

AI stays disabled until you select a provider. The service reads process environment variables. It does not load `.env` automatically.

For an existing local OpenAI-compatible server, set these variables before starting the service:

```sh
export LLS_PROVIDER=local_openai
export LLS_LOCAL_BASE_URL=http://127.0.0.1:1234/v1
export LLM_DIALOGUE_MODEL='your-local-model'
```

Replace `your-local-model` with the model identifier from your local server. The local provider requires a literal loopback IP address. It disables redirects and environment proxies. It never forwards `LLM_API_KEY` to the local server. Cloud consent can remain off.

The local server must run on the service computer. This setting does not install a server or download a model. CPU and AMD-compatible servers can expose the same API. This project's local provider tests use controlled HTTP fixtures. Real model performance remains unverified.

The dashboard can select and save a provider without restarting the service. Saved settings override provider environment values. Hosted requests require configured prices, sufficient daily limits, and explicit cloud consent. Set `LLM_API_KEY` in the service environment. Keep credentials outside Git.

Saving provider settings revokes cloud consent. Review the saved destination, models, and limits before enabling cloud. Revocation survives restart, including a stale `LLS_CLOUD_OPT_IN=1` environment value. See [provider controls](docs/provider-controls.md) and [.env.example](.env.example).

## Run the simulated conversation

```sh
uv run --frozen python -m service.lls --demo
```

The demo uses a temporary database and the fake provider. It makes no cloud calls. It does not run GTA natives.

## Verify the implementation

1. Run the Python checks.

   ```sh
   uv run --frozen ruff check service tests tools
   uv run --frozen pytest -W error
   ```

2. Run the C# core tests with .NET 10.

   ```sh
   dotnet test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release
   ```

3. Run the Python/C# HTTP integration check.

   ```sh
   uv run --frozen python tests/live_bridge_check.py
   ```

4. Run the synthetic speech integration check.

   ```sh
   uv run --frozen python tests/live_bridge_check.py --speech
   ```

Both integration checks start an isolated temporary service. They verify serialized events, commands, subtitles, action acknowledgments, and session closure. Speech mode uses synthetic audio and mocked playback. It verifies push-to-talk states and delivery ordering without opening audio devices. Both checks detect a task-local `.dotnet` installation automatically.

The process-death check kills its owned temporary service during a fake C# action. It verifies withdrawal, reconnect, stale-action rejection, and preservation of the canonical started receipt without inventing completion. The receipt remains visible in the admin profile export; the unpinned ambient actor's session-only memories are purged during recovery, just as on normal close.

```sh
uv run --frozen python tests/live_bridge_crash_check.py
```

The action-outcome check expires and cancels fake action requests over real HTTP. It verifies private outcome memories after service restart and rejects late completion.

```sh
uv run --frozen python tests/live_action_outcome_check.py
```

The [trace replay tool](docs/trace-replay.md) tests recorded turns with a controlled clock and temporary database. It makes no cloud or native calls.

The [skill tools and development bridge](docs/skill-runtime.md) connect declarative candidates, persistent learning, and acknowledged orientation/waiting trials. Native skill execution remains unverified.

The [automatic skill probe](docs/skill-autonomy.md) uses ordinary dialogue and action receipts. It verifies revision, acknowledged adoption, and retained practice after restart without calling skill routes.

```sh
uv run --frozen python tests/live_skill_autonomy_check.py
```

The [social HTTP probe](docs/social-development.md) exercises three simulated NPCs through the development social endpoints. Its `--autonomous` mode verifies automatic initiation and turns. The [social adaptation probe](tests/live_social_adaptation_check.py) checks topic changes and memory erasure across service restarts. Native social interaction remains unverified.

The [neighborhood checks](docs/neighborhood-development.md) compare three simulated days across catch-up, incremental execution, and restart. A separate loopback HTTP check verifies the authenticated routes across service restart. Neither check creates native GTA entities.

```sh
uv run --frozen python tests/live_neighborhood_autonomy_check.py
```

The synthetic load check runs 200 confirmed turns with 36 tracked NPCs. It verifies bounded queues, private context, and persistent output recovery. It reports the HTTP rate-limit wait separately from synthetic turn timing.

```sh
uv run --frozen python tools/simulated_load.py
```

## Game controls

| Key | Action |
|---|---|
| F6 | Select an eligible pedestrian within 8 m of the player and 45° of the camera's look direction, ranked by angle and distance. Without a target in view, show unavailable feedback or name the nearest refused role. |
| F7 | Type a message to the selected pedestrian. |
| N | Hold to talk. Release to send. |
| B | Hold to whisper. Release to send. |
| Backspace | Outside typed input, cancel playback, queued dialogue and actions, and clear the selected target. While typing, delete a character. |
| Escape | While typing, cancel and clear the target. Otherwise reserved for GTA's pause menu; an observed pause also cancels and clears selection. |
| F8 twice | Pin the selected pedestrian. The conversation becomes permanent. The pedestrian may not return after you leave. |

The six interaction keys are configurable in `scripts/LivingNPC/LivingNPC.ini`; Escape stays reserved outside typed input, so choose another key for cancel. Only ordinary civilians are eligible. Police, security, emergency, gang, animal, story, and unrecognized peds stay protected. The [classification capability sheet](docs/actions/ped-classification-and-pin.md) records what still needs rig verification. Camera selection, pause detection, key conflicts, and native cancellation remain provisional until tested in GTA on the rig.

## Build the game adapter

1. Fetch the checksum-pinned public SHVDN compile reference.

   ```sh
   uv run python tools/fetch_shvdn_reference.py
   ```

2. Build the adapter.

   ```sh
   dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release
   ```

3. Stage a deployment bundle.

   ```sh
   uv run python tools/deploy_adapter.py --configuration Release
   ```

Staging does not install the adapter. The deployment manifest records checksums and marks game validation unverified. The Windows rig needs a compatible, user-installed loader and SHVDN runtime.

## Remaining work

The complete objective includes working voice conversations, native perception, and native actions. The [development perception interface](docs/perception-development.md) connects scoped evidence and optional triggers in backend tests. The GTA adapter publishes world and actor snapshots, but it does not emit native perception incidents or witness evidence. Persistent spawning and save reconciliation require game tests. Native NPC-to-NPC conversations, native learned behaviors, and neighborhood changes remain unfinished. The first playable alpha will not complete that objective.

See the [implementation and acceptance plan](docs/implementation-plan.md), [development validation](docs/development-validation.md), [speech probes](docs/speech-probes.md), and [dependency record](LICENSES.md). The [reference specification](references/SPECS.md) defines the acceptance gates.
