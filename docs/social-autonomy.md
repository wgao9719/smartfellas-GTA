# Development social scheduler

The development social scheduler starts two-NPC conversations from eligible actor snapshots. The runtime maintenance loop checks opportunities once per second. The scheduler uses the existing social invitation, participation review, and turn paths. It does not call native actions or change relationships.

## Enablement

The scheduler is off by default. It requires all four gates:

- `LLS_RELEASE=0`
- `LLS_DEV_SOCIAL=1`
- `LLS_DEV_SOCIAL_AUTONOMY=1`
- The authenticated session manifest includes `development:social:v1`.

Release adapters do not advertise this capability. The scheduler does not add a bridge capability or enable provider spending. Hosted and local social model calls use the separate Gateway chatter allowance. That allowance defaults to zero and persists independently of the scheduler's counters.

## Eligibility and consent

Both NPCs must have registered persistent or current-session ambient identities, distinct current runtime bindings, and fresh actor snapshots. The player must remain within 20 meters of each NPC. The NPCs must share an area and interior and remain within 10 meters of each other.

World and actor snapshots must be at most two seconds old. Protected game states, danger, foreground conversation, active dialogue commands, and active action commands prevent initiation. The engine rechecks eligibility before and after provider work.

The scheduler favors the least-attempted eligible pair. Within each pair, the actor with fewer initiations becomes the initiator. After two distinct confirmed encounters, that initiator’s own familiarity breaks equal-attempt ties. The familiarity tie-breaker counts at most three encounters. Oldest attempt and stable actor ordering resolve later ties. Attempts apply actor and pair cooldowns, including refused attempts.

Each NPC independently accepts or refuses, including the initiator. Ambient actors decide deterministically from their own state; persistent actors use model decisions. Before its turn, the actor independently reviews whether to continue. Refusal and withdrawal end the scene without assigning another NPC's decision.

## Delivery and hearing

Each turn queues one subtitle. The scheduler then waits for accepted delivery and separate fresh hearing evidence. A subtitle acknowledgment alone does not create listener memory or authorize the next turn.

The development bridge must submit `/v1/bridge/social/delivery` with evidence for the exact delivered quote. The next turn owner must appear in the engine's confirmed listeners. Unknown hearing stops the scene. The scheduler never infers hearing from proximity, subtitle acknowledgment, or generated text.

**Current boundary:** the scheduler can stop while it waits for external hearing evidence. The service does not yet supply native GTA hearing observations. Automatic invitation and generation therefore do not establish unattended native conversations.

## Limits

`Settings.social_scheduler` accepts `SocialSchedulerConfig`. These values are programmatic settings. Only the enablement flags have environment variables.

| Setting | Default | Allowed range | Scope |
|---|---:|---:|---|
| `max_calls_per_minute` | 8 | 4–30 | Each session, rolling 60 seconds |
| `max_calls_per_session` | 24 | 4–100 | Each session |
| `max_scene_attempts` | 4 | 1–16 | Each session |
| `max_concurrent_workers` | 1 | 1–2 | Entire runtime |
| `max_turns_per_scene` | 4 | 2–6 | Each scene |
| `actor_cooldown_seconds` | 60 | 10–600 | Each actor within a session |
| `pair_cooldown_seconds` | 120 | 30–1800 | Each pair within a session |
| `hearing_timeout_seconds` | 8 | 1–30 | Each queued subtitle |
| `scene_timeout_seconds` | 60 | 5–120 | Entire scene, including provider waits |
| `worker_timeout_seconds` | 45 | 1–90 | Each scheduled worker |

For two persistent actors, the first worker reserves four calls: two invitation decisions, one participation review, and one turn. Later workers reserve two calls. Ambient decisions reserve zero calls, so an all-ambient scene reserves one call per line. Mixed scenes count each persistent decision separately. Optional semantic retrieval also consumes the durable chatter budget. Refusal does not refund reservations. These conservative limits supplement Gateway quotas. Scheduler counters are ephemeral and restart with a new session.

The scheduler waits for the final turn's hearing evidence before it completes at the turn limit. Hearing timeout cancels any pending subtitle. Scene and worker time limits cancel and join active provider work. Model and storage cleanup does not acquire the session lock while a caller joins the worker under that lock.

Foreground interruption, actor replacement, protected game state, shutdown, erasure, and provider replacement cancel affected scenes. Erasure joins active storage before removing scheduler state. The scheduler cannot continue against erased actor evidence.

## Diagnostics and verification

`/v1/diagnostics?session_id=...` exposes `social_scheduler`. It contains status, reasons, identifiers, counters, worker activity, limits, and `native_verified=false`. It contains no dialogue text. Cancellation retains `last_wait_reason=waiting_for_hearing` when the scheduler previously waited for hearing.

Run the automatic HTTP simulation:

```sh
uv run --frozen python tests/live_social_check.py --autonomous
```

The probe supplies observations, subtitle acknowledgments, and explicit hearing evidence. It makes no invitation, participation review, or turn requests. Two NPCs exchange two turns. A third NPC receives no reported memories. The probe confirms that acknowledgment alone cannot advance the scene and that the turn limit stops generation.

The probe uses authored fake providers. It makes zero model requests and zero native calls. Its report retains `game_verified=false` and `native_verified=false`. Native hearing and Windows acceptance remain unverified.

## Lasting social adaptation

The scheduler reads retained private social memories only for actors in at least one eligible pair. Geometry and cooldowns decide that before the read, so ticks during cooldowns read nothing. An actor that becomes eligible during a read waits for the next tick. Read failure starts no worker and reserves no model calls.

One read accepts at most 128 owners. Each owner contributes at most 64 decision anchors and 64 hearing anchors from the previous 56 game days. Each owner gets two newest-first index walks, one for decisions and one for hearing, and each stops at its limit. Older history, deleted memories, erasure markers, other actors and profiles, other protagonists, action outcomes, and reports add no walk work. A full window costs about 21,000 SQLite virtual-machine instructions. The read stops after 64,000 instructions per owner and fails closed. Anchors on rewound branches cost about ten instructions each to skip, so several thousand of them newer than an owner's visible anchors can exhaust its budget. Schema version 4 adds the supporting indexes.

Every decision anchor references a valid canonical accept, refuse, continue, or withdraw event. Malformed model output closes the attempt without inventing a lasting preference. A valid refusal or withdrawal pauses that owner’s eligibility for 600 game seconds by default. One to three distinct negative scenes in the preceding game hour multiply that pause. A later valid accept or continue clears it. The configurable base interval is 30–3,600 game seconds, and the total pause never exceeds 3,600 game seconds.

Only confirmed listener hearing establishes directed familiarity with the speaker. Acceptance, generated words, subtitle acknowledgments, and the speaker’s own memories do not establish it. Duplicate receipts and multiple chunks in one scene count as one encounter. Contact weight halves every seven game days. An encounter counts for partner and topic selection only while its weight remains at least 0.5. Familiarity never proves friendship, agreement, resentment, or the truth of quoted words.

After two retained encounters, the speaker’s current partner can receive one exact prior quote as a topic suggestion. The quote is at most 240 characters and belongs to a previous scene. A retelling cannot become a direct shared topic. The context states who said the words and preserves their source. Other actors receive neither that quote nor the listener’s private decision history.

The read respects protagonist identity, save branches, rewind, expiration, corrections, and memory controls. Persistent actors retain eligible history across service restarts. Edited anchors no longer contribute, and an edit that removes the decision or hearing form also frees that anchor's window slot. Deleted memories, deleted source events, and actor resets suppress old adaptation even if the canonical source is replayed. Deleting a memory also removes that character's later lines that repeated or answered it, as described in [Forgetting derived lines](social-development.md#forgetting-derived-lines). The service rechecks live authority and safety after storage work before scheduling.

Run the adaptation HTTP simulation:

```sh
uv run --frozen python tests/live_social_adaptation_check.py
```

The probe uses four isolated service processes and one temporary database. Two confirmed simulated encounters change a later topic after restart. In the third process, another NPC hears that topic line. HTTP deletion of only the first listener's hearing memories also removes both copies of that listener's topic line. The next process has no topic. The other NPC's hearing and the original speaker's own lines remain. The fake provider makes zero model requests. This is development evidence for AUTO-05. Actual GTA behavior and native hearing remain unverified.


## Automatic retelling

The first turn of a new automatic scene may select one retained quote from the speaker's
own memory. Selection examines at most eight source identifiers from a bounded retrieval.
Eligible sources are player words the speaker heard, or a confirmed quote from an NPC
outside the current participants. Whispers and current-scene quotes are excluded.

The existing retelling path rechecks branch, protagonist, session, observer, memory
retention, exact text, and hop limits before generation and delivery. Listeners receive
reported words with lineage only after hearing is confirmed. Hidden source payload fields
never become dialogue. Deleting the source memory prevents selection and derived reuse.
