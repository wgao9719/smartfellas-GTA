# Memory importance and episode summaries

Every memory receives an importance score when the service writes it. Importance sets how long a memory stays prominent in retrieval.
Optional episode summaries condense a persistent character's firsthand lines into one memory. Summaries stay linked to their source lines.

## Importance

The service scores a memory from the event types of its sources. It never reads the memory's words to score it.
Player speech is untrusted. Saying "remember this forever" cannot raise importance.

| Source event type | Importance |
| --- | --- |
| `actor.injured` | 0.9 |
| `player.weapon_drawn` | 0.8 |
| `vehicle.collision` | 0.6 |
| `player.utterance_text`, `social.accept`, `social.refuse` | 0.4 |
| `action.completed`, `social.quote_delivered` | 0.35 |
| `dialogue.delivered`, `social.withdraw` | 0.3 |
| Other `action.*`, `social.continue`, `player.spoke`, `actor.spoke` | 0.25 |
| `actor.greeted` | 0.2 |
| `world.weather_changed` | 0.1 |
| Any other type | 0.5 |

A memory with several sources takes the highest score. Each identical earlier line from the same actor in the current timeline subtracts 0.1, up to 0.3.
Character packs and dashboard edits can still set importance explicitly.

Ranking keeps its existing terms. The decay constant now follows importance when a memory has no explicit `decay_days`:

```text
decay_days = 30 × 8^(2 × importance − 1)
```

Neutral memories keep the previous 30-day constant. A greeting fades over about 9 game days. An injury stays prominent for about 158 game days.
Existing rows keep their stored importance and gain the scaled decay at read time.

## Episode summaries

Summaries are off by default. Set `LLS_MEMORY_SUMMARIES=1` to enable them.
Remote and local OpenAI-compatible providers also need a background allowance. The default background allowance is zero. Hosted providers additionally require cloud consent and configured prices.
The fake provider writes visibly labeled `Simulated summary:` text. The disabled provider writes nothing.

### What qualifies

- **Actors:** curated, spawned persistent, and anchored service characters. Ambient pedestrians' memories end with their session.
- **Lines:** `heard`, `directly_observed`, and `system_fact` memories of the current protagonist, visible in the current save timeline, from the last 14 game days.
- **Hearsay:** `reported` and `unverified` lines are never summarized. They keep their exact words and teller.
- **Episodes:** one actor's lines with no gap longer than 30 game minutes. An episode is ready 30 game minutes after its last line if it holds at least three lines. Twelve lines are summarized without waiting.

### Worker

`MemoryConsolidator` polls each session every 15 seconds. It runs one request at a time per session.
It does not start while the session has a foreground turn, speech, a social or skill task, or undelivered dialogue or actions. Session cancellation stops it.
An unavailable provider or failed request delays the next attempt by 120 seconds. Pending lines stay in storage, so work interrupted by shutdown resumes in a later session.

The model receives the character's name, at most 1,000 characters of biography, the protagonist, a rule-based importance, and the numbered lines with their times and provenance.
It returns JSON with `text`, at most 600 characters, and `importance`. Duplicate keys, extra fields, out-of-range values, and incomplete replies are rejected.
The stored importance stays within 0.25 of the highest line score.

### Storage

A summary is an ordinary private memory with these properties:

- It cites every source event of its lines.
- It keeps the weakest provenance and lowest confidence among its lines.
- It uses the game time of its newest line.
- `summary_sources` links it to each line.

Storage rejects a summary when a line changed or disappeared during generation. It also rejects lines that a visible summary already covers.

### Retrieval

A selected summary hides the lines it covers. A line that ranks above its summary still appears beside it.
Semantic retrieval keeps summaries and lines together in its candidate window and collapses them at final selection.

### Erasure and timelines

Deleting a line or a source event deletes every summary that used it. Editing a line's text or confidence also deletes those summaries, so no stale paraphrase survives a correction.
Deleting only a summary leaves its lines, which become eligible again. Derived-line erasure never starts from a summary, even though the summary cites the same events.
A summary belongs to its save branch. Loading a save from before its newest line hides it, and the visible lines become eligible again on the new branch.

## Validation boundary

```sh
.venv/bin/python -m pytest tests/test_memory_consolidation.py -q -W error
```

The tests cover:

- Importance and repeat scoring, and decay wiring.
- Episode selection, provenance and confidence rules.
- Collapsed retrieval, erasure, correction, derived lines, and save branches.
- Worker scheduling, backoff, cancellation, and changes during generation.
- Provider request shape, budget denial, and invalid replies.
- One runtime pass through the gateway with the fake provider.

No real-provider call occurred. Summary faithfulness is unmeasured. Storage enforces provenance and sources, but nothing checks that the paraphrase omits no qualification.
Windows execution and native GTA behavior remain unverified.
