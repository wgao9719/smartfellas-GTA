# Living Los Santos

Interactive, persistent NPCs for **GTA V Legacy Story Mode**. The Python service runs locally; the C# game adapter runs on Windows with Script Hook V and ScriptHookVDotNet. Development and simulated checks also run on macOS.

**Status:** typed conversation, recent memory, facing the player, cancellation, and recovery have been observed in [Windows GTA QA](docs/windows-qa/README.md). Native NPC-to-NPC integration is being developed on the [npc2npc branch](https://github.com/wgao9719/smartfellas-GTA/tree/npc2npc); it is not included in main. Acceptance gates G0–G4 remain open; this is not a release-ready mod.

## What works

- Player dialogue, generated civilian personas, pinning, persistent memories, relationships, and save-scoped timelines.
- Backend social scenes with invitations, bounded turns, and confirmed-hearing memories, exercised through simulated development probes.
- A local dashboard for characters, memories, backups, timelines, and provider settings.
- Optional speech, semantic memory, development actions, learned skills, and neighborhood simulation. Native coverage varies; see [progress and limitations](docs/progress.md).

## Run the service

Requires Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen --group dev
uv run --frozen python -m service.lls
```

Open <http://127.0.0.1:8765>. For development features, set `LLS_RELEASE=0` before startup (`$env:LLS_RELEASE='0'` in PowerShell). The [Windows rig launcher](tools/windows-rig/start-service.ps1) selects development mode automatically.

The service binds to loopback and stores data in the user's application-data directory. `LLS_DATABASE` accepts an absolute database path. Environment settings come from the process; `.env` is not loaded automatically. The service defaults to release mode; development feature flags remain off unless explicitly enabled.

AI starts disabled. Select a provider in the dashboard; saved provider settings override environment values. Hosted AI requires model/pricing configuration and explicit cloud consent. See [provider setup](docs/provider-controls.md) and [.env.example](.env.example). Keep credentials outside Git.

## Build and install the adapter

Requires .NET 10. Build **Debug** for development action testing:

```sh
uv run python tools/fetch_shvdn_reference.py
dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug
uv run python tools/deploy_adapter.py --configuration Debug
```

Staging creates a deployment bundle; it does not install it. The Windows game needs compatible Script Hook V and SHVDN runtimes. Follow the [Windows rig guide](docs/windows-qa/README.md). Configure `ProfileId` and `SaveSlot` in `scripts/LivingNPC/LivingNPC.ini`, then confirm the current profile with F10 in-game.

Debug actions require `EnableUnverifiedActions=true`: face, wait, follow, stop following, safe-point movement, wander, flee, and verified gestures. Release builds advertise no native action capabilities. Pin persistence has a separate `EnableUnverifiedPinPersistence` setting.

## Game controls

| Key | Action |
| --- | --- |
| F10 | Confirm the current save/profile association. |
| F6 | Select an eligible civilian within 8 m and the camera's targeting cone. |
| F7 | Type a message to the selected NPC. |
| N | Hold to talk; release to send. |
| B | Hold to whisper; release to send. |
| Backspace | Cancel dialogue/actions and clear selection; delete characters while typing. |
| Escape | Cancel typed input; otherwise open GTA's pause menu. |
| F8 twice | Pin the selected NPC's identity and memory; does not guarantee their return. |

Keys are configurable in `LivingNPC.ini`; Escape stays reserved outside typing. Protected roles, including police and story characters, are excluded. Voice requires speech setup; see [speech probes](docs/speech-probes.md).

## Verify

```sh
uv run --frozen ruff check service tests tools
uv run --frozen pytest -W error
dotnet test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release
uv run --frozen python tests/live_bridge_check.py
```

The bridge probe runs a simulated C# adapter against a real temporary Python service. It does not validate GTA rendering or native tasks. Optional backend social testing is documented in the [social development guide](docs/social-development.md). For a standalone fake-provider conversation, run `uv run --frozen python -m service.lls --demo`.

See [development validation](docs/development-validation.md), the [implementation plan](docs/implementation-plan.md), and [dependency licenses](LICENSES.md) for broader checks and remaining work.
