---
title: "GTA V Living NPCs"
subtitle: "Product & Technical Specification — SkyrimNet-Level Foundation, Windows Runtime, Mac Development"
author: "Engineering working draft"
date: "10 October 2026"
version: "0.3"
status: "Proposed; G0 toolchain spike not yet run"
---

# Contents

This specification separates **required alpha capabilities** from **post-alpha research**. Start with **§2 (feasibility)** and **§16 (first sprint)**, then use the requirement IDs in **§5** as the issue and test checklist. Companion doc: `SPECS_agentdesign.md` (NPC roles, control policies, memory rules, and the post-v1 NPC-to-NPC design).

| Product and implementation | Delivery and governance |
|---|---|
| [0. Executive brief](#sec00) | [12. Non-functional requirements and performance budgets](#sec12) |
| [1. Goals, non-goals, and assumptions](#sec01) | [13. Developer experience, repository, and CI](#sec13) |
| [2. Platform decision and technical feasibility](#sec02) | [14. Observability, testing, and quality assurance](#sec14) |
| [3. SkyrimNet parity contract](#sec03) | [15. Milestones, staffing, and delivery plan](#sec15) |
| [4. Players, scenarios, and experience design](#sec04) | [16. First sprint: issue-ready backlog](#sec16) |
| [5. Functional requirements and acceptance criteria](#sec05) | [17. Failure handling and risk register](#sec17) |
| [6. System architecture](#sec06) | [18. Security, privacy, accessibility, and IP](#sec18) |
| [7. Network/API contracts](#sec07) | [19. Phase 2+: make Los Santos feel alive](#sec19) |
| [8. NPC identity, world state, and persistence](#sec08) | [20. Demo scripts and sign-off](#sec20) |
| [9. Data model and retrieval](#sec09) | [21. Open engineering decisions](#sec21) |
| [10. AI and speech orchestration](#sec10) | [22. Source notes and verification links](#sec22) |
| [11. Game action safety, eligibility, and execution](#sec11) |  |


> **Decision in one sentence.** Build a **single-player GTA V Legacy mod for Windows** using **Script Hook V + ScriptHookVDotNet**, with a **Python AI/voice/memory service** running beside the game on the same PC over localhost; **develop on Macs** and test in-game on a Windows test rig. Do not depend on leaked game code, GTA Online, or invasive memory patches. **Feature work starts after a short G0 toolchain spike (§2.3).**

> **Scope clarification.** “SkyrimNet-level” means strong *functional parity* in conversation, perception, character identity, memories, voice, action dispatch, triggers, and diagnostics—not a promise to match SkyrimNet's mature installation UX, 3,000+ bios, every extension API, or its engine-native latency at v1.0. This document describes what to implement and what must be proven.

# 0. Executive brief {#sec00}

**Product name (working):** Living Los Santos (LLS). **Platform:** GTA V PC Story Mode on Windows 10/11 (x64), initially Legacy; the game and the AI service both run on the player's PC. **Development environment:** macOS on Apple Silicon for writing, building, and backend testing; a Windows test rig for in-game testing. **Audience:** players who want memorable, responsive characters; mod developers who need an extensible AI layer; internal researchers testing autonomous NPC behavior.

The project makes selected GTA V pedestrians into persistent, memorable AI characters. Players can approach and speak to a person using push-to-talk or typed text; the NPC considers what it could realistically perceive, its personality and private memories, and responds with generated speech and subtitles. Some responses include *validated*, limited in-game actions—turn around, follow, move, wait, flee—rather than just textual role-play. Nearby NPCs may comment on events they could perceive; NPCs talking with each other comes after v1 (§19). The AI backend is modular so GTA is one adapter rather than the permanent center of the product.

**Hard distinction:** GTA V's background pedestrians are often disposable engine entities, unlike Skyrim's named persistent actors. V1 therefore uses **12–20 curated persistent characters** in test neighborhoods, plus session-only ambient pedestrians. Identity never relies solely on a volatile in-game entity handle. Later phases can build a persistent neighborhood simulation that re-materializes characters as the player travels.

## 0.1 Release gates

| Gate | Definition | Result required |
|---|---|---|
| G0 — Toolchain feasibility | The Windows test rig runs GTA V Legacy Story Mode with Script Hook V, SHVDN, a C# script **built on a Mac and deployed to the rig**, and a working localhost bridge; the Mac dev setup is reproduced on a second Mac | All §2.3 checks pass before core build |
| G1 — Interaction vertical slice | Player targets a named NPC, types/speaks a line, receives streamed text + audible reply, and sees one allowed action | Repeatable in 20 consecutive trials |
| G2 — Memory and world truth | NPC recalls a prior interaction after restarting game/service, without claiming events it never witnessed | Pass adversarial and persistence test suite |
| G3 — SkyrimNet-level alpha | Context, voice, memory, scoped knowledge, triggers, actions, dashboard, and failure recovery work together | Full acceptance matrix passed |
| G4 — Beta | Stable on the supported Windows hardware/game-build matrix with clear install/recovery docs and profiling evidence | Release candidate approved |

## 0.2 Product principles

1. **NPCs act, not only talk.** Spoken intent and in-game action are separate, both visible and testable.
2. **NPCs know only what they could know.** Perception, hearsay, and knowledge provenance are first-class.
3. **Memory is durable but selective.** Write meaningful events; retrieve relevant episodes; support correction and deletion.
4. **The game thread never waits for AI.** LLM, speech, storage, and HTTP I/O must be outside the script tick.
5. **Safe game actions are deterministic.** Models propose structured actions; a game-side allowlist authorizes them.
6. **Windows runtime, Mac development.** The game adapter and shipped service run on Windows; every developer workflow (build, unit tests, backend integration against a fake game) works on macOS. No runtime component may depend on a Mac, and nothing except release packaging may require Windows to build.
7. **Own the implementation and licenses.** Ship only original code and permitted dependencies; users provide their legally obtained GTA files.

# 1. Goals, non-goals, and assumptions {#sec01}

## 1.1 Goals for the first SkyrimNet-level alpha

- **Conversation:** targeted 1:1 chat via text and push-to-talk (the player and one NPC per turn); responsive text streaming, subtitles, and interrupt/cancel.
- **Personality:** durable identity, occupation, attitude, conversational preferences, per-character voice and relationship with the player.
- **Perception:** nearby actor and player state; position/distance, approximate visibility/hearing, time of day, weather, player vehicle/weapon, and observed local events where game APIs permit.
- **Memory:** first-person episodic entries, semantic retrieval (or robust retrieval fallback), importance, decay, source/provenance, editable dashboard.
- **Social state:** relationship variables (trust, affinity, fear, respect) derived from grounded events rather than freeform LLM guesses.
- **Actions:** small allowlist of real world effects with preconditions, acknowledgment, and explicit failures.
- **Triggers:** configurable event-to-reaction rules, rate-limits, cooldowns, and NPC-initiated remarks to the player.
- **Operations:** local diagnostic dashboard, event traces, model/provider selectors, safe fallback modes, data export/erase.
- **Platform:** documented, reproducible Windows runtime on a pinned hardware/software matrix, plus a documented Mac development setup (build, deploy to the rig, backend tests) that any team Mac can reproduce.

## 1.2 Explicit non-goals for v1.0

- GTA Online/FiveM or bypassing multiplayer anti-cheat; multiplayer builds are out of scope.
- Any leaked GTA V source code, unauthorized redistribution of game assets, or bypassing Rockstar account/DRM requirements.
- Modifying original story missions/quest flags, mission-critical pedestrians, police dispatch internals, or Rockstar's existing cutscene dialogue.
- Guaranteed realistic 3D positional speech, matching lips to arbitrary synthesized phonemes, or cloning original actors' voices.
- Thousands of simultaneously reasoning LLM agents; global off-screen economic/city simulation belongs in Phase 2+.
- General execution of LLM-written scripts, arbitrary GTA natives, shell commands, or unrestricted engine console commands.
- Commercial distribution without separate platform/IP/license review.
- Supporting GTA V on macOS (natively or through a compatibility layer) as a player configuration. CrossOver is at most an optional developer convenience (§2.4).
- NPC-to-NPC conversation, group scenes, NPCs joining the player's conversation, and social initiatives between NPCs. These are the first post-v1 milestone (§19).

## 1.3 Assumptions to verify

- Team has at least **one Windows 10/11 x64 PC with a GPU** that runs GTA V Legacy at a stable frame rate (the **test rig**), reachable from developer Macs over the local network. Developers use Apple Silicon Macs; Intel Macs can build and run the service but are not the reference.
- GTA V Legacy can be licensed and run in Story Mode on the rig; current launcher, game build, and installed dependencies are pinned in `compatibility.yaml`.
- SHVDN on Windows with `.NET Framework 4.8` is the standard, documented configuration [S02]. Versions are still pinned, because a Rockstar game update can break Script Hook V until a matching release ships [S01].
- A `net48` class library built on macOS against reference assemblies loads unmodified on the rig [S06]; verified in G0.
- The game mod can exchange HTTP messages with the service over Windows loopback. In dev-LAN mode (§6.1), rig-to-Mac traffic passes both machines' firewalls; both are explicitly tested.
- The service's Python dependencies (STT, TTS, audio I/O, SQLite with FTS5) have working Windows builds; verified in G0 and on Windows CI.
- The game must stay usable when every external model provider or the Python service is disconnected.

# 2. Platform decision and technical feasibility {#sec02}

## 2.1 Selected implementation stack

| Layer | Candidate | Why selected | Confidence |
|---|---|---|---|
| Game | GTA V **Legacy** Story Mode on Windows 10/11 x64 | Most established Script Hook V + SHVDN path; the platform those tools target | High; pin the build |
| Loader | Script Hook V + ASI Loader | Accesses game native functions; explicitly not for GTA Online [S01] | High on Windows |
| Gameplay adapter | ScriptHookVDotNet API v3 (C# targeting .NET Framework 4.8) | Public source, NPC and task APIs, quick testing; **builds on macOS**, runs on the rig [S02][S06] | High; verify Mac-built DLL in G0 |
| AI/orchestration | Python 3.11/3.12, FastAPI, asyncio | Fast iteration, portable provider adapters; runs on Windows for players and natively on macOS for development | High |
| Transport | Versioned JSON over loopback HTTP; command long-poll | Simple to debug; same-machine loopback in release, explicit LAN address only in dev-LAN mode | High |
| Storage | SQLite WAL + FTS5; optional vector index | Local, inspectable, migratable; good enough for v1 scale | High |
| Speech-to-text | Service-side microphone capture + whisper.cpp | Local and cross-platform: CUDA/Vulkan/CPU on Windows, Metal on the Mac for development [S08] | High |
| Speech synthesis | Service-side playback with pluggable local/hosted TTS; Windows-capable local candidates (Kokoro, Piper) to benchmark | Keeps audio decoupled from the game [S13][S14] | Medium; voice quality, latency, license, and GPU contention to benchmark |
| LLM | Any vetted OpenAI-compatible remote endpoint; optional local model | Swappable model for dialogue/action/summary | High as abstraction |
| UI | Simple in-game subtitles/target key + localhost web dashboard | Avoids complicated in-game browser integration initially | High |

**Edition policy.** Do not attempt simultaneous Legacy and Enhanced support for v1.0. A separate SHVDN Enhanced fork exists [S10], but introduces another compatibility and QA matrix. Prototype against Legacy, then assess Enhanced as a post-alpha port. Script Hook V's supported GTA builds change over time; **there is no hardcoded “latest compatible” version in this specification** [S01].

## 2.2 Development and runtime split

The **Mac is the authoring and build machine**; the **Windows rig is where GTA runs**. Most work never needs the rig: the service runs natively on macOS against `IGameAdapter` fakes and replayed traces (§6.1, FR-OPS-04).

- **Game runtimes (rig):** install the version-matched Script Hook V and ASI loader, and SHVDN with all binaries from **one release**. Windows 10/11 normally include .NET Framework 4.8; confirm it, and install the Visual C++ redistributable SHVDN requires [S01][S02].
- **Build on Mac:** use the macOS .NET SDK and `Microsoft.NETFramework.ReferenceAssemblies` to compile a `net48` class library against the **stable SHVDN v3 API**, even when the installed GTA build needs a compatible nightly runtime [S06]. Unit-test pure C# classes under modern .NET on the Mac.
- **Deploy loop:** `tools/deploy_adapter.py` copies the built DLL and PDB from the Mac to the rig's `scripts/` folder (SMB share, or SSH via Windows OpenSSH Server); the developer then reloads scripts from the SHVDN console. G0 verifies whether a loaded DLL can be overwritten in place; if it is locked, deploy to versioned file names.
- **Debugging:** structured logs and trace bundles first. To step through adapter code, attach a debugger on the rig (e.g., Visual Studio over remote desktop). Remote debugging from the Mac is optional and must be proven before anyone relies on it.
- **Cross-platform Python:** `pathlib` and per-platform app-data directories, no shell-outs to OS-specific tools, a cross-platform audio I/O library, a lockfile resolved for both Windows and macOS, and the backend test suite running on Windows CI as well as macOS. Confirm the bundled `sqlite3` has FTS5 on both platforms.
- **Mac-only tools are dev conveniences only.** Apple-only providers such as MLX-Audio [S09] may be used for prototyping behind the same interfaces, never as the shipped default.
- **Hardware (rig):** a GPU with headroom beyond GTA's own needs. Local STT/TTS/LLM compete with GTA for GPU and VRAM on the same PC; a hosted dialogue LLM is the initial default.
- **Audio and microphone:** the service captures the mic and plays speech on whichever machine it runs on. On Windows, desktop-app microphone access must be enabled in privacy settings; on a Mac in dev mode, macOS grants mic permission to the terminal or IDE that launched the service. Start with system playback and subtitles; in-engine spatial playback is a **research spike**, not a committed v1 feature.
- **Repository hygiene:** UTF-8 everywhere; `.gitattributes` normalizes line endings so Mac and Windows checkouts stay identical.

## 2.3 Mandatory G0 toolchain spike (before feature development)

Perform the following on the **Windows test rig** and a **developer Mac**, recording logs and a screen capture:

1. **Rig:** install Steam/Rockstar launcher and a legally purchased GTA V **Legacy** instance. Launch Story Mode without mods; capture a 5-minute FPS/frame-time baseline. Record Windows version, CPU/GPU, driver, storefront, and game build in `compatibility.yaml`.
2. **Rig:** install the correct Script Hook V distribution, `.asi` loader, SHVDN matching the GTA build, .NET Framework 4.8, and Visual C++ runtime; keep all SHVDN binaries from **one release** [S01][S02]. Confirm the launch path into Story Mode works with Script Hook V loaded; never connect to GTA Online with mods installed.
3. **Mac → rig:** build a minimal `net48` adapter on the Mac, deploy it with `tools/deploy_adapter.py`, open the SHVDN console (`F4`) and run `Reload()`. Verify the script targets an NPC and requests a harmless task, then redeploy a change and confirm the new code runs after reload.
4. **Bridge:** with the service on the rig, confirm the in-game script reaches `http://127.0.0.1:8765/health`. Then test dev-LAN mode: the service on the Mac, bound to an explicit private address with a session token, is reachable from the rig and refuses sources that are not on its allowlist.
5. **Rig:** test typed interaction; push-to-talk start/stop from a GTA keybind; Windows microphone permission; TTS playback; voice and subtitle timestamps; quit/relaunch persistence; the full in-game date/time is readable and restored when an earlier save is loaded (§8.3).
6. **Rig:** run 20 interaction cycles and a 20-minute soak; measure unmodded versus modded frame-time, memory growth, and crashes.
7. **Second Mac:** reproduce step 3 using only the written Mac setup guide.

**Pass criteria:** steps 1–6 pass on the rig and step 7 passes on a second Mac; zero bridge-caused crashes or deadlocks during step 6; recovery from service restart without GTA restart; measured overhead within alpha budget (§12). **If blocked:** when a Rockstar update leaves no Script Hook V build compatible with the installed game, pause in-game work, continue backend work against the fake adapter and replayed traces, and resume when a compatible release ships. Never use leaked source or binary patches to get around it. If the team has no rig yet, G0 may run on a cloud Windows GPU machine or under CrossOver (§2.4), but results are labeled **provisional** until repeated on a rig.

## 2.4 Optional: CrossOver on a Mac (unsupported convenience)

A SHVDN maintainer reported running SHVDN with a Steam copy of GTA V under CrossOver on macOS, using a Wine `dinput8` DLL override [S03][S05]. Developers may use this for quick in-game checks without the rig. It is **not** a gate, not in the support matrix, and not a player configuration: bugs that reproduce only under CrossOver are not release blockers, and frame-time or latency measured there does not count toward §12. CrossOver 27 is announced as Apple Silicon-only [S04].

# 3. SkyrimNet parity contract {#sec03}

Baseline descriptions are informed by the public SkyrimNet feature documentation [S07]. Parity is **feature-category parity**, not identical code, scale, integration technique, or plug-in format.

| Capability | SkyrimNet reference | GTA V v1.0 deliverable | Later |
|---|---|---|---|
| Context-aware dialogue | NPCs speak based on world/player context | Targeted freeform text/voice with grounded context | Richer sensor coverage |
| Streaming speech | Dialogue/TTS starts before full reply | Stream text; synthesize/play sentence chunks | Full-duplex interruption |
| NPC biographies | Prewritten identities + dynamic bios | 12–20 curated characters + generated ambient archetypes | Neighborhood-scale cast |
| Private episodic memory | Semantic recall, salience, decay, diaries | SQLite memory + embedding retrieval + summaries | Social rumor propagation |
| Physical perception | Hearing/visibility and whisper mode | Radius, line-of-sight where reliable, occlusion heuristics, whisper | More robust interior/audio map |
| World knowledge | Scoped lore and conditions | Scoped location/group knowledge cards | Simulated factions/economy |
| NPC actions | Extensible game actions | Validated allowlisted native tasks | Vehicles/trading/group tasks |
| Triggers | Configurable event reactions | YAML triggers and cooldowns | Trigger composer / scripting SDK |
| Scenes | NPCs can converse with each other | **Deferred:** v1 is the player and one NPC per turn | NPC-to-NPC scenes first after v1 (§19), then group agendas |
| Voice options | Multiple local/hosted STT/TTS | One local STT, one TTS provider + provider interfaces | More voices/effects |
| Editor/dashboard | Web UI for profiles, memory, logs, rules | Compact local control panel | Plugin marketplace / MCP |
| Vision and spatial voice | Screenshots and 3D audio | **Not required for v1** | Optional prototypes |

**Definition of parity complete:** all items in the GTA V v1 column operate together in a recorded demo and are covered by repeatable tests; unavailable game-native capabilities are disclosed, not simulated in a misleading way.

# 4. Players, scenarios, and experience design {#sec04}

## 4.1 Primary scenarios

**S1 — Meet a recurring character.** The player approaches a barista/parking attendant/neighbor in the pilot district, holds a targeting key, and asks about the area. The character speaks in a consistent voice, with a consistent occupation and attitude. After leaving and restarting, the character recalls a prior meeting when it is relevant.

**S2 — React to a witnessed event.** A character close enough to observe a nearby collision comments on the event. Someone behind a wall does **not** incorrectly report having seen it; at most they may have heard something if hearing is plausible.

**S3 — Request a simple action.** The player asks “Can you follow me?” The model selects `follow_player` as a proposal. The policy layer checks valid target, distance, whether an emergency/mission blocks action, and cooldown. The game attempts the task; only after a positive acknowledgment may dialogue claim success.

**S4 — Bystander awareness.** The player talks to one curated NPC while another stands nearby. Only the addressed NPC replies. A bystander within hearing range later remembers what it overheard, tagged `heard`; one out of range does not.

**S5 — Failure gracefully.** Model/voice provider is offline. The game remains responsive, an unobtrusive status appears, and the player can continue normal GTA play. Reconnect and retry do not produce duplicate actions.

## 4.2 Input and interface spec

- Default interaction: look at a valid nearby candidate, use configurable **Interact** key to select, then **hold Push-to-Talk**; release sends audio. Typed text is available through a simple input mode for debugging and accessibility.
- Selection shows a small NPC label and state: **Available**, **Busy**, **Out of range**, or **Not available**. No hidden multi-key shortcut is required for core play.
- Every NPC line has subtitle text, speaker identity, and optional voice; the latest few turns can be reopened in a conversation history panel.
- Mic status must be explicit: **Not recording**, **Recording**, **Transcribing**, **Thinking**, **Speaking**. Mic is off by default outside PTT.
- **Escape/cancel** interrupts playback and queued dialogue, clears interaction targeting, and safely cancels not-yet-dispatched actions.
- The dashboard includes model/provider selection, microphone test, voice test, nearby-NPC inspector, memory editor, trigger viewer, trace viewer, and “reset this NPC” / “reset all AI data.”
- Language support starts with **English**. Configuration and data structures are Unicode-ready; multilingual STT/TTS is later.

# 5. Functional requirements and acceptance criteria {#sec05}

Requirement IDs are stable and should become issue or test IDs. Priority: **P0** required for parity alpha; **P1** immediately after; **P2** research/later.

## 5.1 Identity and world attachment

- **FR-ID-01 (P0):** distinguish `curated_persistent`, `spawned_persistent`, and `ambient_ephemeral` actors (plus `anchored_service` at P1, §8.1); each has stable internal UUID, runtime handle, model/archetype, display name, and identity-confidence field.
- **FR-ID-02 (P0):** curated persistent actors use scripted location/time spawn rules and get reattached/recreated after load. An NPC's GTA runtime handle is **never** used as a long-lived database primary key.
- **FR-ID-03 (P0):** ephemeral pedestrian memory lasts only for that gameplay session unless the user explicitly “adopts”/pins it; pinning creates a new durable identity, not a promise the original random engine entity will survive.
- **FR-ID-04 (P0):** characters de-spawn or disengage when player moves away, missions override world state, or engine removes entity. Pending actions must fail safely.
- **FR-ID-05 (P1):** a curated character can preserve virtual location/routine while not physically spawned; spawn reconciliation must prevent duplicates.
- **FR-ID-06 (P0):** the player is modeled as three protagonist actors (§8.1); memories and relationships never transfer between them.

**Acceptance:** relaunch a save three times with a curated character and recover the same biography/memories; an ambient NPC does not accidentally inherit a different person's memories after handles are recycled.

## 5.2 Dialogue and scene control

- **FR-DLG-01 (P0):** PTT and typed text invoke the same conversation pipeline. Target actor and current scene are locked per turn and revalidated before speech/action.
- **FR-DLG-02 (P0):** each turn runs in two stages. The **dialogue stage** streams the selected speaker's `utterance` as plain text (an optional leading emotion tag is stripped before subtitles/TTS) and is never schema-validated mid-stream. The **action stage** runs once the utterance is complete, as a separate, cheaper call that sees the exchange plus only the currently eligible actions, and returns at most one structured **action proposal** or none; invalid output is repaired once, then dropped.
- **FR-DLG-03 (P0):** sentence-level streaming to TTS and subtitles; subtitles must not claim an action completed before a corresponding game acknowledgment.
- **FR-DLG-04 (P0):** interrupt/cancel, cooldowns, speech queue arbitration, and speaker priorities prevent overlap.
- **FR-DLG-05 (P2):** NPC-to-NPC group scenes (2–3 actors, turn cap, each with its own knowledge and memory) are deferred to the first post-v1 milestone (§19). In v1, only the player's locked target replies in a turn.
- **FR-DLG-06 (P1):** player can address a named NPC through direct-target syntax in text mode; proximity rules still apply. Addressing a group arrives with group scenes (§19).

**Acceptance:** a targeted reply and an unsolicited remark from another NPC never play over each other; a canceled response produces no late action after cancellation; an invalid action-stage response leaves the spoken line untouched and produces no action.

## 5.3 Perception and ground truth

- **FR-PER-01 (P0):** collect game time, approximate location, weather if available, distance, orientation, on-screen/nearby status, player vehicle/weapon/combat indicators, actor health/alive/mission/occupied status where accessible.
- **FR-PER-02 (P0):** use validated line-of-sight natives when available; otherwise conservative heuristics and **unknown** rather than fabricated visible events.
- **FR-PER-03 (P0):** event provenance marked `directly_observed`, `heard`, `reported`, `system_fact`, or `unverified`; hearsay is never presented as direct eyewitness knowledge.
- **FR-PER-04 (P0):** cap hearing radius for normal speech, reduce for whisper, and require same area/compatible interior context when known. The exact distance is a configurable playtest parameter, not assumed engine behavior.
- **FR-PER-05 (P1):** local change detector infers credible incidents from multiple signals with a confidence score. Avoid claiming the game exposes universal event callbacks; some signals need polling/heuristics.

**Acceptance:** 20 occlusion/visibility fixtures; characters cannot assert an unseen event as witnessed. Report false positives separately from missed events.

## 5.4 Memory, relationships, and world knowledge

- **FR-MEM-01 (P0):** maintain canonical event log, NPC first-person episodic memories, and shared scoped knowledge as different stores.
- **FR-MEM-02 (P0):** semantic lookup plus actor and time filters. FTS-only fallback works when local embedding model unavailable.
- **FR-MEM-03 (P0):** memory has `importance` (0–1), confidence, time, source event IDs, visibility scope, and optional TTL/decay policy.
- **FR-MEM-04 (P0):** important memories persist across service/game restart. Memory selection respects relevance + importance + recency and bounded prompt tokens.
- **FR-MEM-05 (P0):** deterministic relationship updates for familiarity, trust, affinity, fear, and respect, kept per NPC and per protagonist, based on grounded events (the agent design's comfort and irritation map onto affinity); freeform model judgments are suggestions, not authority.
- **FR-MEM-06 (P0):** CRUD/delete/export via dashboard, per-actor reset, whole-profile deletion, and backups/migrations.
- **FR-MEM-07 (P1):** background diary/summaries condense events without replacing original provenance; avoid writing duplicate inferred facts on each revisit.
- **FR-MEM-08 (P0):** memories, relationships, and knowledge follow the save timeline: after loading an earlier save, NPCs don't remember events from later on that timeline (§8.3).

**Acceptance:** fixed-memory regression set of 50 questions, including misinformation bait; recovery after 10 simulated days; delete means subsequent retrieval does not surface deleted memory.

## 5.5 NPC action system

- **FR-ACT-01 (P0):** action registry lists id, description, JSON schema, preconditions, cooldown, risk tier, executor, and rollback/stop policy.
- **FR-ACT-02 (P0):** models may propose only registered action IDs. Every action is validated on both Python policy and game-side adapter **at dispatch time**, including the entity-ownership classification in §11.4 (a mission-entity flag alone neither blocks the mod's own curated peds nor clears anyone else's).
- **FR-ACT-03 (P0):** every action attempt has correlation/idempotency key, queue state, result, and recorded reason on failure.
- **FR-ACT-04 (P0):** only one conflicting locomotion task per actor at a time. Higher-priority emergency/engine behavior overrides AI instructions.
- **FR-ACT-05 (P0):** initial allowlist: `face_player`, `follow_player`, `stop_following`, `go_to_safe_point`, `wait_here`, `wander_nearby`, `flee_from_threat`, `play_verified_gesture` (gesture only if tested).
- **FR-ACT-06 (P1):** `enter_nearby_vehicle`, `exit_vehicle`, `approach_actor`, `call_for_help` only after game-native proof and anti-softlock tests.
- **FR-ACT-07 (P2):** trading, transferring money/items, driving, combat behavior, criminal or law-enforcement escalation, quest integration, and long-range missions require individual feature gates and deeper testing.

**Acceptance:** 100 generated candidate actions, including malicious or invalid requests, cannot execute outside the allowlist or on a mission-critical/invalid entity. Mod-owned curated actors remain eligible even though they carry the persistent/mission-entity flag.

## 5.6 Triggers and unsolicited remarks

- **FR-TRG-01 (P0):** YAML-defined trigger: event match, optional condition, scope, priority, cooldown, probability, and effect type (speak to the player, thought/memory, knowledge write, suggested action; scene requests arrive with NPC-to-NPC scenes, §19).
- **FR-TRG-02 (P2):** continuous NPC-to-NPC scenes are deferred with FR-DLG-05 (§19). When built, they must respect the participant cap, be interruptible, and stop when the player leaves the area or danger starts.
- **FR-TRG-03 (P0):** spontaneous remark budget: default max one unsolicited local remark to the player in a configurable interval; per-actor and per-area cooldowns.
- **FR-TRG-04 (P2):** rumor spread between NPCs is deferred to §19; when built, it travels only through actual NPC-to-NPC interactions, tagged `reported` with the teller recorded.

## 5.7 Reliability and administration

- **FR-OPS-01 (P0):** when AI service is offline, game adapter gives one non-blocking status notice and disables AI actions while retaining regular GTA play.
- **FR-OPS-02 (P0):** configuration has strict schema validation, explicit defaults, version migration, and **no API keys written to game logs**.
- **FR-OPS-03 (P0):** dashboard shows actors, sessions, queued actions, last model output, perception snapshot, retrieved memories, cost/latency, crashes, and compatibility versions.
- **FR-OPS-04 (P0):** deterministic local replay of captured event traces without GTA for backend tests.
- **FR-OPS-05 (P1):** export/import character packs and rule packs with namespaced identifiers; model prompts and actions versioned in source control.

# 6. System architecture {#sec06}

## 6.1 Components and process boundary

```text
  WINDOWS GAMING PC  (player's PC in release; test rig in development)
+----------------------------------+      +--------------------------------------+
| GTA V Legacy / Story Mode        |      | Living NPC Service (Python)          |
|  Script Hook V + SHVDN v3        |      |  - HTTP bridge + session registry    |
|  LivingNPC.GameAdapter.dll       |      |  - dialogue / scene orchestrator     |
|  [Game Tick]                     | HTTP |  - perception/knowledge assembler    |
|    observe / candidate targeting |<---->|  - memory / relationships / SQLite   |
|    action queue / execute native |      |  - LLM + TTS/STT provider adapters   |
|    subtitles / UI / key events   |      |  - mic capture + audio output        |
+----------------------------------+      |  - local admin dashboard             |
                                          +--------------------------------------+
       HTTP over 127.0.0.1. No network, DB, or AI calls block the GTA script tick.

  DEVELOPER MAC  (development only; never required at runtime)
+--------------------------------------------------------------------------------+
|  - builds LivingNPC.GameAdapter.dll (net48) and deploys it to the rig          |
|  - runs the same service against FakeGameAdapter / replayed traces             |
|  - dev-LAN mode: hosts the service for a game running on the rig               |
+--------------------------------------------------------------------------------+
```

**Game adapter (C#, in GTA):** `GameStateSampler`, `EntityResolver`, `TargetSelector`, `InputController`, `ActionExecutor`, `SubtitlePresenter`, `BridgeClient`, `SafetyGovernor`, `TelemetryQueue`. Expose a small internal interface `IGameAdapter` so fake game states can test most orchestration without launching GTA.

**Service (Python; on the gaming PC, and natively on macOS for development):** `BridgeAPI`, `SessionManager`, `ActorRegistry`, `PerceptionEngine`, `ConversationCoordinator`, `ModelGateway`, `SpeechPipeline`, `MemoryRepository`, `KnowledgeService`, `ActionPolicy`, `TriggerEngine`, `Dashboard`, `TraceRecorder`.

**State ownership:** GTA owns live transforms/tasks, Python owns durable identity, memories, relationships, model transactions, and user preferences. Neither pretends to authoritatively own the other's state.

**Deployment topologies.** One codebase, four ways to run it:

| Topology | GTA runs on | Service runs on | Mic and speakers | Used for |
|---|---|---|---|---|
| Release | Player's Windows PC | Same PC, `127.0.0.1` | Player's PC | Players; G1+ acceptance |
| Integrated dev | Windows test rig | Same rig, `127.0.0.1` | Rig | In-game testing, performance, voice |
| Split dev (dev-LAN) | Windows test rig | Developer Mac, explicit private address | Mac | Fast backend iteration against a live game; **not** valid for latency or performance acceptance |
| Backend-only | Nowhere (`FakeGameAdapter` / replayed traces) | Developer Mac or CI | Mac / none | Most backend work; automated tests |

Dev-LAN mode is off by default, refused by release builds, requires the session token, and accepts only allowlisted source addresses (§7, §18). The game adapter reads the service URL from its own INI file; the default is `http://127.0.0.1:8765`.

## 6.2 Threading and lifecycle requirements

- The GTA **script tick** reads a bounded sensor sample, drains a bounded verified command queue, and renders local UI; it does not perform HTTP requests, audio inference, disk writes, or model calls.
- A dedicated C# worker handles outbound/inbound JSON over HTTP. Worker queues are thread-safe. Game natives run only in the appropriate SHVDN/script execution context, never from arbitrary HTTP callbacks [S11].
- All commands carry `session_id`, `actor_id`, `runtime_generation`, `expires_at`, and `idempotency_key`. A reloaded actor invalidates commands for a prior generation.
- Session generation changes at game load or SHVDN reload. Old, in-flight model replies become **non-executable**; text may be shown only if user still in same explicitly resumed scene.
- A small bounded queue drops low-value sensor snapshots before high-value control messages when overloaded; critical action acknowledgment is never silently discarded.
- All long-running effects have stop conditions and release control when actor is dead, removed, busy, far away, in a mission, or player cancels.

# 7. Network/API contracts {#sec07}

**Protocol:** JSON, UTF-8, `schema_version: 1`, loopback only (dev-LAN mode, §6.1, is the sole exception and is never enabled in release builds); canonical request timestamps UTC ISO-8601; GTA in-world time separate. Use typed Pydantic models and equivalent C# DTOs. Maximum body size 256 KiB for events/commands; bulk audio uses separate internal transport and is **not** embedded in event JSON.

## 7.1 Endpoints (game adapter → service)

| Method + path | Intent | Notes |
|---|---|---|
| `GET /health` | Availability, version, provider readiness | 100 ms target; no secrets |
| `POST /v1/bridge/sessions` | Open game session, manifest + capabilities | Returns `session_id`, token, policy version |
| `POST /v1/bridge/events:batch` | Player/NPC observations and interaction events | Ordered per session, deduplicated event IDs |
| `POST /v1/bridge/control` | Latency-critical control events: PTT start/stop, cancel | Sent immediately on a priority lane, never batched; keeps `seq` ordering; deduplicated by event ID |
| `GET /v1/bridge/commands?after=<seq>` | Long-poll for commands | Request on worker, 5 s max poll |
| `POST /v1/bridge/commands/{id}/ack` | Started/completed/rejected/expired status | Required for claim-of-success |
| `POST /v1/bridge/heartbeat` | Health, FPS/queue/actor count | Every 2–5 s; avoid tick blocking |
| `POST /v1/bridge/sessions/{id}/close` | Flush/cancel on game exit | Best effort; tolerate abrupt death |

For PTT, the in-game key sends `voice.ptt_started`/`voice.ptt_stopped` (and the cancel key sends `scene.cancel`) through `/control`, so the microphone opens without waiting for the next event batch. The service controls the microphone on the machine it runs on, and the in-game status shows **Recording** only once the service confirms the mic is open, so players know when to start speaking. For audio playback, the **service** owns the output queue, while the game receives synchronized subtitle/speaking-state commands.

## 7.2 Standard event envelope

```json
{
  "schema_version": 1,
  "event_id": "evt_01JEXAMPLE",
  "session_id": "ses_8d2a",
  "seq": 184,
  "kind": "player.utterance_text",
  "occurred_at_utc": "2026-10-10T16:13:00Z",
  "game_datetime": "2013-09-17T11:28:48",
  "actor_id": "npc_2d9e",
  "runtime_generation": 7,
  "payload": {
    "text": "Can you follow me?",
    "input_mode": "typed",
    "observed_by": ["npc_2d9e"]
  }
}
```

The timestamps above are **illustrative**, not a real event; `game_datetime` is the full in-game date and time, used for save-timeline handling (§8.3). Every event type must have its own schema; unknown fields are rejected or ignored according to the protocol version, never executed as instructions.

## 7.3 Action proposal → authorization → execution → acknowledgment

```json
{
  "proposal_id": "ap_001",
  "actor_id": "npc_2d9e",
  "action_id": "follow_player",
  "arguments": {"max_follow_meters": 15},
  "reason_for_player": "Agreed to accompany player",
  "conversation_turn_id": "turn_094"
}
```

Python `ActionPolicy` checks schema, permissions, scene context, relation, action budget, cooldown, and whether this proposal is a duplicate. If valid, the command includes an **ephemeral, bounded authorization**. The game adapter separately rechecks existence, proximity, mission state, cooldown and real task availability before calling any GTA native. The adapter posts `started`, then `completed`/`rejected`/`expired` with reason. A `started` result does not mean the NPC reached the destination; completion requires an explicit condition when observable.

**Never:** accept freeform C# code, arbitrary native hash strings, file paths, operating-system commands, SQL, or network URLs from the model as game actions. All model text is untrusted even if generated by a reputable provider.

## 7.4 Compatibility and protocol negotiation

`POST /sessions` includes `game_edition`, `gta_build`, `scripthookv_build`, `shvdn_build`, `adapter_version`, `game_os_version`, `service_os`, `service_host_mode` (`local` or `dev_lan`), `compat_layer` (normally `none`; `crossover` for §2.4), `arch`, `capability_flags`. Service rejects major schema mismatch with actionable error; minor optional fields are feature-negotiated. Record manifest snapshots for reproducibility.

# 8. NPC identity, world state, and persistence {#sec08}

## 8.1 Kinds of NPC identity

1. **Curated:** authored NPC UUID, archetype, voice, spawn rules, biography, optional fixed neighborhood, social links. This is the first 12–20-person vertical slice.
2. **Spawned persistent:** a system-created NPC with stable UUID and spawn/reconciliation rules. Its engine entity can disappear and return without changing the character's memory.
3. **Ambient ephemeral:** unmodified GTA pedestrian recognized for one session and one actor generation; no automatic cross-save identity matching.
4. **Anchored service role (P1):** an NPC that a game script creates for a service (shop clerk, barber, mechanic), often re-created on each visit. Identity attaches to the role and place (for example, the clerk at one specific store), not to the entity. The mod never takes ownership of it (§11.4); interaction is conversation and expression only, around the native service.

**The player is three people.** Michael, Franklin, and Trevor are separate actor IDs. Relationships and memories are kept per protagonist: an NPC who met Franklin treats Michael as a stranger, and nothing one protagonist experienced privately reaches an NPC's knowledge of another. The active protagonist is part of every event and scene snapshot.

**Never use** runtime `Ped.Handle`, game model hash, clothing, or coordinates as durable identity. A `(session_id, handle, generation)` tuple can identify a *currently live* game entity only. Actor identity mapping must have confidence and be rechecked when entering action execution.

## 8.2 Curated cast: initial content contract

Define 12–20 original characters in one pilot region (e.g., a Los Santos commercial block, not a promise of canonical lore). Each profile has: `id`, display name, occupation, district, short/long bio, speech style, motivations, taboo topics, starting trust/fear/affinity, relationships, stable voice choice, spawn windows, allowed actions, and flags such as `protected_from_conflict` or `no_crime_reactions`. Do not copy copyrighted dialogue into bios.

**Minimum distribution:** at least 3 distinct occupations; 3 personalities with conflicting views; 2 pre-existing relationships; 2 characters who should know about a local event and 1 who should not. This tests differences in knowledge rather than just differences in generated wording.

## 8.3 Save semantics

- Store service data in a **profile ID** explicitly associated with a GTA Story Mode save/character slot chosen by the user. Where stable engine save GUID isn't available, require manual profile selection on first load.
- Game save never contains the full model database. The service DB persists separately with periodic snapshots and crash-safe SQLite WAL transactions.
- **No memories from the future.** Every durable record stores the in-game date/time and a timeline branch ID. When a session starts at an in-game time earlier than the latest record on the active branch (the player loaded an older save), open a new branch from that point; records after it stay on the old branch and are not retrieved. G0 verifies that the full in-game date is readable and restored by loading a save. Recognizing which branch a later reload belongs to is an open decision (§21).
- On session reconnect, reconcile `curated` actors by scripted spawn rule and known tags; never spawn duplicates of existing characters.
- If a mission, wanted state, or story transition makes the pilot district unstable, delay reconciliation and fall back to non-intrusive UI status.
- If the user changes GTA save slots, warn before attaching memories to a potentially different story state.

# 9. Data model and retrieval {#sec09}

## 9.1 Logical entities

| Entity | Required fields / responsibility |
|---|---|
| `profiles` | local user profile ID, created UTC, settings, game-save association |
| `actors` | stable UUID, actor class, archetype, persona JSON, created UTC, voice config |
| `actor_instances` | session, runtime handle, generation, status, last transform; **ephemeral** |
| `game_sessions` | edition/build/version manifest, start/end, mapped profile, lifecycle |
| `events` | immutable fact/event envelope, confidence, provenance, dedupe ID |
| `memories` | actor, first-person text, summary, source IDs, salience, confidence, expiry |
| `relationships` | from/to actor (each protagonist is its own actor), familiarity/trust/affinity/fear/respect, update reason |
| `knowledge_cards` | fact text, owner/scope/group/region, source/provenance, active dates |
| `conversation_state` | per-scene current topic, pending question, last speaker; short-lived |
| `intentions` | short-lived plans with an expiry; a proposed plan is never recorded as something that happened |
| `canonical_context` | read-only story state and what a character legitimately knows; never written by generated dialogue |
| `dialogue_turns` | scene ID, speaker, input, generated utterance, provider, latency, token count |
| `action_attempts` | proposal, authorization, executor, status, failure reason, timestamps |
| `voice_assets` | model/voice ID, license class, source, approved use; optional cached audio |

## 9.2 Example schema (illustrative SQLite, not complete migration)

```sql
CREATE TABLE actors (
  actor_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  actor_kind TEXT NOT NULL CHECK (
    actor_kind IN ('curated_persistent','spawned_persistent','ambient_ephemeral','anchored_service')),
  display_name TEXT NOT NULL,
  persona_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE memories (
  memory_id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL REFERENCES actors(actor_id),
  text_first_person TEXT NOT NULL,
  source_event_ids_json TEXT NOT NULL,
  provenance TEXT NOT NULL,
  importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  created_at TEXT NOT NULL,
  game_datetime TEXT NOT NULL,
  timeline_branch TEXT NOT NULL,
  expires_at TEXT,
  deleted_at TEXT
);
CREATE INDEX idx_mem_actor_recent ON memories(actor_id, created_at DESC);
CREATE INDEX idx_mem_active ON memories(actor_id, deleted_at, expires_at);
```

**Vector store decision:** start with SQLite FTS5 + an embedding table/index behind `MemoryIndex` interface. Choose an embedded vector extension only after license, Windows and macOS wheel/build, and recall performance benchmarks. This avoids turning a vector database install into a launch blocker on either platform. Use `memory_id` to join lexical/semantic retrieval; no duplicate copies of private facts in untracked caches.

## 9.3 Memory write and retrieval policy

- **Write:** after a completed dialogue turn or grounded event, generate at most a few candidate memories; deduplicate similar facts, attach event IDs, and assign importance and provenance. “I overheard a rumor” stays reported, not witnessed.
- **Retrieve:** filter by actor + active profile + visibility scope; select lexical/semantic hits plus important recent events; rerank using relevance, recency, importance, confidence, and diversity. Cap total memory tokens (configurable).
- **Decay:** deprioritize low-importance memories gradually; do not delete critical character or relationship facts on a schedule without policy. Physical deletion is user-controlled or retention-policy-driven.
- **Summarize:** optional diaries every meaningful session/day at idle; summaries cite event IDs, and corrections propagate in retrieval.
- **Forget:** actor memory deletion removes primary records, vectors, summary references, and cached snippets; append a scrub-audit record without retaining deleted content.
- **Truth hierarchy:** verified game state > canonical user-authored profile > sourced memory > hearsay > model speculation. When the source is uncertain, NPC language must reflect uncertainty.

# 10. AI and speech orchestration {#sec10}

## 10.1 Turn pipeline

1. User selects NPC; game publishes an immutable local scene snapshot and PTT state.
2. The service captures the microphone only between PTT start/stop; speech recognition returns transcript plus confidence. Text input bypasses STT.
3. Service validates speaker, profile, and scene. Build **actor-specific** perception and retrieved memory context; the speaker doesn't receive global omniscience.
4. The reply comes from the player's locked target; v1 has no multi-NPC speaker selection. Precompute action eligibility while STT completes.
5. **Dialogue stage:** the dialogue model receives system policy, actor bio, world facts with provenance, relevant memories, recent transcript, the names and one-line descriptions of currently eligible actions (so the NPC doesn't agree to something impossible), and budgets. It streams plain text.
6. Pass complete safe sentence fragments to TTS and subtitles as they arrive, and associate produced audio with the speaker. Dialogue text never triggers actions by itself.
7. **Action stage:** once the utterance is complete (while it is still being spoken), a separate, cheaper call receives the exchange and the eligible action schemas and returns at most one structured proposal or `none`. Validate it (one bounded repair, otherwise drop), run Python policy, enqueue the validated game command, and track its acknowledgment asynchronously. A rejected or expired action is fed into the next turn as a system fact so the NPC can acknowledge it in character.
8. **Persist without waiting for the action.** As soon as the utterance finishes, write the dialogue turn, its events, and candidate memories, recording only what was actually delivered if the line was interrupted. When the action's acknowledgment arrives or it expires, append the outcome as a separate event linked by `conversation_turn_id`, then write any relationship updates and memories that depend on it. Track latency and provider usage throughout.

## 10.2 Model interfaces

- `DialogueModel`: streams plain-text dialogue; OpenAI-compatible API initially. `ActionSelector`: a separate, cheaper structured-output call for the action stage (§10.1 step 7). A configurable cheap model can also do intent classification (and speaker selection once group scenes arrive, §19).
- `EmbeddingProvider`: local embedding model preferred, or lexical fallback. Keep embedding data in local storage unless user explicitly enables remote embeddings.
- `STTProvider`: `whisper.cpp` baseline (CUDA, Vulkan, or CPU on Windows; Metal on the Mac for development); optional cloud fallback. The service captures the microphone [S08].
- `TTSProvider`: first implement one natural, licensed voice that runs on Windows, plus a second fallback. Benchmark Kokoro (Apache-2.0 weights) [S13], Piper (GPL-3.0; license review before bundling) [S14], and at least one hosted provider on the rig while GTA is running. `mlx-audio` [S09] is allowed only as a Mac development provider. Stream/segment speech where supported, cache short stable assets by `(voice, model, text hash)`.
- `SummaryModel`: inexpensive background model; hard cap calls per actor/time to control cost.
- `Safety/Policy`: deterministic code, not a general-purpose model judge; tests independent of provider.

## 10.3 Prompt assembly rules

**Inject:** persona, speaking style, current location, accurate world state, confidence-provenanced events, a bounded selection of memories, relationships, scene participants, behavior constraints, and allowed actions.

**Do not inject:** unbounded raw event logs, undisclosed real-world player data, credentials, arbitrary browser pages, or entire city state. Treat all player-supplied speech and lore-pack text as untrusted narrative input; never allow it to override action authorization or system constraints. Separate data fields from system instructions.

**Dialogue constraints:** NPCs may be mistaken in character but must not *know* unseen ground truth. NPCs can decline requests; never automatically report a task complete. Dialogue output is plain text, so it needs no schema repair; if the stream fails or comes back empty, use a neutral fallback line and capture the trace. The action stage gets one bounded schema repair; if it is still invalid, no action is taken and the trace is captured.

## 10.4 Audio playback and lip-sync decision

For v1: generate/stream playable audio through the **service's own system audio output** on the gaming PC (the Mac in dev-LAN mode), provide synchronized in-game subtitle/speaker indicator, optionally rotate/gesture the GTA ped. This is sound from the computer rather than guaranteed **world-space spatial audio**. Avoid claiming a documented path for injecting arbitrary WAV samples into GTA's native entity audio without a successful prototype. Lip-sync and 3D audio require an isolated feasibility spike before promising either.

Avoid audio overlap: one active speech lane at a time (a reply and an unsolicited remark never overlap), duck game audio if possible through approved settings, and allow fast cancellation. Include a **text-only mode** if TTS fails or users do not wish to share mic audio.

# 11. Game action safety, eligibility, and execution {#sec11}

## 11.1 Action policy matrix

| Action | Guard conditions | Observable completion | Phase |
|---|---|---|---|
| `face_player` | live, within interaction range, not busy | heading changes/turn task accepted | P0 |
| `follow_player` | live, mobile, player accepted, no mission override | task active and NPC remains near player | P0 |
| `stop_following` | actor currently in mod-owned follow state | mod task cleared or replaced | P0 |
| `go_to_safe_point` | reachable coordinates bounded to nearby navigable zone | arrived within threshold or timeout | P0 |
| `wait_here` | actor not fleeing/mission-locked | task accepted; stays within tolerance | P0 |
| `wander_nearby` | valid ped, walkable region | wander task accepted | P0 |
| `flee_from_threat` | credible threat; actor can flee | flee behavior starts or fails safely | P0 |
| `play_verified_gesture` | tested animation mapping, not in vehicle/combat | animation starts; no soft-lock | P0 conditional |
| `enter_nearby_vehicle` | player consent, safe seat, path, game-native verified | ped seated | P1 |
| `drive_to_point` | valid vehicle and driver, route & combat handling | end location reached safely | P2 |
| `trade_item` | true inventory and transaction primitives verified | durable transfer + rollback test | P2 |

**Important:** no `Hash.<native>` calls are included in the spec as guaranteed implementations. Game programmers must map each action to specific vetted SHVDN API/native behavior in a one-page **Action Capability Sheet**, including supported game build, known failure modes and tests.

## 11.2 Action state machine

`PROPOSED → AUTHORIZED → QUEUED → DISPATCHED → STARTED → {COMPLETED | FAILED | CANCELED | EXPIRED}`. Any invalid actor/session/scene transition terminates the action. Replays with identical idempotency keys return the existing result. A speaking NPC may announce intended action (“I'll follow you”), but must not assert arrival/success until observed.

## 11.3 Protection and control tiers

- **Tier A / always eligible if safe:** face, wait, benign gesture.
- **Tier B / consent and context:** follow, approach, move, flee, optional vehicle access.
- **Tier C / separate future opt-in:** aggressive/combat, inventory, monetary changes, persistent travel, police/wanted-level changes, mission/story modification.

A “sandbox dev” mode may visualize rejected proposals; it must **not** relax rules through prompt-only instructions. No mods loaded into GTA Online.

## 11.4 Entity ownership and eligibility

A plain "is this a mission entity?" check is not enough. In GTA, entities that a script creates or keeps from despawning are flagged as mission (persistent) entities, so the mod's own curated peds normally carry the same flag as a story mission's peds. Verify this on the pinned build and record it in the Action Capability Sheets. The adapter classifies every candidate entity before authorizing an action:

| Class | How it is recognized | Default AI eligibility |
|---|---|---|
| **Mod-owned** | Handle is in the adapter's own spawn registry for the current `runtime_generation` | Eligible, subject to §11.1 guards |
| **Game-script-owned** | Mission/persistent entity not in the mod's registry; record the owning script when a native exposes it | **Protected:** no AI actions unless a specific, tested integration allows a limited policy (e.g., conversation-only around a shop service) |
| **Ambient** | Neither of the above | Eligible for Tier A/B actions, subject to §11.1 guards |

Global mission, cutscene, and random-event state are additional inputs that further restrict every class; they are not sufficient on their own to clear an entity. The adapter must never take ownership of a game-script-owned entity (for example, by marking it as its own mission entity) to make it eligible. Taking ownership of an *ambient* ped, such as to pin it (FR-ID-03), moves it into the mod's registry.

Each actor also has a **control policy** from `SPECS_agentdesign.md`: **Ambient** (normal AI behavior when eligible), **Service-limited** (conversation and expression only, around the native service; P1), or **Protected** (no autonomous behavior: story characters and mission or random-event participants). Game-script-owned entities default to Protected; Service-limited requires a tested integration for that specific service.

# 12. Non-functional requirements and performance budgets {#sec12}

All targets below are **engineering acceptance goals**, not measured current performance. Profile on the reference Windows rig(s), with hardware recorded in `compatibility.yaml`, at the beginning of each release stage. Numbers from dev-LAN or CrossOver setups do not count.

| Area | Alpha target | Measurement |
|---|---|---|
| GTA tick budget | adapter median < 1 ms, p95 < 2 ms of script-owned work | instrument tick durations over 5-min loop |
| Frame-rate impact | ≤ 5% relative median FPS degradation on fixed benchmark scene | A/B mod disabled vs enabled, same settings |
| Typed-to-visible reply | p50 ≤ 3 s; p95 ≤ 8 s on designated hosted model | first meaningful generated text displayed |
| PTT-release-to-first audio | p50 ≤ 5 s; p95 ≤ 12 s on reference mic/model | end-of-recording to audible speech |
| PTT press to mic open | p95 ≤ 100 ms on loopback | key event to service mic-open timestamp |
| Action dispatch | p95 ≤ 500 ms after authorization, excluding game animation | command queued → adapter dispatch |
| Service recovery | reconnect without GTA restart within 10 s of service return | kill/restart service; validate safe state |
| Durable memory | zero missing confirmed writes after graceful restart | restart DB test; SQLite transaction checks |
| Nearby tracked NPCs | 12 curated + up to 24 transient sampled, only 1 speaking at a time | staged stress fixture |
| Simultaneous model work | bounded, configurable; default one foreground scene plus background summarizer | task queue metrics |
| Security | loopback-only in release (dev-LAN opt-in for development), auth token, no arbitrary native/SQL execution, secrets redacted | automated security tests |

## 12.1 Scheduling budgets

- Sample actor vicinity at ~2–5 Hz, **not** every render frame; sample selected speaker state more frequently as needed. Exact rates adapt to CPU pressure.
- Throttle full scene context snapshots, memory search and embedding writes on unchanged inputs; one in-flight dialogue generation per scene.
- Circuit breaker opens after repeated provider failures and drops to text/no-AI; a dead service must not hang game tick.
- Establish cost guardrails: daily request count, maximum prompt/response tokens, per-scene budget, background-event budget, fallback provider. Display spending estimate **based on actual provider pricing configured at runtime**, not a hardcoded universal cost.
- Prefer hosted LLM initially to avoid competing with GTA for GPU and VRAM on the same PC. Benchmark a local provider only once G0 and latency baselines pass.

# 13. Developer experience, repository, and CI {#sec13}

## 13.1 Proposed monorepo

```text
living-los-santos/
  README.md
  docs/
    specification.md
    windows-runtime.md
    mac-dev-setup.md
    action-capability-sheets/
    deployment-and-troubleshooting.md
  game-adapter/
    LivingNPC.GameAdapter/            # net48 C# plugin
    LivingNPC.GameAdapter.Tests/      # game-free logic tests
    vendor-refs/README.md             # no redistribution of proprietary DLLs
  service/
    living_npc/bridge/                 # FastAPI and DTOs
    living_npc/actors/                 # profiles, identity, runtime reconciliation
    living_npc/ai/                     # prompts, models, structured output
    living_npc/memory/                 # SQLite, embeddings, summaries
    living_npc/speech/                 # PTT, STT, TTS, audio output
    living_npc/actions/                # safety policy and command dispatch
    living_npc/scenes/                 # targeting, turn selection
    living_npc/triggers/               # YAML rule engine
    living_npc/dashboard/              # local admin pages
    tests/fixtures/                    # synthetic traces, consent fixtures
  shared/
    schemas/                            # JSON Schema and example payloads
  packs/
    los-santos-pilot/                  # bios, voice assignments, triggers
  tools/
    smoke_test.py
    replay_trace.py
    collect_manifest.py
    deploy_adapter.py
    validate_pack.py
  compatibility.yaml
  .env.example
  LICENSES.md
```

## 13.2 Build, deploy, and CI

- **C# gameplay adapter:** SDK-style `net48` class library using `Microsoft.NETFramework.ReferenceAssemblies` (NuGet) and a local reference to the stable `ScriptHookVDotNet3.dll`; `dotnet build` on macOS (it also builds on Windows). Do not copy installed ScriptHookV game assets into the public repo [S06].
- **Deploy to rig:** `tools/deploy_adapter.py` copies the DLL and PDB to the rig and records the adapter version and git commit, so the session manifest (§7.4) shows exactly which build is loaded.
- **Python service:** dependencies locked for both Windows and macOS, environment-variable credentials, `python -m living_npc`; local `http://127.0.0.1:8765` unless dev-LAN mode is explicitly enabled. The player-facing Windows launcher/installer is built on a Windows CI runner, not on a Mac.
- **Tests:** unit and integration tests run on macOS and Windows without GTA; in-game smoke tests run on the rig, manually or through a controlled runner. Don't claim headless GTA CI is available.
- **CI:** macOS and Windows runners for Python lint/format/type-check and tests; validate JSON Schema/YAML packs; test protocol compatibility; build C# target; static-check secrets; package signed or checksum-labeled Windows releases.
- **Version pinning:** commit `compatibility.yaml` with date, rig hardware and driver, game build, Script Hook V/SHVDN versions, expected loaded ASIs, and the Mac toolchain (.NET SDK, Python). Use feature branches to test new Rockstar builds.

## 13.3 Configuration example

```yaml
profile: pilot-story-mode
server:
  host: 127.0.0.1
  port: 8765
  require_session_token: true
  dev_lan:                       # development only; ignored by release builds
    enabled: false
    bind: 192.168.1.20           # developer Mac's private address
    allow_sources: [192.168.1.30]  # the test rig
models:
  dialogue_provider: openai_compatible
  dialogue_model: ${LLM_DIALOGUE_MODEL}
  action_model: ${LLM_ACTION_MODEL}
  cloud_requests_opt_in: true
speech:
  input_mode: push_to_talk
  stt_provider: whisper_cpp
  tts_provider: kokoro           # illustrative; final choice from the §10.2 benchmark
  voice_fallback: subtitles_only
world:
  pilot_pack: los-santos-pilot
  max_nearby_tracked: 36
  max_scene_speakers: 1        # v1: one NPC replies per turn; raised with NPC-to-NPC scenes (§19)
  emit_unprompted_dialogue: true
  unsolicited_cooldown_seconds: 120
memory:
  sqlite_path: ${LLS_DATA_DIR}/lls.sqlite3   # per-platform default, see note below
  embeddings: local_or_fts_fallback
actions:
  allow_tiers: [A, B]
  deny_game_script_owned: true   # §11.4; the mod's own curated peds stay eligible
```

The config illustrates *intended configuration surface*. Model names/API keys are environment-provided and validated at startup. Per-platform data directories (`%LOCALAPPDATA%\LivingLosSantos` on Windows, `~/Library/Application Support/LivingLosSantos` on macOS), `~` expansion, and YAML secrets injection must be implemented explicitly. The game adapter has its own small INI holding the service URL (§6.1).

# 14. Observability, testing, and quality assurance {#sec14}

## 14.1 Trace structure

Trace one turn by `session_id`, `scene_id`, `turn_id`, and `correlation_id`. Record: model/provider/version, transcript confidence, actor target resolution, perception facts and provenance, memory IDs retrieved, action eligibility, generated proposal, policy decision, native action result, TTS chunks, latency spans, user-facing errors. Redact raw microphone recordings by default. Provide per-turn **replay bundle** of sanitized event JSON.

## 14.2 Automated backend tests

- Contract tests: all request and response schemas; version mismatch; duplicate event IDs; out-of-order `seq`; retry idempotency.
- Identity tests: recycled handle, despawn mid-action, game reload, pinned NPC reconstructed, mismatched game profile, protagonist switch (an NPC who met Franklin treats Michael as a stranger), earlier save loaded (no memories from later on that timeline).
- Conversation tests: input canceled, invalid action-stage JSON (spoken line unaffected), long response, interrupted line persisted as delivered, scene speaker mis-target, stale response, duplicate TTS chunk.
- Knowledge tests: unseen event vs heard event, low confidence source, false fact planted by adversarial player, data correction.
- Memory tests: persistent restart, retrieval relevance, actor isolation, delete/export, source event deletion, crash recovery.
- Policy tests: model requests illegal action, “ignore previous instructions,” arbitrary native hash, path traversal, prompt injection, stale authorization, game-script-owned actor refused, mod-owned curated actor with mission-entity flag still eligible.
- Speech tests: PTT pressed while an event batch is in flight (mic opens without waiting), mic denied, empty transcript, low-confidence ASR, interruption mid-sentence, no cloud consent, missing voice model.
- Load tests: 36 visible tracked entities, repeated nearby snapshots, bystanders in hearing range during a 1:1 conversation, service killed during action, 200 synthetic turns.
- Platform tests: the full backend suite passes on macOS and Windows; path, audio-device, and SQLite FTS5 differences are covered; a dev-LAN bridge rejects non-allowlisted sources and is refused by release builds.

## 14.3 In-game manual test matrix

| ID | Scenario | Expected observable result |
|---|---|---|
| T01 | Fresh rig install | SHVDN script starts and `/health` reports ready |
| T02 | Target curated NPC | Correct character selected; no duplicate |
| T03 | Target ambient NPC | Ephemeral ID; does not inherit another actor history |
| T04 | Text chat | Targeted NPC replies with subtitles |
| T05 | Voice chat | PTT + mic permission + transcript + audible response |
| T06 | NPC distance | Out-of-range speaker rejects interaction |
| T07 | Obstructed witness | Does not claim direct sight of hidden event |
| T08 | Memory after relaunch | Curated NPC recalls grounded earlier encounter |
| T09 | Wrong memory bait | NPC recognizes uncertainty or corrects player |
| T10 | Bystander nearby | Only the targeted NPC replies; an in-range bystander later recalls what it overheard as `heard`, an out-of-range one doesn't |
| T11 | Follow action | Reaches started state and NPC follows |
| T12 | Invalid actor/mission | AI action refused without game disruption |
| T13 | Actor de-spawn mid-turn | Action expires safely; no wrong-target execution |
| T14 | Service killed | GTA keeps running; user informed once |
| T15 | Service restarted | Reconnects; no duplicated action/utterance |
| T16 | Provider timeout | Bounded response or text-only fallback |
| T17 | Delete NPC memories | Prior facts absent from retrieval |
| T18 | Cancel during speech | Audio/subtitles stop; queued actions dropped |
| T19 | Save slot/profile switch | Wrong profile not silently attached |
| T20 | 20-min rig soak | No deadlocks; telemetry within alpha budget |
| T21 | Mac build → rig deploy | Adapter built on a Mac loads after reload; its version and commit appear in the session manifest |
| T22 | Protagonist switch | An NPC who met Franklin treats Michael as a stranger |
| T23 | Load an earlier save | NPCs don't remember events from after that save |

## 14.4 Evaluation rubric

Use a blinded human scoring set: **believability**, **personality consistency**, **groundedness**, **memory utility**, **action success**, **speech naturalness**, and **intrusiveness** (1–5). Track hard failures separately. An apparently entertaining fabricated memory counts as a groundedness failure, even if dialogue is compelling.

# 15. Milestones, staffing, and delivery plan {#sec15}

Estimates below are **planning ranges**, not commitments. Assume 2–3 developers with GTA scripting, Python, and backend experience; one tester/content designer part-time. G0 determines feasibility before a full schedule is accepted.

| Phase | Indicative effort | Work products | Exit condition |
|---|---|---|---|
| **0. Toolchain spike** | 3–5 days | Pinned rig + game/Script Hook V/SHVDN builds, Mac→rig build/deploy loop, command bridge, smoke footage | G0 passed |
| **1. Game bridge** | 2–3 weeks | Actor sampler, target picker, subtitles, protocol, action queue | Repeatable text → NPC → safe action |
| **2. Voice + conversation** | 2–3 weeks | Service PTT/STT/TTS validated on Windows, streaming/interrupt, provider abstraction | Voice demo on the rig |
| **3. Persistent characters** | 2–3 weeks | 12–20 bios, actor reconciliation, SQLite memories, retrieval, relationships | Persistent test after save/service restart |
| **4. Context + actions** | 2–3 weeks | Provenance/perception, triggers, action validator | G3 parity-alpha matrix passed |
| **5. Hardening/beta** | 2–4 weeks | Profiling, crash recovery, dashboard, installer docs, regression suite | G4 beta checklist passed |

**Indicative total:** 11–17 calendar weeks including G0, with some parallel development **after** the G0 go decision. This assumes no major native audio or Rockstar-update blocker. Lip-sync, audio injection, Enhanced edition, and whole-city autonomy are not inside this range.

## 15.1 Dependency chain

`G0 toolchain (rig + Mac build/deploy) → C# bridge + DTO contract → one-ped action vertical slice → speech I/O → curated identity → memory/retrieval → perception/trigger → stabilization.` The backend can parallelize storage, AI model gateway, prompt templates and dashboard against a fake game adapter while G0 is tested.

## 15.2 Initial team ownership

- **Game integration lead:** test rig setup and version pinning, Mac→rig deploy tooling, game state sampling, targeting, native tasks, game-side validation, UI/subtitles, version matrix.
- **AI/backend lead:** FastAPI, scene orchestration, memory, provider adapters, prompt contracts, policy, tracing.
- **Speech/client lead (may be combined):** PTT/STT/TTS on Windows with Mac dev parity, playback, cancellation, dashboard/UI tests.
- **Content/QA:** actors and relationships, rule packs, test scripts, usability and prompt safety evaluation.
- **Rig access:** if several developers share one rig, schedule in-game time or add a second rig; keep as much testing as possible on the fake adapter so the rig is not a bottleneck.

# 16. First sprint: issue-ready backlog {#sec16}

| Ticket | Owner area | Deliverable | Dependency |
|---|---|---|---|
| LLS-001 | Integration | Set up Windows test rig: GTA Legacy + Script Hook V + SHVDN, FPS baseline, logs | None |
| LLS-002 | Integration | Mac dev setup: build `net48` adapter on macOS, deploy to rig, reload; write setup guide; record compatibility manifest | 001 |
| LLS-003 | Integration | Minimal C# SHVDN v3 script: target and task one ped | 002 |
| LLS-004 | Backend | FastAPI `/health`, `/sessions`, events, command poll, acks | None |
| LLS-005 | Integration | Nonblocking HTTP bridge; worker-to-tick command queue; priority control lane for PTT/cancel | 003, 004 |
| LLS-006 | Backend | JSON schemas and generated representative test payloads | 004 |
| LLS-007 | Backend | Typed-dialogue mock provider; streamed dialogue stage and action-stage schema | 004 |
| LLS-008 | Speech | Service PTT mic capture → whisper.cpp proof on Windows (and on the Mac for dev) | None |
| LLS-009 | Speech | TTS audio + cancellation + subtitles sync proof | 003, 004 |
| LLS-010 | Product/QA | Pilot NPC bios (3 first) and 20 annotated scenarios | None |
| LLS-011 | Backend | SQLite schema, migrations, memory CRUD, actor isolation tests | 006 |
| LLS-012 | QA | G0 decision report + G1 vertical slice video | 001–010, 013 |
| LLS-013 | Backend | Dev-LAN mode: rig→Mac bridge with session token and source allowlist; refused by release builds | 004, 005 |

**Definition of ready for a ticket:** API/schema named; tests and failure mode defined; no undocumented game native assumption. **Definition of done:** tests, metrics, minimal documentation, working smoke run, and changelog entry for runtime-compatibility effects.

# 17. Failure handling and risk register {#sec17}

| Risk | Likelihood / impact | Mitigation / decision |
|---|---|---|
| Rockstar update breaks Script Hook V/SHVDN | High / High | Pin versions; delay game updates where the storefront allows; rollback notes; keep backend work moving on the fake adapter until a compatible release ships; support a single edition first |
| Game ped handles recycled/despawn | High / High | Stable service UUID separate from runtime generation; game-side revalidation |
| Script-thread blocking / hard freeze | Medium / High | All HTTP/AI/DB off game tick; bounded queue; timeouts; long-poll worker |
| LLM invents unseen facts | High / Medium | Actor-specific perception, provenance, groundedness tests |
| LLM initiates bad/invasive action | Medium / High | Hard allowlist, two-stage checks, no arbitrary natives, cancel/expiry |
| Audio won't be 3D or lip-synced | High / Medium | Honest v1 native playback + subtitles; separate spikes for engine spatial audio |
| Local LLM/TTS competes with GTA for GPU/VRAM | Medium / High | Hosted dialogue default, small local STT/TTS, concurrency budgets, profile on the rig |
| Mission/script conflict | Medium / High | No mission-critical actors, task governor, stop on mission context |
| Personal/cloud data and mic privacy | Medium / High | PTT only, opt-in remote calls, short local retention, no audio recording by default |
| License/IP takedown or distribution issue | Medium / High | No leaked code/assets, read current Rockstar guidelines, publish only original plugin and instructions |
| Cost unpredictability | Medium / Medium | Token quotas, request dashboard, scheduled summaries, scene budgets |
| Ambient actors feel generic | High / Medium | Curated cast first; distinctive speech/relationships; limit canned respawns |
| Code passes on the Mac but fails on the rig | Medium / Medium | Windows CI runner; in-game smoke test on the rig every sprint; no Mac-only runtime dependencies |
| One shared test rig becomes a bottleneck | Medium / Medium | Schedule rig time; add a second rig; maximize fake-adapter and replay coverage |

**Kill-switches:** service unreachable; repeated command failure; actor in unsafe/mission context; schema mismatch; tick over-budget; user disables AI. A kill-switch must stop pending actions without affecting normal game mechanics.

# 18. Security, privacy, accessibility, and IP {#sec18}

- **Single-player only.** Script Hook V's official page says it does not work in GTA Online [S01]. Block loading of AI game commands in online contexts; do not implement anti-cheat bypasses.
- **Intellectual property:** GTA V source and game assets are proprietary. A publicly leaked archive is not an open-source license. Distribute original C#/Python, documented plugin dependencies and independently licensed content; not `GTA5.exe`, game scripts, Rockstar audio assets, or leaked code.
- **Policy status:** Rockstar's official support page links terms, creator platform agreement and mod guidelines; follow the **current** version and obtain counsel if distributing/monetizing [S12]. This design is not legal advice.
- **Voice identity:** do not clone original GTA cast/voice actors or real people without rights/permission. Local generic synthesized voices are the default; track model and voice asset licenses in `LICENSES.md`.
- **Microphone:** no ambient hot mic in v1. Mic permission and recording indicator are mandatory. Do not retain original voice clips unless separately opted in for debugging.
- **Cloud AI:** display which provider receives transcripts, profiles and memories; opt-in required. Strip unnecessary personal data and secrets. Offer no-cloud text-only mode where local models are unavailable.
- **Local API security:** bind to 127.0.0.1 (dev-LAN mode: explicit private address, source allowlist, never in release builds); issue session tokens; CSRF protection for dashboard actions; origin checks; cap request body/rate; store secrets in local credential facility/environment, not world packs.
- **Injection resistance:** lore, player speech, NPC memories, and model output are untrusted. None can expand permission tiers, execute shell commands, or write arbitrary files.
- **Accessibility:** full subtitles, text input/output mode, remappable keys, font scale, color-contrast controls, optional TTS off, mic test/permission help.
- **Data retention:** provide per-character and whole-profile deletion and export; by default retain no raw mic audio; document backup locations.

# 19. Phase 2+: make Los Santos feel alive {#sec19}

These features are intentionally **not** required for SkyrimNet-level v1.0. Their purpose is to evolve from an interactive cast to a simulated neighborhood and eventually a city.

**First post-v1 milestone — NPC-to-NPC conversation.** Group scenes of 2–3 NPCs with turn-taking, a turn cap, and each participant's own knowledge; NPCs joining the player's conversation; NPC-initiated contact with a real accept/decline step from the other NPC; information passed only through actual conversations, tagged `reported` with the teller recorded; NPC-to-NPC actions (face or approach another actor, a short shared walk, idle routines); and a separate budget for unprompted NPC chatter. Behavior design: `SPECS_agentdesign.md`. Success: two curated NPCs hold a short, interruptible, budgeted conversation that reflects their different knowledge, and neither claims the other agreed to something it didn't.

**Phase 2 — Living neighborhood.** Off-screen virtual simulation for 30–100 curated/persistent characters in a defined district; schedules, work/rest/social goals, gossip, recurring locations; spawn actors near player, reconcile with world state. Run inexpensive deterministic planners most of the time; call an LLM for meaningful decisions, conflict and storytelling only. Add relationship graph and rumor confidence. Success: player revisits after in-game days and finds consistent, explicable change.

**Phase 3 — Emergent story system.** NPC agents coordinate errands, relationships, requests, group activities, favors and rivalries. Define an “intent to scene” interface so high-level LLM plans do not blindly overwrite existing GTA AI. Introduce distinct goal-planner / action-executor / narrator tasks and world consistency validator. Success: repeatable story arc with at least three participants and measurable consequences.

**Phase 4 — Experimental city-scale autonomy.** Large off-screen ECS/agent scheduler; zone-level updates; selective near-field NPC embodiment; simulation snapshots; sparse LLM use and strict cost controls; optional vehicle behaviors. Do not claim every GTA pedestrian is continuously simulated; define tiered fidelity: background aggregate → simulated named agent → live scripted ped.

**Optional research spikes (independent):** GTA-native spatial audio, speech-driven lip sync, true engine event hooks, richer vision perception, Enhanced edition, plugin SDK/MCP, procedural quest chains. Each requires a feasibility note and reproducible proof before being assigned a roadmap date.

# 20. Demo scripts and sign-off {#sec20}

## 20.1 Alpha demo (8 minutes)

1. Launch GTA on the Windows rig, open the local dashboard, show the game + plugin manifest (including an adapter build produced on a Mac).
2. Select curated NPC “Mara” and use typed text, then PTT; hear differentiated voice and see subtitles.
3. Ask about a prior encounter; show correct memory recall after service restart.
4. Ask for following: view policy result and physically moving NPC, then tell them to stop.
5. Trigger a local event with one valid witness and one occluded non-witness; compare responses.
6. Have a nearby NPC make an unsolicited remark, then interrupt it with Escape; audio never overlaps and nothing queued plays late.
7. Disable the AI service mid-turn; demonstrate no game crash and one clear fallback status; restart service.
8. Open trace dashboard and show perception → memory → model → action → acknowledgment with measured latency.

## 20.2 Sign-off checklist

- [ ] G0 passed on the Windows rig; version matrix, rollback, and Mac dev setup reproducible.
- [ ] 12+ curated stable characters have unique profiles, voices, and replay-safe identity.
- [ ] PTT/text conversation, streaming subtitles/TTS, interruptions work.
- [ ] Per-NPC memory and source-aware knowledge persist and can be deleted.
- [ ] Perception tests distinguish observed, heard, reported and unknown facts.
- [ ] Unsolicited remarks respect their budgets, never overlap a reply, and can be interrupted.
- [ ] All P0 native actions documented and proven; double validation and idempotency tested.
- [ ] No game tick network, model, database, or audio work.
- [ ] Provider outage and game reload paths tested; no duplicate actions.
- [ ] Quantified performance/latency targets reported on the reference Windows rig(s).
- [ ] Story Mode-only restriction and license/privacy disclosures reviewed.
- [ ] Windows installer, compatibility matrix, Mac developer setup and end-user troubleshooting written.

# 21. Open engineering decisions {#sec21}

The following are **questions to resolve through spikes**, not reasons to postpone the first build:

1. What Windows version, GPU, and driver define the reference rig, and what minimum spec do we publish for players?
2. Which GTA V Legacy storefront/game build and Script Hook V/SHVDN combination passes G0?
3. Does SHVDN's .NET network I/O behave correctly across script reloads? Is dev-LAN mode reliable through both machines' firewalls?
4. Which in-game HUD/subtitle approach survives changes in resolution, pause menus and controller input?
5. Are the line-of-sight and ped mission checks trustworthy enough to authorize actions? Document each tested native.
6. Which Windows-capable local TTS sounds good enough without hurting GTA frame time on the reference rig, and how will audio be synchronized with GTA subtitles?
7. What game state can actually be sampled on the target build without fragile memory scanning?
8. What stable mapping to GTA saves can be proven versus requiring explicit AI profile selection?
9. When an NPC physically de-spawns, what precise behavior should the current conversation and follow task have?
10. What packaging installs the C# DLL into the GTA folder and launches the service on Windows with minimal manual steps?
11. How do Mac-based developers debug the adapter on the rig: Visual Studio over remote desktop, a remote debugger, or logs and traces only?
12. How do we recognize which timeline branch a reloaded save belongs to (in-game date alone, or date plus stat fingerprints), and what do we show the player when it's ambiguous?

# 22. Source notes and verification links {#sec22}

Implementation references, not endorsements or warranties. Recheck compatibility, licenses, and policies when freezing the release matrix. Accessed **10 October 2026**.

- **[S01]** [Script Hook V — official](https://www.dev-c.com/gtav/scripthookv): ASI loader, native functions, supported patches and Story Mode limitation.
- **[S02]** [ScriptHookVDotNet repository](https://github.com/scripthookvdotnet/scripthookvdotnet) and [Getting Started](https://github.com/scripthookvdotnet/scripthookvdotnet/wiki/Getting-Started): runtime, API v3 and build compatibility.
- **[S03]** [SHVDN maintainer Mac/CrossOver report](https://github.com/scripthookvdotnet/scripthookvdotnet/discussions/1414): March 2026 field report, not certification. Optional CrossOver path only (§2.4).
- **[S04]** [CodeWeavers CrossOver 27 roadmap](https://www.codeweavers.com/blog/mjohnson/2026/06/11/whats-in-and-whats-out-for-crossover-27): macOS, Apple Silicon, Intel roadmap. Optional CrossOver path only (§2.4).
- **[S05]** [CodeWeavers GTA V Legacy compatibility media](https://www.codeweavers.com/compatibility/crossover/media/grand-theft-auto-v): representative run reports. Optional CrossOver path only (§2.4).
- **[S06]** [Microsoft .NET Framework reference assemblies](https://learn.microsoft.com/dotnet/framework/migration-guide/reference-assemblies): cross-platform `net48` build targeting.
- **[S07]** [SkyrimNet public game-plugin README](https://github.com/MinLL/SkyrimNet-GamePlugin/blob/main/README.md): parity benchmark and feature definitions.
- **[S08]** [whisper.cpp](https://github.com/ggml-org/whisper.cpp): local, cross-platform STT (CUDA/Vulkan/CPU on Windows, Metal on macOS).
- **[S09]** [MLX-Audio — official upstream](https://github.com/Blaizzy/mlx-audio): Apple Silicon TTS/STT; Mac development provider only.
- **[S10]** [SHVDN Enhanced fork](https://github.com/Chiheb-Bacha/ScriptHookVDotNetEnhanced): potential later Legacy/Enhanced support.
- **[S11]** [SHVDN ScriptDomain threading source](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/main/source/core/ScriptDomain.cs): game native dispatch/threading.
- **[S12]** [Rockstar Story Mode mod policy entry](https://support.rockstargames.com/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods): current legal policy links.
- **[S13]** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M): 82M-parameter open-weight TTS, Apache-2.0 weights; Windows performance to benchmark.
- **[S14]** [Piper (OHF-Voice/piper1-gpl)](https://github.com/OHF-Voice/piper1-gpl): fast local neural TTS, GPL-3.0; license review required before bundling.

---

**Recommendation for kickoff:** approve **G0 only**, not the 11–17-week implementation estimate. The G0 deliverable should be a verified rig compatibility manifest, a Mac-built adapter running on the rig, a first NPC control demo, the localhost service bridge plus dev-LAN mode, and a recording. After that, authorize the G1 vertical slice and lock the v1 action contract.
