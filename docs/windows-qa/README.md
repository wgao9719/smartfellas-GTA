# Living Los Santos on Windows: what works, where things are, how to keep going

Hand-off from the overnight Windows test on 2026-10-10/11. The test machine was "TOWER-PENGU": Windows 11, Ryzen 9 9950X, Radeon RX 9070 XT, 4K screen, with GTA V Legacy from Steam (game version 1.0.3889.0). Everything here is on branch `win/validation`.

## In one paragraph

Living Los Santos is a GTA V Story Mode mod. It lets you walk up to an ordinary person on the street and have a real conversation with them. Their replies come from an AI model, and they remember what you told them. On this branch that works in the real game on Windows. The AI model runs on the same PC (no internet or cloud account needed). You pick a person, type a message, and they answer in subtitles a few seconds later. They turn to face you, remember your name and job for the rest of the conversation, and won't invent missions or offer you money. Police officers and people who are part of a mission are left alone. Voice chat, people remembering you between game sessions, and several bigger features from the spec are not done yet (see "What to build next").

## The controls, in plain English

| Key | What it does |
|---|---|
| **F6** | **Pick a person to talk to.** It chooses the closest ordinary pedestrian roughly in front of you, within about 8 metres. A small blue arrow appears above their head, and the top-left corner says "Target: Nearby resident (Available)". If nobody suitable is close, it says so. If the nearest person is a police officer, for example, it tells you they're "not available". |
| **F7** | **Type to that person.** A text box opens at the bottom of the screen ("You: _"). Type your message and press **Enter** to send it. Their reply appears as subtitles at the bottom of the screen. Press **Escape** to close the box without sending. |
| **Backspace** | **Stop.** It cancels the current reply and forgets who you picked. |
| **F8, twice within 5 seconds** | **Keep this person.** Their memories are saved permanently, so the game remembers them, not just this one chat. (Bringing the same person back after they walk away and disappear isn't built yet.) |
| **N** (hold) | **Talk with your microphone** instead of typing (push-to-talk). Speech recognition is set up, but this hasn't been tried in the game yet. |
| **B** (hold) | **Whisper** with your microphone. Only a person within about 3 metres "hears" it. |
| Starting a typed message with `/w ` | Whisper a typed message. |
| **F10** | **"Yes, this is my save."** When you load into the game, a warning asks you to confirm which save file's memories to use. Press F10 to confirm. If you play a different save, edit the profile in the settings file first. This stops one save's memories leaking into another. |
| F4 | Developer only: opens the Script Hook V .NET command console. |

All keys can be changed in `scripts\LivingNPC\LivingNPC.ini` in the game folder.

## What was tested in the real game, and the result

| What | Result |
|---|---|
| The mod loads in GTA V 1.0.3889.0 with Script Hook V v3889.0 and Script Hook V .NET 3.7 nightly 193 | Works |
| Picking a pedestrian (F6) | Works, and the blue arrow shows who you picked |
| Typing a message and getting a sensible, in-character reply (F7) | Works. Replies take about 2–4 seconds with the local model |
| The person turns to face you, and the game confirms they did | Works |
| They remember what you told them earlier in the conversation ("What's my name?" → "Your name is Franklin.") | Works |
| Asking for jobs, missions or money doesn't make them invent any | Works in the game tests. In 30 offline attempts, a vague hint still slipped through 4 times |
| Police are refused, people too far away are refused, and Backspace cuts off a reply | Works |
| Turning the AI service off and on while the game runs | Works. The game keeps running and shows one "service unavailable" message |
| Loading the wrong save is caught | Works |
| 20 minutes of play with everything running | Smooth: 120 fps, no crashes from the mod, memory stays flat |
| Talking with a microphone in the game | Not tried yet (needs a person at the mic) |
| People overhearing conversations | Not working in the game yet (the "perception" feature is still development-only) |
| A kept person (F8) coming back later | Not built yet |

Videos of the real game are on the test PC in `artifacts\rig\video\` (too large for git). The best ones:
- `clip-split-before-after.mp4`: stock GTA vs the mod, side by side.
- `clip-memory-callback.mp4`
- `clip-no-invented-missions.mp4`
- `clip-demo-boardwalk-90s.mp4`

## Where everything is

| What | Where |
|---|---|
| All code changes | Branch `win/validation`: about 25 fix commits on top of `7aeabaa` (`living-los-santos-foundation`) |
| The AI service (Python) | `service/lls/` |
| The in-game part (C#, runs inside GTA through Script Hook V .NET) | `adapter/LivingNPC.GameAdapter/` (game code), `adapter/LivingNPC.Core/` (logic you can test without the game) |
| Scripts to set up and test a Windows PC | `tools/windows-rig/` (described below) |
| The overnight report | `docs/windows-qa/overnight-report-2026-10-11.md` |
| In-game test reports | `docs/windows-qa/ingame-qa-1.md`, then `ingame-qa-2-reverify.md`, then `ingame-qa-3-spotcheck.md` (the latest) |
| How the code compares to the spec (543 requirements, with gaps and severities) | `docs/windows-qa/audits/a-*.md` to `d-*.md` |
| Notes on other AI-NPC mods, launch videos and GTA modding workarounds | `docs/windows-qa/research-launch-and-workarounds.md` |
| Speech recognition on Windows, and the local AI model setup | `docs/windows-qa/speech-whisper-windows.md`, `docs/windows-qa/local-llm-setup.md` |
| Detailed fix records (one per fix) | `docs/development-validation.md`, `docs/first-gta-adapter-fixes.md`, `docs/first-conversation-fixes.md`, `docs/conversation-reverify-fixes.md`, `docs/dialogue-grounding.md` |
| Screenshots, logs, frame-time captures, videos | Test PC only: `artifacts\rig\` and `artifacts\windows-test\logs\` (git ignores `artifacts/`) |

## Setting up a Windows PC from scratch

Nothing here needs administrator rights. Tools go into `%USERPROFILE%\.lls-tools\`, where `tools/windows-rig/rig-paths.ps1` expects them. Every path can be overridden with an environment variable listed in that file.

1. **GTA V Legacy** from Steam (not Enhanced). Launch it once, accept the installer prompts, sign in to Rockstar, and play through the start until you can walk around freely. The prologue and the first Franklin mission must be finished, because the mod deliberately does nothing during missions.
2. **Developer tools:**
   - **uv:** install uv 0.12.17 into `.lls-tools\uv` (`$env:UV_UNMANAGED_INSTALL` set to that folder, then the official install script).
   - **.NET 10 SDK:** run `dotnet-install.ps1 -Channel 10.0 -InstallDir <repo>\.dotnet`.
   - **NuGet source:** if `dotnet restore` says "Unable to resolve", your NuGet config has no package source. Set `$env:RestoreSources='https://api.nuget.org/v3/index.json'` for the session.
3. **Downloads**, all into `.lls-tools\rig\downloads`:
   - `ScriptHookV_3889.0_1158.13.zip` from dev-c.com. Its version must match the game version.
   - `ScriptHookVDotNet-v3.7.0-nightly.193.zip` from the scripthookvdotnet-nightly GitHub releases. The stable 3.6 release crashes on game versions 3258 and later.
   - Optionally, PresentMon from GitHub for frame-time measurement. It works without admin.
4. **Local AI model** (optional; the service also has an "AI off" mode and a fake test mode):
   - Download the llama.cpp Windows Vulkan release and the `Qwen2.5-7B-Instruct` Q4_K_M model into `.lls-tools\llm\` (see `local-llm-setup.md` for the exact files).
   - Start it with `tools\windows-rig\start-llm.ps1`. It uses about 4.7 GB of graphics memory next to GTA.
5. **Install the mod:**
   - Close GTA, then run `tools\windows-rig\rig-install.ps1`.
   - It snapshots the game folder, copies in Script Hook V and the .NET runtime (plus `args.txt`, which turns off BattlEye for Story Mode), builds the in-game adapter, copies it into `scripts\`, and writes the settings file.
6. **Start the AI service** with `tools\windows-rig\start-service.ps1 -Provider local_openai`. Use `-Provider fake` for canned test replies with no model.
7. **Play:**
   - Launch GTA V Legacy and click **Story Mode**. Do **not** click Online, which sits right next to it.
   - Press F10 at the save warning, walk up to someone, then press F6, then F7.
   - The dashboard is at http://127.0.0.1:8765/dashboard.
8. **Before playing GTA Online**, run `tools\windows-rig\rig-uninstall.ps1`. It removes everything the installer added and checks that the game folder matches the original snapshot.

## Making a change and testing it (the loop)

1. Change code on `win/validation`.
2. Run the automated checks:
   - `uv run --frozen ruff check service tests tools`
   - `uv run --frozen pytest -W error` (about 4 minutes, 2,700+ tests)
   - `<repo>\.dotnet\dotnet.exe test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj -c Release`
   - `uv run --frozen python tests/live_bridge_check.py`
3. **In-game code changed?** Build and copy it in while the game is running; Windows doesn't lock the files:

   ```
   dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj -c Debug
   uv run --frozen python tools/deploy_adapter.py --configuration Debug --scripts-dir "<GTA folder>\scripts"
   ```

   Then in the game press F4, type `Reload()`, press Enter, press F4 again, then F10.
4. **AI service changed?** Restart it with `start-service.ps1`, then press F10 in the game.
5. **Test in the game.** `tools/windows-rig/rig.ps1` and `rig-qa.ps1` let a script or an AI agent (we used Codex) drive the game:
   - Keyboard and mouse: `Send-RigKey`, `Send-RigText`, `Hold-RigKeys`, `Move-RigMouse`, `Click-RigAt`.
   - Screenshots: `Shot`.
   - Screen recording: ffmpeg Desktop Duplication, no admin needed.
   - Service evidence: `Get-LlsOverview`, `Get-LlsSessionDiagnostics`, `Get-LlsMemories`, `Get-AdapterLogTail`.

   The input functions refuse to type into anything except the GTA window. The in-game QA reports above show the step-by-step test scripts we used.

## Things that bit us (read before debugging on Windows)

- **Script Hook V .NET's settings reader cuts `URL=http://...` down to `http`.** The adapter now reads raw values itself.
- **Don't register game "decorators" after the game has started.** It crashed GTA once. People are now tracked with a check, every frame, that they're still the same person.
- **The adapter is built on one thread and ticked on another.** It now binds to the game thread on its first tick.
- **The F4 console animates.** Pressing F4 twice quickly closes it and opens it again.
- **GTA pauses when its window loses focus,** and automated key presses only reach the focused window. At 4K with 150% scaling, screenshot tools must be "DPI aware".
- **A new save starts in the prologue.** Failing a mission section three times offers "Skip".
- **The GTA start screen has ONLINE right next to STORY MODE.** Click carefully.
- **Story phone calls** (Simeon) ring while you're typing. Enter used to answer them; that's fixed.
- **The game version reported in the session info says 3788** while the real game is 3889. Script Hook V .NET's version list lags behind. It's cosmetic but should be fixed.
- **Small local models mix up who said what** unless the conversation is sent as proper back-and-forth chat turns. That's done now.
- **Word-list filters for "don't offer the player jobs or money" don't generalize.** A short yes/no check by the model on suspicious sentences is what made it reliable enough.

## What to build next (from the spec)

The spec is in `references/SPECS.md` and `references/SPECS_agentdesign.md`; the audit reports list every gap. In priority order:
1. **The other everyday actions:** follow me, stop following, walk to a safe spot, wander nearby, back away from danger, and a few safe gestures (spec §5.5, §11). Today only "turn to face you" and "wait here" exist.
2. **Bringing kept and named characters back:** a person you kept with F8, or one of the 12 named pilot characters, should reappear and still be the same person (FR-ID-02/05, §8.3).
3. **Overhearing:** people standing nearby should remember what they overheard, and only what they could actually hear (FR-PER-01..04, test T10).
4. **Voices:** speak the replies aloud with a different local voice per character (§10.4, FR-DLG-03), then microphone push-to-talk tested in the game (T05).
5. **Accessibility:** bigger subtitles and a high-contrast option (§18).
6. **Remaining spec gaps:** marked "Partial" or "Missing" in `docs/windows-qa/audits/`.
