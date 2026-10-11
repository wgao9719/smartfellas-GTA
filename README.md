# Living Los Santos

Interactive, persistent NPCs for **GTA V Legacy Story Mode**. The Python service runs locally; the C# game adapter runs on Windows with Script Hook V and ScriptHookVDotNet. Development and simulated checks also run on macOS.

**Status:** typed conversation, recent memory, facing the player, cancellation, and recovery have been observed in [Windows GTA QA](docs/windows-qa/README.md). NPC-to-NPC integration passes simulated checks but still needs GTA verification. Acceptance gates G0–G4 remain open; this is not a release-ready mod.

## What works

- Player dialogue, generated civilian personas, pinning, persistent memories, relationships, and save-scoped timelines.
- NPC-to-NPC invitations and automatic conversations, bounded native chat tasks, overhead dialogue, confirmed hearing, and authored fallback when AI is unavailable.
- A local dashboard for characters, memories, backups, timelines, and provider settings.
- Optional speech, semantic memory, development actions, learned skills, and neighborhood simulation. Native coverage varies; see [progress and limitations](docs/progress.md).

## Run the service

Requires Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen --group dev
LLS_RELEASE=0 uv run --frozen python -m service.lls
```

Open <http://127.0.0.1:8765>. This command selects development mode and enables automatic NPC-to-NPC scenes. On Windows PowerShell, set `$env:LLS_RELEASE='0'` before running `uv run --frozen python -m service.lls`; the [rig launcher](tools/windows-rig/start-service.ps1) already selects development mode.

The service binds to loopback and stores data in the user's application-data directory. `LLS_DATABASE` accepts an absolute database path. Environment settings come from the process; `.env` is not loaded automatically. Without `LLS_RELEASE=0`, the service defaults to release mode with social scenes disabled.

AI starts disabled. Select a provider in the dashboard; saved provider settings override environment values. Hosted AI requires model/pricing configuration and explicit cloud consent. See [provider setup](docs/provider-controls.md) and [.env.example](.env.example). Keep credentials outside Git.

## NPC-to-NPC defaults

- **Debug adapter:** enabled by default. Existing `LivingNPC.ini` files containing `EnableUnverifiedSocial=false` must be changed to `true` under `[Development]`.
- **Development service (`LLS_RELEASE=0`):** social scenes and automatic initiation default on. `LLS_DEV_SOCIAL=0` disables both; `LLS_DEV_SOCIAL_AUTONOMY=0` disables automatic initiation only.
- **Release:** NPC-to-NPC is disabled in both the adapter and service.

The adapter scans up to eight nearby civilians and allows at most two scenes. Nearby service-directed dialogue uses AI or authored fallback; native chatter farther away uses no model calls. Development limits remain four service scene attempts and 24 reserved model calls per session. See [social behavior and verification limits](docs/actions/npc-social-embodiment.md).

## Build and install the adapter

Requires .NET 10. Build **Debug** for NPC-to-NPC:

```sh
uv run python tools/fetch_shvdn_reference.py
dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug
uv run python tools/deploy_adapter.py --configuration Debug
```

Staging creates a deployment bundle; it does not install it. The Windows game needs compatible Script Hook V and SHVDN runtimes. Follow the [Windows rig guide](docs/windows-qa/README.md). Configure `ProfileId` and `SaveSlot` in `scripts/LivingNPC/LivingNPC.ini`, then confirm the current profile with F10 in-game.

Other native actions require `EnableUnverifiedActions=true` in Debug; see the [18-action catalog](docs/actions/development-task-catalog.md). Pin persistence has a separate `EnableUnverifiedPinPersistence` setting.

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
uv run --frozen python tests/live_bridge_check.py --embodied
```

The embodied probe runs the C# controller against a real temporary Python service with simulated game observations. It does not validate GTA rendering or native tasks. For a standalone fake-provider conversation, run `uv run --frozen python -m service.lls --demo`.

See [development validation](docs/development-validation.md), the [implementation plan](docs/implementation-plan.md), and [dependency licenses](LICENSES.md) for broader checks and remaining work.
