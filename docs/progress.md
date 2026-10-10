# Progress and handoff

Date: 2026-10-10. This note records the state of the project at the first implementation commit. The full discussion is in the Hansard archive. See [Conversation history](#conversation-history).

## Status

Living Los Santos is a development foundation. **G0 through G4 have not passed.** No Windows GTA rig has connected, so nothing in this repository proves gameplay, Windows audio, or release readiness.

The Python service, the C# adapter, and the development autonomy features run and pass tests on macOS against a fake game. [Development validation](development-validation.md) records each check and what it does not prove.

## Done

- Authenticated Python service with streaming dialogue, action proposals, acknowledgments, cancellation, and durable action outcomes.
- SQLite memory with scoped knowledge, relationships, save-timeline branches, and optional semantic retrieval.
- Provider controls with budgets, explicit cloud consent, and a local OpenAI-compatible option. AI starts disabled.
- Pilot pack of 12 original characters. Spawn locations and voices are placeholders.
- C# adapter that builds for `net48` on a Mac against the pinned SHVDN reference. Release builds advertise no native actions.
- Speech probes and a speech coordinator with push-to-talk, subtitles, and delivery receipts. Tests use synthetic audio only.
- Development perception, social scenes, social scheduler, social adaptation, 48-character neighborhood simulation, and skill learning with automatic practice.
- Local dashboard for inspection, memory edits, export, backups, and diagnostics.
- CI workflow for macOS and Windows in `.github/workflows/ci.yml`. No CI run has been observed yet.

## Open issues at handoff

Social adaptation (AUTO-05) is complete in this commit. Repeated confirmed encounters change later topic selection, and an NPC's own refusals pause later invitations for a time.

A read-only audit found these gaps. Nobody has fixed them yet.

- Typed input and push-to-talk are not connected to whisper.cpp transcription.
- Subtitle acknowledgments store the raw text, but GTA shows sanitized text. The receipt can differ from what the player saw.
- Protected-role detection uses an allowlist of six civilian models only.
- Ambient `adopt_actor` keeps the persona internally, but players cannot pin an NPC from the UI.
- The dense social-history read stops at 4 million SQLite steps. 128 owners × 100 decisions passes in about 150 ms. Larger or mixed histories can fail closed.
- After an earlier memory is deleted, later quotes from the same conversation remain as separate history.

## Not done

- All seven G0 rig checks in the [implementation plan](implementation-plan.md#g0-mandatory-checks). These need the Windows GTA V Legacy rig and a second Mac.
- Native perception incidents, native actions, persistent spawning, and save reconciliation inside GTA.
- Real microphone, voice models, provider performance, and frame-time measurements.
- Native NPC-to-NPC conversations, native learned behaviors, and visible neighborhood changes.

## Next steps

1. Run the checks in the [README](../README.md#verify-the-implementation) to confirm that your machine matches this commit.
2. Connect the Windows rig over Tailscale. Follow [Mac development](mac-development.md) and [Windows runtime](windows-runtime.md).
3. Work through G0-1 to G0-7 and record evidence under `docs/evidence/g0/`.
4. After G0 passes, start the one-ped G1 trials.

Backend work that does not need the rig can continue in parallel. Keep native capability claims provisional until G0 passes.

## Conversation history

The work was done by several agent sessions on 2026-10-10. Search Hansard for these sessions to read the decisions and reasoning.

| Session | Harness | Role |
|---|---|---|
| `01a12723-db1c-7071-b857-86ff2a9995b9` | Codex | Coordinator. Wrote the plan, split the work, implemented most features, and reviewed the incoming Claude conversation. |
| `33d3d3b9-ec96-44b9-9667-6c5ba38c345d` | Claude Code | Python backend owner. |
| `5eb98758-f60f-4222-81b6-3d369226fce5` | Claude Code | G0 toolchain and SHVDN C# adapter owner. |

The specifications are in `references/`. [`SPECS.md`](../references/SPECS.md) defines the acceptance gates.
