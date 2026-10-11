# Launch patterns and GTA V workarounds (research, 2026-10-11)

Web research only; nothing was downloaded or run. Tags: **[V]** = read in a primary source (official page, repo, issue). **[S]** = press or forum. **[U]** = unverified, conflicting, or my own inference. I could not get YouTube durations or view counts, so video lengths are not measured.

## 1. How comparable AI-NPC mods launched

| Project | What the launch showed | Engagement / criticism |
|---|---|---|
| **Mantella** (Skyrim VR/SE, FO4), 2023 to 0.14 (Apr 2026) | Reddit post and YouTube dev updates: first-person VR, spoken questions, an NPC describes a held sword, a shopkeeper reasons about closing time, memory from conversation summaries [1][2]. 0.14 was launched as text patch notes (5-NPC group chats, actions) [3]. | "Incredibly surreal". Criticism: ~15 s replies, footage **sped up** after prompts, player voice replaced in the edit, voices "far from convincing" [1][2]. |
| **SkyrimNet** (2025–26) | Text-first GitHub README and Discord, no official trailer. The README sells latency work (TTS starts on the first sentence), NPC-to-NPC scenes and a localhost dashboard [5][6]. | Reach came from third-party YouTubers; one install video is titled around "closing the delay" [7]. **Latency is the headline metric.** |
| **Herika → CHIM** | Companion aware of location, nearby NPC chatter, combat and books [8]. | Setup complexity and API cost [8]. |
| **Sentient Streets** (Inworld + ElevenLabs, GTA V, Aug 2023) | One clear scenario: a cop picks a partner from 3 officers with distinct personalities, using push-to-talk [9]. | Press within days. Then **Take-Two claimed the YouTube video and DMCA'd the Nexus page** [10]. |
| **Inworld Origins, NVIDIA Covert Protocol, Ubisoft NEO NPC** (2023–24) | Polished tech demos. | Called "stilted", with robotic VO and lip sync. Seen as a gimmick unless tied to game systems. Voice-actor labour concerns [11]–[14]. |
| **2026 GTA V competitors** | **LivingLSAIs** (Jun 2026, ~21.8k downloads, Gemini, memory, gossip, needs SHVDN nightly; video "GTA 5 NPCs ARE ACTUALLY ALIVE NOW! 🧠") [15]. **Los Santos Alive** (May 2026, ~30k downloads, RagePluginHook + Gemini) [16]. | Comments: slow or missing replies on free-tier keys, NPCs "shun the player in unison", peds behave alike, crashes on Enhanced, mic failures [15][16]. |
| **Creator videos** | "I Gave GTA 5 NPC's AI VOICES!" and "This AI NPC Realized He Was in a Video Game" [17]. | The "NPC learns it's a game" reveal is a recurring hook [S]. |

**Takeaways [U]:**
- Open with the payoff (a memory callback) in the first 3 s.
- One scenario per clip.
- Every line readable on mute.
- Prove it is real time; the hidden speed-up in [1] was called out.
- Show NPCs acting, not just talking.
- Show variety between peds; sameness is the main complaint about the GTA competitors.

**Shot list (60–75 s, 16:9 master plus a 9:16 crop):**
1. **0:00–0:03** Franklin returns to a ped from an earlier scene. Ped: "You again? You clipped my car on Vinewood." Caption: *"He remembers."*
2. **0:03–0:12** Split screen, **VANILLA | LIVING LOS SANTOS**: same ped model, spot and bump. Canned bark on one side, contextual reply on the other.
3. **0:12–0:25** One unedited exchange. "Real time, no cuts" badge, latency timer, burned-in subtitles with speaker names.
4. **0:25–0:38** Gossip: ped A sees an event. Card: *"2 in-game hours later"*. Ped B mentions it.
5. **0:38–0:48** Safe action: directions, follow, or flee when threatened.
6. **0:48–0:58** Card: *"Next day, game reloaded"*. Memory callback.
7. **0:58–1:05** 2–3 peds react differently to one event.
8. **End card:** "Story Mode only, free, not affiliated with Rockstar/Take-Two", plus the link.

Production rules:
- Radio off to avoid Content ID claims [U].
- No cloned real voices, no reused Rockstar VO, no generated voices for the protagonists [26].
- Disclose any speed-up.
- No API keys on screen.
- Post on more than one platform [10].

## 2. GTA V modding workarounds (build 3889)

**BattlEye**
- Rockstar documents two ways to disable it for Story Mode: the Rockstar Games Launcher toggle, or `-nobattleye` "for third-party launchers" [19] **[V]**.
- Script Hook V's `args.txt` reportedly acts like `commandline.txt` [20] **[S]**. I found no confirmation that Steam builds read it **[U]**. To be sure, also set `-nobattleye` in Steam Launch Options (no admin needed).
- Leftover args.txt, commandline.txt or Steam options later cause "BattlEye is required to play GTA Online". Remove all of them before going Online [20].
- Script Hook V v3889.0 (15 Jul 2026) supports 1.0.3889.0 (Legacy) and 1.0.1158.13 (Enhanced). It exits the game if you enter Online [21] **[V]**.
- Each patch breaks hooks for days to weeks [S]. Avoid game updates mid-QA.

**SHVDN**
- Stable v3.6.0 does not work with game ≥1.0.3258.0, so a nightly is required. Use the ini that ships with the nightly, and prefer stable APIs (nightly.191 changed `Ped.CreateRandom`) [22] **[V]**. The latest is nightly 193 (8 Oct).
- **Open issue #1805 (27 Jul 2026):** on 3889 the game crashes silently when Story Mode loads. The SHVDN log is blank and the exit code is 0xE0434352 (a .NET exception). It is reported on nightlies 181/182/191. One user fixed it by **turning off Windows compatibility mode** on the exe; Win7 compat brings the crash back. PR #1810 is an unmerged "hacky" fix [22] **[V]**.
- **Console and reload:** F4 opens the console (`ConsoleKey`), Insert reloads (`ReloadKey`), or type `Reload()`. Past bugs [22]:
  - #1387: crash when opening the console (race condition, fixed 2024).
  - #923: reload fails if a script changes the current directory.
  - #547: SHVDN dies after Script Hook V's own Ctrl+R reload.
  - Crowd Control warns about an accidental Scroll Lock reload binding [24].
- **[U]** Dispose HttpClient, timers and threads in `Aborted`, and restart the game instead of hot-reloading in automated runs.
- **Crash triage:**
  - Requires .NET ≥4.8 and the latest x64 VC++ redistributable [22].
  - Read `ScriptHookV.log`, `ScriptHookVDotNet.log` and `asiloader.log` [23].
  - For pool-limit crashes, update gameconfig [S].
  - Don't follow advice to downgrade the game.

**Legacy vs Enhanced**
- They are separate Steam apps (271590 vs 3240220) with separate Documents folders.
- Community SHVDN supports Legacy only. Enhanced needs the SHVDNE fork, whose API differs from nightly [23]. Enhanced users report DirectStorage errors and crashes [15][16].
- Make sure automation launches **Legacy**.

**Policy**
- Rockstar's 2017 stance: Take-Two "generally" won't act against single-player, non-commercial mods. This is "not a license" [25].
- **Mod Guidelines (10 Sep 2026)** [26]:
  - No real person's voice or likeness without permission.
  - No reuse of Rockstar VO.
  - No changes to official storylines or characters.
  - Monetization only through approved storefronts.
- Rockstar warned IlloJuan over AI-cloned voices of real people (Sep 2026) [26], and Sentient Streets was taken down in 2023 [10].
- So: synthetic voices only, no paywall, and expect claims against videos.

## 3. Fast path to free roam

- There is **no official skip**. The Prologue is set in North Yankton, which has no Los Santos peds. Free roam opens after *Franklin and Lamar*.
- **Mission-fail skip:** after about 3 fails, the Mission Failed screen offers to skip *that section only*. Some players report crashes afterwards [27] **[S]**. It is slow across two missions.
- **Third-party save** "Fresh Start – Introduction Completed" (2016, ~92k downloads) goes in `Documents\Rockstar Games\GTA V\Profiles\<id>\` [28]. Risks:
  - Archive of unknown provenance; some users say it was corrupt.
  - Untested on 3889.
  - Overwrites `SGTA5000x` slots.
  - Possible cloud-save conflicts [U].
- **Recommended:** play both missions once (about 20–30 min **[U]**). Back up `SGTA5000x` as a golden fixture and restore it before each run.

## 4. Frame-time capture without admin

- **PresentMon:**
  - The README requires membership of **Performance Log Users**; otherwise you get "failed to start trace session (access denied)". Joining the group needs admin [29] **[V]**.
  - v2.3.1 lets the GUI run without admin, but installing it very likely still needs admin **[U]**.
  - Check membership with `whoami /groups`.
- **CapFrameX, OCAT and FrameView** are built on PresentMon/ETW, so the same limit applies. OCAT has been stale since 1.6.x [30].
- **Game Bar FPS** needs UAC approval and a reboot [31].
- **Options that work without admin:**
  1. **AMD Adrenalin logging:** Ctrl+Shift+L writes a CSV to `%LOCALAPPDATA%\AMD\CN`, sampled every 0.25–5 s. These are **aggregates, not per-frame**, and AMD's docs give the units inconsistently [32].
  2. **GTA V benchmark:** `-benchmark -benchmarkFrameTimes -benchmarkIterations=N` [33] **[V]**. It writes frame-time percentiles to `Documents\Rockstar Games\GTA V\Benchmarks\` [33] **[S]**. Good for mod on/off A/B tests, but the scenes are scripted.
  3. **Script-side:** `Game.LastFrameTime` is `GET_FRAME_TIME` in seconds, and `Game.FPS` is its inverse [34] **[V]**. It is the game timestep and may be affected by time scale or clamping **[U]**. Also log `Stopwatch` time between ticks and our own `OnTick` cost, and flush in batches.
  4. RTSS/Afterburner: the installer needs admin **[U]**.

## 5. Recording without admin (AMD, 4K, 150% DPI)

- Use **borderless windowed at 3840×2160**. Exclusive fullscreen breaks GDI capture [37].
- **Game Bar (Win+Alt+R):** no admin needed.
  - It records only the **focused game**. Output goes to `%USERPROFILE%\Videos\Captures`, with a 2 h default cap [35].
  - Audio can be Game, All or None [35]. If TTS plays from the Python process, "Game" would miss it, so use **All** **[U]**.
  - Users find its HDR better than ReLive's [36] **[S]**.
- **Adrenalin Record:** Ctrl+Shift+E in recent guides, Ctrl+Shift+R in older ones; check Settings > Hotkeys.
  - Turn on "Record Desktop" for borderless capture.
  - Instant Replay goes up to 20 min. H.264, HEVC and AV1 are available [36] **[S]**.
  - **HDR footage comes out washed out** (tagged BT.709). Turn HDR/Auto HDR off while recording (Win+Alt+B) [36].
- **ffmpeg `ddagrab`** captures D3D11 frames at native pixels, so 150% DPI doesn't matter [37] **[V]**:
  - The default is 30 fps; set `framerate=60`.
  - For HDR, use `output_fmt=10bit` with `allow_fallback`.
  - Pairing it with `h264_amf`/`hevc_amf` is **[U]**. The safe path is `hwdownload,format=bgra` plus libx264.
- **`gdigrab`** is not DPI-aware (ticket #4505), so it captures a cropped region at 150% [37].
- **ffmpeg has no system-audio loopback.** Use Stereo Mix through dshow, or record audio separately **[U]**.
- **[U]** Some hotkey hooks ignore keys sent with SendInput. Test each tool once.

## References

1. Road to VR, Mantella demo (2023): https://www.roadtovr.com/chatgpt-skyrim-vr-ai-mod/
2. Futurism: https://futurism.com/the-byte/chatgpt-mod-npcs-skyrim
3. Mantella 0.14: https://ingamenews.com/2026/04/this-skyrim-ai-mod-update-brings.html · https://github.com/art-from-the-machine/Mantella
4. (Mantella Nexus) https://www.nexusmods.com/skyrimspecialedition/mods/98631
5. SkyrimNet README: https://github.com/MinLL/SkyrimNet-GamePlugin/blob/main/README.md
6. SkyrimNet Beta 25: https://github.com/MinLL/SkyrimNet-GamePlugin/discussions/639
7. https://www.youtube.com/watch?v=0mcBlQntyZY · https://www.youtube.com/watch?v=5q5qA9XLiAM
8. Herika: https://www.pcgameshardware.de/Kuenstliche-Intelligenz-Hardware-279517/News/ChatGPT-Begleiter-fuer-Skyrim-1420070/ · CHIM: https://www.nexusmods.com/skyrimspecialedition/mods/126330
9. Sentient Streets: https://videogameschronicle.com/news/gta-5-modder-adds-ai-characters-you-can-talk-to-with-a-microphone · https://80.lv/articles/a-new-gta-v-ai-mod-lets-you-talk-to-npcs-with-a-microphone/
10. Take-Two takedown: https://hitmarker.net/news/gta-v-mod-allowing-players-to-talk-to-ai-npcs-reportedly-removed-by-take-two-518722
11. https://www.axios.com/2023/08/07/inworld-ai-origins-demo
12. https://www.pcgamer.com/gaming-industry/i-tried-two-demos-of-machine-learning-ai-npcs-and-they-didnt-convince-me-the-ai-will-lead-to-anything-that-immersive-sims-like-deus-ex-havent-already-done-better/
13. https://mobilesyrup.com/2024/03/20/ubisoft-ai-neo-npc-bad/
14. https://videogames.si.com/features/covert-protocol-hands-on
15. LivingLSAIs: https://www.gta5-mods.com/scripts/livinglsais-0706c201-ba7c-4e19-9c70-d77db561f835 · https://www.youtube.com/watch?v=ryGmx2XsoyY
16. Los Santos Alive: https://www.gta5-mods.com/scripts/los-santos-alive-next-generation-ai-npcs · https://www.youtube.com/watch?v=2J_mW5EXPxk
17. https://www.youtube.com/watch?v=-RSG-PYZi-w · https://www.youtube.com/watch?v=bFukoN5QwJ0 · https://www.youtube.com/watch?v=r-JfLrt0kfo
18. AI-voice ethics debate: https://www.gfinityesports.com/article/skyrim-modding-ai-voice-acting-ethics-debate
19. Rockstar BattlEye: https://support.rockstargames.com/articles/1nenwhZlVrJY6CTFeSS2Fx/grand-theft-auto-online-battleye-faq · https://support.rockstargames.com/articles/eUgRHVRSN2V9gimBT32YX/gtav-title-update-1-69-notes-ps5-ps4-xbox-series-x-s-xbox-one-pc · https://support.rockstargames.com/articles/ocorZr1KpQE8WvoHE3gBG/battleye-troubleshooting-for-grand-theft-auto-v
20. Steam threads: https://steamcommunity.com/app/271590/discussions/0/7027393439965553210 · https://steamcommunity.com/app/271590/discussions/0/594011209257219990 · https://steamcommunity.com/app/271590/discussions/0/744883501165185378
21. Script Hook V: http://www.dev-c.com/gtav/scripthookv/
22. SHVDN: https://github.com/scripthookvdotnet/scripthookvdotnet/blob/main/README.md · https://github.com/scripthookvdotnet/scripthookvdotnet-nightly/releases · https://github.com/scripthookvdotnet/scripthookvdotnet/issues/1805 · https://github.com/scripthookvdotnet/scripthookvdotnet/issues/1387 · https://github.com/scripthookvdotnet/scripthookvdotnet/issues/923 · https://github.com/crosire/scripthookvdotnet/issues/547
23. SHVDNE: https://www.gta5-mods.com/tools/script-hook-v-net-enhanced
24. https://crowdcontrol.live/guides/gtav
25. https://support.rockstargames.com/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods · https://www.gamespot.com/articles/after-angering-gta-5-fans-rockstar-releases-new-st/1100-6451151
26. https://www.rockstargames.com/community-resources/mod-guidelines · https://www.gamedeveloper.com/production/-do-not-rockstar-outlines-modding-rules-before-gta-vi-touches-down · https://www.gtaboom.com/rockstar-is-cracking-down-on-ai-voice-mods-starting-with-illojuan-73b5
27. https://gta.wiki/w/Missions · https://steamcommunity.com/app/271590/discussions/0/611703999972050394
28. https://www.gta5-mods.com/misc/fresh-start-introduction-completed-savegame
29. PresentMon: https://github.com/GameTechDev/PresentMon/blob/main/README.md · https://github.com/GameTechDev/PresentMon/releases
30. OCAT: https://ocat.readthedocs.io/ · https://gpuopen.com/ocat-1-6-1/
31. https://answers.microsoft.com/en-us/xbox/forum/all/fps-counter-not-showing-in-xbox-game-bar/2d6508ac-f970-4b2c-9291-dd6f41181f96 · https://www.howtogeek.com/706162/how-to-see-fps-in-any-windows-10-game-without-extra-software/
32. https://www.amd.com/en/resources/support-articles/faqs/DH3-038.html · https://wccftech.com/how-to-use-radeons-built-in-radeon-performance-logging/
33. https://support.rockstargames.com/articles/2VjbVziQCiTiiVhDbmnexc/full-list-of-command-line-parameters-for-grand-theft-auto-v-on-pc · https://steamcommunity.com/app/271590/discussions/0/611703999968276126
34. https://github.com/scripthookvdotnet/scripthookvdotnet/blob/main/source/scripting_v3/GTA/Game.cs
35. https://elevenforum.com/t/change-max-recording-length-for-gaming-captures-in-windows-11.17924/ · https://elevenforum.com/t/enable-or-disable-record-audio-for-gaming-captures-in-windows-11.17961/ · https://nerdschalk.com/how-to-screen-record-on-windows-11-with-audio/
36. https://www.amd.com/en/resources/support-articles/faqs/DH3-023.html · https://maketecheasier.com/use-amd-radeon-softwares-record-stream/ · https://community.amd.com/t5/pc-drivers-software/its-almost-2023-and-relive-still-cant-record-hdr/m-p/577020 · https://community.amd.com:443/t5/pc-drivers-software/hdr-video-recording-with-amd-software/m-p/520693
37. ffmpeg: https://ffmpeg.org/doxygen/7.1/vsrc__ddagrab_8c.html · https://trac.ffmpeg.org/wiki/Capture/Desktop · https://trac.ffmpeg.org/ticket/4505
