# Erasure and retention integration on Windows

Date: 2026-10-11. Worktree: `erasure-retention`, branch:
`win/fix-erasure-retention`. Integrated `win/validation` at
`83d096849b6c5c87a26449dc793b87726a4c37fe` into erasure commit `03512d5`.

## Conflicts and schema

- `service/lls/storage.py`: both branches assigned different changes to v5.
  Preserve the session-cleanup v4-to-v5 SQL from `b9ecd01`; retention now follows
  at v5-to-v6. The supported-version guard rejects versions greater than 6.
  Shared SQL definitions let fresh databases create the complete schema in one
  transaction with only a v6 version write; upgrades still commit each forward step.
- `tests/test_storage.py`: preserve cleanup and erasure coverage. Freeze a real
  v4 schema and seeded data from `f84de44`, and the upstream session-cleanup v5
  migration from `b9ecd01`, instead of only relabeling the current schema.
  Tests check both tables and their columns, preserved rows and pending sessions,
  collection-time backfill, fresh/upgrade schema equivalence, and refusal of v7.
- `docs/progress.md`: retain both features and identify retention as schema v6.

The automatic runtime merge exposed a separate integration defect: an operational
database error during retention killed maintenance before session cleanup could
retry. Maintenance now continues cleanup and retries retention on the next tick.
The regression verifies both retries and preservation of durable memories.

The migration regression first produced **3 failures, 1 pass**. The maintenance
regression first produced **1 failure**. After implementation, the focused
storage/gateway/skill suite passed **182 tests**, and the retention/session-cleanup
suite passed **20 tests**.

## Commands and observed results

All commands ran on TOWER-PENGU, in the assigned worktree and its own `.venv`.
In the table, `uv` means `& C:\Users\Brian\.lls-tools\uv\uv.exe` in PowerShell.
The C# commands used these process-local settings:

```powershell
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Command | Result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | **1,633 passed, 1 skipped**, 197.58 seconds; skip is opt-in real whisper.cpp |
| `uv run --frozen python tools/fetch_shvdn_reference.py` | Verified pinned SHVDN v3.6.0 compile reference in this worktree |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | **412 passed**, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | Passed, **0 warnings, 0 errors** |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | Passed, **0 warnings, 0 errors** |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitles, 1 simulated action, 5 ACKs |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/subtitle chunks, 13 ACKs, PTT record/stop |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8 durable memories, session closed, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | **Failed**, exit 1: line 163 expected a started-action memory after restart |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed: 2 private outcome memories and 2 service outcomes after restart, duplicate suppression, late completion rejected |
| `uv run --frozen python tests/live_social_check.py` | Passed: 3 participants, 6 delivered turns, 12 reported memories |
| `uv run --frozen python tests/live_social_check.py --autonomous` | Passed: automatic initiation, 2 delivered turns, 2 reported memories |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed: 4 service processes, 2 encounters, erasure removed topic and 2 derived speaker lines, independent hearing preserved |
| `uv run --frozen python tests/live_skill_check.py` | Passed: 2 candidate versions, 4 primitive commands, 8 canonical memories, restart retention and invalid-ACK rejection |
| `uv run --frozen python tests/live_skill_autonomy_check.py` | Passed: 2 external actions, 2 candidate versions, 4 practice primitives, restart retention |
| `uv run --frozen python tests/live_neighborhood_check.py` | Passed: 48 actors, 3 game days, 2,361 events, restart equivalence |
| `uv run --frozen python tests/live_neighborhood_autonomy_check.py` | Passed: 30 actors, 7 game hours, 120 events, automatic advancement and restart preservation |
| `uv run --frozen python tools/simulated_load.py` | Passed: 200 turns, 36 actors, 200 isolation checks, 200 completion/delivery events after restart, 0 network attempts |
| `uv run --frozen python -m service.lls --demo` | Passed: 3 events, 3 dialogue chunks, 1 simulated action command, 0 cloud calls |

The first C# test attempt reported 411 passes and one failure because this worktree
lacked the compile-only reference DLL. Fetching the verified reference resolved it.
An initial Ruff invocation during test editing found an import-order error; it was
corrected before the final passing check.

The crash-check failure reproduces the reported upstream session-cleanup issue:
startup recovery purges ambient session history, while the check still expects the
started-action memory. This task did not change that script or fix the separately
owned issue. Every other requested check passed. Command logs are retained locally
under the gitignored `artifacts/erasure-retention-integration/` directory.

All started probe processes exited through their cleanup paths. No GTA, Steam, or
Rockstar process was launched. Simulated transport, speech, skill, and neighborhood
results do not verify GTA natives, physical audio, model quality, or gameplay.
