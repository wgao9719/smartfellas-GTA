# Overnight report: Living Los Santos tested on this Windows PC

Night of 2026-10-10/11. Everything ran on TOWER-PENGU: Windows 11 Pro 23H2, Ryzen 9 9950X, Radeon RX 9070 XT, 4K display.

## TL;DR
- **The mod now works inside GTA V on this PC**, using a local AI model (Qwen2.5-7B through llama.cpp, no cloud):
  - F6 targets a pedestrian (a blue marker appears over them), F7 lets you type, and the NPC replies in streamed subtitles within about 2–4 s.
  - The NPC **turns to face Franklin, and the game confirms it**.
  - It **remembers** what you said ("Your name is Franklin." / "You fix cars, Franklin.").
  - It **won't invent missions or cash** ("I don't have any jobs or missions to offer.").
  - Police are refused, out-of-range targets are refused, and Backspace cancels mid-reply.
  - Videos are in `artifacts/rig/video/` (watch `clip-split-before-after.mp4` and `clip-memory-callback.mp4` first).
- **The real game exposed 9 defects that no automated test could see. All are fixed, and all except #5 were re-verified in-game.** #5 needs an incoming phone call to trigger; it's covered by tests.
  1. The INI parser cut the URL down to `http`, so the adapter crashed on load.
  2. The adapter claimed the wrong thread, so it aborted on its first frame.
  3. Targeting depended on game "decorators". Registering one late **crashed GTA once** (05:08), so it was replaced with a check, every frame, that the NPC is still the same one.
  4. The NPC spoke its action text out loud.
  5. Pressing Enter in the chat box answered GTA's phone.
  6. The status line got stuck, and there was no target marker.
  7. The NPC forgot what you'd just told it.
  8. Then the 7B model mixed up who said what.
  9. Job leads like "the docks are always busy, look there" got through.
- **Everything automated passes on Windows at `c181fdf`** (20/20 steps):
  - Python: 2,736 tests passed, 1 skipped (it needs a real whisper.cpp install).
  - C#: 581 tests passed.
  - Builds: 0 warnings.
  - All live checks pass.
  - **At the start of the night:** 2 Windows failures, 10 skipped tests, and C# couldn't build here.
- **Checking the code against the spec** covered 543 requirement rows. It produced **25 fix commits** on the local branch `win/validation`. **Nothing has been pushed.**
- **Performance with the mod and AI connected:**
  - Idle: 119.8 fps with no frames over 33 ms.
  - 20-minute soak: 119.8 fps, p99 13.3 ms, 3 frames over 33 ms (worst 42 ms), memory flat after +121 MB, no health failures.
- **Not done:**
  - Voice/push-to-talk in game (needs you at the mic). Speech-to-text was verified from audio files.
  - Revisiting a pinned NPC: pinning works, but the NPC despawned before it could be revisited. Bringing pinned NPCs back after a despawn isn't implemented yet (spec gap).
  - The bystander "overheard" memory: it needs perception, which is a development flag.
  - A fair unmodded vs modded frame-time comparison at the same spot.
  - The big spec features (other actions, spawning, perception, voices). See §3.

## What you can try right now
GTA is running modded with the service up. To restart it all yourself:
1. Start the local model, then the service. Run `powershell -File $env:USERPROFILE\.lls-tools\llm\start-llm.ps1`, then `powershell -File $env:USERPROFILE\.lls-tools\rig\start-service.ps1 -Provider local_openai`. Stop the model with `…\llm\stop-llm.ps1`.
2. Launch GTA V Legacy from Steam and choose **Story Mode**. When the save-association warning appears, press **F10**.
3. Walk up to a pedestrian, press **F6** (target, with a blue marker over them), then **F7**, type, and press **Enter**. **Backspace** cancels, and pressing **F8** twice pins them.
4. Dashboard: http://127.0.0.1:8765/dashboard

**Before going online:** run `powershell -File $env:USERPROFILE\.lls-tools\rig\rig-uninstall.ps1`. It removes Script Hook V, SHVDN, `args.txt` (which disables BattlEye) and the adapter. It then compares the game folder against the snapshot taken before install. Script Hook V closes the game if you go online, and Online won't connect without BattlEye anyway.

## Results by area

### 1. Automated checks on Windows (final pass on `win/validation`)
`artifacts/windows-test/logs/final-c181fdf/summary.tsv`: **20/20 steps passed** at the final commit `c181fdf`:
- ruff;
- pytest `-W error`: 2,736 passed, 1 skipped (opt-in real whisper.cpp);
- C# Core.Tests 581/581;
- GameAdapter Release and Debug builds: 0 warnings;
- live bridge (plain, speech, pin), crash recovery, action outcome, social (plain and autonomous), social adaptation, skill and skill autonomy, neighborhood and neighborhood autonomy;
- simulated load: 200 turns and 36 NPCs, p50 49.9 ms, p95 58.4 ms;
- service demo.

| | Start of night (`7aeabaa`, Windows) | Now (`c181fdf`) |
|---|---|---|
| pytest | 1,311 passed, **2 failed**, 10 skipped | 2,736 passed, 0 failed, 1 skipped |
| C# Core.Tests | couldn't build (no .NET 10, no NuGet source) → 271 once set up | 581 |
| Live checks | 13/13 (after setup) | 13/13 plus crash, outcome and other checks |

### 2. In-game QA (G0 rig checks plus the spec §14.3 matrix)
| Check | Result | Evidence |
|---|---|---|
| G0-1 unmodded baseline | Partial: 104 s captured in the prologue (61.7 fps, p99 19.9 ms; the prologue runs differently) | `artifacts/rig/perf/g0-1-*` |
| G0-2 loaders | PASS: Script Hook V reports "game version is VER_1_0_3889_0", the ASI loader loaded ScriptHookVDotNet.asi, and SHVDN 3.7.0.193 loaded the adapter | game-root logs, `artifacts/rig/game-root-*.csv` |
| G0-3 deploy, reload and redeploy | PASS: redeployed 8 times while GTA was running with no DLL lock; F4 `Reload()` picks up each new build (module id and DLL hash change in `adapter_loaded`) | `scripts/LivingNPC/LivingNPC-*.log` |
| G0-4 loopback health and bridge | PASS: `bridge_connect` → `bridge_ready`, session opened. Dev-LAN half: N/A (no second machine) | adapter log, `artifacts/rig/evidence/` |
| T01 fresh install | PASS (after fixes 1–3) | |
| T03/T04 ambient target and text chat | PASS: civilian classified (`ped_type=4`), reply streamed as subtitles | `clip-after-*.mp4` |
| T11 (face_player) | PASS: `action.execute face_player` → completed (`heading_observed`) | diagnostics JSON |
| T14 service killed | PASS: "service unavailable. AI stopped." shown once, GTA kept running | `q14-*` |
| T15 service restarted | PASS: new generation, new session, old session cleaned | |
| T19 save-slot switch | PASS: service returns 409 and the adapter shows a refusal, no session attaches | `q17/q18/q20-*` |
| G0-6 20-min soak | PASS (numbers above) | `artifacts/rig/perf/g0-6-soak-20min.*` |
| T05 voice / mic | Not run: needs you present (a real voice on the mic). Real whisper.cpp speech-to-text was verified from a file: exact transcript, 0.93 confidence, 828 ms | `artifacts/speech-real/report.md` |
| T06 out of range | PASS: at 16.2 m, F7 is refused with "select an available target… (Out of range)" | `artifacts/rig/ingame-qa.md` |
| T12 protected role | PASS: "Nearest: police officer — not available" (Mission Row; role=police, ped_type=6) | `ingame-qa.md` |
| T18 cancel during reply | PASS: Backspace after the first subtitle clears it, and the next chunk is canceled and never delivered | `video/qa-t18-cancel.mkv` |
| Same-actor memory | **PASS after 3 fix rounds** (final `c181fdf`): "What's my name?" → "Your name is Franklin." and "What do I do for work?" → "You fix cars, Franklin."<br>• Run 1: the introduction wasn't recalled.<br>• Run 2: the model claimed the name for itself.<br>• Final: named chat turns, 10/10 correct on the service path with the real model. | `rig/ingame-spotcheck.md`, `video/clip-memory-callback.mp4` |
| Grounding (job bait) | **PASS in game** (final `c181fdf`): "I'm sorry, Franklin, but I don't have any jobs or missions to offer." and "Hmm, looking to make some quick money, huh?"<br>• Earlier runs leaked docks/racetrack leads.<br>• Remaining measured risk: 4/30 bait replies still delivered a lead (2 clear, 2 ambiguous) in the offline 30-prompt service test. | `rig/ingame-spotcheck.md`, `video/clip-no-invented-missions.mp4` |
| 90-s demo | PASS: 4 natural turns with one seated beach-goer. Replies took 2.5–3.8 s for the whole turn, including the extra check | `video/clip-demo-boardwalk-90s.mp4` |
| Pin | Promotion PASS ("Pinned as Resident 60a7…", now persistent). Revisit BLOCKED: the NPC despawned, as the status warns | `ingame-qa.md` |
| T10 bystander | BLOCKED: development perception is off by default | `ingame-qa.md` |
| Manifest build | Minor bug: the session manifest reports the game as `v1_0_3788_0`, but it's 3889. SHVDN's version enum lags the game | `ingame-qa.md` |

### 3. Spec conformance (4 Codex audits, 543 requirement rows)
Reports: `artifacts/spec-audit/{a,b,c,d}-*.md`. Fixed tonight, in order of severity:
- **Safety and storyline:**
  - Cancelling could clear a task on an NPC that had since become protected.
  - Moving out of range left the mod's task running on the ped.
  - Safety stops now latch after repeated failures or schema faults.
  - A dialogue grounding filter blocks invented missions, rewards and story claims, and claims of completed actions without a confirmed receipt. It has two layers: rules that take the player's question into account, then, only for sentences a keyword filter flags, an 800 ms yes/no check by the local model that drops the sentence on any doubt.
  - Measured with the real local model at the final commit `c181fdf` (the model check now also sees the previous sentence and the player's question):
    - Checked-in corpus: 0/179 misses and 3/246 false positives.
    - Held-out set it never trained on: 1/9 misses (a true rumor about Michael) and 0/16 false positives.
    - 30 job-bait prompts through the service: a lead was delivered 4 times (2 clear, 2 ambiguous).
    - It adds about 130–170 ms, and only to flagged sentences.
  - The rules alone generalized poorly (5 of 7 held-out job leads got through), which is why the model check was added. It is still not a guarantee.
  - Actions are re-authorized when they're dispatched.
- **Privacy:**
  - Secrets and personal data are scrubbed from cloud payloads.
  - Deleting a conversation also removes the source text from exports.
  - Raw history is kept for 7 days.
  - There's a per-character export.
  - Session cleanup retries, so ambient history can't silently survive a failed close.
- **Saves:** a profile must be explicitly tied to a save slot (spec §8.3), with an F10 confirmation, and ambiguous timelines are refused.
- **Bridge:**
  - Command responses are paginated to fit the adapter's 256 KiB / 64-command limits.
  - Command schema versions are explicit, with cross-language fixtures.
  - Capabilities are negotiated.
  - Heartbeats are independent of other traffic.
  - Periodic crash-safe database snapshots.
- **Controls:**
  - Targeting uses the camera look direction.
  - Cancel clears the selection.
  - Manual skill trials require the same preflight as automatic ones.

**Still open, and larger than one night.** These are mostly spec items that need features, not fixes:
- Follow, stop, wander and flee actions.
- Curated character spawning and reconciliation.
- Re-embodying pinned NPCs.
- Perception sensors.
- Per-character voices.
- A visibility test matrix.
- Accessibility (font scale, contrast).
- The §14.4 evaluation rubric.
- Config migrations.
- Release CI packaging.

Details and severities are in the audit reports.

### 4. Other findings
- **Unchanged since 7aeabaa, and still open on this machine:**
  - Your user NuGet.Config has **no package sources**. I passed nuget.org per process and didn't edit your config.
  - uv's minor-version link can't be followed from sandboxed shells. I used explicit interpreter paths.
- **Competitors already exist.** At least two GTA V AI-NPC mods launched in mid-2026 (LivingLSAIs, Los Santos Alive). Take-Two has taken down an AI-NPC showcase video before. Rockstar's 10 Sep 2026 mod guidelines ban cloned voices and changes to story characters. See `artifacts/research/launch-and-workarounds.md`.

## Where everything is
- **Code:** branch `win/validation`, in worktree `C:\Users\Brian\Documents\Github\smartfellas-GTA-wt\integration`. It's 25 fix commits on top of `7aeabaa`, 142 files, +15.1k/−0.6k lines. **Not pushed.** The per-fix branches and worktrees were merged and removed.
- **Videos:** `artifacts/rig/video/`. Watch these first:
  - `clip-split-before-after.mp4`: 30 s side by side, stock GTA greeting vs Living Los Santos.
  - `clip-memory-callback.mp4`: he remembers your name and job.
  - `clip-no-invented-missions.mp4`: no invented missions or cash.
  - `clip-demo-boardwalk-90s.mp4`: a 90-s, 4-turn conversation.
  - All are real time with no cuts.
  - `clip-before-vanilla.mp4`: stock GTA interaction (canned).
  - `clip-after-first-conversation.mp4`: the first real conversation.
  - `clip-after-fixes-boardwalk.mp4`: after the overnight fixes.
  - Full raw recordings `0*.mkv`.
- **Evidence:** `artifacts/rig/` (screens, perf, evidence JSON, logs), `artifacts/windows-test/logs/`, `artifacts/spec-audit/`, `artifacts/speech-real/`, `artifacts/local-llm/`, `artifacts/research/`, `artifacts/STATE.md` (working log).
- **Tools installed for your user only** (no admin):
  - `%USERPROFILE%\.lls-tools\` holds uv, Python 3.11/3.12, ffmpeg, whisper.cpp with `base.en`, llama.cpp Vulkan with Qwen2.5-7B, the rig scripts, and the Script Hook V / SHVDN / PresentMon downloads with SHA-256 hashes.
  - The .NET 10 SDK is in the repo's gitignored `.dotnet`.
- **Codex sessions:** I stopped the two Hansard-started Codex app-servers at 02:40, as you asked. Hansard's web UI later restarts its own idle app-server; I left that one alone.
