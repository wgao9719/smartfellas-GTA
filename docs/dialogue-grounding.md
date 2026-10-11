# Dialogue grounding and dispatch checks

The 2026-10-11 Windows fix addresses audit G2 in `d-agent-design.md` and
High finding 1 in `a-sections-0-5.md`. Before the fix, display sanitization
allowed an invented Lester mission, reward, and discount into dialogue commands.
Python authorized actions when proposed but did not recheck them when polled.
These violate the agent-design conversation/storyline boundaries and
`SPECS.md` FR-DLG-02/03, FR-ACT-02, and sections 10.1–10.3 and 11.

## Speech boundary

`context.py` supplies bounded `speech_constraints`; `providers.py` and the
versioned dialogue policy explain those constraints. Dialogue remains a plain-text
stream. The separate action selector still runs once, after the accepted utterance,
with refreshed eligibility, and sees only the text retained for delivery. The optional
bounded sentence classifier described below also runs before delivery; the dialogue
stream is never converted to JSON or schema-validated midstream.

`grounding.py` applies deterministic English sentence rules after display
sanitization and before subtitles or TTS. Category words alone do not make a
sentence unsafe. Mission/job/objective assignments and economic offers must be
directed at the player; unlocks and service outcomes must concern player access or
service delivery. Story rules require a canonical character's relationship-change
or revelation predicate, not a name mentioned in passing. Completion rules cover
registry actions toward the player, here, or at a requested spot in this interaction.
Personal history, everyday activities, opinions about prices, and neutral greetings
to protagonists pass. Clause-local history, intentions, and denials do not authorize
an unsafe assertion in another clause.

The 2026-10-11 in-game QA exposed an indirect job lead: in response to
`Got any jobs or missions for me? I need cash.`, the NPC suggested the docks or
racetrack and said `Heard they got some work goin' on there.` The previous gate
required explicit assignment/offer language and let both sentences through.
The foreground gate and prompt constraints now use the locked current player
utterance. Requests for jobs, income, or ambiguous help make hiring claims and
indirect referrals to people or places restricted, including sentences that omit
the purpose because an earlier sentence or the player's question supplied it.
Each streamed sentence is checked independently before subtitle or TTS delivery.
Ordinary navigation questions, questions about the NPC's working life, personal
work history, and refusals remain allowed. Social and authored speech retain the
same explicit-claim gate without borrowing an unrelated player's request.

Explicit predicates also cover scores/gigs, free goods, cash handoffs/transfers,
discounts, safehouse and other access claims, and colloquial canonical relationship
changes or reported threats. Neither a rumor nor a quoted character authorizes
one of these claims. The tests preserve the stronger requirement that every
ordinary corpus sentence survive unchanged, even under a job-request context.

These boundaries preserve the ambient background flavor in
`SPECS_agentdesign.md` and the freeform dialogue, personality and believability
goals in `SPECS.md` sections 0.2, 3, and 14.4. Player text, quotations, and proposed
or started actions cannot authorize player/story changes. Unsafe sentences are
removed independently; safe sentences retain their wording. If none remain:

> I'm not sure what to say right now.

Complete sentences are checked before splitting for the display limit, so an
unsafe suffix cannot be hidden beyond a 400- or 500-character prefix. Foreground
streaming stays bounded by the existing reply budget. Social speech and authored
trigger remarks use the same gate before clipping. Social receipts refer to the
filtered words; a changed retelling loses exact-quote metadata, and a wholly
replaced fallback has no retained source carrier.

Kept sentences retain derivation links to every actor-local memory supplied to
the model, including invitation decisions recalled alongside the retold source.
These erasure links do not assert an exact quote or expose the source's rejected
claims to listeners. Under `SPECS.md` §9.3 and §18, forgetting either carrier
blocks pending delivery and scrubs the speaker's derived content after delivery;
listeners keep only their independently delivered, filtered words. The merge
regressions cover both carriers, replay after erasure, and the independent neutral
fallback. The two grounding tests now expect the decision carrier as well as the
retold source, preserving the retention contract without changing the speech gate.

Only a correlated completed adapter receipt can permit one fixed completion line
for an action in this interaction, such as “I've turned to face you.” It must match the current
session, generation, actor incarnation, protagonist, and timeline branch.
Service cancellations/expiry, started receipts, failed actions, unrelated actors,
and narrative memories confer no permission. Completion of one action never
authorizes an unrelated success or an appended reward. Evicted command evidence
is conservatively unavailable for completion speech.

## Measured vocabulary scope

The checked-in `tests/fixtures/grounding_corpus.json` contains 246 ordinary lines
and 179 adversarial lines. It retains all 216 ordinary and 140 adversarial lines
from the previous gate, adds the seven reported held-out claims, both QA lead
sentences, 30 further adversarial paraphrases and 30 neutral ambient lines. It
includes the 12 earlier reported false positives, personal
history, progressive everyday activities, price opinions, neutral named-character
mentions, directed offers/assignments, canonical revelations, and unacknowledged
registry actions. Independent minimal pairs exercise historical versus current
actions, social-media versus physical following, greetings versus revelations,
refusals versus offers, and identical referrals under job-seeking versus ordinary
questions. The corpus marks 12 context-dependent leads with the QA player input;
the remaining adversarial lines must be rejected without that input. All ordinary
lines are checked both with and without the job-seeking input, and count as a false
positive if either version changes them.
Additional independent regressions distinguish negated referrals from a referral
embedded under unrelated uncertainty in the same clause; uncertainty about hiring
does not authorize a suggestion to ask someone else.

| Gate version / corpus | False positives | False negatives |
|---|---|---|
| Original `4fefcd5`, earlier corpus | 114/216 (52.78%) | 25/140 (17.86%) |
| Narrowed predicates, earlier corpus | 0/216 (0.00%) | 0/140 (0.00%) |
| Pre-QA-fix `412fc4b`, expanded corpus | 0/246 (0.00%) | 39/179 (21.79%) |
| Deterministic gate at `1caa66b`, expanded corpus | 0/246 (0.00%) | 0/179 (0.00%) |

Run `uv run --frozen pytest -W error tests/test_grounding_corpus.py -q -s` to
print rates. The aggregate test requires no false negatives and at most 2% false
positives; individual tests additionally require every ordinary line unchanged
and every adversarial line removed. Receipt-backed completion tests remain separate.

The expanded aggregate test failed before production changes with all 39 new
adversarial lines retained. The final focused grounding/corpus run passed 773
tests. That deterministic-only result required no extra provider calls, but did not
generalize to the fresh QA examples. It is not the result for the optional classifier.

Measured on TOWER-PENGU with
`uv run --frozen python tools/benchmark_grounding.py --baseline 412fc4b --iterations 200`:

| Input group | Calls per version | Previous p50 / p95 (ms) | Current p50 / p95 (ms) |
|---|---:|---:|---:|
| Ordinary, neutral context | 49,200 | 0.0254 / 0.0449 | 0.0361 / 0.0617 |
| Ordinary, job-request context | 49,200 | 0.0249 / 0.0413 | 0.0408 / 0.0688 |
| Adversarial, corpus context | 35,800 | 0.0172 / 0.0384 | 0.0220 / 0.0473 |

The ordinary p95 increase was 0.0168 ms without a request and 0.0275 ms with one.
These are warm per-call wall-clock microbenchmarks, including intent detection,
over the fixed corpus; they do not measure model generation or native subtitle/TTS
latency. The baseline ignores player context because that API did not exist.

This is a bounded English vocabulary gate, not a general semantic verifier or a
held-out model benchmark. These measured rates describe this checked-in corpus;
other paraphrases, ambiguous wording, and languages can still produce errors.
The tests do not establish arbitrary-model factuality or native game truth.

## Bounded provider check

`grounded_text_checked` first applies the deterministic gate, then selects surviving
sentences using broad work/economy/access keywords, canonical names with story or
relationship predicates, and police references. Player work, money, mission or help
language selects every surviving sentence, even a sentence with no topic keywords.
The prefilter deliberately includes neutral statements; it does not decide safety.
Unselected sentences incur no model request or asynchronous wait. Exact
receipt-authorized completion lines retain their existing authorization.

The classifier uses the configured provider's action model, or its dialogue model
when no action model is configured. A short independent prompt receives only the
sentence and player utterance as untrusted data, requesting `yes` or `no`. It uses
temperature zero, at most four output tokens, no retries, and an 800 ms deadline.
Only a complete `no` (ignoring case and surrounding whitespace) keeps a candidate.
`yes`, malformed/truncated output, errors, exhausted budgets and timeouts drop it;
an entirely removed reply uses the neutral fallback. A result returned after timeout
cancellation is also rejected. Cancellation joins provider accounting cleanup;
800 ms is the inference deadline, not a hard real-time guarantee for OS scheduling
or durable cleanup.

Foreground checks run sequentially before each sentence's subtitles/TTS, in an
explicit child task that owns its gateway reservation while dialogue streams. Safe
earlier sentences can be delivered before later checks finish. No later sentence
overtakes a pending check, and the separate action stage sees only the retained
complete utterance (FR-DLG-02/03). Cancellation and stale-turn checks still apply.
Social and authored speech use background budgets and no unrelated player input.
All model checks use the existing durable consent gate, outbound cloud scrubber,
concurrency limit, request/token/cost accounting, and usage settlement. Classification
errors never trigger a new fallback-provider attempt. A concurrency limit of one
will refuse candidate checks while foreground dialogue holds its request slot.

The check defaults on for configured real providers. Set
`LLS_GROUNDING_CLASSIFIER=0` before service startup to retain only deterministic
grounding (`Settings.grounding_classifier_enabled=False` in code). Disabled and
fake providers have no default classifier and make no additional calls; tests may
inject a classifier explicitly. No running service configuration was changed for
validation.

The report-only `tests/fixtures/grounding_holdout.json` remains separate from the
regression corpus and is read only by `tools/benchmark_grounding_model.py`. Five
unsafe sentences are verbatim user examples; the two abbreviated already-blocked
examples are explicitly marked reconstructions. The original ordinary list was
not supplied, so the fixture labels its 16 new ordinary controls independently.
All holdout lines use `Know where I can make some quick money?`.

On TOWER-PENGU, 2026-10-11 at 12:15:30 UTC, the final code ran against the existing
llama.cpp server at `http://127.0.0.1:8091/v1`, model alias `qwen2.5-7b-instruct`:

```powershell
uv run --frozen python tools/benchmark_grounding_model.py
```

| Set | Deterministic misses | With classifier misses | With classifier false positives |
|---|---:|---:|---:|
| Full regression corpus | 0/179 (0%) | 0/179 (0%) | 0/246 (0%) |
| Five verbatim fresh unsafe examples | 5/5 (100%) | 1/5 (20%) | See independent controls below |
| Holdout including two reconstructed already-blocked examples | 5/7 (71.43%) | 1/7 (14.29%) | 0/16 independent controls (0%) |

Deterministic false positives were also zero on both sets. Corpus ordinary lines
were run in both neutral and job-request contexts and count once, failing if either
context changes them. Adversarial corpus lines retain their prescribed contexts.
All 179 corpus adversarial lines were already rejected deterministically, so the
zero corpus miss rate does **not** demonstrate model detection of those claims.
The surviving holdout miss is:

> Word is Michael's been seeing a therapist behind everyone's back.

The model returned `no` for it. Fail-closed error handling cannot correct a wrong
well-formed verdict. The initial prompt produced 60/246 corpus false positives and
2/16 independent holdout-control false positives. One prompt revision based only
on corpus errors clarified that a player's money request cannot turn personal
history, ordinary circumstances, refusals or price opinions into an offer. The
holdout was neither added to tests/corpus nor used as prompt examples; its remaining
miss was unchanged. This small reused set is a generalization report, not a
guarantee of arbitrary-paraphrase or multilingual accuracy.

| Final measurement group | Gate cases | Added latency p50 / p95 (ms) |
|---|---:|---:|
| Full corpus (including both ordinary contexts) | 671 | 92.898 / 105.396 |
| Corpus ordinary, job-request context | 246 | 99.525 / 109.222 |
| Entire holdout | 23 | 99.995 / 109.400 |

Added latency is per checked gate call minus a paired deterministic call. This
was one sequential pass without warmup, with nearest-rank p95, in 37.025 seconds.
Unselected and deterministically rejected calls stay in the aggregate; a line can
contain multiple sentences. Model-call latency alone was 99.159/108.222 ms p50/p95
for corpus candidates and 100.510/109.423 ms for holdout candidates. There were 346
corpus classifier calls (all `no`) and 21 holdout calls (17 `no`, 4 `yes`): **zero
malformed responses, timeouts or provider errors**. The isolated ledger recorded
367 requests, 82,410 input tokens and 734 output tokens, with zero pending or
uncertain reservations. No requests used the QA service's database or budgets.

Raw per-case records, fixture/code hashes and timings are in the generated local
`artifacts/grounding-model-benchmark.json`; the initial run remains in
`artifacts/grounding-model-benchmark-initial.json`. Artifacts are Git-ignored;
the fixture, benchmark tool and these results are committed. The benchmark measures
standalone sentence checks, not live generation, TTS or subtitle latency. A model
server busy generating dialogue may queue the check, increasing latency or causing
fail-closed timeouts. No hosted provider or native GTA behavior was measured.

## First in-game conversation: action narration

The first local Qwen2.5-7B conversations exposed a separate speech defect:
`Turn toward the player.` reached subtitles before the ordinary reply. That is
the registry description of `face_player`, copied from dialogue context. The
completion-claim gate did not reject imperative stage directions.

The model's dialogue prompt now excludes eligible-action metadata and raw action
outcomes, retaining the existing speech constraints and receipt-authorized fixed
completion lines. The system prompt and dialogue policy require only spoken
words. The separate action call retains its eligibility information.

`narration.py` rejects every registered action ID and description, plus bounded
English direction patterns and synonyms for turning, following, waiting, and the
other registry actions. Ordinary first-person intentions and history remain
allowed. Square brackets, parentheses and asterisks are treated as nonspoken
markup; their spans are removed, including nested brackets, mixed asterisk widths
and unfinished spans. A streaming filter retains delimiter state across provider
tokens and sentence boundaries so the second sentence of a direction cannot leak.
The shared grounding gate also removes markup from social and authored speech.

`tests/fixtures/action_narration_corpus.json` records the observed local-model
style replies, direction paraphrases and ordinary controls. Registry IDs and
descriptions are additionally enumerated from the current registry in the test.
Paused-provider bridge regressions verify a safe sentence reaches delivery before
the provider finishes, while the action selector sees only the cleaned utterance
and confirmed-delivery memory excludes directions. FR-DLG-02 stays a plain-text
stream with a separate action stage; no midstream schema validation was added.

These are deterministic corpus and synthetic-stream tests. The English direction
vocabulary is bounded, and treats parenthetical or emphasized text conservatively
as nonspoken markup; it is not a semantic guarantee for arbitrary paraphrases or
languages. Repeat the two observed conversations with the local model in GTA to
verify subtitles, speech and action timing. Native behavior remains unverified.

## Action dispatch boundary

Immediately before building a command-poll response, Python rechecks action
schema, policy version, expiry, session/generation/turn epoch, timeline branch,
protagonist, actor incarnation, snapshot freshness, current stored permissions,
role/ownership, capabilities, consent, and skill limits where applicable.
It repeats the check after awaiting outcome persistence. Refused commands become
service cancellations with recorded reasons and are omitted from delivery.
Those records do not claim native completion or withdrawal.

This covers foreground, trigger, and skill action commands through the shared
poll path. The adapter's independent checks remain necessary for changes after
the HTTP response. No native capability was enabled or game installation changed.

## Validation boundary

The [Windows validation record](development-validation.md#dialogue-grounding-on-windows)
records the failing reproductions and final commands. Automated tests use synthetic
providers, adapter acknowledgments, and actor snapshots. The bounded-classifier
benchmark also exercises the real local model. No hosted model, GTA, audio hardware,
or native storyline/service integration was exercised; no rig gate is passed.
