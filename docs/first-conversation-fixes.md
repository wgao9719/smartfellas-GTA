# First GTA conversation fixes

On 2026-10-11, TOWER-PENGU reproduced and fixed the five reported conversation
defects directly on `win/validation`, starting at `427eebe`. These changes have
automated Windows evidence only. They were not deployed or verified in GTA.

## Supplied game evidence

The reported environment was GTA V Legacy 1.0.3889.0, SHVDN
3.7.0-nightly.193, adapter `427eebe`, and local Qwen2.5-7B through llama.cpp,
with Franklin at Legion Square. The main clone's
`artifacts/rig/evidence/diag-first-conversation.json` and
`diag-memory-turn.json`, plus `artifacts/rig/screens/q5*.png` and `q6*.png`,
show action narration entering subtitles and a cancellation status persisting
over a successful reply. The second conversation addresses a different actor
UUID with no retrieved history; this supports the targeting report, rather than
establishing a memory-storage defect. Enter answering Simeon's phone call and
the ambiguous-timeline refusal were reported by the in-game tester.

## Defects and changes

1. The dialogue prompt included eligible action descriptions, and the grounding
   gate allowed imperative narration. The real provider's speech prompt now
   excludes action metadata and requires spoken words. A deterministic gate
   removes registry names/descriptions and bounded English stage-direction
   paraphrases. Bracket, parenthesis and asterisk spans are removed across token
   and sentence boundaries. The action stage retains its separate context and
   sees only accepted speech. Corpus, paused-stream and TTS tests cover the
   observed `Turn toward the player.` replies. FR-DLG-02 sentence streaming is
   preserved; FR-DLG-03's separate action stage remains intact. See
   [dialogue grounding](dialogue-grounding.md).
2. Enter cleared the text box before SHVDN's next Tick, bypassing its previous
   default-group control suppression. Core now requests explicit phone/frontend
   controls in all three groups on every typing tick and through key release.
   The adapter uses SHVDN's `DISABLE_CONTROL_ACTION` native wrapper; the pinned
   `Game.DisableControlThisFrame` helper has no group overload. Phone call/task
   detection blocks submission and keeps the draft. This implements typed-input
   isolation under SPECS.md §4.2.
3. F7's turn restart wrote `conversation canceled`, and status had no expiry.
   Typing, thinking and delivered dialogue now replace it; transient notices
   expire after five seconds. Recording remains explicitly indicated.
4. Selection had no persistent visible link to the ped. The script thread now
   draws a small blue cone above the selected ped's head and renders a persistent
   label with availability while the guarded binding is valid. Invalid bindings
   remove it; an unavailable native marker is reported without claiming it was
   drawn. This addresses §4.2 and audit `a-sections-0-5.md` finding 6. Camera-cone
   scoring and actor identity rules are unchanged.
5. All session-open 409s became a generic save/profile refusal. The service now
   returns `reason_code: timeline_ambiguous` for `TimelineAmbiguityError`. The
   adapter directs the player to choose a history in the dashboard, then press
   the configured confirmation key (F10 by default) to reconnect. Tests cover
   real ambiguous branches, dashboard selection/retry, remapped keys, generic
   409/426 behavior and bounded malformed-error handling. Existing §8.3 and
   FR-MEM-08 timeline isolation remain unchanged; see the
   [wire contract](wire-contract.md).

## Reproduction before implementation

- Speech corpus, provider prompt and streaming regressions: **54 failed,
  10 passed** before the fix. Additional paraphrase cases first produced
  **11 failed, 63 passed**; nested-asterisk cases first produced
  **2 failed, 81 passed**. Final focused speech validation passed **786 tests**
  across the nine-file run (785) and additional direct-TTS regression (1).
  A final spot check added four more close paraphrases and four ordinary controls:
  **4 failed, 87 passed** before correcting those patterns; the final focused
  corpus/streaming/TTS command passed **98 tests**.
- `uv run --frozen pytest -W error tests/test_adapter_conversation_ui.py -q`:
  **4 failed** before implementation, then **5 passed** including timeline retry
  wiring. These are source-wiring failures; they do not reproduce GTA natives.
  Executable Core input/presentation regressions also pass.
- `uv run --frozen pytest -W error tests/test_timeline_refusal.py -q`:
  **1 failed, 1 passed** before implementation; then **2 passed**.
- The C# test filtered to `TimelineAmbiguityDirectsPlayer` first reported
  **1 failed**, showing the generic profile notice instead of dashboard guidance.

## Windows validation

The existing worktree `.venv` used Python 3.12.14 and uv 0.12.17. Commands ran
in PowerShell 5.1 with these process-local settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Command | Observed result |
|---|---|
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 581 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 2,280 passed, 1 skipped in 226.54 seconds; exit 0 |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitle chunks, 5 acknowledgments, 1 simulated action, 2 delivery events |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 acknowledgments, confirmed PTT start/stop, 2 delivery events |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, post-pin turn delivered, 8 durable memories including 1 pre-pin memory, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 withdrawal, reconnect, stale action rejected, started receipt retained in export, ambient history purged, no invented completion |
| `git diff --check` and `git diff --cached --check` | Passed |

The first full Python run passed **2,272 tests, 1 skipped** in 229.20 seconds,
before the final four paraphrase corrections. The table records the final Python
rerun and all four live checks repeated after those corrections. The sole skip
is the opt-in real whisper.cpp test. The initial full ruff check found one extra
blank line in the new native-wiring test; it was corrected before the passing run.

Logs are in the worktree's ignored `artifacts/validation-first-conversations/`.
The live scripts use ephemeral loopback ports, temporary databases, fake game
state and synthetic speech, and stop their own fixture processes. The QA listener
on port 8765 stayed on process 35412. No game, launcher, installation folder,
service configuration, cloud setting, deployment or remote git state was changed.

## Native verification still required

Repeat both local-model conversation prompts and inspect streamed subtitles,
TTS, action timing and delivered history for narration. During F7 input, exercise
an incoming story call, Enter and arrows, key repeats, and Escape; confirm the
call is not accepted by submission and an active-call draft survives. Cancel and
start another turn, checking current status and expiry. In a crowd, identify the
seated man by the marker before sending, keep the same actor for a memory turn,
and verify marker movement and disappearance after invalidation/cancel. Select
an ambiguous history in the dashboard and use F10 (and a remapped confirmation
key) to reconnect, confirming future memories remain excluded.

The [UI rig checklist](actions/ped-classification-and-pin.md) records these as
provisional. English narration rules are bounded, not a semantic guarantee for
all paraphrases or languages. No new native action capability or rig gate is
marked verified.
