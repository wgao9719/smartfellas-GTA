# Explicit save/profile association: Windows validation

Date: 2026-10-11. Machine: TOWER-PENGU. Worktree: `profile-association`,
branch `win/fix-profile-association`, starting at `f84de44`.

## Finding and scope

Audit `b-sections-6-11.md` finding 1 reproduced. Before the fix,
`test_session_open_requires_explicit_save_profile_selection` expected HTTP 409
but received HTTP 200. A profile ID alone silently opened a session, and the
adapter supplied `default`. The existing `profiles.save_slot` association check
was never used by session-open.

SPECS §8.3 requires a user-selected save/profile association, manual selection
without a stable save GUID, and a warning before changed-slot attachment.
Existing rewind branching and ancestor time cutoffs already implement the
separate no-future-memories requirement (FR-MEM-08). FR-ID-06 protagonist
isolation also already exists. Neither mechanism identifies equal-time saves
in different slots. §5.7 diagnostics now expose the confirmed association and
session-open warnings.

The fix requires nonblank `profile_id` and `save_slot` plus explicit
`profile_confirmed=true` before opening a session. It stores the first manual
association, including for legacy unassociated profiles, and rejects changing
an existing nonempty association. Missing/ambiguous selection stays disconnected;
it does not create a child branch that might inherit unrelated memories.
Rewind and parent-branch selection behavior remains unchanged.

The adapter has blank defaults and a warning/confirmation gate before each new
session. The INI selects the profile and manual save label; F10 confirms them.
The dashboard explains this flow and displays the association in diagnostics.
Fake/demo, C# probe, and recorded replay inputs explicitly select simulated saves.

## Commands and observed results

All commands ran on Windows in this worktree, using its own `.venv`. Process-local
tool setup was:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
uv sync --frozen --group dev --python C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe
```

Python is 3.12.14; .NET SDK is 10.0.401. The existing SHVDN compile reference was
copied into this worktree after checking the repository-pinned SHA-256. No game
installation was changed and no mod was downloaded.

| Command | Observed result |
|---|---|
| `uv run --frozen pytest -W error tests/test_bridge.py::test_session_open_requires_explicit_save_profile_selection -q` before fix | 1 failed: HTTP 200 instead of 409 |
| Same reproduction command after fix | 1 passed |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` final run | 1,360 passed, 1 skipped, 0 failed in 175.29 s |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 307 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitles, 1 simulated action, 5 ACKs |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 subtitles/audio chunks, 13 ACKs, PTT confirmed |
| `uv run --frozen python tests/live_bridge_check.py --whisper` | Passed: 2 subtitles, 5 ACKs; durable whisper flag |
| `uv run --frozen python tests/live_bridge_check.py --speech --whisper` | Passed: 2 subtitles/audio chunks, 13 ACKs; durable whisper flag |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8/8 durable memories, 1 closed session |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 withdrawal, reconnect, stale-action rejection, no invented completion |

An intermediate full Python run had 10 failures, 1,337 passes, and 1 skip. All
10 failures were the recorded replay fixture missing the new explicit selection
fields. The fixture was updated; the focused association/replay run then passed
31 tests. The final full run includes all new regression cases. The sole expected
skip is the opt-in real whisper.cpp test.

Regression coverage includes first load, blank/missing selection, legacy profile
association, reopened database/service continuity, rejected equal-time slot
reassociation (including explicit parent branch), a separate root for a new
profile, rewind cutoffs for memories/knowledge/relationships, and protagonist
switch privacy. C# cases cover warning-before-confirmation and invalidation on
observed loads, full-date rewinds, reconnects, and protagonist changes.

## Remaining limits

All bridge observations use fake game/speech adapters. GTA was not launched.
Native load detection, full-date restoration, warning rendering, F10 conflicts,
and physical audio remain unverified. There is no verified engine save GUID;
equal-time divergent saves require manual selection. A missed load boundary
requires an adapter reload and correct selection. Confirmation is conservatively
required after pause/resume and other unsafe-world transitions that close the
bridge. Existing native curated reconciliation limitations remain unchanged.
