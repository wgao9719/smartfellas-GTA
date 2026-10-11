# Conversation attribution and contextual leads: Windows re-verification fixes

Work stayed in the integration worktree on `win/validation`, on TOWER-PENGU,
2026-10-11. The evidence is the main clone's
`artifacts/rig/ingame-reverify.md` and `evidence/qa2-*.json`. Both reported
defects reproduced before their fixes. No deployment or native GTA verification
occurred, and no release gate changed. The existing QA service on 8765 and local
llama server on 8091 were left running.

Final verdict: **attribution passed this 10-exchange sample; strict no-lead
behavior still fails (4/30 conservatively scored responses)**. Automated code
regressions cover the two exact reported leaks; broader local-model reliability
remains limited as detailed below.

## Reproduction and changes

The new provider-structure test initially failed: the actual dialogue request
contained only `system,user`, with the recent conversation inside JSON. The
provider now starts with the NPC's own identity and the named protagonist, then
supplies chronological named player `user` turns and delivered NPC `assistant`
turns. Recent transcript text is absent from the context blob; provenance metadata
remains there. Retrieved player memories render as `Franklin told you: ...`.
Storage, source links, actor/profile/protagonist/branch isolation, erasure and
cloud scrubbing remain in place. Context fitting checks both the internal context
and rendered message content, including dynamic identity text; the gateway still
accounts for the entire request.

`tests/test_grounding_window.py` initially reported **4 failed, 1 passed**:
the busy-workplace/look-there pair and an orphan invitation survived, while
previous-sentence context and verdict diagnostics were missing. The gate now
passes the player's question and the last sentence accepted for delivery to the
classifier, bounded to 512 characters each. Rejected sentences never become the
previous sentence. Deterministic rules also cover contextual busy-workplace claims
and pointer invitations. The previous sentence is queued speech, not an assertion
that an adapter has already acknowledged it. The unchanged regression corpus and
separate reporting holdout are measured independently; the two exact QA cases
were added only to the holdout.

The admin regression initially failed with missing
`recent_conversation_memory_ids`. Administrative diagnostics now expose those IDs,
the existing refusal marker, and per-sentence `grounding_verdicts`. Each verdict
contains its index, rule/classifier/fallback source, allowed flag, fixed reason
and whether previous-sentence context was used. No candidate or question text is
added to diagnostics.

These changes implement `SPECS.md` FR-MEM-02/04, section 9.3, FR-DLG-02/03 and
sections 10.1-10.3, plus the agent-design conversation/storyline boundaries
identified by audit G2 and sections 0-5 High finding 1.

## Real-model method and initial result

`tools/benchmark_service_model.py` uses the real in-process HTTP service, provider
gateway, sentence gate, command polling, delivery acknowledgments and durable
memory. Each replicate uses a fresh temporary database and a fake ambient
`Nearby resident`, with Franklin as protagonist. No listening socket is opened.
The model is the already-running `qwen2.5-7b-instruct` at
`http://127.0.0.1:8091/v1`; default dialogue sampling is unchanged. Each attribution
replicate uses the exact introduction, name question and work question on the same
actor. Each of the three exact QA job prompts runs ten times independently.
Semantic review checks delivered answers, not keyword presence in generated text.

The first 60-turn measurement exposed single-slot contention: **1/10** exchanges
delivered both correct recalls, although none adopted Franklin's identity or job.
Ten attribution sentences and twenty job-response sentences were dropped after
classifier timeouts. **3/30** job responses still delivered leads: job postings,
selling unwanted items, and an orphan invitation to hear more. The initial raw
record and per-case review remain in
`artifacts/service-model-benchmark-qa2.json`.

The local server's read-only `/props` response reported one inference slot.
The classifier could queue behind ongoing dialogue generation longer than its
800 ms deadline. A mocked occupied-slot regression reproduced that loss before
the correction. Local classification now has a three-second allowance, capped by
`model_timeout_seconds`; remote checks keep 800 ms. Both the provider and outer
gate use the same bound. The 30-second foreground-turn cap, strict verdicts,
fail-closed errors, one attempt, cancellation cleanup and request budgets remain.
This changes latency tolerance, not which claims are permitted.

The intermediate repeat eliminated all classifier timeouts, but only **4/10**
exchanges delivered both recalls: all ten generated correct attribution, while
the classifier incorrectly removed six occupation answers. Its strict contextual
lead count was **5/30**. That evidence remains in
`artifacts/service-model-benchmark-qa2-local3s.json`. One general prompt correction
clarified that either speaker's existing identity and occupation are allowed,
including the player's stated work, while new income/work opportunities remain
restricted. An unrelated gardener-recall provider-contract regression failed
before this correction and passed after it. No new lead patterns were added from
the measured output, and no holdout was moved into the regression corpus.

## Final service-path measurement

The final run delivered both correct answers in **10/10** attribution exchanges:
name **10/10**, car-fixing occupation **10/10**, and no NPC self-attribution in any
exchange. All 60 turns generated successfully, with zero classifier timeouts,
provider errors or malformed verdicts. Nine job responses used the neutral
fallback because all their sentences were filtered. The final artifact is
`artifacts/service-model-benchmark-qa2-final.json`, including generated text,
delivered chunks, text-free traces, source hashes and per-case semantic review.

The strict contextual job-lead result remains **4/30 (13.33%)**, counting two
clear indirect leads and two ambiguous orphan follow-ups conservatively as
failures. The clearer lead rate alone is **2/30 (6.67%)**. Per prompt, strict counts
are jobs/missions **1/10**, quick money **2/10**, and hiring **1/10**:

| Case | Delivered text relevant to failure | Interpretation |
|---|---|---|
| Jobs/missions, repeat 10 | `Mind if I take a look?` | Ambiguous orphan assistance after the construction-site offer was removed. |
| Quick money, repeat 6 | `What do you think?` | Ambiguous orphan follow-up after the suggested activities were removed. |
| Quick money, repeat 9 | `People often sell off things they no longer need.` | Indirect income activity in the money-request context. |
| Hiring, repeat 7 | `Hmm, not around me. Usually it's the local cafe or the construction site down by the river. But they're not advertising anything right now.` | Places offered as usual hiring sources despite the closing disclaimer. |

These are well-formed classifier `no` mistakes, not errors that fail-closed
handling can resolve. The two exact reported QA replies are blocked, but this
small model still does not reliably reject every paraphrase. The final sample is
reported without adding its new phrases to rules or prompt examples.

| Service group | Turns | Generation + grounding p50 / p95 | Through fake delivery ACK p50 / p95 |
|---|---:|---:|---:|
| Attribution | 30 | 2,398.121 / 4,686.090 ms | 2,411.043 / 4,704.591 ms |
| Jobs/missions | 10 | 2,481.618 / 3,131.153 ms | 2,493.562 / 3,145.939 ms |
| Quick money | 10 | 2,858.538 / 3,216.420 ms | 2,869.009 / 3,226.896 ms |
| Hiring | 10 | 2,202.988 / 2,766.206 ms | 2,217.309 / 2,785.769 ms |

These are whole-turn service times, not time to first subtitle, physical audio
latency, or game frame time. Fake ACKs occur after generation finishes. The
three successive samples diagnose implementation failures; they are not a
controlled before/after estimate because sampling and prompts differ. No hosted
model or native game capability was tested.

## Final corpus and holdout measurement

The corpus remains 179 adversarial and 246 ordinary lines. Ordinary corpus lines
run in both neutral and job-request contexts and count once, failing if either
context changes them. The separate holdout has nine adversarial cases and 16
ordinary controls. One sequential pass uses the real local classifier with an
isolated budget ledger. The gate first applies rules; all 179 corpus adversarial
lines already fail those rules, so their zero miss rate does not establish model
generalization.

| Set | Rules-only misses / false positives | With real classifier misses / false positives | Added latency p50 / p95 |
|---|---|---|---:|
| Corpus, 671 gate calls | 0/179 / 0/246 | **0/179 / 3/246 (1.22%)** | 127.003 / 168.105 ms |
| Holdout, 25 gate calls | 5/9 / 0/16 | **1/9 (11.11%) / 0/16** | 136.496 / 144.586 ms |
| Two exact QA additions, included above | 0/2 / no controls | **0/2 / no controls** | Rules block all three sentences; no model calls. |

The remaining holdout miss is `Word is Michael's been seeing a therapist behind
everyone's back.` The three ordinary corpus false positives, all under a job
request, are `I have a new job at the bakery.`, `My aunt offered me a job years
ago.`, and `Work is hard to find when the weather gets like this.` They were not
tuned away. The holdout includes previously known user-reported cases and is not
an unseen generalization estimate.

Added latency is each checked gate call's wall time minus a paired rules-only
call, including unselected and rule-rejected cases; p95 uses nearest rank. The
ordinary corpus job-request subset adds 145.767 / 173.840 ms p50 / p95. There were
367 real classifier calls (346 corpus, 21 holdout), seven `yes`, 360 `no`, and
zero malformed outputs, timeouts or provider errors. The temporary ledger ended
with zero pending or uncertain requests, 118,757 reported input tokens and 734
output tokens. The existing QA ledger was not used.

The final service run was 13:28:11-13:30:54 UTC and the corpus run
13:31:06-13:32:00 UTC. Source hashes in both artifacts match the final code.
The artifact `artifacts/grounding-model-benchmark-qa2-final.json` contains every
case, verdict source, timing and source hash. Generated artifacts stay Git-ignored;
the reproducible tools, fixtures and this measurement record are committed.

## Validation method

All commands run from the integration worktree with user-level tools:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_DOTNET = 'C:\Users\Brian\Documents\Github\smartfellas-GTA\.dotnet\dotnet.exe'
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'
```

Live fixtures use owned temporary services on allocated loopback ports and clean
them up. Simulated load forbids network access. No adapter source changed;
standalone C# tests and GameAdapter builds are outside this change's required
validation. The live probes exercise the actual C# worker, with fake game state.
Model measurements run before the heavy validation jobs to avoid that contention.

An existing semantic-retrieval HTTP mock assumed the final user message was JSON.
Its two variants failed after the new chat structure; the fixture now reads the
context metadata message. Its retrieval, isolation, erasure and budget assertions
remain unchanged. The focused rerun passed all 13 tests.

| Command | Observed result |
|---|---|
| `uv run --frozen ruff check service tests tools` | All checks passed. |
| `uv run --frozen pytest -W error` | **2,736 passed, 1 skipped in 234.27s**, exit 0. The skip is opt-in real whisper.cpp. |
| `uv run --frozen python tools/benchmark_service_model.py --output artifacts/service-model-benchmark-qa2-final.json` | Default ten replicates: 40 isolated cases, 60 generated turns; semantic rates and timings above. |
| `uv run --frozen python tools/benchmark_grounding_model.py --output artifacts/grounding-model-benchmark-qa2-final.json` | 696 gate cases, 367 real classifier calls; rates above. |
| `uv run --frozen python tests/live_bridge_check.py` | Passed: two display-safe subtitles, final marker ACK, five ACKs, two durable delivery events. |
| `uv run --frozen python tests/live_bridge_check.py --speech` | Passed: synthetic PTT recording/stop, two audio/text chunks, 13 ACKs, two durable delivery events. |
| `uv run --frozen python tests/live_bridge_check.py --pin` | Passed: one pin, post-pin dialogue, eight durable memories including pre-pin input, ambient officer purged. |
| `uv run --frozen python tests/live_bridge_crash_check.py` | Passed: reconnect, one task withdrawal, stale-action rejection, durable started receipt exported, no invented completion, ambient history purged. |
| `uv run --frozen python tests/live_social_check.py` | Passed: three participants, six delivered turns, 12 reported memories. |
| `uv run --frozen python tests/live_social_check.py --autonomous` | Passed: two participants, two automatically initiated/delivered turns, two reported memories. |
| `uv run --frozen python tests/live_social_adaptation_check.py` | Passed: two confirmed encounters across four temporary service processes; two derived speaker lines erased, independent hearing preserved. |
| `uv run --frozen python tools/simulated_load.py` | Passed: 36 NPCs, 200 turns, 200 confirmed deliveries/completions and isolation checks; zero network attempts or uninvolved-actor outputs, bounded queues, maximum context 4,596 bytes, 66 persistent output memories retained and 134 ephemeral ones removed. 62.70s total; synthetic turn p50/p95 48.63/55.35 ms. |

The final live logs are in `artifacts/qa2-live-*.txt`. These probes use simulated
game state; their success does not replace another in-game re-verification.
Standalone load output is `artifacts/qa2-simulated-load.txt`; its elapsed time
includes 52.78 seconds of actual HTTP rate-limit waits. All owned probe processes
exited. The existing QA and model services remained healthy and listening.
