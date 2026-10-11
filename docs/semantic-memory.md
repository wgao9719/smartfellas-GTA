# Semantic memory retrieval

Embeddings are optional numerical representations used to rank related memories. The service keeps vectors in its existing SQLite database.
Embeddings start disabled. Disabled mode uses the existing lexical retrieval unchanged.

## Configuration

The `embedding` field belongs to Settings and the saved administrator provider configuration.

| Field | Default | Behavior |
| --- | --- | --- |
| `provider` | `disabled` | Selects `disabled`, `local_openai`, or `openai`. |
| `model` | Empty | Requires an explicit model when enabled. |
| `base_url` | Empty | Uses the corresponding dialogue endpoint when empty. |
| `dimensions` | `null` | Requests and verifies a dimension count when configured. |
| `batch_size` | `8` | Caps the number of documents in each request. |
| `max_candidates` | `512` | Caps the authorized candidate window. |
| `max_index_per_query` | `32` | Caps document embedding work during one retrieval. |

Environment equivalents cover provider, model, endpoint, and dimensions:

```text
LLS_EMBEDDING_PROVIDER=local_openai
LLM_EMBEDDING_MODEL=<model-served-by-your-existing-local-server>
LLS_EMBEDDING_BASE_URL=http://127.0.0.1:1234/v1
LLS_EMBEDDING_DIMENSIONS=<optional-supported-dimension-count>
```

Local endpoints require a literal loopback address. Remote endpoints require HTTPS.
Both modes reject embedded credentials, query strings, and fragments. Disabled configuration can retain either validated endpoint type.
The service does not download models or start an embedding server.

Remote embeddings send query text and authorized memory text to the configured provider.
Remote requests require the existing cloud consent, an environment `LLM_API_KEY`, configured model pricing, and available request budgets.
Saving changed provider configuration revokes cloud consent. The embedding configuration participates in the consent revision.
Local requests never receive the hosted API key. The client disables redirects and environment proxies.

Each embedding request uses the existing `OutboundGate`. Query requests and document batches each consume one request allowance.
Foreground dialogue uses the foreground budget. Social retrieval and explicit rebuilds use the background budget.
The default background allowance is zero. Missing permission, exhausted budgets, unavailable providers, and malformed responses activate lexical fallback.
Embedding failures do not select a different remote endpoint or download a local model.

The gateway reserves the configured input ceiling and zero output tokens. Valid provider usage can settle that reservation.
Uncertain or failed requests retain their conservative reservation. Input and response limits use `context_byte_budget` and `model_response_bytes`.
The service closes each HTTP client after its request. Consent revocation and provider replacement cancel requests through the shared gateway.

## Retrieval and erasure

The candidate window uses existing lexical relevance, importance, confidence, and recency ranking.
Semantic ranking operates within that bounded window. It does not promise exhaustive recall across every stored memory.
The service combines bounded lexical relevance with cosine similarity, importance, confidence, and decay.
Decay follows each memory's importance. Final selection hides lines covered by a selected episode summary.
See [Memory importance and episode summaries](memory-consolidation.md).
Existing duplicate suppression and memory token limits still apply.

The index checks profile, actor, protagonist, branch ancestry, game time, expiry, session, private visibility, and canonical source events.
It checks source observation and provenance before sending document text.
It rechecks batches after cache lookup and after gateway admission. It rechecks canonical rows before saving vectors or returning memories.
Consumers revalidate fetched memories before building their prompts. Cancellation joins pending database work before shutdown continues.

Each vector row contains its memory ID, provider configuration hash, text/source digest, and numerical vector.
It contains no second copy of the memory text. Model, endpoint, provider, and requested dimensions contribute to the configuration hash.
Edits invalidate cached vectors through the content digest. Changed configuration cannot reuse vectors from another configuration.
Invalid vectors trigger reindexing or lexical fallback.

Existing foreign keys remove vectors when memories disappear. Source deletion, actor reset, profile deletion, and ephemeral-session closure retain their existing deletion behavior.
Deleting a memory also deletes its summaries and the same actor's later social lines derived from it, with their vectors. See [Forgetting derived lines](social-development.md#forgetting-derived-lines).
An in-flight embedding response cannot recreate a deleted memory or its vector.

`MemoryIndex.rebuild(...)` clears the selected actor's vectors, then rebuilds its current authorized candidate window.
It returns counts and a content-free failure reason. Rebuilds require background allowance unless the caller explicitly chooses another budget class.
Queries are not cached. Unchanged document vectors are reused after validation, including after database reopen.

## Validation boundary

Run the isolated checks:

```sh
.venv/bin/python -m pytest tests/test_embeddings.py tests/test_memory_index.py tests/test_runtime_memory_index.py tests/test_network_consent.py -q -W error
```

The fixed evaluation uses all 50 questions in `data/memory_regression.json` after database reopen and ten simulated days.
The same questions run with synthetic semantic vectors and with an unavailable embedding provider.
Both modes preserve misinformation qualifications and reject the wrong actor or protagonist.
Ten additional paraphrases exercise the semantic ranking path.

The HTTP fixture supplies deterministic topic vectors. These results establish retrieval plumbing and safety boundaries, not real-provider recall quality.
Separate tests cover deletion during embedding, changed text, malformed vectors, cache invalidation, rebuild, response limits, and cancellation.
The network-consent subprocess retains the real HTTP transport and blocks actual connections through resolver and socket hooks.
It verifies zero DNS/socket attempts before consent and after revocation. Explicit consent reaches the blocked hooks.

No paid provider call or model download occurred during these checks. Real-provider semantic quality and Windows execution remain unverified.
These checks do not establish native GTA behavior or hardware performance.
