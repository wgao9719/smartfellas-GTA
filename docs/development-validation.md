# Development validation

## Conversation re-verification fixes on Windows - 2026-10-11

The [re-verification fix and measurement record](conversation-reverify-fixes.md)
covers the follow-up speaker-attribution failure, two-sentence job leads, and
missing diagnostic fields from `artifacts/rig/ingame-reverify.md`. It records the
failing regressions, real Qwen2.5-7B service-path runs, corpus/holdout error rates,
latency and complete final validation. Work stays on `win/validation`; the running
QA and model services are untouched. No deployment or native re-verification is
claimed.

## In-game QA memory and job-lead fixes on Windows - 2026-10-11

Both failures in `artifacts/rig/ingame-qa.md` reproduced before their production
fixes, directly on `win/validation` in the integration worktree on TOWER-PENGU:

- `uv run --frozen pytest -W error tests/test_recent_conversation.py -q`
  initially failed the new runtime name-recall regression: the introduction was
  absent from the second model context (1 failed). Embeddings were disabled and
  the fake provider inspected the actual runtime context.
- `uv run --frozen pytest -W error tests/test_runtime_grounding_context.py -q --tb=short`
  initially reported 2 failed, 2 passed. The exact QA docks/racetrack referral and
  its following work-availability claim reached dialogue commands.
- `uv run --frozen pytest -W error tests/test_grounding_corpus.py::test_grounding_corpus_rates -q -s`
  initially reported 1 failed: 39/179 adversarial misses (21.79%) and 0/246
  ordinary false positives. The previous corpus was retained and expanded with
  the reported failures, 30 further adversarial lines and 30 ordinary lines.

The [recent conversation window](semantic-memory.md#recent-conversation-window)
supplies four prior exchanges independently of lexical or embedding scores,
within the existing context byte budget. It reads surviving memory projections
with canonical session/turn correlation, scopes by profile, protagonist, actor
and branch, and carries provenance and erasure links. This implements the recent
transcript and bounded recall requirements in `SPECS.md` FR-MEM-02/04, section 9.3
and sections 10.1/10.3 without adding a second text cache or schema migration.

The [speech gate](dialogue-grounding.md) now receives the accepted player's text
when checking each streamed sentence. It rejects indirect job/income referrals
under a corresponding request, along with additional economic offers, unlocks
and canonical relationship claims. Safe sentences still stream; subtitles, the
action selector and confirmed memories receive only the retained text.

Final corpus rates are **0/179 adversarial misses (0.00%)** and **0/246 ordinary
false positives (0.00%)**. Ordinary sentences are tested both without player
context and under the QA job request. Additional paired-context and denial tests
cover neutral navigation, work history, referrals and uncertainty. Deterministic
rules meet the requested threshold, so no optional classifier was added.

`uv run --frozen python tools/benchmark_grounding.py --baseline 412fc4b --iterations 200`
measured 49,200 ordinary calls per context and 35,800 adversarial calls per version.
Final ordinary p50/p95 were 0.0361/0.0617 ms without a request and 0.0408/0.0688 ms
with a job request; p95 increases over the baseline were 0.0168 and 0.0275 ms.
Adversarial p50/p95 were 0.0220/0.0473 ms. There were zero additional provider calls.
These measure the English gate on the fixed corpus, not local-model generation,
end-to-end speech latency or arbitrary-paraphrase factuality.

Commands used the worktree's existing environment with these process-local settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Final command | Observed result |
|---|---|
| `uv run --frozen ruff check service tests tools` | All checks passed. |
| `uv run --frozen pytest -W error` | **2,649 passed, 1 skipped in 233.49s**, exit 0. The skip is the opt-in real whisper.cpp test. |
| `uv run --frozen pytest -W error tests/test_recent_conversation.py tests/test_context.py tests/test_runtime_erasure.py tests/test_runtime_memory_index.py tests/test_bridge.py -q` | 137 passed in 14.04s, including 20 new memory regressions. |
| `uv run --frozen pytest -W error tests/test_grounding.py tests/test_grounding_corpus.py -q -s` | 773 passed; corpus rates 0/179 misses, 0/246 false positives. |
| `uv run --frozen pytest -W error tests/test_grounding_corpus.py -q -s` | Root verification: 672 passed in 0.41s; same zero-error rates. |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 display-safe subtitles, final marker acknowledged, 5 acknowledgments, 1 simulated action, 2 durable delivery events. |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: synthetic PTT recording/stop, 2 audio/text chunks, 13 acknowledgments, 2 durable delivery events. |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, post-pin turn delivered, 8 durable memories including pre-pin input, ambient officer purged on close. |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 task withdrawal, reconnect, stale-action rejection, durable started receipt visible in export, ambient history purged, no invented completion. |
| `uv run --frozen python tools/simulated_load.py` | Passed: 36 actors, 200 confirmed turns/completions, 200 isolation checks, 134 ephemeral output memories removed, 66 persistent output memories retained, maximum context 4,594 bytes, bounded queues, zero network attempts; 62.94s. Synthetic turn p50/p95 52.70/58.72 ms, including snapshots, SQLite, polling and acknowledgments. |

The existing QA service on port 8765 was left running. Tests use temporary
databases, fake providers and isolated bridge ports; the load tool uses in-process
ASGI and forbids network access. All four live commands exited 0 and cleaned up
their owned service processes. No adapter source changed, so standalone C# tests
and GameAdapter builds were not rerun; the live probes exercised the C# worker.
No game deployment,
GTA launch, real-model quality check or native verification occurred. Session and
reply correlation metadata applies to newly written events; old events remain
eligible for ordinary retrieval but are not backfilled into a session window.

## First real conversation fixes on Windows — 2026-10-11

The [first-conversation validation record](first-conversation-fixes.md) covers
the five reported defects from adapter `427eebe`, including failing regressions,
full Windows checks and remaining native verification. Work stayed on
`win/validation` in the `integration` worktree; the existing QA service on port
8765 was left running. These fixes are not deployed or game-verified.

## Dialogue-grounding merge on Windows — 2026-10-11

Completed the in-progress merge of `win/fix-dialogue-grounding` (`4fefcd5` and
`82f7067`) into `win/validation` in the `integration` worktree on TOWER-PENGU.
The existing Python QA service was left running; live fixtures used dynamically
allocated loopback ports and cleaned up their own processes. No test bound port
8765. No adapter source changed and no GTA, microphone, or native action result
was verified.

The two reported social failures reproduced: the gate already removed the
unsupported claims, but the tests expected only the retold source as a carrier.
Retention also tracks the speaker's recalled invitation decision. Both sources
must remain linked to retained sentences for actor-local forgetting under
`SPECS.md` §9.3/§18; exact-quote metadata must clear when grounding changes a
retelling under §10 and the agent-design conversation/storyline rules. The two
expectations now include `social_decision_0`. Production gating, derivation, and
scrub code did not need an additional change beyond merging the two branches.
Five combined regressions cover either carrier's deletion before/after delivery,
speaker content/FTS scrubbing, unchanged listener hearing, replay suppression,
and a wholly filtered fallback independent of its erased inputs.

Broader validation reproduced two further fixture interactions and corrected
their setup without relaxing their assertions:

- `test_foreground_derivations_use_only_memories_in_final_bounded_context`:
  mandatory `speech_constraints` metadata left no room for either memory under
  the old 2,048-byte allowance. Add the serialized policy size to that fixture's
  budget, still require exactly one of two memories to survive, verify the final
  byte bound, and check that only retained recall creates a derivation.
- `test_every_command_emits_and_requires_schema_version[action.execute-payload3]`:
  its literal `policy_version="test"` was correctly revoked by dispatch
  reauthorization. Use the current `POLICY_VERSION` so this schema test exercises
  a deliverable action; stale-policy refusal remains covered by dispatch tests.

The runtime merge preserves both `TABLE_VERSION` and `DispatchRuntime` imports.
The social source fixture preserves both `remember` and `source_text` parameters.
Both branches' documentation sections remain present.

Commands ran from the integration worktree with this process-local environment:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Command | Observed result |
|---|---|
| `uv run --frozen pytest -W error tests/test_social.py -k 'unbacked_retelling_is_gated_without_false_quote_metadata or retelling_checks_grounding_before_display_clipping'` | Reproduced: 2 failed, 1 passed, 75 deselected. |
| `uv run --frozen pytest -W error tests/test_social.py tests/test_social_replay_erasure.py tests/test_runtime_erasure.py tests/test_retention.py tests/test_retention_maintenance.py` | First run: 120 passed, 1 failed (bounded-context fixture). After fixture correction: 121 passed in 7.67s. |
| `uv run --frozen pytest -W error tests/test_command_schema.py` | Reproduced invalid-policy fixture: 4 passed, 1 failed. |
| `uv run --frozen pytest -W error tests/test_command_schema.py tests/test_runtime_dispatch.py` | After policy-fixture correction: 31 passed in 3.13s. |
| `uv run --frozen ruff check service tests tools` | All checks passed on both runs, including after the last test change. |
| `uv run --frozen pytest -W error` | First run: 2,172 passed, 1 skipped, 1 failed in 209.61s; only the invalid-policy schema fixture failed. Final complete rerun: **2,173 passed, 1 skipped in 210.71s**. The skip is the opt-in real whisper.cpp test. |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 451 passed, 0 failed, 0 skipped. |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 delivered text events, final marker acknowledged, display-safe subtitles. |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: synthetic PTT start/stop, 2 audio/text chunks, 2 delivery acknowledgments. |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: pin persisted, 8 durable memories, ambient officer purged. |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: reconnect, stale-action refusal, ambient-history purge, started receipt retained, no invented completion. |
| `uv run --frozen python tests/live_social_check.py` | Passed: 3 participants, 6 delivered turns, 12 reported memories. |
| `uv run --frozen python tests/live_social_check.py --autonomous` | Passed: 2 participants, 2 automatically initiated/delivered turns, 2 reported memories. |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed: 2 confirmed encounters, 2 derived speaker lines erased, independent hearing preserved across 4 fixture service processes. |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed: 2 service outcomes and 2 private outcome memories after restart; duplicate cancellation suppressed, late completion rejected, no uninvolved-actor outputs. |

## Erasure/session-cleanup integration on Windows

The [integration record](erasure-retention-integration.md) documents merging
`win/validation` at `83d0968`, retaining session cleanup at schema v5 and moving
retention to v6. Fresh databases create v6 directly. Final Windows checks passed
Ruff, **1,633 pytest tests** (1 opt-in skip), **412 C# tests**, both GameAdapter builds
with zero warnings, and every requested live check except the known upstream
crash-recovery memory assertion. The 200-turn simulated load and fake demo passed.
That historical run left the crash check separately owned and unmodified; the
later "Crash-check retention correction" below records its repair. The current
dialogue-grounding integration reran it successfully.

## Windows privacy fixes — 2026-10-11

Worktree `erasure-retention`, branch `win/fix-erasure-retention`, on TOWER-PENGU.
Created its own `.venv` with the explicit uv-managed Python 3.12.14 interpreter.
Verified the audit findings against `SPECS.md` §9.3, §14.1, §17, and §18 before
implementation. Initial regressions failed for source text remaining in export,
missing character export, absent raw-history retention, foreground/social
general-recall paraphrases, and stale cached hearing. Further failing regressions
covered replay after erasure and derivation chains with no intermediate memory.

Final commands, run from the worktree on Windows PowerShell:

```powershell
& C:\Users\Brian\.lls-tools\uv\uv.exe run --frozen ruff check service tests tools
& C:\Users\Brian\.lls-tools\uv\uv.exe run --frozen pytest -W error
```

Ruff: **all checks passed**. Full pytest: **1,380 passed, 1 skipped in 176.68s**.
The skip is the opt-in real whisper.cpp test. The dashboard privacy wrapper also
passed; its direct Node run passed all 10 response-race tests. Schema upgrade tests
cover v1/v2/v3/v4 to v5, fresh-schema equivalence, preserved data, collection-time
backfill, reopen, foreign keys, and rejection of future versions.

The first integration run had 8 failures (old schema assertions and fixtures that
recreated erased sources); those were resolved while preserving their isolation
and stale-vector checks. The final full run includes all fixes and new regressions.
No adapter files changed, so this task did not rerun C# or live bridge checks.
No GTA, Steam, or Rockstar process was launched. These results do not verify native
GTA behavior, physical audio, or provider quality. See the [privacy policy](privacy-retention.md)
for historical derivation, durable-memory, and backup limits.

## Ambient dialogue scope on Windows

2026-10-11 follow-up to `4fefcd5`, in the same `dialogue-grounding` worktree on
`win/fix-dialogue-grounding`, using its existing `.venv`, Python 3.12.14 and
uv 0.12.17. The first gate matched arbitrary past-tense verbs, money words and
canonical names, dropping harmless background flavor. This conflicted with
`SPECS.md` sections 0.2, 3 and 14.4 and the agent-design ambient-personality rule.

Before production edits, this command reproduced all 12 reported false positives:

```powershell
& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen pytest -W error tests/test_grounding.py -k reported_ambient_false_positives -q --tb=short
```

Result: **12 failed, 36 deselected**. The initial corpus rate test also failed:
98/196 false positives (50.00%) and 21/120 false negatives (17.50%). Twenty
independent minimal pairs then expanded the corpus to 216 ordinary and 140
adversarial lines. Evaluating the original `4fefcd5` function from `git show`
against that expanded corpus measured 114/216 false positives (52.78%) and 25/140
false negatives (17.86%). No old source was restored or modified for that comparison.

Additional review regressions reproduced four nearby false positives (money
worries, a payment plan, and figurative point/spot wording) and two false negatives
(a free car repair and payment for a job): 6 failed, 65 passed before their fixes.
Those cases now pass alongside the corpus, mixed-clause boundaries, and an
intention following a social-media mention.

The [narrowed gate](dialogue-grounding.md#measured-vocabulary-scope) measures
**0/216 false positives (0.00%) and 0/140 false negatives (0.00%)**. Every ordinary
line must pass unchanged and every adversarial line must be removed; the aggregate
regression separately enforces the requested ≤2% false-positive / 0% false-negative
thresholds. These rates describe a checked-in regression corpus, not a general
semantic guarantee.

Five existing fixture occurrences were corrected to express prohibited claims:

- `test_bridge.py::test_ungrounded_sentence_never_reaches_delivery_or_memory`:
  “That haircut costs twenty dollars” became “Your haircut costs twenty dollars”;
  “I already moved the car to the docks” became “I've reached the requested spot.”
- `test_bridge.py::test_grounding_completion_needs_corresponding_completed_ack`:
  the same generic moved-car line became the requested-spot completion.
- `test_runtime_social.py::test_social_grounding_removes_only_unbacked_claim_sentences`:
  “The haircut costs fifty dollars” became “Your haircut costs fifty dollars.”
- `test_grounding.py::test_grounding_refuses_claim_paraphrases`:
  “That haircut is twenty” became “Your haircut is twenty.”

All original bare haircut prices and the unrequested car-movement line now appear
in the ordinary corpus. No receipt, dispatch, actor-safety or fallback assertion
was weakened. Plain-text streaming, sentence delivery, receipt-backed completion,
and dispatch reauthorization remain in place. No adapter changes or GTA checks.

Final checks against the follow-up source changes:

| Command | Observed result |
|---|---|
| `& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen pytest -W error tests/test_grounding_corpus.py tests/test_grounding.py -q -s --tb=short` | 428 passed; printed false positives 0/216 (0.00%), false negatives 0/140 (0.00%). |
| `& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen ruff check service tests tools` | All checks passed. |
| `& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen pytest -W error` | 1,850 passed, 1 skipped, 0 failed in 186.77 seconds. The skip is the opt-in real whisper.cpp binary test. |

The full suite used `LLS_DOTNET` pointing to the documented local .NET 10 SDK
and `RestoreSources=https://api.nuget.org/v3/index.json`. No native capability,
real model, audio hardware, or GTA behavior was verified by this follow-up.

## Dialogue grounding on Windows

2026-10-11, TOWER-PENGU, Windows PowerShell 5.1, Python 3.12.14, uv 0.12.17.
Worktree: `smartfellas-GTA-wt/dialogue-grounding`, branch
`win/fix-dialogue-grounding`. A separate `.venv` was created first with:

```powershell
& 'C:\Users\Brian\.lls-tools\uv\uv.exe' sync --frozen --group dev --python 'C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe'
```

Both assigned findings reproduced before their production changes. Commands below
used `& 'C:\Users\Brian\.lls-tools\uv\uv.exe'` as the executable:

| Arguments | Before-fix result |
|---|---|
| `run --frozen pytest -W error tests/test_bridge.py -k 'ungrounded or grounding' -q` | 17 failed, 3 passed, 67 deselected. Invented claims reached delivery; the small-talk negatives passed. |
| `run --frozen pytest -W error tests/test_runtime_dispatch.py -q` | 21 failed, 3 passed. Changed authorization still reached command polling. |
| `run --frozen pytest tests/test_runtime_social.py -k grounding -q` | 9 failed, 3 passed, 18 deselected. Social narrative claims reached delivery. |

Additional regressions reproduced unsafe retellings, authored remarks, long-sentence
clipping, and direct claim paraphrases before their corresponding fixes. A preliminary
full-suite run during regression development reported 3 failed, 1,436 passed,
1 skipped: its loaded long-social-output fixtures still hit the default 500-character
raw provider bound. Final fixtures explicitly configure the supported 1,000-character
bound to exercise grounding before the independent 500-character display limit.

Final checks against all source changes:

| Command | Observed result |
|---|---|
| `& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen ruff check service tests tools` | All checks passed. |
| `& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen pytest -W error` | 1,458 passed, 1 skipped, 0 failed in 177.06 seconds. The skip is the opt-in real whisper.cpp binary test. |
| `git diff --check` and `git diff --cached --check` | Passed. |

The final suite includes the additional action-stage eligibility refresh regression;
the preceding complete run passed 1,457 tests with the same single skip. A new
regression first showed that eligibility computed before speech could survive into
the action-selector call; it now passes with eligibility refreshed after speech.

See [dialogue grounding and dispatch checks](dialogue-grounding.md) for the policy,
receipt correlation, paths covered, and vocabulary limitations. No `adapter/` files
changed; this fix does not claim a new C# build, native action, audio-device, or GTA
verification. No game or launcher was started, and no cloud provider was configured.

The earlier sections below retain their original dates and evidence boundaries.

Date: 2026-10-10. Environment: macOS 27.0, arm64, Python 3.12.4, uv 0.12.17, .NET SDK 10.0.100.

These results concern the local working tree based on `200c39cfc02323f8d2811f180bd8606964810f1a`. The implementation remains uncommitted. Later changes require their own checks. No G0–G4 gate has passed.

The sections up to [Pending acceptance](#pending-acceptance) record the first session. [Second session](#second-session-second-mac-windows-ci-and-closed-handoff-issues) records the continuation committed on `living-los-santos-foundation`.

## Verified locally

| Check | Result | Boundary |
|---|---|---|
| `dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | Passed with zero warnings and errors | Compiles `net48` against the pinned public SHVDN API. Does not prove that GTA loads it. |
| `dotnet test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 104 tests passed | Pure C# safety, queues, transport, cancellation, subtitles, push-to-talk, diagnostics, registry validation, and sampled movement tracking. No native game calls. |
| `uv run python tests/live_bridge_check.py` | Passed | Actual C# HTTP worker and controller against an isolated Python service with a fake game. |
| `uv run python tests/live_bridge_check.py --speech` | Passed | Actual C# HTTP worker with synthetic capture, transcription, synthesis, and playback. No audio hardware. |
| `uv run python tests/live_bridge_crash_check.py` | Passed | Actual C# worker against an owned service process killed after a durable started acknowledgment. One fake task withdrawal, reconnect, stale-action rejection, and no invented completion. |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed | Real loopback HTTP, automatic expiry, control cancellation, late completion rejection, duplicate suppression, and two private outcome memories after service restart. No native action execution. |
| `uv run python tests/live_social_check.py` | Passed | Three fake NPCs, six delivered turns, twelve reported memories, distinct opinions, and enforced turn limit over real loopback HTTP. |
| `uv run python tests/live_social_check.py --autonomous` | Passed | Two automatic turns, distinct opinions, two reported memories, and an uninformed third actor over real loopback HTTP. Explicit synthetic hearing evidence remains necessary. |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed | Four service processes, two simulated encounters, a changed later topic, and source-memory erasure through HTTP. Independently delivered later speech remains separate history. No native hearing or model calls. |
| `uv run --frozen pytest -q -W error -o faulthandler_timeout=90` | 1,026 tests passed in 102.27 seconds | Includes service tests and `tools/tests`, semantic retrieval, microphone readiness, dashboard privacy, durable service outcomes, social adaptation and replay erasure, automatic skill practice, neighborhood advancement, and the corrected 36-NPC/200-turn load check. No real game or audio devices. |
| `uv run python tools/trial_skill.py --demo` | Passed | Two authored candidate versions and simulated primitive receipts. No generated or persisted repertoire. |
| `uv run python tools/learn_skill.py --demo` | Passed | Fake candidate provider, refused trial, revised trial, private adoption, and version-two selection after database reopen. No model or native calls. |
| `uv run python tests/live_skill_check.py` | Passed | Real loopback HTTP, refused version one, acknowledged version two, adoption, service restart, and retained execution through four primitive commands. No real model or native calls. |
| `uv run --frozen python tests/live_skill_autonomy_check.py` | Passed | Ordinary dialogue and action receipts trigger revision, acknowledged adoption, and retained practice after restart. Four practice primitives, two external actions, zero direct skill-route requests. |
| `uv run python tools/neighborhood_demo.py` | Passed | 48 actors, three game days, 2,304 activity records per mode, matching catch-up/incremental/restart results, and zero backlog. No native bindings or model calls. |
| `uv run python tests/live_neighborhood_check.py` | Passed | Normal service over loopback HTTP, 48 actors, three game days, ten bounded advancement calls, and matching virtual state after service restart. No native commands or model requests. |
| `uv run --frozen python tests/live_neighborhood_autonomy_check.py` | Passed | Thirty virtual actors, seven game hours, three automatic attempts, 120 confirmed writes, zero backlog, and preserved state after restart. No manual advancement requests. |
| `uv run python -m service.lls --demo` | Passed with zero cloud calls | Explicit fake provider and temporary data. |
| `uv run --frozen ruff check service tests tools` | Passed | Static checks only. |
| `git diff --check` | Passed | Tracked diff whitespace only. Most new implementation files remain untracked. |

The cross-language probe confirmed two displayed subtitle chunks, the final marker, one simulated action, and five acknowledgments. It waited for the complete dialogue. The service accepted the action's `started` and `completed` sequence.

The speech probe confirmed actual recording status before stop, transcribing status after microphone stop, two audio chunks, two subtitle receipts, the final marker, and thirteen acknowledgments. Its synthetic providers never opened audio devices. Both probes keep game verification false.

The process-death probe kills only its owned temporary service process after verifying the started acknowledgment in SQLite. The C# controller withdraws its fake action once. Restarting the same database establishes a new session and rejects the stale action. Canonical history retains the started event, visible in the admin profile export, and contains no completed event for that action. The unpinned ambient actor and its session-only memories, including the non-completion memory, are purged during recovery just as on normal close. The earlier live check incorrectly expected that private memory to survive; see [Crash-check retention correction](#crash-check-retention-correction). Native task withdrawal remains unverified.

Automatic action expiry and cancellation now persist separate service authorization events linked to the conversation turn. These events explicitly leave native completion and withdrawal unconfirmed. For persistent actors, their private memories survive service restart. Fourteen focused tests cover pinned timestamps, bounded retries, partial writes, command eviction, lifecycle cleanup, and erasure. Independent blocked-acknowledgment and administrative mutation probes found no new defect.

Pending outcomes prevent command eviction until their writes finish. Administrative edits and erasure join these writes before changing data. A persistence failure returns 503 and preserves bounded retry state. A late acknowledgment cannot restore an expired or canceled command. Pending records remain in memory until committed, so abrupt process death can still lose an uncommitted outcome.

The storage tests cover process reopen, actor isolation, protagonist isolation, timeline rollback, ambiguous branch selection, source deletion, derived-data deletion, confidence limits, backups, and a fixed retrieval regression set. Semantic and fallback modes use synthetic vectors. They validate retrieval behavior, not real embedding-model quality.

Optional embeddings share the provider gateway, consent state, and durable budgets. Source checks run before each outbound batch and after gateway admission. Joined executor reads prevent shutdown from overtaking canceled retrieval. Tests delete sources during cached lookup and admission. The service returns lexical results when embeddings fail.

The shared action registry supplies Python and C# argument bounds. Registry metadata does not activate native capabilities. Local OpenAI-compatible provider tests use controlled HTTP fixtures. No real local model server or hosted provider trial has passed.

Development perception now connects witness filtering, private canonical events, memories, and optional triggers. Tests cover closed default gates, current bindings, sample freshness, duplicate incidents, speech priority, and reported quote receipts. The ordinary observation endpoint still rejects grounded truth claims. Native perception remains unavailable.

Conversation familiarity changes only after every reply chunk and the final marker receive full delivery acknowledgments. Tests reject partial, canceled, expired, authored, and disabled-provider output. Relationship reset suppression survives database reopen and source reimport. A retry regression preserves one completion timestamp and one familiarity update after a projection failure.

The speech-focused suite passed 87 tests with mocked devices and providers. Independent microphone review passed 69 tests and 100 cancellation, replacement, and late-error probe cycles. Runtime tests cover push-to-talk identity, unknown confidence, subtitle timing, delivery deduplication, and joined erasure. Stalled audio-driver lifecycle calls leave the service event loop responsive. Cleanup still waits for the driver to return.

Provider tests cover persistent budget reservations, configured-price estimates, explicit local fallback, and consent revocation. Independent review found and fixed cancellation-to-fallback, incomplete-stream action, and canceled-admission accounting bugs. An OS-backed lease excludes a second AI controller while allowing ordinary database readers. macOS cross-process locking passed. Windows locking remains unverified.

Social core and runtime tests cover recipient-owned decisions, private context, fresh hearing evidence, exact retelling, timeline visibility, interruption, and joined storage cleanup. The development scheduler now starts eligible two-NPC scenes and requests turns automatically. Its 28 focused tests cover fair selection, cooldowns, independent acceptance, foreground interruption, deadlines, and bounded calls. The autonomous HTTP probe sends no invitation, review, or turn requests. Native listening remains unverified.

Four isolated subprocess tests intercept DNS and socket access through real provider wiring, including runtime embedding retrieval. Disabled, pre-consent, and revoked phases make zero outbound attempts. Consent-on reaches the intercepted DNS and connection paths. Explicit local-provider selection and configured local fallback reach only the intercepted loopback connection. The probes use synthetic credentials and prevent actual network traffic.

The full synthetic load check uses 36 tracked NPCs and 200 confirmed turns through the real service with in-process HTTP transport. It preserves all 200 delivery and completion events after reopening the database. It preserves 66 persistent output memories and expires 134 ephemeral output memories on session closure. Every provider call checks actor-private context. The uninvolved persistent actor receives no outputs. Command history remains within 256 entries. The tool respects the unchanged HTTP rate limit and reports its wait separately. Its timing measures synthetic service work, not game performance or model latency.

The final validation pass exposed a rolling-window retry bug in the load driver. The old calculation could wait 118.2 seconds against its 90-second timeout. The driver now tracks its own admission timestamps and waits for the next slot. The focused load suite passed three tests in 61.77 seconds. The service rate limit and test timeout remain unchanged.

The neighborhood simulation passed 37 focused tests and independent review. It records separate virtual plans and outcomes with bounded catch-up, timeline visibility, erasure suppression, and exact-owner embodiment reservations. Reservations create no native actor bindings. Work and social intentions remain unfulfilled.

Authenticated neighborhood routes expose explicit initialization, bounded advancement, and inspection at the current session time. Their 21 tests passed, including joined cancellation before actor erasure. An optional scheduler now advances initialized neighborhoods automatically within event and attempt budgets. Its 25 tests cover shared-scope fairness, cancellation, erasure, actor bindings, and reservations. Native embodiment and persistent world consequences remain unfinished.

The skill compiler and interpreter passed 23 tests and independent adversarial review. They reject arbitrary execution, duplicate keys, altered dependencies, forged receipts, suppressed cancellation, and unsupported composed follow operations.

Skill learning now connects the background provider gateway, bounded private source excerpts, candidate compilation, interpreter trials, and actor-private adoption. The persistence tests cover revisions, retirement, rewind, source deletion, reset suppression, and retrieval after reopening the database. A transaction-level audit check rejects deletion and reimport races across database connections. The fake learning demonstration selected revision two after reopening and left a second actor's repertoire empty.

Development skill routes connect generation, bridge trial, adoption, and selected execution through the current action policy and command queue. The runtime permits only `face_player` and `wait_here`. It resolves primitive receipts after canonical event and memory persistence. Trials retain terminal game time, canonical receipt sources, and `game_verified=false`. Independent review found and fixed execution continuing after retirement. Skill acknowledgments pin each status's first receipt time across projection retries. Real model trials and native skill execution remain unverified.

Independent review also found delayed acknowledgment persistence restoring a canceled command. The final publication check revalidates ownership, command state, and deadlines after all awaited writes. The regression preserves canonical observation evidence while refusing trial success, further primitives, and adoption. All 34 runtime skill tests passed independently and within the final combined run.

The loopback skill probe runs candidate generation, refused trial, revision, acknowledged trial, and private adoption. After a service restart, it executes the retained revision through two fresh commands. The complete probe records four primitive commands and eight canonical action memories. It rejects missing-start and malformed acknowledgments and verifies diagnostics during execution. Its provider and adapter receipts are explicit fakes.

The automatic skill probe creates an external measured action failure through ordinary dialogue and receipts. The scheduler generates version one, records its simulated refusal, generates version two, and adopts its acknowledged bridge trial. A fresh external experience after service restart selects version two for bounded practice. The probe makes no skill-route requests. This demonstrates post-outcome practice, not automatic replacement of a foreground action proposal.

Independent review found and fixed two scheduler races. Canceling a claimed worker before its first task step now ends the durable episode immediately. A reserved social worker blocks competing skill scheduling before social state exists. The runtime also retains episode authority checks while joined cancellation removes the worker reference. Twenty-four scheduler tests and eleven independent adversarial checks passed.

The adapter reports sampled cumulative movement for orientation and waiting. The tracker refuses nonfinite positions, backward time, overflow, and sampling gaps above 250 milliseconds. Seventeen pure C# tests cover the tracker. These sampled measurements do not establish continuous native distance tracking or GTA acceptance.

A combined test run exposed an AI controller deadlock during garbage collection. A reentrant registry lock fixes finalization during owner registration. A subprocess regression confirms that the controller can release and reacquire ownership.

The adapter now uses `scripts/LivingNPC/LivingNPC.ini` and writes bounded timestamped logs beside it. Build-produced identity pins the adapter and core DLL digests. Staging cannot relabel an older DLL with current source metadata. The service health response reports its startup source fingerprint and registry digest. It explicitly identifies a source-tree process without claiming a packaged release.

The G0 evidence validator checks file digests, required artifacts, and measurement fields. Supporting service and build metadata can truthfully remain unverified. Gameplay evidence cannot use simulation output. The validator always requires manual gate review and cannot certify native observations.

Deterministic replay passed 18 focused tests and an independent review. Reports distinguish queued output from submitted delivery receipts. They retain action outcomes after closure and report timeout failures. Replay uses a temporary database, recorded model responses, and an injected clock. It performs no cloud or native calls.

## Retained social adaptation

Repeated confirmed hearing now affects the listener’s later partner preference and topic selection. Own valid refusal or withdrawal creates a bounded game-time pause. Invalid model output creates no durable preference. Separate actor contexts preserve direction and source identity. Memory correction, deletion, actor reset, rewind, and protagonist changes remove ineligible effects.

Independent review found two defects and verified their fixes. Speaker-only memories could displace heard encounters from the retained-history window. Canonical replay could also restore an erased ordinary memory even when adaptation ignored it. Separate decision/hearing windows and suppression checks now cover both paths.

The history read uses indexed owner-first joins and a fixed 4,000,000 SQLite-instruction ceiling. An in-memory measurement with 128 owners and 100 decision anchors each used approximately 3.135 million instructions and 152 milliseconds. A regression covers that dataset. The complete 1,026-test Python run includes it. Larger or mixed dense histories can still exhaust the ceiling. Exhaustion stops scheduling without reserving model calls. The second session replaced this read with bounded per-owner windows. See [Second session](#second-session-second-mac-windows-ci-and-closed-handoff-issues).

The four-process HTTP probe verifies source-linked topic selection after restart and deletion. It does not prove that deleting one memory erases every independent later quotation. The broader social suite checks private boundaries, fair partner choice, stale-read cancellation, malformed decisions, retained-source correction, replay suppression, and bounded context.

The incoming Claude validators prompted a separate read-only audit. Current GTA text sinks already remove formatting tokens, but subtitle acknowledgments still report the original text. The adapter also uses a six-civilian-model allowlist without explicit police, military, or gang role detection. These are remaining native-delivery and classification gaps. Typed/PTT whisper controls and a user-facing ambient adoption flow remain unfinished. The second session closed these gaps. "Whisper controls" meant whisper mode, not whisper.cpp transcription.

## Rendered dashboard check

The review used a fresh temporary database containing the 12-character pilot pack and explicit authored knowledge fixtures. An isolated installed headless Chrome profile opened the real local service. The review did not use the user's live data.

The browser displayed all 12 character choices. Selecting Lena Ortiz displayed the authored fixture memory. Editing that memory persisted through the HTTP route. The profile export contained the edited text. The deletion dialog required the exact memory identifier. Canceling the dialog preserved the memory.

The 390-pixel layout had no horizontal overflow. Screenshots were inspected locally. The temporary browser profile and service stopped after review. The computer-use browser connector could not open localhost, so the review used the installed browser directly through its standard debugging interface.

This check establishes those dashboard flows on this Mac. It does not establish Windows browser behavior, a complete accessibility audit, or every administrative control.

A second temporary Chrome review exercised provider selection, configured prices, explicit cloud consent, revocation, and persistence after reload. It confirmed zero model requests and no horizontal overflow at 390 pixels. Live session diagnostics displayed a simulated actor and its compatibility manifest. The review stopped its browser and service and removed the temporary browser profile.

A third isolated Chrome review exercised embedding configuration, saved dimensions, embedding-only cloud consent, revocation, and persistence after reload. It confirmed zero model requests and no horizontal overflow at 390 pixels. The browser connector still refused localhost, so the review used the installed Chrome debugging interface. The temporary browser profile and service stopped after review.

A fourth isolated Chrome check held completed export, diagnostics, and memory responses in the browser. It reset an actor through the real dashboard and HTTP mutation, then released those stale responses. The export stayed hidden and empty. The diagnostics and memory views did not restore the previous text. Eight permanent response-race checks also cover profile changes, page hiding, export closure, pagination, and competing requests.

The incoming Claude plan prompted a microphone readiness review. The real microphone now waits for valid callback-delivered PCM before reporting recording. Silence is valid input. Focused tests cover first-frame delay, timeout, malformed frames, callback errors, cancellation, and replacement isolation. These tests use fake devices and do not establish microphone permission or audible input on the rig.

## Pending acceptance

The Windows rig has not connected. Tailscale deployment, game loading, native action behavior, microphone permission, actual voice models, save restoration, and performance remain unverified. A second Mac reproduced the Mac-side build and checks, but not deployment or reload on the rig.

Grounded native observations remain disabled. Native action capabilities remain absent from Release builds until the rig validates them. Development autonomous social scheduling and virtual neighborhood simulation have local evidence. Native NPC-to-NPC interaction, native learned behaviors, and neighborhood changes remain unfinished.

Real provider performance and billing comparison remain unverified. Service authorization outcomes and acknowledged action outcomes persist as distinct canonical events and private memories. These events do not establish native acceptance.

The workflow in `.github/workflows/ci.yml` defines Windows and macOS checks. The first run, for `a2727d8`, passed on macOS and failed on Windows. The second session fixed those failures. A new Windows run must confirm the fixes.

## Second session: second Mac, Windows CI, and closed handoff issues

Date: 2026-10-11. Environment: Apple M4, macOS 15.3.1 (24D70), 24 GB, uv 0.9.16 with Python 3.12.12, and .NET SDK 10.0.401 installed with the official `dotnet-install.sh` into `.dotnet/`.

### Second Mac reproduction of `a2727d8`

The second Mac followed [Mac development](mac-development.md) and the README checks without other guidance.

| Check | Result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -q -W error` | 1,026 passed in 100.45 seconds |
| `dotnet test adapter/LivingNPC.Core.Tests/...` | 104 passed |
| `dotnet build adapter/LivingNPC.GameAdapter/... --configuration Release` | Passed with zero warnings. The first attempt failed while NuGet downloaded `Microsoft.NETFramework.ReferenceAssemblies.net48` 1.0.3. An immediate retry passed. |
| Live bridge, speech, process-death, action-outcome, social, autonomous social, social adaptation, skill, skill autonomy, neighborhood, and neighborhood autonomy checks | All passed |
| `trial_skill.py --demo`, `learn_skill.py --demo`, `neighborhood_demo.py`, `simulated_load.py`, `python -m service.lls --demo` | All passed |
| `tools/deploy_adapter.py --configuration Release` | Staged a bundle whose build identity names `a2727d8` with a clean tree |

This reproduces the Mac half of G0-7. Deployment to the rig, `Reload()`, and in-game behavior remain unverified, so G0-7 is not done.

### First GitHub Actions run

Run `38090114882` checked `a2727d8` after the branch was pushed. The macOS backend jobs for Python 3.11 and 3.12 and the macOS adapter job passed. The Windows backend jobs failed with 9 or 10 test failures and 5 errors. The Windows adapter job failed in the process-death check. The failing steps hid every later Windows step.

| Failure | Cause | Fix |
|---|---|---|
| Four speech process tests | Product defect. `OpenProcess` lacked `PROCESS_SET_QUOTA`, so `AssignProcessToJobObject` always failed and no speech executable could run on Windows. | Correct access mask and a regression with a fake kernel32 |
| Four network-consent tests | The Proactor event loop connects through overlapped `ConnectEx`, which bypassed the probe | The probe intercepts `sock_connect` on both event loop families and backstops raw proactor connects |
| Synthetic load test | The network guard broke the Proactor loop's loopback self-pipe | The tool creates its loop before arming the guard |
| Five trigger and neighborhood errors | pytest put an oversized parameter into `PYTEST_CURRENT_TEST`, which exceeds Windows' environment limit | Short explicit test IDs |
| One Python 3.11 scheduler failure | Lifespan maintenance raced the test's frozen clock | The fixture stops the background maintenance task |
| Process-death check | `dotnet build` ran with a minimal environment that Windows NuGet cannot use | Filtered inherited environment and captured build output on failure |
| Neighborhood live checks (hidden) | Product defect. `Settings.from_env()` resolved the user profile even with an explicit `LLS_DATABASE`. | Lazy profile resolution |

These fixes passed on macOS. A new Windows CI run must confirm them.

### Windows product audit

A read-only audit found Windows defects that CI does not exercise.

- SSH deployment built a remote command of about 10,000 characters. Windows OpenSSH runs commands through cmd.exe, which stops at 8,191. Deployment now uploads `deploy.ps1` and a JSON plan and runs a 114-character command.
- Local deployment staged DLLs inside `scripts\`, where ScriptHookVDotNet loads every DLL. It now stages beside `scripts\`.
- Device names matched several Windows host APIs and failed. Names now resolve within one host API, WASAPI opens with automatic conversion, and errors keep the driver's reason.
- Speech subprocesses inherited the whole service environment, including `LLM_API_KEY` and `LLS_BRIDGE_TOKEN`. They now receive an allowlisted environment. SAPI uses PowerShell from the system directory with a pinned output format.
- Path settings now expand `~` and environment variables and refuse relative results.
- The demo's JSON output no longer fails on a cp1252 console.

The audit also suggested `SO_EXCLUSIVEADDRUSE` for the listening socket. It was not adopted. Microsoft documents that such a port cannot be rebound while accepted connections linger, which would break the documented service-restart recovery.

### Handoff issues closed

| Handoff issue | Resolution | Remaining boundary |
|---|---|---|
| "Typed input and push-to-talk are not connected to whisper.cpp" | The handoff note misread the audit. whisper.cpp was already connected. The audit meant whisper-mode controls. A hold-to-whisper key and a `/w ` typed prefix now lock the whisper flag per capture. The 3 m whisper radius limits development bystander hearing. | Key conflicts, the radius, and native hearing need the rig |
| Subtitle receipts differed from displayed text | One display rule in Python and C# with shared fixtures. Producers normalize before speech, queuing, receipts, and memories. The adapter refuses unsafe text instead of rewriting it. | GTA font coverage and subtitle wrapping need the rig |
| Six-model protected-role allowlist | A shared role table and classifier. Model, ped type, and relationship group must all indicate a civilian. The service recomputes the role and keeps the stricter policy. Missing facts are protected. | Every sampled value needs the rig. See the [capability sheet](actions/ped-classification-and-pin.md). |
| No user-facing pin | F8 twice, or the dashboard, pins an eligible ambient civilian. The actor is promoted in place, so its conversation memories survive session closure with valid provenance. | Persistence natives are Debug-only and unverified. Pinned characters are not re-embodied in later sessions. |
| Dense social-history read capped at 4,000,000 steps | The cost came from an unindexed `scrub_audit` scan per candidate. Schema v4 adds indexes and bounded per-owner reads. | One bad owner still fails the whole read closed |
| Later quotes survived memory deletion | Deleting a memory scrubs raw source words and removes same-actor derived lines, including new general-recall paraphrases. Other actors' independent hearing remains. | Historical untracked derivations require character reset or profile deletion; see [retention policy](privacy-retention.md). |

Two further defects were found and fixed. A pruned or recycled runtime handle could never bind again in the same session, so long sessions lost targetable peds. whisper.cpp ran with `-oj`, which carries no token probabilities, so voice turns could never become action-eligible.

### Real whisper.cpp check on the Mac

whisper.cpp v1.9.5 (`d1be6fde`) built from the official source with Metal, with `ggml-base.en.bin` (SHA-256 `a03779c8…`). Neither file is in Git.

| Input | Result before | Result now |
|---|---|---|
| `say "Please look at me."` | Text with unknown confidence | Text with confidence 0.884, action-eligible |
| `say -v Whisper` | Not measured | Text with confidence 0.815, dialogue only |
| Low hiss | `[BLANK_AUDIO]` as player text | Gated before transcription |
| Broadband noise | `(blender whirring)` as player text | Empty transcript |
| Digital silence | "You" at confidence 0.109 | Gated before transcription |

The silence gate defaults to −60 dBFS RMS. Transcripts below confidence 0.3 start no turn. Windows microphones, headset levels, and noisy rooms still need rig measurements.

### Final local check

The integrated branch at `0adde69` passed these checks on the second Mac:

- `ruff check service tests tools`.
- 1,319 pytest tests, with four skips: three Windows-only deployment tests and the opt-in real whisper.cpp test.
- 271 C# tests.
- `net48` Release and Debug builds with zero warnings.
- The live bridge check in typed, speech, whisper, speech-plus-whisper, and pin modes.
- The process-death, action-outcome, social, autonomous social, social adaptation, skill, skill autonomy, neighborhood, and neighborhood autonomy checks.
- The skill, neighborhood, and service demos.
- The synthetic load check with zero network attempts.
- Release deployment staging.

### Second GitHub Actions run

Run `38115204368` checked `18d3e29`. The macOS jobs passed. On Windows, every failure from the first run passed, including the speech process tests. The Job Object fix therefore holds on Windows.

The run found five remaining Windows failures:

- A role-change test waited only 10 milliseconds for the first dialogue chunk. It now waits for the chunk itself.
- The social scheduler fixture let the lifespan maintenance loop tick concurrently. On a slow runner that tick canceled the driven worker. The fixture now stops background maintenance, like the neighborhood scheduler fixture.
- The deployment script called `Get-FileHash`. Windows PowerShell 5.1 could not load it under the PowerShell 7 module path inherited in CI. The script now hashes with .NET directly.
- One Windows deployment test created a folder its fixture had already created.
- The Windows adapter job reached the live bridge check, and the check itself passed. Removing its temporary folder then failed, because the terminated service's interpreter still held the database. Terminating the virtual-environment launcher ends the real interpreter moments later. The seven live checks that stop a service now retry removal briefly.

These fixes passed on macOS. A Windows run of the following commit must confirm them. No result here establishes GTA behavior.

### Local Windows SQLite temporary-directory failure

On 2026-10-11, TOWER-PENGU reproduced the load-test failure at `7aeabaa` using a separate `.venv` in the `win/fix-load-sqlite` worktree. Toolchain: uv 0.12.17, Python 3.12.14, SQLite 3.53.1, Windows PowerShell 5.1. Environment setup:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
uv sync --frozen --group dev --python C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe
```

Before the fix:

| Command | Repetitions | Observed result |
|---|---|---|
| `uv run --frozen python tools/simulated_load.py` | 3 | All passed, each with 200 confirmed turns and zero network attempts. Measured workload times: 62.18, 62.81, 62.90 seconds. |
| `uv run --frozen pytest -W error tests/test_simulated_load.py -q` | 3 | Each reported 2 passed, 1 failed, in 64.69, 65.30, 65.11 seconds. All failed in `end_session` during the final session-close request, after all 200 turns. |

The failure was deterministic under the test's stripped subprocess environment. It removed `TMP`, `TEMP`, `TMPDIR`, and the user-profile variables. In that child, Python selected `C:\Windows\Temp` for its database directory, but Windows `GetTempPathW` selected `C:\Windows\` for SQLite's temporary files. A native SQLite log callback captured `SQLITE_CANTOPEN` (14), naming `C:\WINDOWS\etilqs_TFlGdb9yESTOtUT` at the cascading actor deletion. The absolute database path, its parent, and its WAL/SHM files still existed. Changing only the child's `TMP`/`TEMP` to a writable worktree directory allowed the same cleanup to succeed immediately on the same connection; all reopen and privacy assertions then passed. A second diagnostic run intercepted SQLite's VFS `xOpen`: the failed open had flags `0x201e`, including `SQLITE_OPEN_SUBJOURNAL`, identifying a statement journal. The identical open returned success after the environment-only change. That process had 339 handles and 34 threads at cleanup. Both diagnostic runs completed all final assertions after the retry.

The ordinary launch environment had `TMP` and `TEMP` pointing to `C:\Users\Brian\AppData\Local\Temp`. Its `TMPDIR` pointed to `C:\Users\Public\Documents\Wondershare\CreatorTemp`, which Python used successfully in all three direct runs. That nonstandard directory was not the cause. `SQLITE_TMPDIR` was unset; SQLite used its default file-backed temporary-store policy (`TEMP_STORE=1`, `PRAGMA temp_store=0`).

This is a harness environment defect. The repository shares one locked SQLite connection across worker threads, and the temporary database directory encloses shutdown and reopen. The failed temporary-file path and successful environment-only retry do not support a database/WAL lock, early directory deletion, or connection exhaustion as the cause. The fix gives the isolated child `TMPDIR`, `TEMP`, and `TMP` pointing to pytest's writable `tmp_path`. Credential isolation, workload size, timeout, SQLite durability, and resource limits remain unchanged. The existing full workload is the regression test. Players launching with a similarly incomplete environment need writable Windows temporary-directory settings; see [Windows diagnostics](windows-runtime.md#installed-paths-and-diagnostics).

After the fix, `uv run --frozen pytest -W error tests/test_simulated_load.py -q` passed three consecutive times: 3 passed per run, in 64.08, 64.10, and 64.21 seconds. `uv run --frozen ruff check service tests tools` also passed.

The full suite then ran with the rig's documented toolchain settings:

```powershell
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
uv run --frozen pytest -W error
```

Result: **1,312 passed, 10 skipped, 1 failed in 140.61 seconds** (exit 1). The load test passed. The sole failure was the separately owned `tools/tests/test_deploy_adapter.py::test_windows_deploy_script_rejects_corrupt_upload_and_rolls_back_locked_dll`: `DID NOT RAISE CalledProcessError`, with the script reporting "Deployed adapter" after the corrupt upload. Deployment code was not changed. No CI, GTA, or native performance checks were run for this fix, and the earlier CI runner's temporary-directory permissions were not inspected.

### Cloud payload minimization on Windows

On 2026-10-11, `win/fix-cloud-scrub` reproduced audit `c-sections-12-22.md` finding 1 on TOWER-PENGU. Before production changes, `uv run --frozen pytest tests/test_cloud_scrub.py -q` reported **1 failed in 1.22 seconds**: a synthetic e-mail address reached the fake HTTP transport unchanged. This violated SPECS.md §18's cloud minimization requirement; §12 requires secret redaction and §14.1 requires sanitized trace data.

The outbound inventory found hosted HTTP only in the dialogue/action/skill provider and embedding provider. Social turns use dialogue; skill learning uses the skill provider. Context and persona are nested JSON strings, and action repairs resend provider output. All these calls now use one shared HTTP client that scrubs the final JSON body before dispatch, including retries. It preserves necessary provider authentication headers, checks the resulting prompt against the reserved token ceiling, and attaches only filtered bodies to HTTP errors. Invalid bodies fail closed without retaining the original decoder exception. Existing traces remain content-free metadata; no outbound payload logging was added. Local inference and stored source text are unchanged.

The worktree has its own `.venv`, created with uv 0.12.17 and the explicit Python 3.12.14 interpreter. Checks used Windows PowerShell 5.1, the documented `LLS_DOTNET` path, and `RestoreSources=https://api.nuget.org/v3/index.json` for the full suite:

| Command | Observed result |
| --- | --- |
| `uv run --frozen pytest -W error tests/test_providers.py tests/test_memory_index.py -q` | 47 passed in 2.50 seconds |
| `uv run --frozen pytest -W error tests/test_cloud_scrub.py tests/test_outbound_scrub.py -q` | 157 passed in 0.96 seconds |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 1,502 passed, 1 skipped in 178.10 seconds |

The 157 new tests cover synthetic secrets and personal data through dialogue, actions and repair, embeddings, a social invitation/turn, and skill requests. They also cover nested/clipped JSON, credential values with escapes, punctuation, Windows usernames with spaces, safe failures, debug-log capture, and unchanged game dialogue, dates, coordinates, IDs and local payloads. The sole full-suite skip is the opt-in real whisper.cpp check. No adapter source changed, so separate C# tests, adapter builds, and live bridge checks were not rerun. No real cloud request or GTA/native validation was performed. Pattern-based filtering is not a guarantee of anonymity or detection of every personal fact or encoded secret; real provider and native acceptance remain pending.

## Retryable session cleanup on Windows

On 2026-10-11, TOWER-PENGU reproduced audit `b-sections-6-11.md` finding 2 in the `win/fix-session-cleanup` worktree, based on Windows validation commit `f84de44`. A separate `.venv` was created with `uv sync --frozen --group dev --python C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe`. Toolchain: uv 0.12.17, Python 3.12.14, Windows PowerShell 5.1.

Before implementation, `uv run --frozen pytest -W error tests/test_session_cleanup.py -q` reported **1 failed**: after an injected first-attempt SQLite `OperationalError`, a second close returned without deleting the ambient actor. The runtime marked the session closed before the purge, then skipped every subsequent attempt. This violated FR-ID-03's session-only ambient memory requirement; the fix follows SPECS.md §§8.3, 9.3, and 17.

SQLite schema v5 adds a cleanup-pending table. Closing revokes authorization, joins session work, and records pending intent before the purge. Purging ambient actors and bindings, setting the original end timestamp, and removing pending intent commit together. Failed operations retain retry state. Maintenance retries failed closes without terminating its loop, and startup recovers all unfinished sessions before accepting new opens. The existing unfinished session row covers failure or process death before the pending marker can be written. Affected profiles cannot open another session while cleanup is pending; pending sessions are not pruned. The admin overview and dashboard show affected IDs and safe error types, including recovery failures without a live session.

Regression coverage includes explicit close and maintenance retry, repeated failures with another session still closing, database reopen and startup retry, failure to write the marker or read recovery inventory, transaction rollback partway through the purge, migration from v4, repeated-close idempotence, pending session reuse/pruning, ambient actor/memory/FTS removal, and unchanged pinned/curated history. A close-time subtitle regression was also reproduced and fixed: trusted internal render receipts still settle for durable actors after authorization closes, while external acknowledgments remain rejected. Canceling startup releases the database and service ownership lease.

Final checks used the process-local uv PATH, `LLS_DOTNET`, and `RestoreSources` settings documented above:

| Command | Observed result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error tests/test_session_cleanup.py tests/test_admin_diagnostics.py -q` | 13 passed in 4.47 seconds |
| `uv run --frozen pytest -W error` | **1,360 passed, 1 skipped in 178.64 seconds**; exit 0 |
| `git diff --check` | Passed |

The full suite adds 15 passing regressions to the supplied Windows baseline; the opt-in real whisper.cpp test remains skipped. An earlier full run passed 1,359 tests with one skip before the startup-cancellation regression was added, so the full suite was rerun against the final code.

These checks use temporary databases, synthetic failures, fake providers, and simulated speech. No adapter files changed, and C# tests/builds or live bridge checks were not rerun for this fix. GTA, launchers, real microphone/model providers, and native gameplay were not exercised. Cleanup remains pending while storage is unavailable; no claim is made that unavailable storage can be erased immediately.

### Bounded command polls and independent heartbeats

On 2026-10-11, TOWER-PENGU reproduced audit `b-sections-6-11.md` finding 4 in the `win/fix-command-pagination` worktree with its own `.venv`. Four new Python regressions failed before implementation: a poll returned 80 commands; 80 ASCII dialogue chunks produced 354,086 bytes and the UTF-8 case produced 994,086 bytes; a single oversized status command was accepted. Three new C# regression cases also failed: blocked event and acknowledgment requests delayed heartbeats, and the worker requested another page with only 32 free command slots. The heartbeat defect was starvation behind traffic; idle heartbeats already existed.

The service now returns an ordered prefix bounded by both limits in `data/bridge_limits.json`: 262,144 UTF-8 bytes and 64 commands. C# embeds the same file in `LivingNPC.Core.dll`; both language suites check its values. The byte budget includes JSON escaping, separators, envelope, and cursor. Commands are never split or removed by polling; `after` advances only to the last returned sequence, retries return the same pending page, and acknowledgments retain their existing meaning. Terminal and expired commands are still filtered.

A command that cannot fit alone is rejected with HTTP 413 before enqueueing or allocating its sequence. The poll route also reports 413 if an invalid in-memory mutation makes a queued command oversized, without silently skipping that command. The worker waits for capacity for a full page before polling. Heartbeats run in a separate cancellable worker loop, every three seconds after a successful send, independently of event/ACK traffic and long polls. HTTP work remains off the game tick (SPECS.md sections 6.2, 7.1, 7.3, and 12).

Regression coverage includes complete backlog draining, stable retries, order and sequence preservation, UTF-8 byte counting, an exactly full response at a cursor digit boundary, a one-byte overflow, explicit oversized-command handling, blocked-traffic heartbeats, and adapter queue backpressure. No GTA process, physical audio device, or game installation was used; native lifecycle and performance acceptance remain pending.

The live action-outcome check initially passed its assertions but exited 1 while removing `service.log` (`WinError 32`). It still used plain `TemporaryDirectory`, unlike the other service probes. It now uses `live_cleanup.temporary_directory`, which retries the documented Windows launcher/interpreter shutdown race for up to 15 seconds and still raises if cleanup cannot finish. Its rerun exited 0. Automatic approval review rejected removing the failed run's leftover folder, `C:\Users\Public\Documents\Wondershare\CreatorTemp\lls-action-outcomes-jqywr2eg`; it was left in place. A process check found no Python process for that fixture.

Validation used uv 0.12.17, Python 3.12.14, .NET 10.0.401, and PowerShell 5.1. The pinned SHVDN compile reference was copied from the main clone after verifying SHA-256 `f328a36445c1d3e282e70b2791efc0d516ae3009d0d1461bf076893805a01cd9`; no loader was installed. Process-local settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

| Command | Observed result |
|---|---|
| `uv sync --frozen --group dev --python C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe` | Created this worktree's `.venv` |
| `uv run --frozen pytest -W error tests/test_bridge.py -k 'command_backlog or oversized_single_command' -q` | Before fix: 4 failed; after fix: 4 passed |
| `uv run --frozen pytest -W error tests/test_bridge.py -q` | 74 passed |
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 1,352 passed, 1 skipped in 173.22 seconds; final rerun after the cleanup fix: 1,352 passed, 1 skipped in 171.94 seconds. Skip is opt-in real whisper.cpp. |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 303 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors |
| `uv run --frozen python tests/live_bridge_check.py` | Passed |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed |
| `uv run --frozen python tests/live_bridge_check.py --whisper` | Passed |
| `uv run --frozen python tests/live_bridge_check.py --speech --whisper` | Passed |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed, including durable identity after session close |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: reconnect, withdrawal, stale-action rejection, no invented completion |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed after cleanup fix: 2 outcomes and 2 private memories after restart, late completion rejected |

## Schema-v1 wire-contract Windows validation

Date: 2026-10-11. Machine: TOWER-PENGU. Worktree: `protocol`, branch
`win/fix-protocol`, starting at `74c1d3a` (the validation branch already included
save/profile association and cancellation fixes). Python 3.12.14, uv 0.12.17,
.NET SDK 10.0.401, Windows PowerShell 5.1. This worktree created its own `.venv`
before reproduction. Commands used process-local tool settings:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
uv sync --frozen --group dev --python C:\Users\Brian\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe
```

The existing SHVDN compile reference was copied into this worktree after checking
its repository-pinned SHA-256; no mod was downloaded or installed.

Audit finding 7's assigned wire gaps reproduced before implementation:

- `uv run --frozen pytest -W error tests/test_command_schema.py -q` failed all
  five command kinds because the serialized version was missing; the same
  regression then passed five tests with an explicit producer field.
- Initial spec acceptance tests in `tests/test_wire_contract.py` failed twice:
  section 7.2 omits required protagonist/scene identity and contains unsupported
  witness input; section 7.3 includes correlation fields outside `ActionProposal`.
- Initial negotiation regressions reported four failures and four passes:
  unsupported majors returned 422, future action offers failed before negotiation,
  and the response omitted negotiated feature flags.
- Three initial xUnit regressions failed: missing/null command versions decoded
  successfully, and the HTTP 426 player notice was opaque. A later adapter-offer
  bound test also reproduced an overbroad returned action list before the adapter
  intersection was added.

The [wire contract](wire-contract.md) records implementation details and deliberate
spec-example deviations. The shared corpus contains 28 messages (six valid,
22 invalid), each consumed by pytest and xUnit. Focused corpus checks passed
36 pytest tests and 34 xUnit tests including coverage/provenance guards. These
counts overlap the full suites below and must not be added to their totals.

| Final command | Observed result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error` | 1,411 passed, 1 skipped, 0 failed in 180.66 s |
| `& $env:LLS_DOTNET test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj --configuration Release` | 373 passed, 0 failed, 0 skipped |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Release` | 0 warnings, 0 errors |
| `& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug` | 0 warnings, 0 errors |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: two subtitles, one simulated action, five ACKs |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: two audio/subtitle chunks, 13 ACKs, recording and stop confirmed |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: one pin, eight durable memories, one closed session, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: one withdrawal, reconnect, stale action rejected, started ACK persisted, no invented completion |
| `git diff --check` | Passed |

The one skip is the opt-in installed whisper.cpp check. Live speech uses synthetic
audio; all bridge checks use a fake game. GTA, native task release, real microphones,
and native pin persistence remain unverified. This change does not add durable
manifest/proposal audit records, paginate commands, or redesign auxiliary v1
endpoint bodies. Shared fixture agreement is not exhaustive schema equivalence.

## Crash-check retention correction

On 2026-10-11, `win/fix-session-cleanup` fast-forwarded to `win/validation` at `345457d` before this follow-up. The existing worktree `.venv` and the Windows toolchain settings above were reused. The unmodified `uv run --frozen python tests/live_bridge_crash_check.py` reproduced the reported assertion failure: the private `:started:memory` row was absent after recovery. Extracting that same assertion into a testable helper also produced **2 failed** pytest cases, one after normal close and one after startup recovery.

The old expectation was inconsistent with the actor's retention policy. The crash branch in `adapter/LivingNPC.BridgeProbe/Program.cs` publishes ambient ownership and omits `actor_kind`; `ActorSnapshot` defaults it to `ambient_ephemeral`. Pinning is a separate probe mode. Normal `Repository.end_session` already deletes the ambient actor and cascades its session-scoped memories, including the action's private non-completion memory. Startup recovery correctly applies that same policy.

The spec decision follows [SPECS.md](../references/SPECS.md): **FR-ID-03** and §8.1 limit unpinned ambient memory/identity to one gameplay session; §8.3 requires crash-safe persistence but grants no ambient retention exception; §9.3 allows retention-policy-driven deletion and requires scoped retrieval/provenance. **§11.2** forbids claiming completion without observation, and **§17** requires service-unreachable kill switches to stop pending actions. The durable canonical `action.started` receipt records the incomplete outcome independently of the actor's private memory. It remains available through the existing admin profile export. Old session diagnostics are runtime-only and are not claimed to survive restart.

The corrected check verifies ambient actor/memory scope and the non-completion text **before** the kill, then requires the exact unchanged started receipt, no invented completion, a closed old session, and no old ambient actor/memory/binding or cleanup-pending rows after restart. It also reads the receipt through the real authenticated admin profile-export endpoint. Six pytest regressions cover normal close, startup recovery, retained ambient history, and lost, altered, or invented receipts. README wording now distinguishes the canonical receipt from session-only memory. No service retention logic or adapter code was changed in this follow-up.

Observed follow-up checks on TOWER-PENGU, using process-local `LLS_DOTNET` and `RestoreSources` as above:

| Command | Observed result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error tests/test_bridge_crash_check.py -q` | Before correction: 2 failed; final: 6 passed in 0.78 seconds |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 subtitle chunks, 5 acknowledgments, 1 simulated action |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 acknowledgments, confirmed simulated PTT |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8 durable memories, 1 pre-pin memory retained, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Initially failed at the retained-memory assertion; final passed: 1 withdrawal, reconnect, stale-action rejection, unchanged started receipt visible in profile export, ambient history purged, no completion invented |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed: 2 service outcomes and 2 persistent-actor private memories after restart; late completion rejected; 0 uninvolved outputs |
| `uv run --frozen python tests/live_social_check.py` | Passed: 3 participants, 6 delivered turns, 12 reported memories, 3 opinions |
| `uv run --frozen python tests/live_social_check.py --autonomous` | Passed: 2 participants, 2 automatic turns, 2 reported memories |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed: 2 encounters, 4 service processes; changed topic and scoped erasure, other listener preserved |
| `uv run --frozen python tests/live_skill_check.py` | Passed: 2 versions, 4 primitives, 8 canonical action memories, retained execution after restart |
| `uv run --frozen python tests/live_skill_autonomy_check.py` | Passed: 2 external actions, 2 candidates, 4 practice primitives, retained practice after restart |
| `uv run --frozen python tests/live_neighborhood_check.py` | Passed: 48 actors, 3 game days, 10 advancement calls, 2,361 events, 0 backlog, restart equivalent |
| `uv run --frozen python tests/live_neighborhood_autonomy_check.py` | Passed: 30 actors, 7 game hours, 3 automatic attempts, 120 confirmed events, 0 backlog, restart preserved |
| `uv run --frozen python tools/simulated_load.py` | Passed: 36 actors, 200 confirmed turns, 134 ephemeral output memories removed, 66 persistent output memories retained, 0 network attempts, 62.19 seconds |
| `uv run --frozen pytest -W error` | **1,545 passed, 1 skipped in 186.74 seconds**, exit 0; skip remains opt-in real whisper.cpp |
| `git diff --check` | Passed |

Every final live-check invocation above exited 0 and completed its owned-process and temporary-directory cleanup. The action-outcome check also had an earlier successful invocation before repeating it with the documented .NET environment. The additional autonomous social mode was run because it is a documented live check. These remain fake-game, synthetic-audio, and local HTTP/ASGI validations; native GTA behavior, real audio hardware, and real model providers remain unverified.

## Bounded grounding classifier on Windows

On 2026-10-11, work continued directly on `win/validation` in the `integration`
worktree, starting at `1caa66b`. No branch/worktree was created. The existing QA
service on port 8765 and llama.cpp on port 8091 were left running. The existing
Windows Python 3.12.14/uv environment and the process-local .NET/RestoreSources
settings above were used; no provider/cloud environment setting was changed.

Before production changes, the new runtime regression failed because
`Someone down the block pays for errands.` survived a job request even with an
injected classifier returning `yes`. Seven initial unit cases also failed because
there was no asynchronous second gate, and the provider regression failed because
there was no classifier method. During integration, another failing regression
showed a cancellation-suppressing classifier could return `no` after its timeout;
the final helper checks the timeout scope's expired state. Two social regressions
reproduced loss of the local fallback route across a worker-task boundary; the
selected classifier now travels back with that worker's result.

The fix retains deterministic grounding and adds a broad prefilter followed by an
optional, sequential, 800 ms yes/no model check. It preserves the plain-text
sentence stream and separate action stage required by FR-DLG-02/03, plus the
agent-design story/economy boundaries. Only strict `no` permits a candidate;
malformed/error/timeout/budget-denied results drop it. Fake/disabled providers are
inert by default. Provider tests cover cloud consent, outbound scrubbing, local
payloads, request and cost settlement, background budgets, nested streaming
reservations and fallback routing. Streaming tests cover delivery order, action
input, neutral fallback, cancellation and late results. No adapter source changed.

The holdout remains reporting-only in `tests/fixtures/grounding_holdout.json`;
`grounding_corpus.json` is unchanged. The five verbatim unsafe examples and two
reconstructions are distinguished, and the 16 ordinary controls are explicitly
independent because the user's original ordinary list was not supplied.

`uv run --frozen python tools/benchmark_grounding_model.py` exercised the real
existing `qwen2.5-7b-instruct` alias at `http://127.0.0.1:8091/v1`, with isolated
temporary SQLite budgets. Final run: 12:15:30.611-12:16:07.636 UTC (37.025 s), one
sequential pass without warmup, 671 corpus gate cases and 23 holdout cases.

| Set | Final miss rate | Final false-positive rate | Added gate latency p50 / p95 |
|---|---:|---:|---:|
| Full corpus | 0/179 (0%) | 0/246 (0%) | 92.898 / 105.396 ms |
| Holdout, including two reconstructed blocked examples | 1/7 (14.29%) | 0/16 (0%) | 99.995 / 109.400 ms |
| Verbatim unsafe holdout subset | 1/5 (20%) | Not applicable | Included above |

Before the classifier, the deterministic holdout miss rate was 5/7 (5/5 verbatim).
All corpus adversarial lines already failed the deterministic gate and made no
classifier requests. Corpus ordinary lines were checked with both neutral and
job-request contexts; a unique line would count as a false positive if either
version changed. Added latency subtracts a paired deterministic call; p95 uses
nearest rank. Actual candidate-call p50/p95 was 99.159/108.222 ms on the corpus
and 100.510/109.423 ms on the holdout.

All 367 classifier calls completed without malformed output, timeout or provider
error. The ledger recorded 82,410 input and 734 output tokens, with zero pending
or uncertain reservations. The initial prompt produced 60/246 corpus and 2/16
holdout-control false positives. One corpus-only prompt calibration removed those
false positives; it did not fix the remaining well-formed `no` for the held-out
Michael/therapist rumor. No held-out line was added to the prompt or regression
corpus. Raw initial/final records and hashes remain in Git-ignored
`artifacts/grounding-model-benchmark-initial.json` and
`artifacts/grounding-model-benchmark.json`. See
[dialogue grounding](dialogue-grounding.md#bounded-provider-check) for design,
configuration, denominators and limitations.

Observed automated validation for this change:

| Command | Result |
|---|---|
| `uv run --frozen ruff check service tests tools` | Passed |
| `uv run --frozen pytest -W error tests/test_grounding_classifier.py tests/test_runtime_grounding_classifier.py tests/test_grounding_provider.py -q` | 59 passed in 5.16 s |
| `uv run --frozen pytest -W error tests/test_grounding_social_classifier.py tests/test_social.py tests/test_runtime_grounding_classifier.py -q` | 99 passed in 6.99 s |
| `uv run --frozen pytest -W error` | **2,710 passed, 1 skipped in 230.33 s**, exit 0 |
| `git diff --check` | Passed |

The single skip remains the opt-in real whisper.cpp check. The 61 new regressions
are included in the full-suite total; focused test counts overlap that total.

Isolated live probes (all use a fake game; speech and whisper modes use fixtures):

| Command | Observed result |
|---|---|
| `uv run --frozen python tests/live_bridge_check.py` | Passed: 2 display-safe subtitles, 5 acknowledgments, 1 simulated action, 2 durable delivered events |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: 2 audio/text chunks, 13 acknowledgments, PTT recording/stop confirmed, 2 durable events |
| `uv run --frozen python tests/live_bridge_check.py --whisper` | Passed: 2 subtitles, 5 acknowledgments, typed whisper flag confirmed |
| `uv run --frozen python tests/live_bridge_check.py --speech --whisper` | Passed: 2 audio/text chunks, 13 acknowledgments, PTT and voice whisper flag confirmed |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: 1 pin, 8 durable memories, 1 pre-pin memory retained, post-pin turn delivered, ambient officer purged |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: 1 task withdrawal, reconnect/stale-action rejection, durable started receipt in export, ambient history purged, no invented completion |
| `uv run --frozen python tests/live_action_outcome_check.py` | Passed: 2 service outcomes and 2 private outcome memories after restart, duplicate cancellation suppressed, late completion rejected, 0 uninvolved outputs |
| `uv run --frozen python tests/live_social_check.py` | Passed: 3 participants, 6 delivered turns, 12 reported memories, 3 opinions, 0 model requests |
| `uv run --frozen python tests/live_social_check.py --autonomous` | Passed: 2 participants, 2 delivered turns, 2 reported memories, 2 opinions, 0 model requests |

All nine live invocations exited 0 and completed their owned-service and temporary
directory cleanup. Final Python processes matched the pre-probe baseline, with no
remaining dotnet/fixture process. Listener owners were unchanged: port 8765 PID
47148, port 8091 PID 67548. Neither existing service was stopped or rebound.

These model measurements are standalone checks, not simultaneous live dialogue
generation or native GTA latency. Server contention can produce more timeouts;
inference deadlines do not bound durable cancellation cleanup or OS scheduling.
Real hosted calls, real audio hardware, GTA/native behavior and rig acceptance
remain unverified. No deployment or push was performed.
