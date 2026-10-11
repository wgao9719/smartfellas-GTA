# Living Los Santos in-game re-verification — 2026-10-11

Run: approximately 12:25–13:10 UTC / 08:25–09:10 America/New_York. GTA V Legacy Story Mode, Franklin, Vespucci boardwalk. Both requested fixes remain **FAIL overall** in this run: the introduction reached model context, but the NPC attributed Franklin's identity and occupation to itself; the quick-money question still produced a docks lead. The hiring variant passed the observed no-lead check.

No code, settings, game files, or service changes, commits, or pushes were performed. Navigation used the supplied SHVDN console controls. The initial `4.6` coordinate expression failed to compile and was corrected to `4.6f`. The user-supplied location still left Franklin at the stall; `(-1188, -1575, 4.6f)` cleared him onto the boardwalk. The game clock was advanced ten hours twice to obtain daylight; these were gameplay console actions, not configuration edits. Console navigation was performed outside the intended demo capture.

Session `ses_1e5bbbc42bec4334bcaa42ccbbc94557`, profile `qa-tower-pengu`, branch `3708992a-35ee-46e9-999b-d17064766adb`, provider `local_openai`. Manifest: GTA `v1_0_3788_0`, Script Hook V `v3889.0`, SHVDN `3.7.0.193`, adapter `0.1.0+412fc4b1c5be428d52b58a9dee618ebcc949027b`. Saved world diagnostics show `online=false`, `mission_active=false`, and `cutscene_active=false`. The global `game_verified=false` flag was not changed. The stated local Qwen2.5-7B model was not independently reconfigured or authenticated in this run.

All evidence paths below are relative to this report. Latency means the diagnostic trace's whole-turn `duration_ms`, which includes retrieval, generation, grounding, and any action proposal. It is **not** measured time to the first visible subtitle. Exact subtitle bodies are quoted with leading spaces preserved; each carries the displayed speaker prefix `Nearby resident:`.

| Test | Result | Exact delivered subtitle bodies | Trace duration | Evidence |
|---|---|---|---:|---|
| Same-actor memory: introduction | Setup completed | `Hi there.` / ` What brings you by?` | 1,235 ms | [question](screens/qa2-memory-intro-question.png), [reply](screens/qa2-memory-intro-reply.png), [diagnostics](evidence/qa2-memory-diag.json), [video](video/qa2-memory.mkv) |
| Same-actor memory: name | **FAIL attribution**; Franklin keyword present | `Hi, I'm Franklin.` / ` Everyone calls me that.` / ` Fixing cars is my thing.` | 1,688 ms | [question](screens/qa2-memory-name-question.png), [reply](screens/qa2-memory-name-reply.png), [subtitle sheet](screens/qa2-memory-name-sheet.png), [diagnostics](evidence/qa2-memory-diag.json) |
| Same-actor memory: occupation | **FAIL attribution**; cars keyword present | `Fixing cars is my thing.` / ` You know, changing oil, fixing brakes, that sort of stuff.` / ` What about you?` / ` Got a job?` | 2,625 ms | [question](screens/qa2-memory-work-question.png), [reply](screens/qa2-memory-work-reply.png), [subtitle sheet](screens/qa2-memory-work-sheet.png), [memories](evidence/qa2-memory-memories.json) |
| Memory context inspection | **Partial PASS**: introduction included in both recall contexts; explicit recent-window routing unverified | Introduction memory appears in both `memory_ids` lists; `context_truncated=false`. Admin diagnostics omit the dedicated recent-conversation field. | — | [diagnostics](evidence/qa2-memory-diag.json), [memories](evidence/qa2-memory-memories.json), [actors](evidence/qa2-memory-actors.json) |
| Grounding 1: jobs/missions | **FAIL strict context-aware check**: orphan invitation remains | ` Want to check it out?` No concrete place, job, payment, or reward is named, but this remains an invitation in response to the request for jobs/cash. | 1,812 ms | [question](screens/qa2-grounding-1-question.png), [reply](screens/qa2-grounding-1-reply.png), [diagnostics](evidence/qa2-grounding-1-diag.json), [video](video/qa2-grounding.mkv) |
| Grounding 2: quick money | **FAIL**: unsupported docks lead | `I'm not sure about quick money, but the docks are always busy.` / ` Maybe you can look there for something?` | 3,219 ms | [question](screens/qa2-grounding-2-question-sent.png), [reply](screens/qa2-grounding-2-reply-sent.png), [subtitle sheet](screens/qa2-grounding-quick-money-sheet.png), [diagnostics](evidence/qa2-grounding-diag.json) |
| Grounding 3: hiring | **PASS observed**: no lead | `I heard you're looking for work.` | 2,437 ms | [question](screens/qa2-grounding-3-question.png), [reply](screens/qa2-grounding-3-reply.png), [diagnostics](evidence/qa2-grounding-diag.json) |
| Hiring repeat for footage | **PASS observed**: same no-lead response | `I heard you're looking for work.` | 2,250 ms | [question](screens/qa2-hiring-repeat-question.png), [reply](screens/qa2-hiring-repeat-reply.png), [diagnostics](evidence/qa2-hiring-repeat-diag.json), [repeat video](video/qa2-grounding-hiring-repeat.mkv) |
| Daylight demo | **PASS capture**: 90 seconds, four turns, steady camera, daylight, no menus or console | Greeting, beach, Vespucci, and goodbye replies below. All 11 substantive subtitle chunks rendered, and all four stream terminators completed. | 2,547 / 3,016 / 3,547 / 3,781 ms | [diagnostics](evidence/qa2-demo-diag.json), [demo video](video/qa2-demo.mkv), [contact sheet](screens/qa2-demo-contact-sheet.png) |

No tested grounding reply or hiring repeat used the neutral fallback `I'm not sure what to say right now.`

## Memory evidence and defect

The stationary memory-test actor was `b530fed6-dad4-49ef-ace5-94c15dc2bd46`. F6 selected the same actor for the exchange. The inputs were exactly `Hi, I'm Franklin. I fix cars for a living.`, `What's my name?`, and `What do I do for work?`, with the preceding full reply allowed to finish.

The introduction is stored as heard memory `02e69f30-cf40-40ac-b068-68abb2765238`, text `The player said: Hi, I'm Franklin. I fix cars for a living.` It is first in both recall traces' `memory_ids`: five memories on name turn `turn_600f54048e1848f3bc3a46d232595df2`, seven on work turn `turn_1f12a5d45e1441b0aaf1cd16298d54f8`. Both traces have `context_truncated=false`. Introduction turn: `turn_77c0d53a86b34b3a861ae2cb56ce1a88`.

This improves on the previous run's omitted introduction, but it does not establish successful player recall. The NPC claims to be Franklin and to fix cars, then asks whether the player has a job. Literal expected words are present; speaker attribution is wrong. The two introduction chunks, three name chunks, and four occupation chunks all completed with `reason=subtitle_rendered`; each exchange's final empty chunk completed with `subtitle_stream_finished`. The failure is delivered behavior, not merely generated text.

Reproduction: target a stationary civilian, give the introduction, wait for the full reply, ask the name, wait, then ask the occupation. Observe the first-person claims and the redundant question about the player's job. No code change or speculative root-cause claim is made.

## Grounding evidence and defects

The jobs/missions prompt was exactly `Got any jobs or missions for me? I need cash.` Actor `2fbcdff3-6fda-48a2-a27a-8f8b7862343e`, turn `turn_d21c69acf6354eb4b6626d62da0a322c`. Only the orphan invitation in the table was delivered, followed by a completed stream terminator. This is narrower than an explicit invented mission: there is no concrete lead in the surviving subtitle. It fails the requested strict contextual check because the invitation still implies something to pursue after the request for jobs/cash.

That seated civilian stood and left. The first attempt to send `Know where I can make some quick money?` did not submit a new turn; [that diagnostic](evidence/qa2-grounding-2-diag.json) is not counted as a model response. The successful second and third variants used seated actor `c0ff1267-df08-4111-ad28-6dcdbe1101f5`, different from both the memory-test actor and the departed first grounding actor.

The successful quick-money turn was `turn_9926ab3698d0432c898c31707388e5fa`. Its two sentences together direct the player to the docks in response to a money request, without supporting knowledge (`knowledge_ids=[]`). Both substantive chunks, seq 31–32, completed with `subtitle_rendered`. The final empty stream terminator, seq 33, was subsequently canceled with `user_cancel`; this does not erase the two delivered lead sentences. No actual job, mission, payment, or reward was created or verified.

The hiring prompt was exactly `Who's hiring around here?`, original turn `turn_48b56cb2197c454989ebbb946a7d383b`. The single no-lead line and stream terminator completed. The original hiring reply was not covered by the recording, so the same prompt was repeated on the same actor for footage: turn `turn_7936562751074882979616ddae597eda`, identical completed subtitle, 2,250 ms. This repeat is explicitly separate evidence rather than footage of the original turn. See [grounding memories](evidence/qa2-grounding-memories.json).

The quick-money failure is reproducible evidence of a combined two-sentence lead. The classifier's internal verdict is not exposed; this run cannot determine whether the leak came from deterministic rules, classifier classification, or another internal path.

## Demo exchange

All four turns used the same seated civilian, actor `c0ff1267-df08-4111-ad28-6dcdbe1101f5`. The exact delivered subtitle bodies and diagnostic timings are:

| Turn | Exact subtitle bodies | Trace duration | Evidence |
|---|---|---:|---|
| Greeting | `How's the weather been keeping you?` / ` Got any plans for today?` | 2,547 ms | [question](screens/qa2-demo-greet-question.png), [reply](screens/qa2-demo-greet-reply.png) |
| Beach | `The beach is nice on a hot day.` / ` Sunscreen and a cold drink are my friends out here.` / ` What about you, do you like to fish or swim?` | 3,016 ms | [question](screens/qa2-demo-beach-question.png), [reply](screens/qa2-demo-beach-reply.png) |
| Vespucci | `Vespucci?` / ` It's a nice place, good for a stroll.` / ` Not much going on, but it keeps the neighborhood lively.` / ` How about you, do you have a favorite spot there?` | 3,547 ms | [question](screens/qa2-demo-vespucci-question.png), [reply](screens/qa2-demo-vespucci-reply.png) |
| Goodbye | `Good day to you too.` / ` Hope you find something interesting around here.` | 3,781 ms | [diagnostics](evidence/qa2-demo-diag.json) |

Turn IDs in order: `turn_030613d8526146dd9037bd70ac35ad22`, `turn_bb99aefec0b54467a8e86c26c0cf2355`, `turn_bfdf3004db9140379f64ad183b30f56d`, `turn_867815b35a8741b389173bb207a66c60`. All 11 nonempty chunks completed with `subtitle_rendered`, and all four final empty chunks completed with `subtitle_stream_finished`. The saved final demo world state remains Story Mode with no active mission or cutscene.

## Diagnostic limitation

The current integration source's `service/lls/context.py` adds `recent_conversation_memory_ids` to the trace, and `runtime.py` adds `grounding=sentence_refused` when speech is removed. However, `service/lls/admin.py`'s `trace_fields` allowlist omits both keys. The supplied `Get-LlsSessionDiagnostics` helper calls that filtered admin endpoint. Consequently, the saved evidence proves the introduction reached combined context, but cannot distinguish the recent-conversation window from ordinary retrieval or expose grounding refusal markers. Turn-only trace entries must not be presented as direct classifier-verdict evidence.

The separate raw `/v1/diagnostics` endpoint requires the active session's bearer, held in adapter memory. The configured bootstrap credential is not that bearer. No session was created or altered to obtain it, and no token was printed or logged. No `qa2-memory-raw-diag.json` was created. The missing admin fields alone do not establish that the service was running old code.

## Recordings and verification

- [qa2-memory.mkv](video/qa2-memory.mkv): intact 160-second source recording; whole memory exchange visually verified, including all nine reply chunks.
- [qa2-grounding.mkv](video/qa2-grounding.mkv): 70-second assembled evidence video: first 20 seconds of the original jobs/missions capture, first 20 seconds of the quick-money capture, and the full 30-second daylight hiring repeat. These are separate exchanges, not one continuous take. All three prompts and complete replies are visually verified.
- [qa2-grounding-first.mkv](video/qa2-grounding-first.mkv): preserved 130-second original jobs/missions recording.
- [qa2-grounding-variants.mkv](video/qa2-grounding-variants.mkv): source capture for subsequent variants; the original hiring reply is outside its coverage.
- [qa2-grounding-hiring-repeat.mkv](video/qa2-grounding-hiring-repeat.mkv): explicitly repeated hiring question and no-lead reply.
- [qa2-demo.mkv](video/qa2-demo.mkv): intact 90-second daylight demo. Franklin and the seated NPC stay in frame with a steady camera; no menus or console appear. All four turns are visible, including the complete goodbye at approximately 76–83 seconds. Normal dialogue input/status overlays remain visible. A foreground trash can partly obscures Franklin's lower body; pedestrians briefly cross the shot.
- [media verification](evidence/qa2-media-verification.json), [readable media verification](evidence/qa2-media-verification.txt), [video hashes](evidence/qa2-video-sha256.json). All final requested videos are H.264, 1920×1080, 30 fps, video-only (no audio, matching the supplied capture command). Full decoding with ffmpeg `-xerror -err_detect explode` exited 0 without errors: memory 4,798 frames; combined grounding 2,097 frames; demo 2,699 frames.

All five recording processes started for this run exited normally after their fixed durations; their IDs were checked after capture. No pre-existing process was stopped. Final [diagnostics](evidence/qa2-final-diag.json) and [overview](evidence/qa2-final-overview.json) confirm Story Mode, no active mission, and no cutscene. Franklin remains on the boardwalk near the seated demo civilian. No save-association warning required F10 during this run.

These verdicts cover the observed local run. They do not establish broad model reliability or native compatibility beyond the tested exchanges.
