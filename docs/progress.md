# Progress and handoff

Date: 2026-10-11. This note records the project state after the second working session on `living-los-santos-foundation`. The first session committed `a2727d8` on another Mac. The second session continued on a second Mac and closed the handoff's open issues. The full discussion is in the Hansard archive. See [Conversation history](#conversation-history).

## Status

Living Los Santos is a development foundation. **G0 through G4 have not passed.** No Windows GTA rig has connected, so nothing in this repository proves gameplay, Windows audio, or release readiness.

The Python service, the C# adapter, and the development autonomy features run and pass tests on macOS against a fake game. [Development validation](development-validation.md) records each check and what it does not prove.

The first GitHub Actions run passed on macOS and failed on Windows. The second run, for `18d3e29`, confirmed those fixes on Windows: the speech process, network consent, synthetic load, and harness tests passed. It found five remaining Windows-only failures:

- one role-change timing assumption;
- one social-scheduler race with background maintenance;
- three deployment-script tests that had never run before;
- a live check that removed its database folder while the stopped service still held the file.

The following commit fixes those failures. Its own Windows run must confirm them.

## Done

- Authenticated Python service with streaming dialogue, action proposals, acknowledgments, cancellation, and durable action outcomes.
- SQLite memory with scoped knowledge, relationships, save-timeline branches, and optional semantic retrieval.
- Provider controls with budgets, explicit cloud consent, and a local OpenAI-compatible option. AI starts disabled.
- Pilot pack of 12 original characters. Spawn locations and voices are placeholders.
- C# adapter that builds for `net48` on a Mac against the pinned SHVDN reference. Release builds advertise no native actions.
- Speech probes and a speech coordinator with push-to-talk, subtitles, and delivery receipts.
- Development perception, social scenes, social scheduler, social adaptation, 48-character neighborhood simulation, and skill learning with automatic practice.
- Local dashboard for inspection, memory edits, export, backups, and diagnostics.
- Character-scoped export, source conversation erasure, and configurable seven-day raw-history retention. Schema v5 preserves durable memories and relationships; [policy and migration limits](privacy-retention.md) distinguish retained experiences from raw transcripts.
- CI workflow for macOS and Windows in `.github/workflows/ci.yml`.

### Added in the second session

- A second Mac reproduced the Mac-side checks for `a2727d8` from the setup guide. Deployment to the rig, `Reload()`, and in-game behavior remain unverified, so G0-7 is not done.
- Windows fixes from the first CI run. Speech processes could never start on Windows because the Job Object assignment used the wrong process access right. Settings also resolved the user profile when `LLS_DATABASE` was explicit. The remaining failures were test-harness defects.
- Windows fixes from a code audit. SSH deployment now uploads its PowerShell script, so the remote command fits cmd.exe's limit. Local deployment stages outside `scripts\`. Audio devices resolve within one host API. Speech subprocesses receive no secrets. Path settings expand `~` and environment variables.
- Display-safe text. The service and adapter share one display rule. Subtitles, speech, receipts, and memories quote exactly what GTA shows.
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
- Native NPC-to-NPC conversations, native learned behaviors, and visible neighborhood changes.

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
