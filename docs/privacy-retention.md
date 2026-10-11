# Conversation erasure and raw-history retention

The policy implements `SPECS.md` §9.3 (Forget), §17 (short local retention),
§18 (character/profile deletion and export), and §14.1 (sanitized traces).
It does not establish native GTA or real microphone acceptance.

## Forget and export

Deleting a memory removes its actor's memories of the same source, summaries,
and transitively derived lines, including vectors and FTS entries. Raw source
payloads and derived snippets are scrubbed from canonical events, owned knowledge
cards, and sourced skill records. Content-free provenance envelopes remain so
other actors' independently retained memories and relationship facts still validate.
Social hearing has separate actor-owned sources: forgetting the speaker does not
erase listeners' independently heard experiences or their copies of the words.
A whole-profile export can therefore still contain another character's own memory.

Foreground replies and social lines record identifiers for sources included in
the final bounded prompt. These links conservatively treat recalled context as a
possible derivation; they do not claim the model actually used every recalled fact.
Sources trimmed out of the prompt receive no link. Admin erasure cancels and joins
active profile work and clears cached dialogue and traces before storage deletion.
Scrub audits retain operation names, identifiers, and timestamps, never deleted text.
Retries of scrubbed source IDs cannot restore raw content or recreate projections.

Authenticated, same-origin admin routes include:

- `GET /v1/admin/profiles/{profile_id}/actors/{actor_id}/export`: only the selected
  actor's retained profile, memories, owned knowledge, relationships, skill records,
  referenced skill definitions, and observed/referenced events across branches.
  The dashboard's **Show character export** control uses this route.
- `POST /v1/admin/profiles/{profile_id}/actors/{actor_id}/reset`: confirm with
  `{"confirm_actor_id":"..."}`. Remove acquired character state and raw observations;
  preserve the authored persona and other characters' independently retained state.
- `DELETE /v1/admin/profiles/{profile_id}/actors/{actor_id}/conversations/{event_id}`:
  confirm both `confirm_actor_id` and `confirm_event_id`. Erase the selected source
  and its actor-local derivations, including raw sources with no memory projection.
  This source-scoped operation is available through the API.
- Existing whole-profile export/deletion and pinned-pedestrian deletion remain available.

## Retention policy

`LLS_RAW_EVENT_RETENTION_DAYS` sets the raw transcript/turn-event window to an integer
from 1 through 365 days; the default is **7 days**. Runtime maintenance checks at
startup's first maintenance pass and then every 60 seconds. Age uses UTC collection
time, not fictional game time. Cleanup also expires old terminal command text,
player speech, delivered utterance caches, and speech receipts in long-lived sessions.

Expired event payloads lose raw text and arbitrary nested content. Identifier,
provenance, visibility, and validated social-decision metadata remain. Durable
memories (which may themselves quote dialogue), knowledge, relationship facts,
and their indexes are retained until explicit deletion or another defined policy.
Neighborhood events encode durable simulation state and are outside this raw-history
policy. The service does not keep raw microphone recordings by default; temporary
capture/playback cleanup is unchanged.

Schema **v6** adds indexed UTC collection timestamps after the unchanged **v4 → v5**
session-cleanup migration. Existing databases advance **v5 → v6**; fresh databases
create both tables in one transaction and write v6 directly. Earlier databases lack
collection times, so existing events receive
a fresh retention window at migration. Reopening does not restart that window.
Earlier releases did not record all general-recall dependencies; missing historical
links cannot be reconstructed. Reset the character or delete the profile to erase
historical paraphrases whose derivations were never recorded.

The live SQLite database is selected by `LLS_DATABASE`. Dashboard backups are separate
copies in its sibling `backups` directory. Previously downloaded exports, backup
files, and manually prepared replay bundles are independent copies; live erasure
and retention do not rewrite them. This is logical application erasure, not a promise
of forensic removal from storage media or external providers.

## Windows validation

Regression tests reproduce source-text export leakage, general-recall paraphrases,
stale cached hearing, absent retention, and missing actor export before their fixes.
Coverage includes source replay suppression, gaps in derivation chains, actor/profile
isolation, clock boundaries, durable relationship preservation, and schema v1/v2/v3/v4/v5
upgrades. Genuine older-schema fixtures check preserved data and cleanup intent;
a SQLite trace verifies direct fresh-v6 creation, and v7 is refused. Transient
retention database errors retry without stopping pending session cleanup.
See [Development validation](development-validation.md) for the final run.
