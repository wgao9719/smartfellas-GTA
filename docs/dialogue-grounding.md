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
with refreshed eligibility, and sees only the text retained for delivery. No model judge or midstream JSON
validation was added.

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
| Current context-aware gate, expanded corpus | 0/246 (0.00%) | 0/179 (0.00%) |

Run `uv run --frozen pytest -W error tests/test_grounding_corpus.py -q -s` to
print rates. The aggregate test requires no false negatives and at most 2% false
positives; individual tests additionally require every ordinary line unchanged
and every adversarial line removed. Receipt-backed completion tests remain separate.

The expanded aggregate test failed before production changes with all 39 new
adversarial lines retained. The final focused grounding/corpus run passed 773
tests. No optional local-model classifier was needed to meet the measured target;
there are no extra provider calls, timeouts, or network waits in the gate.

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
records the failing reproductions and final commands. Tests use synthetic providers,
adapter acknowledgments, and actor snapshots. No real model, GTA, audio hardware,
or native storyline/service integration was exercised; no rig gate is passed.
