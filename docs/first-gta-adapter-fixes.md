# First GTA adapter configuration and threading fixes

On 2026-10-11, work continued directly on `win/validation` in the integration
worktree on TOWER-PENGU, starting at `f013351`. The reported rig is GTA V Legacy
1.0.3889.0, Script Hook V v3889.0, and SHVDN 3.7.0-nightly.193. This change was
not deployed or run in GTA. The existing QA service was left running.

## Reproduction and fix

Loading the installed SHVDN DLL in Windows PowerShell 5.1 and reading a temporary,
synthetic INI reproduced `URL=http://127.0.0.1:8765` becoming `http`. No installed
INI, token, or game file was modified. Before the fix, four xUnit cases against
the extracted existing endpoint construction/validation failed with
`UriFormatException` for `http`, empty text, malformed text, and an invalid port.
This violates configuration defaults and safe failure in SPECS.md FR-OPS-02;
transport restrictions follow sections 6.1 and 7.1.

`RawIni` in Core now reads every setting without SHVDN's comment parser. This
also preserves punctuation in tokens and save labels and avoids a load-time
exception for values beginning with `//`. `ServiceEndpoint` validates the
existing numeric-IP/HTTP/loopback policy and Debug private-network opt-in,
rejects credentials and URL suffixes, and returns a safe loopback default with
a fallback flag. Invalid typed values and unreadable files use defaults.
Only fixed operational codes enter logs. Configuration warnings render after
thread binding for eight seconds; profile confirmation still gates connection.

`ThreadAffinity` extracts and tests first-tick binding from the `f013351` hotfix,
with explicit rejection of unbound calls and attempts to rebind from another
thread. Key handlers ignore pre-tick and wrong-thread events. Abort no longer
partially mutates controller state before a game-thread exception; it skips
game/controller cleanup off-thread and attempts transport/log disposal
independently. Game-thread rules follow SPECS.md section 6.2.

## SHVDN source review

The [nightly.193 release](https://github.com/scripthookvdotnet/scripthookvdotnet-nightly/releases/tag/v3.7.0-nightly.193)
identifies source commit `4afee05937377d33d87930821e922bb2d3987dae`:

- [ScriptSettings.cs, lines 62–99](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/scripting_v3/GTA/Shvdn/Script/ScriptSettings.cs#L62)
  subtracts one from the `//` index before taking a substring, truncating the
  URL and throwing for values beginning with `//`.
- [Script.cs, lines 395–425](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/core/Script.cs#L395)
  dispatches queued keyboard events before Tick, including on the first loop.
- [Script.cs, lines 470–495](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/core/Script.cs#L470)
  permits script- or domain-thread abort, warns about races, and raises Aborted
  before terminating the script thread.
- [ScriptDomain.cs, lines 1066–1154](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/core/ScriptDomain.cs#L1066)
  constructs scripts on its domain thread. [NativeFunc.cs, lines 246–256](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/core/NativeFunc.cs#L246)
  and [ScriptDomain.cs, lines 1493–1564](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/core/ScriptDomain.cs#L1493)
  support native calls in that context. Constructor decorator registration is
  therefore retained; there is no demonstrated thread defect in those calls.

No other constructor Game/World calls were found. The bridge and log workers
use HTTP, queues, and files; they do not call game methods. Remaining rig risks
include decorator registration and identity, player validity during loads,
load-boundary detection, native task completion/withdrawal, pin persistence,
and cleanup during domain-thread abort or AppDomain unload. Off-thread abort
cannot guarantee native cleanup. Compilation still uses the pinned v3.6.0
public API while the rig uses nightly.193. Source review and simulated checks
do not certify that runtime combination or pass G0.

## Windows validation

Windows PowerShell 5.1, uv 0.12.17, Python 3.12.14, and .NET SDK 10.0.401.
The existing worktree environment was reused, with these process-local settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Exact command | Observed result |
|---|---|
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release --filter FullyQualifiedName~ConfigurationTests` | Before fix: 4 failed with `UriFormatException` |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | **451 passed**, 0 failed, 0 skipped; includes 39 new configuration/affinity cases |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors |
| `uv run --frozen ruff check service tests tools` | All checks passed |
| `uv run --frozen pytest -W error` | **1,663 passed, 1 skipped in 200.01 seconds**, exit 0 |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitle chunks, 5 ACKs, 1 simulated action |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 ACKs, recording and stop confirmed |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8 durable memories, pre-pin memory retained, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 withdrawal, reconnect, stale action rejected, started receipt retained in profile export, ambient history purged, no invented completion |
| `git diff --check` | Passed |

The sole pytest skip is the opt-in real whisper.cpp check. All requested live
checks exited 0 and completed owned-service and temporary-directory cleanup;
they selected free ports and did not bind the QA service's port 8765. Speech
and native actions in these checks are simulated.

A separate PowerShell 5.1 smoke loaded the built Core DLL under .NET Framework
and passed literal URL/token parsing, malformed endpoint fallback, and IPv6
loopback validation. Read-only peer review additionally checked unbound and
bound affinity there. This covers framework differences from the net10 xUnit
host; it does not run the GameAdapter inside SHVDN.
