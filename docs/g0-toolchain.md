# G0 toolchain status

G0 is **not done**. The source builds on macOS and Windows. No in-game native, microphone, or gameplay performance check has passed. The second Mac repeated only the Mac half of G0-7.

| Evidence | Current result |
|---|---|
| .NET SDK | 10.0.100 on the first Mac and 10.0.401 on the second, both macOS arm64; 10.0.401 on Windows |
| Public compile reference | SHVDN v3.6.0, SHA-256 pinned in `compatibility.yaml` |
| Adapter build | `net48`, Debug and Release compile successfully |
| Pure C# tests | 360 tests pass on `net10.0` on Windows after the target/cancel fix. One reads `GTA.PedHash` from the compile reference |
| Actual C# → Python HTTP round trip | Passed with a simulated game and explicit fake provider |
| Native actions | `face_player` and `wait_here` have provisional executors. Runtime support is unverified. |
| Release native capabilities | Empty until the rig establishes support |
| Tailscale deployment | Tooling implemented. No connected rig has verified deployment. |

The HTTP probe observed two rendered subtitle chunks, one final marker, one simulated action, and five acknowledgments. The service diagnostics recorded the simulated action as completed. This establishes the cross-language transport, not GTA behavior.

## Implemented boundaries

`LivingNPC.Core` targets `netstandard2.0`. It has no GTA dependency. `IGameAdapter` isolates current game state, actor resolution, native task dispatch, observed completion, subtitles, and status display.

`LivingNPC.GameAdapter` targets `net48`. Only the SHVDN script thread calls GTA APIs. The worker has separate HTTP lanes for controls, observations/acknowledgments, and command polling. A stalled observation request or long poll does not block PTT. Body sizes and queue capacities are bounded.

The action controller checks the session, runtime generation, immutable runtime instance, expiration, policy version, ownership, control policy, proximity, and current game state. Persistent actors qualify only when the adapter's current registry classifies them as mod-owned. A pinned pedestrian enters that registry under its existing actor id. The registry keeps it only while it is still the mod's. The adapter never adopts a script-owned ped. It creates no curated persistent actors yet.

The controller records `started` after task dispatch. It records `completed` only after the adapter observes the completion condition. Tick stops an action when its actor or world becomes ineligible, including distance beyond 15 m. Task release has a separate, shared check used by Tick, direct cancellation (Backspace), and `scene.cancel`: a safe world and an existing, live actor with matching generation and runtime instance, no busy/combat state or Protected policy, and ambient or mod ownership. Distance alone does not prevent release. Cancellation is acknowledged `canceled`; a still-owned task is released, while a takeover, identity change, protection change, combat, or unsafe world stops tracking without clearing the engine's task. The adapter rechecks the binding's model/decorator, generation and instance, samples fresh ownership/policy, and clears only while the recorded script task still reports active. Missing native facts fail closed. Release exceptions emit the fixed `task_release_failed` code through the bounded adapter log and still remove the task record.

Three native start refusals or observed action failures without an intervening observed completion latch a safety stop. An unsupported command schema version stops immediately. The controller cancels active and queued work using the same release guards, displays an error, and continues acknowledging commands without dispatching new work. Only a new session controller, created by session reset or adapter reload, clears the latch; ordinary cancellation does not.

Queue saturation stops dispatch. Safety cancellation and expiration cleanup still run when acknowledgments are backlogged. Pending acknowledgments stay queued for retry. Deferred terminal acknowledgments survive an acknowledgement-queue overflow in bounded session state.

Targeting classifies nearby peds from sampled facts: model, ped type, relationship group, cuffed state, police vehicle, player group, mission flag, and owning script in Debug builds. A ped becomes a target only when its model, ped type, and relationship group all indicate a civilian and its ownership is ambient. The adapter and service share `data/ped_roles.json`. The service recomputes the role from the snapshot's facts and keeps the stricter result. Missing facts classify as unknown and protected. Refused peds receive no decorator. Each transient binding gets a random decorator marker and runtime instance ID. A missing marker, changed model, or changed instance invalidates the binding. The marker is not a persistent identity. The [classification capability sheet](actions/ped-classification-and-pin.md) lists the facts and expected values that require rig verification.

F6 selects an eligible target within 8 m of the player and a 45° half-angle cone around the camera look direction. The pure Core scorer minimizes `distance / 8 + angle / 45`, then breaks ties by player distance and runtime handle. `GameplayCamera.Position` and `Direction` are sampled on the script thread. Invalid camera geometry or no eligible candidate in the cone yields no target. Refused peds remain unselectable and receive no new decorator; the fallback still names the nearest refused role by player distance, for example `Nearest: police officer — not available`. Selection reports `Available`, `Busy`, `Out of range`, or `Not available`. The cone only applies to selection: a locked turn retains the existing proximity and identity revalidation. These geometry fixtures prove the pure algorithm, not native camera behavior or visibility; cone tuning and GTA selection still need rig verification.

The adapter uses remappable Backspace to cancel by default outside typed input. Cancellation clears playback, queued dialogue/actions, input, and `SelectedActorId`, and disarms an unconfirmed pin. Explicit cancellation sends the service's existing global cancel even without bound input; a bound scene also retains its local late-command rejection. F7 opens a small typed input field, where a leading `/w ` whispers and `//` keeps a literal leading slash. Backspace edits text; Escape cancels the conversation and clears the target. Outside typing, Escape is reserved for GTA's pause menu, including when configured as CancelKey; use another cancel key. An observed pause or other unsafe world state also cancels and clears selection, even without an active bridge/input. Actual pause-menu key delivery and cancellation timing remain unverified on the rig. The independent HTTP polling/control lanes do not establish an instantaneous barrier for unsolicited commands already in flight.

N sends PTT controls. Holding B sends whispered PTT controls with the whisper flag locked at key-down. F8 pressed twice pins the selected pedestrian. Starting F7, N, B, or a confirmed pin interrupts the old scene while preserving the selected actor. All six interaction keys are configurable. The B and F8 defaults still require a rig check for game key conflicts. The input field currently supports Latin letters, digits, spaces, and basic punctuation. Controller bindings, text composition, font scaling, and full accessibility remain incomplete.

Subtitles follow display rule v1. `service/lls/display.py` and `LivingNPC.Core/DisplayText.cs` implement it, and `tests/fixtures/display_text.json` holds the shared cases. The rule turns `~n~` into a space and removes GTA formatting tokens, tags, and any remaining `~`, `<`, or `>`. It turns control characters and line or paragraph separators into spaces, removes invisible format characters, and collapses whitespace without trimming. The service also applies NFC and removes every Unicode format, private-use, and surrogate code point, so its output always passes the adapter check.

The service applies the rule before speech synthesis, queuing, delivery receipts, and memories. The adapter renders dialogue and speaker labels verbatim. It rejects unsafe dialogue with an `unsafe_display_text` acknowledgment and never rewrites it. Status text and the typed echo use the same rule locally.

## Shared action contract

`data/action_registry.json` supplies the same eight action descriptions and argument schemas to Python and C#. Python reads the file. The C# build embeds it as a resource. Both reject unknown arguments, invalid types, nonfinite numbers, missing required fields, and values outside the structural bounds.

The registry grants no capabilities. Every executor is marked unverified. The adapter's native capability gate and live-state policy remain separate. G0 additionally limits face/wait durations to five seconds.

Transient bindings now leave the registry after despawn, identity mismatch, script takeover, or distance beyond 60 meters. The adapter queues `actor.removed` for the service. The service then releases that runtime binding. A recycled handle can bind a new identity, which never sees the old identity's memories. A released ambient identity cannot bind any entity again. Cancellation records remain for 125 seconds, beyond the 120-second speech command window. Saturation fails closed instead of evicting an active cancellation record.

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

Curated spawn/reconciliation, verified pin persistence, controller input, persistent identity binding, full native action coverage, and complete input accessibility remain incomplete. Native status values, heading conditions, decorator reliability, mission ownership, random events, and pause behavior require recorded tests.

The adapter cannot detect every save-load boundary from dates alone. It detects loading state, protagonist changes, and a full-date rewind; these native signals still need rig verification. No verified stable engine save GUID is available. There is no default profile: configure `ProfileId` and a stable manual `SaveSlot` label in the INI, then read the warning and press `ConfirmProfileKey` (default F10) before every new bridge session, including transport reconnects and protagonist switches. The service requires explicit confirmation and a matching stored association before attaching memories; unknown or different saves require a matching explicit selection or a new profile. A nonempty stored association cannot be changed through session-open. See [Save/profile association](windows-runtime.md#saveprofile-association).

An earlier full game date still creates a timeline branch that excludes future records. Optional `ParentBranchId` selection resolves existing timeline ambiguity after the profile/save association check. Equal-time divergent saves cannot be inferred from the date, and a missed load boundary remains a rig limit. Reload the adapter and select the correct association if the save change was not detected. Protagonist switches within one save retain its profile and save label; memory and relationship isolation remains per protagonist.

The subtitle presenter displays one queued chunk at a time. It acknowledges exactly the rendered text, with `display_rule`, only after a render pass. Its receipt does not prove that the player read it. Audio timing and interrupted delivery require further integration before the speech gate can pass.
