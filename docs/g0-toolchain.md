# G0 toolchain status

G0 is **not done**. The source builds on macOS and Windows. No in-game native, microphone, or gameplay performance check has passed. The second Mac repeated only the Mac half of G0-7.

| Evidence | Current result |
|---|---|
| .NET SDK | 10.0.100 on the first Mac and 10.0.401 on the second, both macOS arm64; 10.0.401 on Windows |
| Public compile reference | SHVDN v3.6.0, SHA-256 pinned in `compatibility.yaml` |
| Adapter build | `net48`, Debug and Release compile successfully |
| Pure C# tests | 545 tests pass on `net10.0` on Windows with the continuity guard. One reads `GTA.PedHash` from the compile reference |
| Actual C# → Python HTTP round trip | Passed with a simulated game and explicit fake provider |
| In-game targeting | Failed with `8df3db5` and `a98b0bd` on GTA V Legacy 1.0.3889.0 / Script Hook V v3889.0 / SHVDN 3.7.0-nightly.193. Post-startup decorator registration was followed by a GTA crash; that implementation is withdrawn. The decorator-free guard needs in-game verification. |
| Native actions | `face_player` and `wait_here` have provisional executors. Runtime support is unverified. |
| Release native capabilities | Empty until the rig establishes support |
| Tailscale deployment | Tooling implemented. No connected rig has verified deployment. |

The HTTP probe observed two rendered subtitle chunks, one final marker, one simulated action, and five acknowledgments. The service diagnostics recorded the simulated action as completed. This establishes the cross-language transport, not GTA behavior.

## Implemented boundaries

`LivingNPC.Core` targets `netstandard2.0`. It has no GTA dependency. `IGameAdapter` isolates current game state, actor resolution, native task dispatch, observed completion, subtitles, and status display.

`LivingNPC.GameAdapter` targets `net48`. Only the SHVDN script thread calls GTA APIs. The worker has separate HTTP lanes for controls, observations/acknowledgments, and command polling. A stalled observation request or long poll does not block PTT. Body sizes and queue capacities are bounded.

The action controller checks the session, runtime generation, immutable runtime instance, expiration, policy version, ownership, control policy, proximity, and current game state. Persistent actors qualify only when the adapter's current registry classifies them as mod-owned. A pinned pedestrian enters that registry under its existing actor id. The registry keeps it only while it is still the mod's. The adapter never adopts a script-owned ped. It creates no curated persistent actors yet.

The controller records `started` after task dispatch. It records `completed` only after the adapter observes the completion condition. Tick stops an action when its actor or world becomes ineligible, including distance beyond 15 m. Task release has a separate, shared check used by Tick, direct cancellation (Backspace), and `scene.cancel`: a safe world and an existing, live actor with matching generation and runtime instance, no busy/combat state or Protected policy, and ambient or mod ownership. Distance alone does not prevent release. Cancellation is acknowledged `canceled`; a still-owned task is released, while a takeover, identity change, protection change, combat, or unsafe world stops tracking without clearing the engine's task. The adapter rechecks the binding's handle, model, continuity record, generation and instance, samples fresh ownership/policy, and clears only while the recorded script task still reports active. Missing native facts fail closed. Release exceptions emit the fixed `task_release_failed` code through the bounded adapter log and still remove the task record.

Three native start refusals or observed action failures without an intervening observed completion latch a safety stop. An unsupported command schema version stops immediately. The controller cancels active and queued work using the same release guards, displays an error, and continues acknowledging commands without dispatching new work. Only a new session controller, created by session reset or adapter reload, clears the latch; ordinary cancellation does not.

Queue saturation stops dispatch. Safety cancellation and expiration cleanup still run when acknowledgments are backlogged. Pending acknowledgments stay queued for retry. Deferred terminal acknowledgments survive an acknowledgement-queue overflow in bounded session state.

Targeting classifies nearby peds from sampled facts: model, ped type, relationship group, cuffed state, police vehicle, player group, mission flag, and owning script in Debug builds. A ped becomes a target only when its model, ped type, and relationship group all indicate a civilian and its ownership is ambient. The adapter and service share `data/ped_roles.json`. The service recomputes the role from the snapshot's facts and keeps the stricter result. Missing facts classify as unknown and protected. Each binding receives a fresh actor UUID and runtime instance ID, tied to its handle, model hash, and per-binding continuity record. The [classification capability sheet](actions/ped-classification-and-pin.md) lists the facts and expected values that require rig verification.

**Crash finding: decorators are not used; post-startup decorator registration crashed GTA 1.0.3889.0 with SHVDN 3.7.0-nightly.193 in QA.** The original constructor's registration silently failed. The deployed `a98b0bd` unlock/register/readback attempt then logged `target_decorator_unavailable`. Brian reported that a manual console unlock/register/relock attempt also left registration false and the lock true. About two minutes later, Windows Application Event 1000 at 05:08:06 reported `GTA5.exe` version `1.0.3889.0`, exception `0xc0000005`, faulting module unknown; Windows Error Reporting Event 1001 reported `BEX64`. This is supplied in-game evidence, not a crash reproduced by the automated suite. Treat post-startup unlocking and registration as unsafe on this build. The registration implementation and its former rig instructions are withdrawn; do not retry them.

The replacement uses no decorator API, reflection on `DecoratorInterface`, or writes to game memory for binding. Every tick checks the handle/model pair and a continuity record containing the last position, sample time, alive/dead state, health, ownership, and policy facts. Missing or dead entities, a different handle/model, implausible displacement for elapsed time, ownership/policy changes, and uncertain samples permanently invalidate the binding. Death cannot be undone by a later alive sample. Sampling gaps above 2,000 ms fail closed. Motion uses a 15 m/s foot or 120 m/s vehicle budget and a bounded 1.5 m position reserve; only unused speed budget replenishes that reserve, so repeated small jumps cannot renew slack every tick. Two samples at the same time must have the same position. Health loss while alive is allowed, but health increases or a changed maximum health invalidate the binding. These are conservative implementation limits, not measured GTA tolerances. Native actions, pinning, and persistence release use the same guard. FR-ID-01/FR-ID-04 invalidations queue `actor.removed`; reacquiring a recycled handle creates a fresh actor UUID/runtime instance without the old history or pending actions. This is a conservative continuity check, not an engine identity token: an indistinguishable replacement between observations cannot be proved absent, and native recycling coverage remains unverified.

F6 selects an eligible target within 8 m of the player and a 45° half-angle cone around the camera look direction. The pure Core scorer minimizes `distance / 8 + angle / 45`, then breaks ties by player distance and runtime handle. `GameplayCamera.Position` and `Direction` are sampled on the script thread. No eligible candidate in the cone yields no target; invalid camera geometry is a technical failure. Refused peds remain unselectable; the fallback still names the nearest refused role by player distance, for example `Nearest: police officer — not available`. Selection reports `Target: <name> (Available)`, `Busy`, `Out of range`, or `Not available`. Genuine technical failures have distinct targeting-unavailable feedback and fixed log-once codes (`target_sampling_failed`, `target_camera_unavailable`, `target_binding_limit`, `target_binding_lost`) instead of appearing to be an empty candidate pool or a role refusal. The cone only applies to selection: a locked turn retains the existing proximity and identity revalidation. These geometry fixtures prove the pure algorithm, not native camera behavior or visibility; cone tuning and GTA selection still need rig verification.

Candidate discovery enumerates `World.GetAllPeds()` and explicitly excludes the player, missing/dead entities, and candidates beyond 8 m before the existing cone scorer. In the failed session, the ped-based nearby query returned 0 within 8 m and 5 within 20 m; the position-based 8 m query returned 1 and the complete pool contained 66. SHVDN's [actual overloads](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/scripting_v3/GTA/World.cs#L470-L516) both enumerate the ped pool, rather than `GET_PED_NEARBY_PEDS`; only the ped overload excludes its input ped. The position result may therefore have been the player. The observations do not prove a native nearby-query defect or a distance within 8 m of the player, despite apparent proximity to the camera. The reported camera direction `(0.923, -0.376, -0.079)` was valid. Refusal labels, continuity under walking and vehicle entry, handle recycling, and pin/action guards remain native rig checks.

The adapter uses remappable Backspace to cancel by default outside typed input. Cancellation clears playback, queued dialogue/actions, input, and `SelectedActorId`, and disarms an unconfirmed pin. Explicit cancellation sends the service's existing global cancel even without bound input; a bound scene also retains its local late-command rejection. F7 opens a small typed input field, where a leading `/w ` whispers and `//` keeps a literal leading slash. Backspace edits text; Escape cancels the conversation and clears the target. Outside typing, Escape is reserved for GTA's pause menu, including when configured as CancelKey; use another cancel key. An observed pause or other unsafe world state also cancels and clears selection, even without an active bridge/input. Actual pause-menu key delivery and cancellation timing remain unverified on the rig. The independent HTTP polling/control lanes do not establish an instantaneous barrier for unsolicited commands already in flight.

N sends PTT controls. Holding B sends whispered PTT controls with the whisper flag locked at key-down. F8 pressed twice pins the selected pedestrian. Starting F7, N, B, or a confirmed pin interrupts the old scene while preserving the selected actor. All six interaction keys are configurable. The B and F8 defaults still require a rig check for game key conflicts. The input field currently supports Latin letters, digits, spaces, and basic punctuation. Controller bindings, text composition, font scaling, and full accessibility remain incomplete.

Subtitles follow display rule v1. `service/lls/display.py` and `LivingNPC.Core/DisplayText.cs` implement it, and `tests/fixtures/display_text.json` holds the shared cases. The rule turns `~n~` into a space and removes GTA formatting tokens, tags, and any remaining `~`, `<`, or `>`. It turns control characters and line or paragraph separators into spaces, removes invisible format characters, and collapses whitespace without trimming. The service also applies NFC and removes every Unicode format, private-use, and surrogate code point, so its output always passes the adapter check.

The service applies the rule before speech synthesis, queuing, delivery receipts, and memories. The adapter renders dialogue and speaker labels verbatim. It rejects unsafe dialogue with an `unsafe_display_text` acknowledgment and never rewrites it. Status text and the typed echo use the same rule locally.

## Shared action contract

`data/action_registry.json` supplies the same eight action descriptions and argument schemas to Python and C#. Python reads the file. The C# build embeds it as a resource. Both reject unknown arguments, invalid types, nonfinite numbers, missing required fields, and values outside the structural bounds.

The registry grants no capabilities. Every executor is marked unverified. The adapter's native capability gate and live-state policy remain separate. G0 additionally limits face/wait durations to five seconds.

Transient bindings now leave the registry after despawn, identity mismatch, or script takeover. Distance alone never releases one. Near the 36-binding cap, the adapter releases the farthest bindings that are neither selected nor pinned. The adapter queues `actor.removed` for the service. The service then releases that runtime binding. A recycled handle can bind a new identity, which never sees the old identity's memories. A released ambient identity cannot bind any entity again. Cancellation records remain for 125 seconds, beyond the 120-second speech command window. Saturation fails closed instead of evicting an active cancellation record.

## Historical registration validation (withdrawn implementation)

The following results belong to `a98b0bd`, before the reported GTA crash. They demonstrate automated behavior only. They do not validate the unsafe registration approach and are not the validation results for the replacement guard.

2026-10-11, TOWER-PENGU, `integration` worktree on `win/validation`, starting at `8df3db5`. The reported game failure was reproduced in a pure test by extracting the original registration sequence and simulating GTA's silent refusal while locked: **1 failed, 1 passed** before the fix. The extracted candidate filter initially produced **6 failed, 20 passed**; the extracted binding predicate produced **12 failed, 2 passed** before permanent invalidation was added. These are simulated failures, not a new GTA run. Final focused candidate and binding suites passed 27 and 14 tests respectively; all are included in the full C# count below.

Commands used PowerShell 5.1, uv 0.12.17, Python 3.12.14, and .NET 10.0.401 with process-local settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Command | Observed result |
|---|---|
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release --filter FullyQualifiedName~DecoratorRegistrationTests` | Before fix: 1 failed, 1 passed; expanded final registration suite passes within the full run |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 487 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors; repeated after the final sampling-feedback change |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors; repeated after the final sampling-feedback change |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 2,173 passed, 1 skipped in 221.65 seconds; skip is opt-in real whisper.cpp |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitle chunks, 1 simulated action, 5 acknowledgments |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 acknowledgments, simulated recording and stop confirmed |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8 durable memories, 1 pre-pin memory retained, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: withdrawal, reconnect, stale-action rejection, unchanged started receipt in profile export, ambient history purged, no invented completion |
| `git diff --check` | Passed |

The existing QA listener on port 8765 was left running during those automated checks. Bridge checks used their own ephemeral ports and cleaned up their fixture processes. That worker did not deploy or push; the subsequent deployment and crash were reported by Brian. Real candidate geometry and refusals, handle recycling with history isolation, native commands, and pinning still need the replacement [in-game regression checks](actions/ped-classification-and-pin.md#continuity-and-identity-regression-checks).

## Decorator-free guard Windows validation

The unsafe binding API use was reproduced by `tests/test_adapter_binding_safety.py`: **1 failed** before removal, then **1 passed** after removal. That source check rejects decorator registration/unlock/read/write code in Core and GameAdapter. A pure Core test for a same-model replacement 200 m away after 16 ms also failed before the new guard. These reproduce the unsafe dependency and missing continuity protection, not the native GTA crash.

An additional focused regression exposed repeated per-tick slack allowing sustained 100 m/s foot movement: **1 failed, 67 passed** before the bounded-reserve fix, then **68 passed** after it. The guard suite also covers slow walking, vehicle entry and travel, death/revival, despawn, health changes, sampling gaps, role/ownership changes, and pin continuity. Candidate and pin-policy suites are included in the full C# result.

Validation on 2026-10-11 in the same Windows worktree used the process-local toolchain settings above.

| Command | Observed result |
|---|---|
| `uv run --frozen pytest -W error tests/test_adapter_binding_safety.py -q` | Before removal: 1 failed; after removal: 1 passed |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --filter FullyQualifiedName~RuntimeBindingGuardTests --no-restore --verbosity minimal` | Debug: 68 passed after the bounded-reserve fix |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 545 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors; rerun after the final guard change |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors; rerun after the final guard change |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 2,174 passed, 1 skipped in 212.55 seconds; skip is opt-in real whisper.cpp |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitle chunks, 1 simulated action, 5 acknowledgments, 2 delivery events |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 acknowledgments, PTT start/stop confirmed, 2 delivery events |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, post-pin turn delivered, `spawned_persistent`, 1 pre-pin memory retained, 8 durable memories, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 withdrawal, reconnect, stale-action rejection, started acknowledgment persisted and receipt retained in export, ambient history purged, no invented completion |

The existing QA listener on port 8765 was left running. Bridge checks used ephemeral fixture ports. No GTA launch, deployment, or push is part of this change. Native targeting, continuity thresholds, handle recycling, and pinning must be re-verified in game before claiming support.

## Remaining G0 checks

1. Record the rig hardware, driver, storefront, and GTA Legacy build in `compatibility.yaml`.
2. Capture five minutes of unmodded Story Mode frame times.
3. Verify one compatible Script Hook V and SHVDN runtime release.
4. Load the Mac-built adapter and record its assembly informational version.
5. Test target selection and each provisional action using its capability sheet.
6. Test deploy, reload, DLL lock behavior, and rollback.
7. Test loopback and authenticated Tailscale development networking.
8. Test text, PTT, microphone permission, subtitles, and audio.
9. Test first-load profile selection, service restart, continuing the same save, game reload, protagonist switching, save rewind, and different-slot saves with equal game date/time. Verify the warning and manual confirmation precede each new session, and a mismatched association opens no session.
10. Complete 20 interactions and a 20-minute soak.
11. Repeat the build, deployment, and reload on a second Mac.

Store the evidence under `docs/evidence/g0/` only after each procedure runs. Record failures as failures. Do not change `g0_passed` from `false` until every mandatory check passes.

## Known gaps

The compile reference does not establish compatibility with a current GTA build. Do not install the v3.6.0 loader merely because the project uses its API reference. The rig must use one complete compatible SHVDN release.

Curated spawn/reconciliation, verified pin persistence, controller input, persistent identity binding, full native action coverage, and complete input accessibility remain incomplete. Native status values, heading conditions, continuity thresholds and recycling detection, mission ownership, random events, and pause behavior require recorded tests.

The adapter cannot detect every save-load boundary from dates alone. It detects loading state, protagonist changes, and a full-date rewind; these native signals still need rig verification. No verified stable engine save GUID is available. There is no default profile: configure `ProfileId` and a stable manual `SaveSlot` label in the INI, then read the warning and press `ConfirmProfileKey` (default F10) before every new bridge session, including transport reconnects and protagonist switches. The service requires explicit confirmation and a matching stored association before attaching memories; unknown or different saves require a matching explicit selection or a new profile. A nonempty stored association cannot be changed through session-open. See [Save/profile association](windows-runtime.md#saveprofile-association).

An earlier full game date still creates a timeline branch that excludes future records. Optional `ParentBranchId` selection resolves existing timeline ambiguity after the profile/save association check. Equal-time divergent saves cannot be inferred from the date, and a missed load boundary remains a rig limit. Reload the adapter and select the correct association if the save change was not detected. Protagonist switches within one save retain its profile and save label; memory and relationship isolation remains per protagonist.

The subtitle presenter displays one queued chunk at a time. It acknowledges exactly the rendered text, with `display_rule`, only after a render pass. Its receipt does not prove that the player read it. Audio timing and interrupted delivery require further integration before the speech gate can pass.
