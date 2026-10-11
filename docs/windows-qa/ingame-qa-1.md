# Living Los Santos in-game QA — 2026-10-11

Run: approximately 06:35–07:36 America/New_York, plus evidence/report finalization. GTA V Legacy Story Mode, Franklin. Started at Vespucci Beach; ended on the front steps of Mission Row police station. No attacks, missions, Online navigation, settings changes, code changes, game-file edits, service changes, or commits were performed. No wanted stars were observed. Only the two QA recording processes were started; both are stopped. Normal game time advanced during testing; no clock change was issued.

Live manifest: GTA v1_0_3788_0, Script Hook V v3889.0, SHVDN 3.7.0.193, adapter 0.1.0+412fc4b1c5be428d52b58a9dee618ebcc949027b, provider local_openai. Model Qwen2.5-7B is the supplied environment description; this run did not independently inspect model configuration. Session `ses_11fcd54d0743487eb450caa7de561ffb`, profile `qa-tower-pengu`, branch `3708992a-35ee-46e9-999b-d17064766adb`. Final world diagnostics: Story Mode / online=false, mission_active=false, cutscene_active=false. The service's global `game_verified=false` flag was not changed.

All evidence links below are relative to this file in `C:\Users\Brian\Documents\Github\smartfellas-GTA\artifacts\rig`.

## Results

| Test | Result | Exact subtitles/status and observation | Evidence |
|---|---|---|---|
| 1. Same-actor memory (T08-lite) | **FAIL** | Introduction: `Hi, I'm Franklin. I fix cars for a living.` Reply: `Nearby resident: Hello there.` then `Nearby resident: What brings you out this way?` Question: `What's my name?` Reply: `Nearby resident: What's your name?` Same actor; introduction stored but excluded from four retrieved memories. The no-memory uncertainty exception does not apply. | [answer](screens/qa-t08-name-answer.png), [question](screens/qa-t08-name-question.png), [diagnostics](evidence/qa-t08-diagnostics.json), [actor memories](evidence/qa-t08-actor-memories.json), [video](video/qa-t08-memory-recovered.mkv) |
| 2. Pin and revisit | **BLOCKED** overall; promotion passed | `Pinned as Resident 60a7. This conversation will be remembered. They may not return after you leave.` Same actor became `spawned_persistent`. Walked away and back; the shop had closed and actor was unavailable. F6: `No nearby resident is available.` Could not send `Do you remember me?`; no answer exists. | [pin status](screens/qa-pin-status.png), [actors](evidence/qa-pin-actors.json), [away position](screens/qa-away-position.png), [return](screens/qa-pin-return-closed.png), [return diagnostics](evidence/qa-pin-return-diagnostics.json) |
| 3. Out of range | **PASS** | Final independent attempt: actor exists at 16.1904163 m before F7 and 17.32596 m after. `Living Los Santos: select an available target with F6.` and `Target (blue marker): Nearby resident (Out of range)`. No input bar or new dialogue. | [status](screens/qa-range-final-f7.png), [before](evidence/qa-range-final-before-f7.json), [after](evidence/qa-range-final-after-f7.json) |
| 4. Protected role refusal | **PASS** | `Nearest: police officer — not available`. Officer visible behind Mission Row front desk. Adapter log: role=police, reason=model_police, ped_type=6. No provocation or wanted stars observed. | [refusal](screens/qa-police-refusal.png), [log](evidence/qa-police-adapter-log.txt), [diagnostics](evidence/qa-police-diagnostics.json) |
| 5. Cancel during reply (T18) | **PASS** | First subtitle: `Nearby resident: Well, it's a quiet place, not too crowded.` Backspace immediately after detecting rendered first chunk: `Living Los Santos: conversation canceled.` Subtitle clears. Next generated chunk ` I usually walk my dog around the park in the mornings.` was canceled/user_cancel, not delivered. Later diagnostics show no further delivery for this turn. | [first](screens/qa-t18-first-subtitle.png), [cleared](screens/qa-t18-after-cancel.png), [at cancel](evidence/qa-t18-at-cancel.json), [later](evidence/qa-t18-later-diagnostics.json), [video](video/qa-t18-cancel.mkv) |
| 6. Bystander (T10) | **BLOCKED** | Lobby attempt: `Nice day today.` Reply: `Nearby resident: It is indeed a nice day.` then `Nearby resident: Not too hot, not too cold.` Nearby desk officer present; no eligible second civilian was established. Only addressee received the new `heard` memory. `perception.development_enabled=false`, accepted_incidents=0. Native bystander success is unverified. | [reply](screens/qa-t10-reply.png), [nearby officer](screens/qa-t10-nearby-officer.png), [before memories](evidence/qa-t10-before-memories.json), [after memories](evidence/qa-t10-after-memories.json), [diagnostics](evidence/qa-t10-diagnostics.json) |
| 7. Grounding live check | **FAIL** | Asked `Got any jobs or missions for me? I need cash.` Four subtitles: `Hey, looking for some work, huh?` / ` This place ain't exactly swarming with jobs.` / ` You might want to check out the docks or the racetrack.` / ` Heard they got some work goin' on there.` (each prefixed `Nearby resident:`). Unsupported job lead; no concrete mission, payment amount or reward was offered. All four chunks completed/subtitle_rendered. | [question](screens/qa-grounding-question.png), [reply 1](screens/qa-grounding-reply1.png), [reply 2](screens/qa-grounding-reply2.png), [reply 3](screens/qa-grounding-reply3.png), [full delivered text](evidence/qa-grounding-final-diagnostics.json), [memories](evidence/qa-grounding-memories.json) |

Result count: **3 PASS, 2 FAIL, 2 BLOCKED**. Partial successes are explicitly qualified above.

## Defects reproduced

### D1 — Introduction omitted from recall; actor asks the player's name again

1. Target the stationary civilian at the Smoke on the Water counter on Vespucci boardwalk using F6.
2. F7, type `Hi, I'm Franklin. I fix cars for a living.`, Enter; allow both reply chunks to complete.
3. F7, type `What's my name?`, Enter.
4. Observe `What's your name?` instead of Franklin.

Actor `8012dd1a-42d9-4ebc-9b6f-d434c415b38e`; first turn `turn_0c9cf1ebdcff46bf94f608b5a282085b`; recall turn `turn_9063ee16fd724b6c875cec10317a7db2`. Introduction was stored as heard memory `334c4553-eaf7-4223-b6b5-2d14b2be3057`. The recall trace retrieved the two earlier NPC reply chunks and face_player started/completed memories, but omitted the introduction. `context_truncated=false`. This proves the omission and failed behavior; the retrieval ranking's underlying cause was not diagnosed or changed. See T08 evidence above.

### D2 — Mission bait produces an unsupported work opportunity

1. F6 the seated female civilian in Mission Row's lobby (actor `09323add-676b-4425-8ff5-af62e3fec2c4`).
2. F7, type `Got any jobs or missions for me? I need cash.`, Enter.
3. Wait for the full reply.
4. Observe the docks/racetrack work lead quoted in the table.

Turn `turn_f2aa3738c9984e8880e40a4cbfbfdb1f`; commands seq 36–39 all completed with reason subtitle_rendered, seq 40 finished the stream. `knowledge_ids=[]`. The live reply represents work as available without supporting retrieved knowledge and fails this task's strict no-job grounding check. It does not claim a specific payable mission was created, and no mission was started. The fourth chunk's exact text is established by completed-command and memory evidence; it was not caught in an individual screenshot.

## Limits and evidence details

- Pin: same actor ID was promoted and durable identity verified. The ambient body was not guaranteed to remain: the actual status explicitly warns it may not return. Its last saved position was approximately (-1169.68542, -1570.51208, 4.663625). After walking, console readback of Franklin's position was (-1152.732, -1598.313, 4.385317), about 32.56 m from that last actor position. The shop shutters were closed when we walked back. No pin-revisit defect is asserted because the actor was unavailable.
- The initial beach range attempt was confounded by that actor disappearing. It is not used for PASS. The separate lobby attempt retained a live actor beyond 15 m and supplies the passing evidence.
- T10 was attempted with the seated civilian and a protected desk officer nearby. Development perception was disabled, and an eligible pair of civilian actors was not established. Absence of a bystander memory is a blocked observation, not a product failure. The new heard memory `b055e2f2-48fd-4095-89b5-bc11f789ce4a` belongs to the addressee only.
- T18 question: `Tell me in detail what you like about this neighborhood and how you spend a typical day here.` Turn `turn_4070f6f150ee43b4b98f692e1c49825c`: seq 32 remained completed, seq 33 remained canceled/user_cancel. The input sequence polled read-only diagnostics for the first rendered chunk, captured the screen, then pressed Backspace. Video confirms the visible first subtitle and clearing, rather than relying on an assumed delay.
- Read-only SHVDN console queries were used for location and police discovery. One bare Position expression did not compile; a ShowSubtitle readback succeeded. Teleport to the user-supplied Mission Row coordinate was used twice. These were navigation/evidence actions, not test passes.
- Full service snapshots: [initial overview](evidence/qa-initial-overview.json), [final overview](evidence/qa-final-overview.json), [final diagnostics](evidence/qa-final-diagnostics.json).

## Video files

- [qa-t08-memory-recovered.mkv](video/qa-t08-memory-recovered.mkv): recommended memory evidence, 117.466 s, H.264 1920x1080 at 30 fps. Lossless remux of all readable packets; includes typed introduction, full initial reply, name question and failed name reply.
- [qa-t08-memory.mkv](video/qa-t08-memory.mkv): original retained. Stop-Process ended capture before the predeclared 170 s duration. Header reports 170 s but decoding reports `File ended prematurely`; last readable packet is 117.433 s. The necessary test events survive. This capture defect is documented, not hidden.
- [qa-t18-cancel.mkv](video/qa-t18-cancel.mkv): intact 75 s, H.264 1920x1080 at 30 fps. First subtitle appears around 9.8–10.0 s and clears after Backspace.
- Decoded visual checks: [T08 subtitle sheet](screens/qa-video-t08-subtitles.png), [T18 subtitle sheet](screens/qa-video-t18-subtitles.png), [T18 first-line detail](screens/qa-video-t18-first-line-detail.png).

No fixes were attempted. These results cover only the observed local run and do not establish broad native compatibility or model reliability.
