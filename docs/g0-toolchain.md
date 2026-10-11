# G0 toolchain status

G0 is **not done**. The source builds on this Mac. No Windows rig, second Mac, game native, microphone, or gameplay performance check has passed.

| Evidence | Current result |
|---|---|
| .NET SDK | 10.0.100, macOS arm64 |
| Public compile reference | SHVDN v3.6.0, SHA-256 pinned in `compatibility.yaml` |
| Adapter build | `net48`, Debug and Release compile successfully |
| Pure C# tests | 61 tests pass on `net10.0` |
| Actual C# → Python HTTP round trip | Passed with a simulated game and explicit fake provider |
| Native actions | `face_player` and `wait_here` have provisional executors. Runtime support is unverified. |
| Release native capabilities | Empty until the rig establishes support |
| Tailscale deployment | Tooling implemented. No connected rig has verified deployment. |

The HTTP probe observed two rendered subtitle chunks, one final marker, one simulated action, and five acknowledgments. The service diagnostics recorded the simulated action as completed. This establishes the cross-language transport, not GTA behavior.

## Implemented boundaries

`LivingNPC.Core` targets `netstandard2.0`. It has no GTA dependency. `IGameAdapter` isolates current game state, actor resolution, native task dispatch, observed completion, subtitles, and status display.

`LivingNPC.GameAdapter` targets `net48`. Only the SHVDN script thread calls GTA APIs. The worker has separate HTTP lanes for controls, observations/acknowledgments, and command polling. A stalled observation request or long poll does not block PTT. Body sizes and queue capacities are bounded.

The action controller checks the session, runtime generation, immutable runtime instance, expiration, policy version, ownership, control policy, proximity, and current game state. Persistent actors qualify only when the adapter's current registry classifies them as mod-owned. The current native implementation creates no persistent actors. It never adopts a script-owned ped.

The controller records `started` after task dispatch. It records `completed` only after the adapter observes the completion condition. Mission takeover cancels tracking without clearing the engine's task. Queue saturation stops dispatch. Pending acknowledgments stay queued for retry. Deferred terminal acknowledgments survive an acknowledgement-queue overflow in bounded session state.

Targeting uses an explicit six-model civilian allowlist. Each transient binding gets a random decorator marker and runtime instance ID. A missing marker, changed model, or changed instance invalidates the binding. The marker is not a persistent identity. The allowlist and decorator behavior require rig verification.

The adapter uses Backspace to cancel by default. F6 selects a target. F7 opens a small typed input field. N sends PTT controls. All four keys are configurable. The input field currently supports Latin letters, digits, spaces, and basic punctuation. Escape closes text input. Controller bindings, text composition, font scaling, and full accessibility remain incomplete.

Subtitles follow display rule v1. `service/lls/display.py` and `LivingNPC.Core/DisplayText.cs` implement it, and `tests/fixtures/display_text.json` holds the shared cases. The rule turns `~n~` into a space and removes GTA formatting tokens, tags, and any remaining `~`, `<`, or `>`. It turns control characters and line or paragraph separators into spaces, removes invisible format characters, and collapses whitespace without trimming. The service also applies NFC and removes every Unicode format, private-use, and surrogate code point, so its output always passes the adapter check.

The service applies the rule before speech synthesis, queuing, delivery receipts, and memories. The adapter renders dialogue and speaker labels verbatim. It rejects unsafe dialogue with an `unsafe_display_text` acknowledgment and never rewrites it. Status text and the typed echo use the same rule locally.

## Shared action contract

`data/action_registry.json` supplies the same eight action descriptions and argument schemas to Python and C#. Python reads the file. The C# build embeds it as a resource. Both reject unknown arguments, invalid types, nonfinite numbers, missing required fields, and values outside the structural bounds.

The registry grants no capabilities. Every executor is marked unverified. The adapter's native capability gate and live-state policy remain separate. G0 additionally limits face/wait durations to five seconds.

Transient bindings now leave the registry after despawn, identity mismatch, script takeover, or distance beyond 60 meters. The adapter queues `actor.removed` for the service. Cancellation records remain for 125 seconds, beyond the 120-second speech command window. Saturation fails closed instead of evicting an active cancellation record.

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

Curated spawn/reconciliation, adoption, controller input, persistent identity binding, full native action coverage, and complete input accessibility remain incomplete. Native status values, heading conditions, decorator reliability, mission ownership, random events, and pause behavior require recorded tests.

The adapter cannot detect every save-load boundary from dates alone. It detects loading state, protagonist changes, and a full-date rewind. An explicit profile and optional parent branch selection remain necessary for ambiguous saves.

The subtitle presenter displays one queued chunk at a time. It acknowledges exactly the rendered text, with `display_rule`, only after a render pass. Its receipt does not prove that the player read it. Audio timing and interrupted delivery require further integration before the speech gate can pass.
