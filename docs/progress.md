# Progress and handoff

Date: 2026-10-11. This note records the project state after the second working session on `living-los-santos-foundation`. The first session committed `a2727d8` on another Mac. The second session continued on a second Mac and closed the handoff's open issues. The full discussion is in the Hansard archive. See [Conversation history](#conversation-history).

**Third session (Windows, 2026-10-10/11, branch `win/validation`): start with the plain-English hand-off in [windows-qa/README.md](windows-qa/README.md).** The mod now runs in GTA V Legacy 1.0.3889.0 on a Windows rig with a local AI model. In the game, typed conversation works, the NPC turns to face the player and the game confirms it, recent-conversation memory works, invented job and mission claims are blocked, protected roles are refused, cancel works, and service restart recovery works. All automated checks pass on Windows.

## npc2npc integration with main (2026-10-11)

Merged main at `ee464d3` into the NPC-to-NPC branch. This includes the upstream Windows
QA reports, per-tick binding continuity, profile confirmation, protocol negotiation,
retention/erasure fixes, guarded P0 navigation, and Claude provider support. The social
scanner and extra action executors use the updated binding and cancellation paths.

Validation: the complete merged Python suite ran, followed by passing reruns of the
corrected provider test and the provider/action tests. C# core: **703 passed**. Debug and
Release adapter builds, Ruff, and the embodied conversation HTTP probe passed. The probe
used simulated game observations; the merged NPC-to-NPC path still needs GTA testing.
Upstream GTA observations are recorded in [Windows QA](windows-qa/README.md).

## Status

The in-game re-verification exposed speaker-attribution errors and contextual
two-sentence job leads. The [re-verification fix record](conversation-reverify-fixes.md)
documents named chat-history turns, contextual sentence classification, local
single-slot timeout handling, diagnostic fields, and measured remaining model
errors. These service changes (commit `c181fdf`) were deployed and re-verified in
GTA on 2026-10-11. Name and work recall and both job-bait prompts passed; see
[the latest in-game spot check](windows-qa/ingame-qa-3-spotcheck.md).

The first real conversations on adapter `427eebe` exposed action narration in
speech, an Enter/phone conflict, stale cancellation status, invisible target
selection, and an ambiguous-timeline notice. The
[first-conversation fix record](first-conversation-fixes.md) describes their
reproductions, service/adapter changes, validation and pending in-game checks.
The selected-ped marker and input behavior remain provisional; these fixes have
not been deployed or verified in GTA.

Living Los Santos is a development foundation. **G0 through G4 have not passed.** The first reported Windows GTA run reached adapter startup after the `f013351` thread-binding hotfix, while the prologue kept gameplay interactions disabled. The [configuration/threading follow-up](first-gta-adapter-fixes.md) records the defects, automated checks, and remaining rig work. This does not establish gameplay, Windows audio, or release readiness.

The later free-roam targeting fix `a98b0bd` is withdrawn after a reported GTA crash. **Current rule: decorators are not used; post-startup decorator registration crashed GTA 1.0.3889.0 with SHVDN 3.7.0-nightly.193 in QA.** The adapter logged `target_decorator_unavailable`; a manual console unlock/register/relock also failed to register the marker. About two minutes later, Windows Application Event 1000 at 05:08:06 reported `GTA5.exe` version `1.0.3889.0`, exception `0xc0000005`, faulting module unknown; WER Event 1001 reported `BEX64`. These are Brian's supplied QA observations. Post-startup registration/unlocking is unsafe on this stack and must not be retried.

The replacement removes every binding decorator operation and `DecoratorInterface` reflection. Bindings use handle/model plus per-tick continuity of position, time, life, health, ownership, and policy; any discontinuity or uncertain sample permanently removes the binding and queues `actor.removed`. A recycled handle can only acquire a fresh identity, with no old history. Pinning and native action/release paths use the same guard; pin persistence remains Debug-only and requires its explicit opt-in. This is a conservative sampling guard whose native coverage remains unverified, including indistinguishable replacements between observations.

Windows validation passed: **545 C# tests**, including 68 focused continuity-guard cases; both GameAdapter configurations built with **0 warnings and 0 errors**; ruff passed; full pytest reported **2,174 passed, 1 skipped**; plain, speech, pin, and crash-recovery live bridge checks passed. The source regression first failed on the decorator dependency, and pure guard regressions first failed on same-model recycling and repeated motion slack. [G0 toolchain status](g0-toolchain.md#decorator-free-guard-windows-validation) records exact commands and evidence. The [replacement rig procedure](actions/ped-classification-and-pin.md#continuity-and-identity-regression-checks) covers civilian targeting, refused-role labels, technical errors, walking/vehicle entry, discontinuities, history isolation, pinning, and stability. The QA service on port 8765 stayed running. This change is not deployed to the game and was not pushed.

The Python service, the C# adapter, and the development autonomy features run and pass tests on macOS against a fake game. [Development validation](development-validation.md) records each check and what it does not prove.

The first GitHub Actions run passed on macOS and failed on Windows. The second run, for `18d3e29`, confirmed those fixes on Windows: the speech process, network consent, synthetic load, and harness tests passed. It found five remaining Windows-only failures:

- one role-change timing assumption;
- one social-scheduler race with background maintenance;
- three deployment-script tests that had never run before;
- a live check that removed its database folder while the stopped service still held the file.

The following commit fixes those failures. Its own Windows run must confirm them.

## NPC-to-NPC first slice (2026-10-11)

Implemented current-session ambient social participation, recipient-owned deterministic
decisions, dedicated social prompts with observed partner/activity/weather context, a
separate durable chatter budget and dashboard controls, authored fallback dialogue,
display-time receipt validation, and scoped social interruption/revalidation cleanup.
The automatic scheduler still runs one scene per session.

The C# fake-street probe now exercises eight ambient actors over real HTTP. Two exchange
four lines, reserve four dialogue calls, stop at the turn limit, and create four confirmed
hearing events for that pair only. Closing the session removes its ambient actors and
memories. The fake provider makes no external model requests.

Validation on this Mac: Ruff passed; Python suite **1,394 passed, 4 skipped**; C# core
**281 passed**; `tests/live_bridge_check.py --social` and `--speech` passed. These are
simulated game/audio checks from the first slice. A subsequent Debug integration implements
nearby scans, overhead text, ped-to-ped tasks, and native hearing; it is compiled and
simulation-tested, but G0–G4 remain unpassed. See the
[NPC-to-NPC plan](npc-to-npc-plan.md) for the remaining rig work.

## Debug social integration follow-up

The adapter now scans up to eight nearby civilians, stages bounded approach/facing/chat
and ambient voice tasks, renders overhead text, and captures native listener observations.
One generated scene within 12 m can coexist with native-only chatter in the 12–35 m band.
Transport preserves render snapshots → acknowledgment → hearing order across retries.
Unrelated actor removal no longer cancels social or foreground conversations.

Automatic first-turn retelling selects only a speaker's own retained heard words, excludes
whispers, and preserves the existing exact-quote lineage and deletion rules.

Validation after the follow-up: **1,436 Python tests passed, 4 skipped; 307 C# tests
passed; Ruff and Debug/Release builds passed.** The embodied HTTP probe also passed
against the isolated fixture and the running fallback service.

The service is runnable with authored fallback and no model configuration. Debug and Release
adapter builds compile; the Debug bundle is staged in `artifacts/adapter-social-debug`.
The production social-controller probe passed against real HTTP, including the running
fallback service. All game observations in that probe are simulated. Native tasks, rendering,
voice availability, and frame cost remain unverified on Windows. See the
[social capability sheet](actions/npc-social-embodiment.md).

## Done

- Schema-v1 command versions are explicit and required on both bridge sides. Session opening reports actionable major-version mismatches and negotiates optional capabilities. Shared Python/C# contract fixtures preserve the spec examples and explain deliberate differences; see [wire contract](wire-contract.md).
- Authenticated Python service with streaming dialogue, action proposals, acknowledgments, cancellation, and durable action outcomes.
- SQLite memory with scoped knowledge, relationships, save-timeline branches, and optional semantic retrieval.
- Provider controls with budgets, explicit cloud consent, and a local OpenAI-compatible option. AI starts disabled.
- Shared filtering at the hosted HTTP boundary removes recognizable secrets and personal data from dialogue, action retries, embeddings, social turns, and skill requests. Synthetic HTTP tests cover filtering and unchanged ordinary game content; real provider and GTA behavior remain unverified.
- Pilot pack of 12 original characters. Spawn locations and voices are placeholders.
- C# adapter that builds for `net48` on a Mac against the pinned SHVDN reference. Release builds advertise no native actions.
- Speech probes and a speech coordinator with push-to-talk, subtitles, and delivery receipts.
- Development perception, social scenes, social scheduler, social adaptation, 48-character neighborhood simulation, and skill learning with automatic practice.
- Local dashboard for inspection, memory edits, export, backups, and diagnostics.
- Character-scoped export, source conversation erasure, and configurable seven-day raw-history retention. Schema v6 preserves durable memories and relationships; [policy and migration limits](privacy-retention.md) distinguish retained experiences from raw transcripts.
- Retryable session cleanup with SQLite-persisted pending state, startup recovery, and dashboard diagnostics. Failed cleanup blocks reuse until ambient history is purged; pinned and durable memories are preserved. See the [Windows cleanup validation](development-validation.md#retryable-session-cleanup-on-windows).
- Integrated cleanup v5 and retention v6 with real upgrade fixtures and direct fresh-v6 creation. The [historical integration checks](erasure-retention-integration.md) exposed a crash-recovery memory assertion that has since been corrected and passed in the [grounding merge validation](development-validation.md#dialogue-grounding-merge-on-windows--2026-10-11); retention failures no longer stop cleanup retries.
- CI workflow for macOS and Windows in `.github/workflows/ci.yml`.

### Added in the second session

- A second Mac reproduced the Mac-side checks for `a2727d8` from the setup guide. Deployment to the rig, `Reload()`, and in-game behavior remain unverified, so G0-7 is not done.
- Windows fixes from the first CI run. Speech processes could never start on Windows because the Job Object assignment used the wrong process access right. Settings also resolved the user profile when `LLS_DATABASE` was explicit. The remaining failures were test-harness defects.
- Windows fixes from a code audit. SSH deployment now uploads its PowerShell script, so the remote command fits cmd.exe's limit. Local deployment stages outside `scripts\`. Audio devices resolve within one host API. Speech subprocesses receive no secrets. Path settings expand `~` and environment variables.
- Display-safe text. The service and adapter share one display rule. Subtitles, speech, receipts, and memories quote exactly what GTA shows.
- [Dialogue grounding](dialogue-grounding.md). Deterministic sentence checks now precede foreground speech, social speech, and authored remarks. Python reauthorizes queued actions at delivery. Windows fake-provider regressions cover narrative claims, acknowledgment ordering, and changed authorization; arbitrary-language factuality and native behavior remain unverified.
- Ambient speech scope: the gate preserves personal history, everyday activities, price opinions, and neutral story-character mentions. The checked-in 216 ordinary / 140 adversarial corpus measures 0% false positives and 0% false negatives after narrowing player-directed and current-interaction predicates. These are bounded vocabulary results, not native or unrestricted-model verification.
- Whisper mode. A hold-to-whisper key and a `/w ` typed prefix set the whisper flag. Bystander hearing uses the whisper radius.
- whisper.cpp output with token confidence. Non-speech annotations such as `[BLANK_AUDIO]` no longer become player text. Verified on a Mac with a real whisper.cpp build.
- Role classification. Model, ped type, and relationship group must all indicate a civilian before a ped becomes targetable. The service recomputes the role and keeps the stricter policy.
- Runtime instance rebinding. A removed or recycled handle can bind to a new identity without inheriting history.
- Pinning. Pressing F8 twice, or using the dashboard, pins an eligible ambient civilian. The actor is promoted in place, so its conversation memories survive session closure. Pinned characters are not re-embodied in a later session yet.
- Bounded social-history reads. Schema v4 indexes erasure markers and history windows, so deletions and resets no longer exhaust the step budget. Deleting a memory also deletes the same actor's later lines that carried those words.
- A speech silence gate at −60 dBFS and a dialogue confidence floor of 0.3. Empty push-to-talk captures and whisper.cpp hallucinations no longer start turns.

## Open issues

The handoff's six open issues are closed. The first handoff note described one of them as whisper.cpp transcription. It actually meant whisper-mode controls. These limits remain:

- **Windows CI.** A Windows run must confirm the Windows fixes. Failing steps hid every later step of the first run, so a new run can expose more defects.
- **Rig values.**
  - Every sampled role fact: ped types, relationship groups, cuffed, police vehicle, player group, and script ownership. Record them in the [capability sheet](actions/ped-classification-and-pin.md).
  - Key conflicts for B (whisper) and F8 (pin).
  - The 3 m whisper radius.
  - Subtitle glyphs.
  - The Debug-only pin persistence natives.
  - The silence threshold and confidence floor on real microphones.
  - Activity movement, scenario names, and phone use. Record them in the [persona and activity sheet](actions/ambient-persona-and-activity.md).
- **Roles.** Police, security, emergency, gang, and service roles are fully protected. The agent design's conversation-only tier is deferred.
- **Pinning.** A pinned character is saved but is not re-embodied in a later session. That needs spawn reconciliation.
- **Social memory.**
  - One owner with thousands of rewound-branch anchors can still fail the whole read closed.
  - The partial index depends on the "I chose to " and " told me: " memory text shapes.
  - General-recall sources are now tracked for new replies and source words are scrubbed. Historical untracked dependencies require character reset or profile deletion.
- **Erasure.** Content-free skill experience and episode rows survive forgetting a pinned actor, as they already survived `reset_actor`.
- **Listening socket.** `SO_EXCLUSIVEADDRUSE` was not adopted, because it would break restart recovery.

## Not done

- All seven G0 rig checks in the [implementation plan](implementation-plan.md#g0-mandatory-checks). These need the Windows GTA V Legacy rig. The second Mac has repeated only the Mac half of G0-7.
- Native perception incidents, native actions, persistent spawning, and save reconciliation inside GTA.
- Real microphone, Windows voice models, provider performance, and frame-time measurements.
- GTA verification of the implemented Debug NPC-to-NPC path, native learned behaviors, and visible neighborhood changes. The [NPC-to-NPC plan](npc-to-npc-plan.md) records remaining capabilities.

## Next steps

1. Confirm that the latest Windows CI jobs pass. Fix any failures that earlier failing steps hid.
2. Run the checks in the [README](../README.md#verify-the-implementation) to confirm that your machine matches this commit.
3. Connect the Windows rig over Tailscale. Follow [Mac development](mac-development.md) and [Windows runtime](windows-runtime.md).
4. Work through G0-1 to G0-7 and record evidence under `docs/evidence/g0/`. Include the [classification and pin capability sheet](actions/ped-classification-and-pin.md) checks.
5. After G0 passes, start the one-ped G1 trials.

Backend work that does not need the rig can continue in parallel. Keep native capability claims provisional until G0 passes.

## Conversation history

The work was done by several agent sessions on 2026-10-10 and 2026-10-11. Search Hansard for these sessions to read the decisions and reasoning.

| Session | Harness | Role |
|---|---|---|
| `01a12723-db1c-7071-b857-86ff2a9995b9` | Codex | Coordinator. Wrote the plan, split the work, implemented most features, and reviewed the incoming Claude conversation. |
| `33d3d3b9-ec96-44b9-9667-6c5ba38c345d` | Claude Code | Python backend owner. |
| `5eb98758-f60f-4222-81b6-3d369226fce5` | Claude Code | G0 toolchain and SHVDN C# adapter owner. |
| `8e1d2d23-fe34-4e22-8470-b5e7c28acfa5` | Claude Code | First handoff. Committed `a2727d8` and wrote this note's first version. |
| `0440d26b-45b3-4e43-8e6f-3fca36eb6066` | Claude Code | Second session. Reproduced the build on a second Mac, fixed the Windows CI and audit defects, and closed the handoff's open issues with implementation subagents. |

The specifications are in `references/`. [`SPECS.md`](../references/SPECS.md) defines the acceptance gates.
