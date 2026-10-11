# Living Los Santos implementation and acceptance plan

Audit date: 2026-10-10. Source specification: `references/SPECS.md` v0.3.

The product goal is interactive, persistent, evolving GTA V NPCs whose interactions produce observable changes in their world. The v1 alpha is the foundation for that goal. It does not complete the autonomous NPC objective. NPC-to-NPC interaction, learned behavior, neighborhood simulation, and emergent consequences remain in this plan after v1.

This document records requirements and evidence, not a release approval. At the audit baseline, the repository contained a placeholder README and reference documents. There was no executable product implementation or acceptance evidence. Concurrent source work must earn evidence independently. No gate is passed here.

The [development validation record](development-validation.md) now records local builds, simulated transport tests, storage tests, and a rendered dashboard check. Those results do not change the pending rig gates below.

## Source and evidence boundaries

| Source | Inspected state | Use and limitation |
|---|---|---|
| `references/SPECS.md` | Read in full, 885 lines | Authority for platform, v1 requirements, contracts, gates, and later phases |
| `references/SPECS_agentdesign.md` | Read in full, 287 lines | Authority for roles, control policies, memory, generated skills, and post-v1 social learning |
| `references/SPECS_skyrimnet.md` | Empty, zero bytes | Supplies no additional design or implementation evidence |
| `references/SkyrimNet-GamePlugin` | Coordinator restored gitlink `7274247d312e2a7641e6bc4dff82455128a731c1` during this audit; HEAD verified | The original directory was empty. The pinned reference now permits source inspection. |
| [Pinned SkyrimNet README](https://github.com/MinLL/SkyrimNet-GamePlugin/blob/7274247d312e2a7641e6bc4dff82455128a731c1/README.md) | Fetched on audit date; 25,175 bytes; SHA-256 `60e2f6300601d655a400a85e293863b9bbff090cb4cb5642c9c5eaaa9e5fad3e` | Confirms feature descriptions. The repository describes game assets, prompts, and Papyrus sources. README claims do not prove native runtime internals or GTA compatibility. |

The coordinator's [SkyrimNet reference review](skyrimnet-reference-notes.md) records the inspected prompt, action, and memory patterns. Its SKSE architecture, native audio, Papyrus permissions, installation experience, and reported performance cannot transfer as verified GTA capabilities. GTA's adapter must prove its own behavior. Do not import SkyrimNet's arbitrary Papyrus or console execution surface into the GTA action bridge.

## Decisions and unresolved concerns

| Concern | Implementation decision | Evidence needed to close it |
|---|---|---|
| G0 requires Windows gameplay, a Mac-built DLL, and a second Mac | Keep G0 not done. Build the spike and independent backend work against a fake adapter. Do not label those results in-game acceptance. | All seven G0 checks below |
| The main specification says feature work follows G0, but also permits backend parallel work | Prepare contracts, storage, providers, dashboard, and fake/replay tests now. Keep native action capability and platform claims provisional until G0. | Explicit G0 decision report before dependent release claims |
| v1 defers the user's most autonomous features | Retain the social, learned-skill, neighborhood, story, and city phases. Do not call G3 or G4 completion of the whole objective. | Recorded post-v1 acceptance scenarios with persistent consequences |
| Generated executable skills conflict with the v1 ban on model-written scripts | Keep generated execution out of the v1 bridge. Add a separate post-v1 restricted runtime with capability-only access and per-operation authorization. | Isolation tests, resource limits, simulation trials, and the learning-cycle demonstration |
| World change can conflict with the agent design's storyline protection | Permit persistent social and simulated neighborhood change. Keep mission flags, native faction rules, rewards, services, and canonical availability under GTA control. | Explicit effect classification and consequence tests before expanding world effects |
| Phase 3 mentions errands and requests while the agent design prohibits invented native missions | Use bounded mod-owned activities with implemented outcomes. Treat native quest integration as a separate gate, not dialogue-generated authority. | Implemented activity contract and tested state transitions |
| Save timestamps cannot uniquely identify every save branch | Require explicit profile selection when needed. Implement branch ancestry and temporal filtering. Surface ambiguous branch reattachment instead of guessing. | Older-save, later-branch, same-time-save, and profile-switch tests on the rig |
| A GTA mission-entity flag also marks mod-owned persistent actors | Classify entity ownership using the adapter registry and runtime generation before checking role and current game state. | Mod-owned actor accepted; game-script-owned actor refused on the pinned build |
| A spoken proposal can precede a failed action | Treat intention, started, and completed as distinct facts. The [sentence gate](dialogue-grounding.md) permits only fixed completion lines backed by correlated completed receipts; Python rechecks queued authorization at dispatch. | Windows synthetic regressions cover rejection, expiry, started/completed ordering, and changed dispatch policy. Native observation and unrestricted model factuality remain unverified. |
| Full semantic recall is a parity feature; FTS is a fallback | Implement the memory-index interface and lexical fallback first. Keep semantic recall pending until an embedding provider passes cross-platform recall tests. | Fixed 50-question retrieval set with semantic and fallback results |
| Consent and configuration examples can disagree | Default cloud calls to off until explicit consent. Examples that enable cloud use do not constitute consent. | No-consent tests for dialogue, embeddings, STT, and TTS |
| Provider controls can revoke consent during an active request | The shared gateway cancels active remote dialogue, action, and embedding requests on revocation. Local inference accepts literal loopback only. Hosted summaries and speech remain unimplemented. | Controlled tests cover in-flight revocation, LAN rejection, and secret redaction. Real provider acceptance remains pending. |
| Request limits must survive service restart | The shared gateway persists daily request reservations and configured-price spending estimates. Foreground and background limits remain separate. | Controlled tests cover restart, quota exhaustion, and provider fallback. Real billing reconciliation remains pending. |
| Multiple modules could record one action outcome | Keep dialogue-origin action acknowledgment persistence in the runtime. Let other modules consume those sourced events. | Duplicate acknowledgment and projection retry tests |
| Native audio, lip-sync, and engine event hooks are unproven | Use service audio and synchronized subtitles for v1. Record optional research separately. | Isolated proofs before claiming engine integration |

## Status and evidence record

Every row below starts at **not done**. A source change may become **implemented, unverified**. A fake-adapter test can become **verified in simulation**. Only the required target runtime can establish **verified on rig**. Use **not applicable** only with a recorded specification decision.

For each acceptance result, record these fields:

| Field | Required value |
|---|---|
| Requirement and scenario | Stable requirement ID, fixture or manual scenario, and expected result |
| Source | Commit SHA and relevant source paths |
| Environment | OS, hardware, game/loader/SHVDN builds, service and adapter versions, topology |
| Inputs | Pack version, provider/model version, sanitized fixture or interaction script |
| Procedure | Exact command or reproducible manual steps |
| Result | Exit status, assertions, measurements, failures, and sample count |
| Artifacts | Log, trace, report, video, or screenshot path and digest where practical |
| Review | Reviewer, date, remaining limitations, and gate decision |

Future artifact paths below are planned destinations. They do not imply that files exist. Store secrets and raw microphone recordings outside evidence bundles. Retain raw audio only with separate debugging consent.

## Release gates

| Gate | Required proof | Planned evidence | Current result |
|---|---|---|---|
| G0 toolchain | Every G0 check below passes, including the second Mac | `docs/evidence/g0/decision.md`, manifest, logs, recording | Not done; no Windows or second-Mac proof |
| G1 interaction | Target a named NPC, use text and voice, stream text, hear speech, and observe one allowed action in 20 consecutive trials | `docs/evidence/g1/`, trial ledger and video | Not done |
| G2 memory and truth | Recall after game/service restart; reject unseen facts and future-timeline memories | `docs/evidence/g2/`, regression report and rig traces | Not done |
| G3 parity alpha | All P0 requirements, parity categories, integration checks, demo, and sign-off items operate together | `docs/evidence/g3/`, complete matrix and eight-minute demo | Not done |
| G4 beta | Supported Windows matrix, install/recovery workflow, profiling, and approved release candidate | `docs/evidence/g4/`, installer checksums and release checklist | Not done |
| Post-v1 autonomy | Social interaction, skill evolution, neighborhood change, and emergent consequences meet their own gates | `docs/evidence/autonomy/` | Not done; remains part of the user objective |

### G0 mandatory checks

| ID | Required check from §2.3 | Evidence | Current result |
|---|---|---|---|
| G0-1 | Run legally installed GTA Legacy Story Mode unmodded for five minutes | `compatibility.yaml` with OS/CPU/GPU/driver/storefront/build and baseline frame-time capture | Not done |
| G0-2 | Load compatible Script Hook V, ASI loader, SHVDN, .NET Framework 4.8, and VC++ runtime | Loaded-module log; all SHVDN binaries from one release; Story Mode recording | Not done |
| G0-3 | Build `net48` on Mac, deploy, run `Reload()`, target one ped, request a harmless task, redeploy a change, and reload | `dotnet build` output, deployment receipt, loaded version/commit, task video; DLL-lock behavior | Not done |
| G0-4 | Reach loopback `/health`; test authenticated dev-LAN and reject a disallowed source | Rig/Mac request traces, firewall notes, release-mode rejection | Not done |
| G0-5 | Test typed input, GTA PTT controls, mic permission, TTS/subtitle timing, relaunch persistence, and full game-date restoration | Audio/control traces, screen recording, database checks, earlier-save comparison | Not done |
| G0-6 | Run 20 interactions and a 20-minute soak; recover after service restart without restarting GTA | Zero bridge-caused crashes/deadlocks; FPS/tick/memory measurements against alpha budgets | Not done |
| G0-7 | Reproduce G0-3 on a second Mac using only the setup guide | Independent build/deploy/reload log and reviewer record | Not done |

CrossOver or cloud Windows results remain provisional until repeated on the reference rig. Dev-LAN and CrossOver measurements cannot satisfy release latency or performance targets.

## Functional requirement matrix

Owners are implementation areas, not claims that a person or agent has accepted the work. All current results are **not done** at this audit baseline.

| ID | Priority | Required behavior | Owner | Acceptance evidence |
|---|---|---|---|---|
| FR-ID-01 | P0 | Stable UUID, kind, runtime instance, archetype, name, identity confidence | Identity | Three identity kinds validated; recycled handles cannot join private histories |
| FR-ID-02 | P0 | Curated spawn/reconciliation uses stable identity | Game + Identity | Three save relaunches retain identity; no duplicate embodiment |
| FR-ID-03 | P0 | Ambient history stays session-only; adoption creates durable identity | Identity | Session end clears temporary history; adoption survives restart without aliasing |
| FR-ID-04 | P0 | Despawn, mission takeover, or distance invalidates pending work | Game + Policy | Mid-turn and mid-action removal tests; no wrong-target native call |
| FR-ID-05 | P1 | Off-screen virtual location/routine and duplicate-free reconciliation | Simulation | Travel away/return test with one physical instance |
| FR-ID-06 | P0 | Michael, Franklin, and Trevor have separate identity and relationships | Identity + Memory | T22 and private-experience isolation assertions |
| FR-DLG-01 | P0 | Text and PTT use one pipeline with locked, revalidated target | Conversation | T04/T05/T06; changed-target and stale-scene rejection |
| FR-DLG-02 | P0 | Plain-text streaming followed by separate one-proposal action stage | Conversation | No midstream JSON validation; one repair maximum; invalid action leaves speech intact |
| FR-DLG-03 | P0 | Sentence TTS/subtitles stream; completion claims wait for acknowledgment | Speech + Conversation | Chunk timestamps, delivery trace, failed-action dialogue regression |
| FR-DLG-04 | P0 | Cancel, priorities, cooldowns, and one speech lane prevent overlap | Speech + Conversation | Cancel midstream; targeted reply preempts remark; no late playback/action |
| FR-DLG-05 | P2, first post-v1 | Two or three NPCs converse using individual knowledge and bounded turns | Social | Interruptible participant-scoped scene with no leaked knowledge |
| FR-DLG-06 | P1 | Named direct targeting still respects proximity | Conversation | Valid, ambiguous, protected, and out-of-range target fixtures |
| FR-PER-01 | P0 | Sample supported world/player/actor state with availability markers | Game + Perception | Capability manifest and rig sample traces for each supported field |
| FR-PER-02 | P0 | Use proven line-of-sight or conservative unknown state | Perception | 20 occlusion/visibility fixtures; separate false positives and misses |
| FR-PER-03 | P0 | Preserve observed, heard, reported, system, and unverified provenance | Perception + Memory | No hearsay-to-eyewitness promotion in context or stored memories |
| FR-PER-04 | P0 | Configurable hearing/whisper radius and compatible area/interior | Perception | T10, whisper boundary, floor/interior, and unknown-occlusion fixtures |
| FR-PER-05 | P1 | Multi-signal incident inference includes confidence | Perception | Labeled inferred-incident evaluation; no universal-hook claim |
| FR-MEM-01 | P0 | Separate canonical events, personal memories, and scoped knowledge | Memory | Schema and cross-store isolation checks |
| FR-MEM-02 | P0 | Semantic retrieval plus actor/time filters; FTS fallback | Memory | Semantic and lexical results on the same fixed recall set |
| FR-MEM-03 | P0 | Importance, confidence, source IDs, visibility, time, optional decay/TTL | Memory | Validation, scope, expiration, and provenance persistence tests |
| FR-MEM-04 | P0 | Durable, relevant recall with bounded prompt tokens | Memory | Restart tests, 50-question bait set, 10 simulated days, token measurements |
| FR-MEM-05 | P0 | Grounded deterministic familiarity/trust/affinity/fear/respect per protagonist | Memory | Event-to-update fixtures; model suggestion cannot write authoritative score |
| FR-MEM-06 | P0 | Dashboard CRUD/export/reset, migrations, backups, deletion | Memory + Dashboard | Deletion removes vectors/summaries/caches; backup restore and migration tests |
| FR-MEM-07 | P1 | Background summaries retain provenance and avoid repeated inferred facts | Memory | Summary source links, correction propagation, dedupe regression |
| FR-MEM-08 | P0 | All durable knowledge follows save branches | Memory + Identity | T23; future relationships/knowledge/skill adoption excluded after rewind |
| FR-ACT-01 | P0 | Registry specifies schema, guards, cooldown, risk, executor, stop policy | Policy + Game | Registry validation and Action Capability Sheet for each native operation |
| FR-ACT-02 | P0 | Python and game revalidate registered actions at dispatch | Policy + Game | 100 valid/malicious proposals; mod-owned mission-flag case; protected actor refusal |
| FR-ACT-03 | P0 | Correlation, idempotency, queue state, result, failure reason | Bridge + Policy | Retry/restart replay cannot duplicate effect; result trace is complete |
| FR-ACT-04 | P0 | One conflicting locomotion task; GTA emergency/script behavior wins | Game | Competing tasks and takeover tests; no restoration over newer native task |
| FR-ACT-05 | P0 | Face, follow, stop, safe-point, wait, wander, flee; verified gestures only | Game | Individual capability sheets and observed completion/failure for each action |
| FR-ACT-06 | P1 | Vehicle entry/exit, approach actor, help only after native proof | Game | Per-action proof, participant consent where required, anti-softlock tests |
| FR-ACT-07 | P2 | Transactions, driving, conflict, quests, and travel need separate gates | Game + Simulation | Each feature's native effects, rollback, scope, and safety contract |
| FR-TRG-01 | P0 | YAML event/condition/scope/priority/cooldown/probability/effect rules | Triggers | Pack schema tests and deterministic replay of all supported effect types |
| FR-TRG-02 | P2, first post-v1 | Continuous scenes stop on caps, danger, or departure | Social | Turn/participant/time limits and cancellation traces |
| FR-TRG-03 | P0 | Global local-remark budget plus actor/area cooldowns | Triggers | Burst-event test; one speech lane; configurable interval respected |
| FR-TRG-04 | P2, first post-v1 | Rumors move only through real conversations with teller recorded | Social + Memory | A→B→C trace; uninvolved actor remains unaware; source remains reported |
| FR-OPS-01 | P0 | Offline service produces one notice and disables AI without game disruption | Bridge + Game | T14/T15, tick responsiveness, no repeated notices |
| FR-OPS-02 | P0 | Strict versioned configuration, defaults, migration, secret redaction | Operations | Invalid-config, migration, environment, and log-redaction tests |
| FR-OPS-03 | P0 | Dashboard exposes actors, sessions, actions, context, cost, errors, versions | Dashboard | Real service browser acceptance for every view and mutation |
| FR-OPS-04 | P0 | Deterministic sanitized trace replay without GTA | Operations | `tools/replay_trace.py` reproduces decisions and acknowledgments |
| FR-OPS-05 | P1 | Namespaced character/rule import/export; versioned prompts/actions | Packs | Round-trip pack test, collision rejection, provenance/version record |

### Additional alpha invariants

| Area | Requirement beyond the FR list | Acceptance evidence | Current result |
|---|---|---|---|
| Threading | Tick only samples, drains bounded commands, and renders UI; workers own HTTP, audio, database, models, and disk | Source review plus tick instrumentation under stalled dependencies | Not done |
| Lifecycle | Session, actor, runtime generation, expiry, and idempotency bind each command | Reload, despawn, changed scene, expired authorization, and duplicate tests | Not done |
| Backpressure | Drop low-value snapshots first; preserve control and acknowledgments | Saturated queue test with PTT/cancel/ack delivery and bounded memory | Not done |
| Persistence | SQLite WAL, crash-safe writes, profile association, branch ancestry, periodic snapshots | Crash/restart, backup/restore, ambiguous-save, and branch retrieval tests | Not done |
| Grounding | Verified game state outranks profile, sourced memory, hearsay, and speculation | Actor-specific prompt snapshots and adversarial groundedness evaluation | Not done |
| Delivery truth | Interrupted dialogue records only delivered content; action outcome arrives separately | Interrupted TTS/subtitle test and linked outcome event | Not done |
| Cast | 12–20 original characters with complete profiles and stable voices | Pack validator; at least three occupations, three conflicting personalities, two relationships, two witnesses and one non-witness | Not done |
| Input/UI | Configurable target/PTT/cancel, explicit mic state, labels, history, text-only mode | Keyboard/controller/resolution/pause-menu checks and recording indicator proof | Not done |
| Accessibility | Subtitles, remappable keys, font scale, contrast, optional TTS, mic help | In-game and dashboard acceptance | Not done |
| Speech | whisper.cpp baseline; Windows local TTS plus fallback; compare Kokoro, Piper, and hosted option | Actual GTA-running benchmark, model/voice license records, no-overlap cancellation | Not done |
| Providers | OpenAI-compatible streaming dialogue, separate selector, embedding and summary interfaces | Provider contract tests plus opted-in live provider trial | Not done |
| Cost | Foreground/background quotas, token limits, daily requests, actual configured pricing | Quota-exhaustion tests and dashboard usage reconciliation | Not done |
| Security | Release loopback, tokens, origin/CSRF checks, body/rate limits, no arbitrary execution | Hostile-origin/body/rate/authorization tests; authenticated dashboard mutation checks | Not done |
| Privacy | PTT only; explicit cloud consent; no raw audio by default; local secret storage | Permission-denied and no-consent tests; retention/export/delete audit | Not done |
| Story protection | Online disabled; missions, cutscenes, encounters, services, unavailable characters retain native control | Rig takeover tests and policy fixtures across role/ownership/state combinations | Not done |
| Distribution | Original code/content only, dependency and voice licenses, compatibility pins, rollback notes | `LICENSES.md`, asset audit, reproducible supported build and installer | Not done |
| Platform | Mac authoring; Windows runtime; no Mac-only shipped dependency | macOS/Windows CI and independently reproduced Windows installation | Not done |

## Protocol acceptance

All payloads use UTF-8 and version 1 schemas. Requests use UTC ISO-8601 timestamps and separate full GTA date/time. Events and commands have a 256 KiB limit. Audio uses a separate internal transport. Current result for every endpoint is **not done**.

| Endpoint | Required checks |
|---|---|
| `GET /health` | Availability/version/provider readiness; no secrets; 100 ms target |
| `POST /v1/bridge/sessions` | Manifest, capabilities, profile, session token, policy version; schema mismatch rejection |
| `POST /v1/bridge/events:batch` | Per-type schema, dedupe IDs, sequence ordering, actor/protagonist/generation, bounded body |
| `POST /v1/bridge/control` | Immediate priority PTT/cancel lane; dedupe and cross-lane ordering without waiting for a batch |
| `GET /v1/bridge/commands?after=<seq>` | Worker long-poll at most five seconds; cursor replay; bounded expiry; authenticated session |
| `POST /v1/bridge/commands/{id}/ack` | Valid state transitions; started differs from completed; rejection/expiry reasons; repeat-safe acknowledgment |
| `POST /v1/bridge/heartbeat` | Every two to five seconds; FPS/queue/actor count; no tick blocking |
| `POST /v1/bridge/sessions/{id}/close` | Cancel/flush on normal exit; safe abrupt-death cleanup |

Cross-language DTO fixtures must cover unknown fields, incompatible schemas, invalid actors, stale generations, duplicate requests, out-of-order events, and unauthorized clients. Release builds must refuse dev-LAN. Development LAN requires explicit private bind, source allowlist, and session authentication.

## Quantitative acceptance targets

These are targets from §12. The development validation record contains synthetic measurements and local storage checks. Rig and real-model measurements remain missing.

| Metric | Target | Required environment or sample |
|---|---|---|
| Script tick work | Median <1 ms; p95 <2 ms | Five-minute rig loop |
| Median FPS impact | ≤5% relative loss | Same scene/settings, mod disabled versus enabled |
| Typed input to meaningful visible text | p50 ≤3 s; p95 ≤8 s | Designated hosted model, Windows loopback |
| PTT release to first audible speech | p50 ≤5 s; p95 ≤12 s | Reference rig/mic/model |
| PTT press to mic open | p95 ≤100 ms | Correlated GTA key and service mic timestamps, loopback |
| Authorized command dispatch | p95 ≤500 ms | Queue-to-dispatch, excluding animation duration |
| Service recovery | ≤10 s after service returns | No GTA restart, no replayed effect |
| Durable writes | Zero missing confirmed writes after graceful restart | Database restart checks |
| Tracked actors | 12 curated plus up to 24 transient; one speaker | Staged stress fixture and rig run |
| Model concurrency | One foreground scene plus bounded background summarizer by default | Queue metrics and overload test |
| Perception accuracy | 20 occlusion fixtures; report false positives separately | Controlled scene truth |
| Memory evaluation | 50 fixed questions including bait; 10 simulated days | Semantic and fallback retrieval |
| Action abuse resistance | 100 generated proposals | Invalid/malicious and valid-control cases |
| Synthetic load | 200 turns | Deterministic fake adapter |
| G0/G1 repetitions | 20 G0 cycles; 20 consecutive successful G1 trials | Windows rig |
| Soak | 20 minutes, zero bridge crashes/deadlocks | Windows rig, growth and frame-time report |

Human evaluation separately scores believability, personality, groundedness, memory utility, action success, speech naturalness, and intrusiveness from one to five. Entertaining fabricated memories remain groundedness failures.

## Test and demonstration ledger

All scenarios below have **P0 priority** and are **not run** at this audit baseline. A fake scenario does not replace its rig scenario.

| Test | Observable acceptance |
|---|---|
| T01 | Fresh Windows installation starts SHVDN and reports ready health |
| T02 | Curated target resolves correctly without duplicate embodiment |
| T03 | Ambient target remains ephemeral and receives no unrelated history |
| T04 | Typed conversation produces the selected NPC's subtitles |
| T05 | PTT, permission, transcript, and audible response operate together |
| T06 | Distance invalidates interaction |
| T07 | Occluded actor does not claim direct sight |
| T08 | Relaunched curated NPC recalls the grounded encounter |
| T09 | False-memory bait produces uncertainty or correction |
| T10 | Only target replies; eligible bystander stores heard knowledge; remote bystander does not |
| T11 | Follow reports started and the NPC physically follows |
| T12 | Invalid or protected actor refuses actions without disrupting GTA |
| T13 | Despawn safely expires work without wrong-target execution |
| T14 | Killed service leaves GTA usable with one notice |
| T15 | Restart reconnects without duplicate speech or actions |
| T16 | Provider timeout produces bounded fallback |
| T17 | Deleted facts disappear from all retrieval paths |
| T18 | Cancel stops audio/subtitles and queued actions |
| T19 | Save/profile switch cannot silently attach unrelated history |
| T20 | Twenty-minute soak meets stability and performance targets |
| T21 | Mac-built adapter reloads on rig with the correct version and commit |
| T22 | Meeting Franklin does not make Michael familiar |
| T23 | Earlier save excludes future knowledge |

The backend suites from §14.2 must cover contracts, identity, conversation, knowledge, memory, policy, speech, load, and platform differences. Include interrupted-delivery persistence, duplicate TTS chunks, denied microphone access, missing models, prompt injection, path/native/SQL requests, crash recovery, deletion of source events, and Windows FTS5 support.

The eight-minute alpha demo must show the manifest, typed and spoken Mara conversations, restart recall, physical follow/stop, witness contrast, interrupted unsolicited speech, service outage/recovery, and a complete measured trace. G3 sign-off also requires all twelve §20.2 checklist items. A demo alone does not replace the tests or numeric evidence.

## Multi-agent ownership and implementation sequence

The coordinator owns integration, contract decisions, review, verification, and gate accounting. The coordinator assigns implementation to a verified available Claude owner when possible. This plan does not claim an external session's availability. Agents must agree on contracts before editing shared surfaces.

| Workstream | Owned paths and responsibility | Initial deliverable | Dependency |
|---|---|---|---|
| Specification/QA | `docs/implementation-plan.md`; requirement audit and evidence review | This plan and missing-evidence report | References |
| Protocol/backend | `service/lls/`, `tests/`, `prompts/`; bridge, sessions, models, memory, policy, fake adapter | Runnable authenticated service and contract tests | Agreed DTOs |
| Game integration | `adapter/`, `tools/deploy_adapter.py`, rig setup documents | Mac-buildable G0 adapter and deploy receipt | Stable SHVDN reference; Windows rig for proof |
| Speech/content/operations | Speech providers, dashboard, `data/` content, CI, setup/validation tools under explicitly assigned subpaths | Windows-capable voice spike, pilot pack, reproducible checks | Backend interfaces; rig for audio/performance |

These are ownership areas. If agents run concurrently, assign exclusive files or subdirectories before edits. Contract changes need coordinator review and matching fixture changes. Do not allow two agents to rewrite the service entry point, dependency files, or protocol models concurrently.

The coordinator selected `adapter/`, `service/lls/`, `tests/`, `data/`, and `prompts/` for the initial implementation. These differ from the illustrative tree in §13.1 without changing its responsibilities. The coordinator also added `.github/workflows/ci.yml`. No CI run or passing result has been verified during this audit.

1. Implement the G0 harness and explicit unverified compatibility manifest.
2. Define shared DTOs, action states, identity fields, capability negotiation, and evidence output.
3. Implement authenticated bridge, fake game adapter, replay, and deterministic policy tests.
4. Implement storage, branch-aware memory, scoped knowledge, relationships, and the pilot cast against the fake adapter.
5. Implement streamed dialogue, separate action selection, delivery accounting, cancellation, and provider budgets.
6. Implement Windows speech proof, local dashboard, pack validation, and cross-platform CI.
7. Complete the seven G0 rig checks and record the go decision.
8. Integrate one-ped native behavior and pass twenty consecutive G1 trials.
9. Complete all P0 actions, perception, triggers, persistence, and G2/G3 acceptance.
10. Harden installation, recovery, performance, and the supported matrix for G4.
11. Implement the post-v1 social and learned-skill milestones below.
12. Implement persistent neighborhood and emergent consequence milestones below.

Steps 2–6 may run in parallel against the fake adapter while G0 proceeds. Any native behavior included in G0 remains a spike until validated. The 11–17-week estimate in §15 covers the proposed foundation through beta, not autonomous city completion.

### First-sprint ticket mapping

Every ticket is **not done** at the audit baseline. A ticket requires tests, metrics, minimal documentation, a smoke run, and a compatibility changelog entry when relevant.

| Ticket | Work product | Dependencies | Evidence |
|---|---|---|---|
| LLS-001 | Windows rig, Legacy, loader/runtime, baseline | None | G0-1/G0-2 |
| LLS-002 | Mac build/deploy/reload and compatibility manifest | 001 | G0-3/G0-7 |
| LLS-003 | Minimal SHVDN target/task script | 002 | Harmless physical task recording |
| LLS-004 | Health, sessions, events, command poll, acknowledgments | None | Protocol integration suite |
| LLS-005 | Worker/tick bridge and priority PTT/cancel | 003, 004 | Blocked-network tick and saturated-queue tests |
| LLS-006 | Schemas and representative payloads | 004 | Cross-language valid/invalid fixture results |
| LLS-007 | Mock typed dialogue, streaming, action schema | 004 | Fake-adapter end-to-end turn |
| LLS-008 | Windows and Mac PTT→whisper.cpp | None | Real-device recording/transcription proof |
| LLS-009 | TTS, cancellation, subtitle synchronization | 003, 004 | Timestamped playback/cancel proof |
| LLS-010 | Three initial bios and twenty annotated scenarios | None | Pack validation and scenario review; expand to 12–20 for alpha |
| LLS-011 | SQLite migrations, memory CRUD, actor isolation | 006 | Restart/migration/isolation suite |
| LLS-012 | G0 decision and G1 video | 001–010, 013 | Distinct G0 report and later G1 trial evidence |
| LLS-013 | Authenticated allowlisted dev-LAN, refused in release | 004, 005 | Allowed/disallowed-source and release-mode tests |

## Post-v1 autonomy requirements

The IDs in this section organize the companion design. They do not replace its requirements. Every result is **not done**.

| ID | Requirement | Authoritative acceptance evidence |
|---|---|---|
| AUTO-01 | Two or three persistent NPCs initiate and sustain social scenes without a player prompt | Recorded bounded conversation with distinct opinions, knowledge, and explicit turn ownership |
| AUTO-02 | Social invitations require each participant's actual acceptance | Accept/refuse/withdraw traces; no unilateral claim of friendship, forgiveness, or shared activity |
| AUTO-03 | Social activity yields on danger, script takeover, departure, expiry, or participant loss | In-game interruption tests; every participant releases control cleanly |
| AUTO-04 | Rumors and learned facts travel only through observed or communicated events | A→B→C provenance, teller identity, confidence, time, and restricted information content |
| AUTO-05 | Repeated encounters alter preferences, familiarity, approach frequency, and topic selection | Before/after behavior linked to real outcomes; refusal can be a successful result |
| AUTO-06 | Generated skills run in a restricted runtime through validated capabilities | No arbitrary file/network/shell/native access; CPU/time/memory/operation limits; adversarial escape tests |
| AUTO-07 | Skills follow observe→generate→validate→trial→evaluate→retain/revise/retire | Full recorded learning cycle: executable candidate, failed or limited trial, revised code, later suitable selection |
| AUTO-08 | Retained skills preserve versions, dependencies, limits, cancellation, outcomes, and provenance | Persistent registry, dependency-change revalidation, rollback, retirement, and save-rewind tests |
| AUTO-09 | Shared skill code does not share private knowledge or automatic global adoption | Per-NPC repertoire and adoption event; recipient prerequisites tested; no names/memories copied |
| AUTO-10 | Action classes can evolve without expanding permissions | New class and composition tests; cumulative distance/duration/participants remain bounded |
| AUTO-11 | Living neighborhood simulates 30–100 persistent characters | Multi-day schedules, goals, relationships, rumor confidence, virtual locations, and duplicate-free near-player embodiment |
| AUTO-12 | Off-screen outcomes follow implemented simulation, not fabricated narrative | Event ledger reconstructs completed activities and their consequences; plans remain separate from facts |
| AUTO-13 | A repeatable story arc involves at least three participants | Planner/executor/narrator plus world validator; implemented favors, requests, group activities, or rivalries produce measurable consequences |
| AUTO-14 | Experimental city autonomy uses tiered fidelity and strict budgets | Zone scheduler, snapshots, aggregate→named→embodied reconciliation, scale/cost measurements; no claim that every ped continuously reasons |

The first social milestone uses persistent acquaintances at a few locations: dockworkers, beach regulars, neighborhood pedestrians, and a specifically integrated service NPC. It must demonstrate social continuity and recipient-owned invitation decisions. Each participant receives only its own scoped context. A rumor requires the actual delivered words, teller identity, and retelling lineage. Trigger rules cannot broadcast private hearsay.

Executable skill learning has a separate milestone. The initial design uses a small declarative skill language with validated operations and bounded composition. It does not execute generated Python or shell code. Candidates must pass validation and simulation before a game trial. The skill registry and each NPC's adopted repertoire follow the save timeline.

The learned-skill milestone requires one complete learning cycle. Merely changing a prompt, unlocking a fixed behavior by elapsed time, or storing a scalar preference does not prove executable repertoire evolution. Neighborhood scheduling and social continuity can advance independently while the skill runtime remains unfinished.

### Role and memory coverage

Role and scripted involvement are independent classifications. An ordinary civilian can become protected through an encounter. Every role must pass current-state checks before action. Future skills inherit each primitive's restrictions.

| Group | Required policy and acceptance |
|---|---|
| Story characters, inactive protagonists, families, associates, contacts, antagonists, heist specialists, Strangers & Freaks, mission/random-event participants | Protected by default. No autonomous takeover, invented mission, altered canonical relationship, availability change, or encounter outcome. |
| Clerks, shops, barbers, tattoo artists, mechanics, taxi drivers, venue/hospitality staff, activity participants, property contacts | Service-limited only after specific integration. Conversation and expression cannot change prices, menus, inventory, routing, rewards, results, or eligibility. |
| Residents, workers, tourists, shoppers, rural/transient pedestrians, sport/leisure groups, nightlife, performers, enthusiasts, media, industrial/maintenance/delivery workers | Ambient when eligible. Use implemented safe locations, routines, and gestures. Appearance alone does not prove an actual job, home, service, group, or off-screen event. |
| Vehicle occupants | Optional eligible conversation. Vehicle operation remains native until separately gated. |
| Police, security, tactical/military actors, emergency workers, gangs, bikers, cults, prisoners | Preserve enforcement, access, dispatch, treatment, faction hostility, custody, and encounter behavior. Default protection where eligibility is uncertain. |
| Domestic animals | Optional simple familiarity/routines; no human conversation required. |
| Chop and scripted animals, wildlife | Preserve native activities; dedicated integration required for extra behavior. |
| Phone/radio/TV/unavailable/dead/missing characters | No invented embodiment, communications, resurrection, or relocation. |

Memory coverage includes identity, episodes, preferences, relationships, conversation state, local places, sourced social knowledge, short-lived intentions, procedural skill history, and read-only canonical context. Distinguish observation, hearsay, and interpretation. Weak memories may fade. Generated background flavor must not become false simulation history.

### Optional research and separately gated effects

Spatial audio, phoneme lip-sync, engine event hooks, richer vision, Enhanced edition, SDK/MCP, and procedural quests remain explicit research items. Vehicle travel, trading, monetary or inventory effects, combat, crime, police escalation, and native quest integration need individual capability gates. Each gate needs a feasibility note, a reproducible proof, scope review, and rollback or cancellation behavior before a roadmap commitment.

## Open engineering decisions

All twelve questions in §21 remain **unresolved** until evidence answers them.

| Decision | Evidence that resolves it |
|---|---|
| Reference Windows hardware, OS, drivers, player minimums | Rig benchmarks with GTA plus selected speech/model providers |
| Legacy storefront/build and loader/runtime combination | G0 compatibility manifest and loaded-module proof |
| Network lifecycle across reload and dev-LAN firewalls | Reload/reconnect/stale-worker and LAN tests |
| HUD/subtitles across resolution, pause, controller | In-game UI matrix |
| Line-of-sight and ownership/native checks | Action Capability Sheets and occlusion/mission fixtures |
| Windows TTS quality, resource use, subtitle timing | Kokoro/Piper/hosted comparison during GTA play |
| Reliable game state sampling without memory scanning | Per-field supported capability inventory |
| Save mapping versus manual profile selection | Save-slot detection proof and ambiguity flow |
| Despawn behavior during dialogue/follow | Cancellation policy and real despawn traces |
| Windows packaging and launcher workflow | Clean-install/start/update/uninstall tests |
| Adapter debugging from Mac | Proven log workflow; optional debugger proof |
| Save branch recognition beyond date alone | Same-time and divergent-branch reload tests |

Completion of the user objective requires current evidence for the relevant alpha gates and post-v1 autonomy milestones. Source scaffolding, mocks, passing narrow tests, and a plausible plan cannot establish that GTA NPCs now act, learn, and change their world.
