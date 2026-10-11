# Development validation

Date: 2026-10-10. Environment: macOS 27.0, arm64, Python 3.12.4, uv 0.12.17, .NET SDK 10.0.100.

These results concern the local working tree based on `200c39cfc02323f8d2811f180bd8606964810f1a`. The implementation remains uncommitted. Later changes require their own checks. No G0–G4 gate has passed.

## Verified locally

| Check | Result | Boundary |
|---|---|---|
| `dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | Passed with zero warnings and errors | Compiles `net48` against the pinned public SHVDN API. Does not prove that GTA loads it. |
| `dotnet test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 104 tests passed | Pure C# safety, queues, transport, cancellation, subtitles, push-to-talk, diagnostics, registry validation, and sampled movement tracking. No native game calls. |
| `uv run python tests/live_bridge_check.py` | Passed | Actual C# HTTP worker and controller against an isolated Python service with a fake game. |
| `uv run python tests/live_bridge_check.py --speech` | Passed | Actual C# HTTP worker with synthetic capture, transcription, synthesis, and playback. No audio hardware. |
| `uv run python tests/live_bridge_crash_check.py` | Passed | Actual C# worker against an owned service process killed after a durable started acknowledgment. One fake task withdrawal, reconnect, stale-action rejection, and no invented completion. |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed | Real loopback HTTP, automatic expiry, control cancellation, late completion rejection, duplicate suppression, and two private outcome memories after service restart. No native action execution. |
| `uv run python tests/live_social_check.py` | Passed | Three fake NPCs, six delivered turns, twelve reported memories, distinct opinions, and enforced turn limit over real loopback HTTP. |
| `uv run python tests/live_social_check.py --autonomous` | Passed | Two automatic turns, distinct opinions, two reported memories, and an uninformed third actor over real loopback HTTP. Explicit synthetic hearing evidence remains necessary. |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed | Four service processes, two simulated encounters, a changed later topic, and source-memory erasure through HTTP. Independently delivered later speech remains separate history. No native hearing or model calls. |
| `uv run --frozen pytest -q -W error -o faulthandler_timeout=90` | 1,026 tests passed in 102.27 seconds | Includes service tests and `tools/tests`, semantic retrieval, microphone readiness, dashboard privacy, durable service outcomes, social adaptation and replay erasure, automatic skill practice, neighborhood advancement, and the corrected 36-NPC/200-turn load check. No real game or audio devices. |
| `uv run python tools/trial_skill.py --demo` | Passed | Two authored candidate versions and simulated primitive receipts. No generated or persisted repertoire. |
| `uv run python tools/learn_skill.py --demo` | Passed | Fake candidate provider, refused trial, revised trial, private adoption, and version-two selection after database reopen. No model or native calls. |
| `uv run python tests/live_skill_check.py` | Passed | Real loopback HTTP, refused version one, acknowledged version two, adoption, service restart, and retained execution through four primitive commands. No real model or native calls. |
| `uv run --frozen python tests/live_skill_autonomy_check.py` | Passed | Ordinary dialogue and action receipts trigger revision, acknowledged adoption, and retained practice after restart. Four practice primitives, two external actions, zero direct skill-route requests. |
| `uv run python tools/neighborhood_demo.py` | Passed | 48 actors, three game days, 2,304 activity records per mode, matching catch-up/incremental/restart results, and zero backlog. No native bindings or model calls. |
| `uv run python tests/live_neighborhood_check.py` | Passed | Normal service over loopback HTTP, 48 actors, three game days, ten bounded advancement calls, and matching virtual state after service restart. No native commands or model requests. |
| `uv run --frozen python tests/live_neighborhood_autonomy_check.py` | Passed | Thirty virtual actors, seven game hours, three automatic attempts, 120 confirmed writes, zero backlog, and preserved state after restart. No manual advancement requests. |
| `uv run python -m service.lls --demo` | Passed with zero cloud calls | Explicit fake provider and temporary data. |
| `uv run --frozen ruff check service tests tools` | Passed | Static checks only. |
| `git diff --check` | Passed | Tracked diff whitespace only. Most new implementation files remain untracked. |

The cross-language probe confirmed two displayed subtitle chunks, the final marker, one simulated action, and five acknowledgments. It waited for the complete dialogue. The service accepted the action's `started` and `completed` sequence.

The speech probe confirmed actual recording status before stop, transcribing status after microphone stop, two audio chunks, two subtitle receipts, the final marker, and thirteen acknowledgments. Its synthetic providers never opened audio devices. Both probes keep game verification false.

The process-death probe kills only its owned temporary service process after verifying the started acknowledgment in SQLite. The C# controller withdraws its fake action once. Restarting the same database establishes a new session and rejects the stale action. Canonical history retains the started event and its non-completion memory. It contains no completed event for that action. Native task withdrawal remains unverified.

Automatic action expiry and cancellation now persist separate service authorization events linked to the conversation turn. These events explicitly leave native completion and withdrawal unconfirmed. Their private memories survive service restart. Fourteen focused tests cover pinned timestamps, bounded retries, partial writes, command eviction, lifecycle cleanup, and erasure. Independent blocked-acknowledgment and administrative mutation probes found no new defect.

Pending outcomes prevent command eviction until their writes finish. Administrative edits and erasure join these writes before changing data. A persistence failure returns 503 and preserves bounded retry state. A late acknowledgment cannot restore an expired or canceled command. Pending records remain in memory until committed, so abrupt process death can still lose an uncommitted outcome.

The storage tests cover process reopen, actor isolation, protagonist isolation, timeline rollback, ambiguous branch selection, source deletion, derived-data deletion, confidence limits, backups, and a fixed retrieval regression set. Semantic and fallback modes use synthetic vectors. They validate retrieval behavior, not real embedding-model quality.

Optional embeddings share the provider gateway, consent state, and durable budgets. Source checks run before each outbound batch and after gateway admission. Joined executor reads prevent shutdown from overtaking canceled retrieval. Tests delete sources during cached lookup and admission. The service returns lexical results when embeddings fail.

The shared action registry supplies Python and C# argument bounds. Registry metadata does not activate native capabilities. Local OpenAI-compatible provider tests use controlled HTTP fixtures. No real local model server or hosted provider trial has passed.

Development perception now connects witness filtering, private canonical events, memories, and optional triggers. Tests cover closed default gates, current bindings, sample freshness, duplicate incidents, speech priority, and reported quote receipts. The ordinary observation endpoint still rejects grounded truth claims. Native perception remains unavailable.

Conversation familiarity changes only after every reply chunk and the final marker receive full delivery acknowledgments. Tests reject partial, canceled, expired, authored, and disabled-provider output. Relationship reset suppression survives database reopen and source reimport. A retry regression preserves one completion timestamp and one familiarity update after a projection failure.

The speech-focused suite passed 87 tests with mocked devices and providers. Independent microphone review passed 69 tests and 100 cancellation, replacement, and late-error probe cycles. Runtime tests cover push-to-talk identity, unknown confidence, subtitle timing, delivery deduplication, and joined erasure. Stalled audio-driver lifecycle calls leave the service event loop responsive. Cleanup still waits for the driver to return.

Provider tests cover persistent budget reservations, configured-price estimates, explicit local fallback, and consent revocation. Independent review found and fixed cancellation-to-fallback, incomplete-stream action, and canceled-admission accounting bugs. An OS-backed lease excludes a second AI controller while allowing ordinary database readers. macOS cross-process locking passed. Windows locking remains unverified.

Social core and runtime tests cover recipient-owned decisions, private context, fresh hearing evidence, exact retelling, timeline visibility, interruption, and joined storage cleanup. The development scheduler now starts eligible two-NPC scenes and requests turns automatically. Its 28 focused tests cover fair selection, cooldowns, independent acceptance, foreground interruption, deadlines, and bounded calls. The autonomous HTTP probe sends no invitation, review, or turn requests. Native listening remains unverified.

Four isolated subprocess tests intercept DNS and socket access through real provider wiring, including runtime embedding retrieval. Disabled, pre-consent, and revoked phases make zero outbound attempts. Consent-on reaches the intercepted DNS and connection paths. Explicit local-provider selection and configured local fallback reach only the intercepted loopback connection. The probes use synthetic credentials and prevent actual network traffic.

The full synthetic load check uses 36 tracked NPCs and 200 confirmed turns through the real service with in-process HTTP transport. It preserves all 200 delivery and completion events after reopening the database. It preserves 66 persistent output memories and expires 134 ephemeral output memories on session closure. Every provider call checks actor-private context. The uninvolved persistent actor receives no outputs. Command history remains within 256 entries. The tool respects the unchanged HTTP rate limit and reports its wait separately. Its timing measures synthetic service work, not game performance or model latency.

The final validation pass exposed a rolling-window retry bug in the load driver. The old calculation could wait 118.2 seconds against its 90-second timeout. The driver now tracks its own admission timestamps and waits for the next slot. The focused load suite passed three tests in 61.77 seconds. The service rate limit and test timeout remain unchanged.

The neighborhood simulation passed 37 focused tests and independent review. It records separate virtual plans and outcomes with bounded catch-up, timeline visibility, erasure suppression, and exact-owner embodiment reservations. Reservations create no native actor bindings. Work and social intentions remain unfulfilled.

Authenticated neighborhood routes expose explicit initialization, bounded advancement, and inspection at the current session time. Their 21 tests passed, including joined cancellation before actor erasure. An optional scheduler now advances initialized neighborhoods automatically within event and attempt budgets. Its 25 tests cover shared-scope fairness, cancellation, erasure, actor bindings, and reservations. Native embodiment and persistent world consequences remain unfinished.

The skill compiler and interpreter passed 23 tests and independent adversarial review. They reject arbitrary execution, duplicate keys, altered dependencies, forged receipts, suppressed cancellation, and unsupported composed follow operations.

Skill learning now connects the background provider gateway, bounded private source excerpts, candidate compilation, interpreter trials, and actor-private adoption. The persistence tests cover revisions, retirement, rewind, source deletion, reset suppression, and retrieval after reopening the database. A transaction-level audit check rejects deletion and reimport races across database connections. The fake learning demonstration selected revision two after reopening and left a second actor's repertoire empty.

Development skill routes connect generation, bridge trial, adoption, and selected execution through the current action policy and command queue. The runtime permits only `face_player` and `wait_here`. It resolves primitive receipts after canonical event and memory persistence. Trials retain terminal game time, canonical receipt sources, and `game_verified=false`. Independent review found and fixed execution continuing after retirement. Skill acknowledgments pin each status's first receipt time across projection retries. Real model trials and native skill execution remain unverified.

Independent review also found delayed acknowledgment persistence restoring a canceled command. The final publication check revalidates ownership, command state, and deadlines after all awaited writes. The regression preserves canonical observation evidence while refusing trial success, further primitives, and adoption. All 34 runtime skill tests passed independently and within the final combined run.

The loopback skill probe runs candidate generation, refused trial, revision, acknowledged trial, and private adoption. After a service restart, it executes the retained revision through two fresh commands. The complete probe records four primitive commands and eight canonical action memories. It rejects missing-start and malformed acknowledgments and verifies diagnostics during execution. Its provider and adapter receipts are explicit fakes.

The automatic skill probe creates an external measured action failure through ordinary dialogue and receipts. The scheduler generates version one, records its simulated refusal, generates version two, and adopts its acknowledged bridge trial. A fresh external experience after service restart selects version two for bounded practice. The probe makes no skill-route requests. This demonstrates post-outcome practice, not automatic replacement of a foreground action proposal.

Independent review found and fixed two scheduler races. Canceling a claimed worker before its first task step now ends the durable episode immediately. A reserved social worker blocks competing skill scheduling before social state exists. The runtime also retains episode authority checks while joined cancellation removes the worker reference. Twenty-four scheduler tests and eleven independent adversarial checks passed.

The adapter reports sampled cumulative movement for orientation and waiting. The tracker refuses nonfinite positions, backward time, overflow, and sampling gaps above 250 milliseconds. Seventeen pure C# tests cover the tracker. These sampled measurements do not establish continuous native distance tracking or GTA acceptance.

A combined test run exposed an AI controller deadlock during garbage collection. A reentrant registry lock fixes finalization during owner registration. A subprocess regression confirms that the controller can release and reacquire ownership.

The adapter now uses `scripts/LivingNPC/LivingNPC.ini` and writes bounded timestamped logs beside it. Build-produced identity pins the adapter and core DLL digests. Staging cannot relabel an older DLL with current source metadata. The service health response reports its startup source fingerprint and registry digest. It explicitly identifies a source-tree process without claiming a packaged release.

The G0 evidence validator checks file digests, required artifacts, and measurement fields. Supporting service and build metadata can truthfully remain unverified. Gameplay evidence cannot use simulation output. The validator always requires manual gate review and cannot certify native observations.

Deterministic replay passed 18 focused tests and an independent review. Reports distinguish queued output from submitted delivery receipts. They retain action outcomes after closure and report timeout failures. Replay uses a temporary database, recorded model responses, and an injected clock. It performs no cloud or native calls.

## Retained social adaptation

Repeated confirmed hearing now affects the listener’s later partner preference and topic selection. Own valid refusal or withdrawal creates a bounded game-time pause. Invalid model output creates no durable preference. Separate actor contexts preserve direction and source identity. Memory correction, deletion, actor reset, rewind, and protagonist changes remove ineligible effects.

Independent review found two defects and verified their fixes. Speaker-only memories could displace heard encounters from the retained-history window. Canonical replay could also restore an erased ordinary memory even when adaptation ignored it. Separate decision/hearing windows and suppression checks now cover both paths.

The history read uses indexed owner-first joins and a fixed 4,000,000 SQLite-instruction ceiling. An in-memory measurement with 128 owners and 100 decision anchors each used approximately 3.135 million instructions and 152 milliseconds. A regression covers that dataset. The complete 1,026-test Python run includes it. Larger or mixed dense histories can still exhaust the ceiling. Exhaustion stops scheduling without reserving model calls.

The four-process HTTP probe verifies source-linked topic selection after restart and deletion. It does not prove that deleting one memory erases every independent later quotation. The broader social suite checks private boundaries, fair partner choice, stale-read cancellation, malformed decisions, retained-source correction, replay suppression, and bounded context.

The incoming Claude validators prompted a separate read-only audit. Current GTA text sinks already remove formatting tokens, but subtitle acknowledgments still report the original text. The adapter also uses a six-civilian-model allowlist without explicit police, military, or gang role detection. These are remaining native-delivery and classification gaps. Typed/PTT whisper controls and a user-facing ambient adoption flow remain unfinished.

## Rendered dashboard check

The review used a fresh temporary database containing the 12-character pilot pack and explicit authored knowledge fixtures. An isolated installed headless Chrome profile opened the real local service. The review did not use the user's live data.

The browser displayed all 12 character choices. Selecting Lena Ortiz displayed the authored fixture memory. Editing that memory persisted through the HTTP route. The profile export contained the edited text. The deletion dialog required the exact memory identifier. Canceling the dialog preserved the memory.

The 390-pixel layout had no horizontal overflow. Screenshots were inspected locally. The temporary browser profile and service stopped after review. The computer-use browser connector could not open localhost, so the review used the installed browser directly through its standard debugging interface.

This check establishes those dashboard flows on this Mac. It does not establish Windows browser behavior, a complete accessibility audit, or every administrative control.

A second temporary Chrome review exercised provider selection, configured prices, explicit cloud consent, revocation, and persistence after reload. It confirmed zero model requests and no horizontal overflow at 390 pixels. Live session diagnostics displayed a simulated actor and its compatibility manifest. The review stopped its browser and service and removed the temporary browser profile.

A third isolated Chrome review exercised embedding configuration, saved dimensions, embedding-only cloud consent, revocation, and persistence after reload. It confirmed zero model requests and no horizontal overflow at 390 pixels. The browser connector still refused localhost, so the review used the installed Chrome debugging interface. The temporary browser profile and service stopped after review.

A fourth isolated Chrome check held completed export, diagnostics, and memory responses in the browser. It reset an actor through the real dashboard and HTTP mutation, then released those stale responses. The export stayed hidden and empty. The diagnostics and memory views did not restore the previous text. Eight permanent response-race checks also cover profile changes, page hiding, export closure, pagination, and competing requests.

The incoming Claude plan prompted a microphone readiness review. The real microphone now waits for valid callback-delivered PCM before reporting recording. Silence is valid input. Focused tests cover first-frame delay, timeout, malformed frames, callback errors, cancellation, and replacement isolation. These tests use fake devices and do not establish microphone permission or audible input on the rig.

## Pending acceptance

The Windows rig has not connected. Tailscale deployment, game loading, native action behavior, microphone permission, actual voice models, save restoration, and performance remain unverified. A second Mac has not reproduced the build.

Grounded native observations remain disabled. Native action capabilities remain absent from Release builds until the rig validates them. Development autonomous social scheduling and virtual neighborhood simulation have local evidence. Native NPC-to-NPC interaction, native learned behaviors, and neighborhood changes remain unfinished.

Real provider performance and billing comparison remain unverified. Service authorization outcomes and acknowledged action outcomes persist as distinct canonical events and private memories. These events do not establish native acceptance.

The workflow in `.github/workflows/ci.yml` defines Windows and macOS checks. No GitHub Actions run has been observed for this working tree.

The first Windows CI run of the committed foundation exposed two product defects. Speech processes opened their suspended child with `PROCESS_SET_INFORMATION` instead of `PROCESS_SET_QUOTA`, so every Job Object assignment failed and no speech executable could run on Windows. `Settings.from_env()` also resolved the user profile when `LLS_DATABASE` was explicit, which raises on Windows without `USERPROFILE` and `HOMEPATH`. Both are fixed with macOS-runnable regressions; a Windows CI run must still confirm them.
