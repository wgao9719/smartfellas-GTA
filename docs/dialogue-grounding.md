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
sanitization and before subtitles or TTS. The restricted vocabulary covers mission
and objective assignments, payments/rewards/discounts/prices, unlocks, canonical
relationship changes, plot revelations, service outcomes, and physical completion
claims. Player input, memories, quoted speech, and proposed or started actions
cannot authorize those claims. Unsafe sentences are removed independently; safe
sentences retain their wording. If none remain, the line is:

> I'm not sure what to say right now.

Complete sentences are checked before splitting for the display limit, so an
unsafe suffix cannot be hidden beyond a 400- or 500-character prefix. Foreground
streaming stays bounded by the existing reply budget. Social speech and authored
trigger remarks use the same gate before clipping. Social receipts refer to the
filtered words; a changed retelling loses exact-quote metadata, and a wholly
replaced fallback has no retained source carrier.

Only a correlated completed adapter receipt can permit one fixed completion line
for that action, such as “I've turned to face you.” It must match the current
session, generation, actor incarnation, protagonist, and timeline branch.
Service cancellations/expiry, started receipts, failed actions, unrelated actors,
and narrative memories confer no permission. Completion of one action never
authorizes an unrelated success or an appended reward. Evicted command evidence
is conservatively unavailable for completion speech.

These are conservative vocabulary rules, not a general semantic verifier.
Unsupported paraphrases or other languages can escape vocabulary matching, and
some harmless sentences mentioning restricted domains can be refused. The tests
establish the listed cases; they do not establish arbitrary-model factuality or
complete language coverage. Ordinary small talk and action intentions are covered
by negative regressions, including “I work at the docks”, “Nice weather today”,
“I already ate”, “I finished breakfast”, and “I'll follow you.”

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
