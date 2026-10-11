# G0 toolchain status

G0 is **not done**. The source builds on two Macs. No Windows rig, game native, microphone, or gameplay performance check has passed. The second Mac repeated only the Mac half of G0-7.

| Evidence | Current result |
|---|---|
| .NET SDK | 10.0.100 on the first Mac and 10.0.401 on the second, both macOS arm64 |
| Public compile reference | SHVDN v3.6.0, SHA-256 pinned in `compatibility.yaml` |
| Adapter build | `net48`, Debug and Release compile successfully |
| Pure C# tests | 271 tests pass on `net10.0`. One reads `GTA.PedHash` from the compile reference |
| Actual C# → Python HTTP round trip | Passed with a simulated game and explicit fake provider |
| Native actions | `face_player` and `wait_here` have provisional executors. Runtime support is unverified. |
| Release native capabilities | Empty until the rig establishes support |
| Tailscale deployment | Tooling implemented. No connected rig has verified deployment. |

The HTTP probe observed two rendered subtitle chunks, one final marker, one simulated action, and five acknowledgments. The service diagnostics recorded the simulated action as completed. This establishes the cross-language transport, not GTA behavior.

## Implemented boundaries

`LivingNPC.Core` targets `netstandard2.0`. It has no GTA dependency. `IGameAdapter` isolates current game state, actor resolution, native task dispatch, observed completion, subtitles, and status display.

`LivingNPC.GameAdapter` targets `net48`. Only the SHVDN script thread calls GTA APIs. The worker has separate HTTP lanes for controls, observations/acknowledgments, and command polling. A stalled observation request or long poll does not block PTT. Body sizes and queue capacities are bounded.

The action controller checks the session, runtime generation, immutable runtime instance, expiration, policy version, ownership, control policy, proximity, and current game state. Persistent actors qualify only when the adapter's current registry classifies them as mod-owned. A pinned pedestrian enters that registry under its existing actor id. The registry keeps it only while it is still the mod's. The adapter never adopts a script-owned ped. It creates no curated persistent actors yet.

The controller records `started` after task dispatch. It records `completed` only after the adapter observes the completion condition. Tick, direct cancellation (Backspace), and `scene.cancel` share the same release eligibility check: a safe world and an existing, live actor with matching generation and runtime instance, no busy/combat state, no Protected policy or game-script ownership, and distance at most 15 m. If eligibility is lost, the action is acknowledged `canceled` and tracking stops without clearing the engine's task. Eligible cancellation releases the task; the adapter rechecks the binding's model/decorator, generation and instance, samples fresh ownership/policy, and clears only while the recorded script task still reports active. Missing native facts fail closed. Queue saturation stops dispatch. Pending acknowledgments stay queued for retry. Deferred terminal acknowledgments survive an acknowledgement-queue overflow in bounded session state.

Targeting classifies nearby peds from sampled facts: model, ped type, relationship group, cuffed state, police vehicle, player group, mission flag, and owning script in Debug builds. A ped becomes a target only when its model, ped type, and relationship group all indicate a civilian and its ownership is ambient. The adapter and service share `data/ped_roles.json`. The service recomputes the role from the snapshot's facts and keeps the stricter result. Missing facts classify as unknown and protected. Refused peds receive no decorator. Each transient binding gets a random decorator marker and runtime instance ID. A missing marker, changed model, or changed instance invalidates the binding. The marker is not a persistent identity. The [classification capability sheet](actions/ped-classification-and-pin.md) lists the facts and expected values that require rig verification.

The adapter uses Backspace to cancel by default. F6 selects a target and reports `Available`, `Busy`, `Out of range`, or `Not available`. Without an eligible ped, it names the nearest refused role, for example `Nearest: police officer — not available`. F7 opens a small typed input field, where a leading `/w ` whispers and `//` keeps a literal leading slash. N sends PTT controls. Holding B sends whispered PTT controls with the whisper flag locked at key-down. F8 pressed twice pins the selected pedestrian. All six keys are configurable. The B and F8 defaults still require a rig check for game key conflicts. The input field currently supports Latin letters, digits, spaces, and basic punctuation. Escape closes text input. Controller bindings, text composition, font scaling, and full accessibility remain incomplete.

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
9. Test service restart, game reload, protagonist switching, and save rewind.
10. Complete 20 interactions and a 20-minute soak.
11. Repeat the build, deployment, and reload on a second Mac.

Store the evidence under `docs/evidence/g0/` only after each procedure runs. Record failures as failures. Do not change `g0_passed` from `false` until every mandatory check passes.

## Known gaps

The compile reference does not establish compatibility with a current GTA build. Do not install the v3.6.0 loader merely because the project uses its API reference. The rig must use one complete compatible SHVDN release.

Curated spawn/reconciliation, verified pin persistence, controller input, persistent identity binding, full native action coverage, and complete input accessibility remain incomplete. Native status values, heading conditions, decorator reliability, mission ownership, random events, and pause behavior require recorded tests.

The adapter cannot detect every save-load boundary from dates alone. It detects loading state, protagonist changes, and a full-date rewind. An explicit profile and optional parent branch selection remain necessary for ambiguous saves.

The subtitle presenter displays one queued chunk at a time. It acknowledges exactly the rendered text, with `display_rule`, only after a render pass. Its receipt does not prove that the player read it. Audio timing and interrupted delivery require further integration before the speech gate can pass.
