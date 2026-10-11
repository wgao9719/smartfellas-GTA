"""Bounded semantic recall with canonical SQLite rows as the sole source of facts.

Vectors contain a content digest and numbers, never a second copy of memory text.
Every outbound batch and final result rechecks the actor, timeline, and sources.
The bounded candidate window uses the existing lexical/recency/importance ranking.
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime

from . import salience
from .embeddings import OpenAIEmbeddingProvider, normalized_vector
from .outbound import joined_job, raise_if_cancelling
from .storage import _date, _protagonist


def content_digest(memory):
    return hashlib.sha256(
        json.dumps(
            [
                memory[field]
                for field in (
                    "memory_id",
                    "profile_id",
                    "actor_id",
                    "protagonist_id",
                    "branch_id",
                    "game_datetime",
                    "created_at",
                    "text",
                    "source_event_ids",
                    "provenance",
                )
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class MemoryIndex:
    """Async retrieval interface. Provider errors always leave a lexical fallback."""

    def __init__(self, repository, settings, *, gate, transport=None):
        self.repository = repository
        self.config = settings.embedding.model_copy(deep=True)
        self.provider = (
            OpenAIEmbeddingProvider(settings, gate=gate, transport=transport)
            if self.config.provider != "disabled"
            else None
        )
        self.last_status = {"method": "lexical", "reason": "not_queried"}
        # Do not duplicate indexing work for concurrent turns owned by this index.
        self._index_lock = asyncio.Lock()

    def _snapshot(self, scope, query):
        repo = self.repository
        with repo._lock:
            candidates = repo.retrieve_memories(
                *scope[:5],
                query,
                limit=self.config.max_candidates,
                token_budget=2**63 - 1,
                session_id=scope[5],
                # Summaries and their lines compete here. Final selection collapses them.
                collapse_summaries=False,
            )
            valid = []
            for memory in candidates:
                if memory["visibility_scope"] != "private":
                    continue
                try:
                    sources = repo._sources(
                        memory["profile_id"],
                        memory["branch_id"],
                        memory["game_datetime"],
                        memory["source_event_ids"],
                        memory["provenance"],
                        memory["protagonist_id"],
                    )
                    repo._require_observer(memory["actor_id"], sources, memory["provenance"])
                except (ValueError, KeyError, TypeError):
                    continue
                memory["confidence"] = min(
                    memory["confidence"], *(s["confidence"] for s in sources)
                )
                valid.append(memory)
            return valid

    def _cached(self, candidates, dimensions=None):
        result = {}
        with self.repository._lock:
            for memory in candidates:
                row = self.repository.connection.execute(
                    "SELECT vector FROM memory_vectors WHERE memory_id=? AND model=?",
                    (memory["memory_id"], self.provider.model_key),
                ).fetchone()
                if row is None:
                    continue
                try:
                    if len(row["vector"]) > 131072:
                        continue
                    entry = json.loads(row["vector"])
                    if entry["digest"] != content_digest(memory):
                        continue
                    vector = normalized_vector(entry["vector"], dimensions)
                    result[memory["memory_id"]] = vector
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
        return result

    def _store(self, scope, query, candidates, vectors):
        repo = self.repository
        written = 0
        with repo._lock, repo.connection:
            current = {memory["memory_id"]: memory for memory in self._snapshot(scope, query)}
            for memory, vector in zip(candidates, vectors, strict=True):
                fresh = current.get(memory["memory_id"])
                if fresh is None or content_digest(fresh) != content_digest(memory):
                    continue
                envelope = json.dumps(
                    {"digest": content_digest(fresh), "vector": vector},
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                repo.connection.execute(
                    "INSERT INTO memory_vectors(memory_id,model,vector) VALUES(?,?,?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET "
                    "model=excluded.model,vector=excluded.vector",
                    (fresh["memory_id"], self.provider.model_key, envelope),
                )
                written += 1
        return written

    async def _index(self, scope, query, *, budget_class, maximum, dimensions=None):
        written = 0
        attempted = set()
        while len(attempted) < maximum:
            candidates = await joined_job(asyncio.to_thread(self._snapshot, scope, query))
            cached = await joined_job(asyncio.to_thread(self._cached, candidates, dimensions))
            candidates = self._revalidate(scope, candidates)
            missing = [
                memory
                for memory in candidates
                if memory["memory_id"] not in cached and memory["memory_id"] not in attempted
            ]
            if not missing:
                break
            batch, size = [], 0
            # Leave room for JSON escaping, model identifiers, and protocol overhead.
            ceiling = (
                min(
                    self.provider.gate.config.max_prompt_tokens,
                    self.provider.settings.context_byte_budget,
                )
                - 1024
            )
            for memory in missing:
                if len(attempted) >= maximum or len(batch) >= self.config.batch_size:
                    break
                cost = len(json.dumps(memory["text"], ensure_ascii=False).encode()) + 4
                if cost > ceiling:
                    attempted.add(memory["memory_id"])
                    continue
                if size + cost > ceiling:
                    break
                batch.append(memory)
                attempted.add(memory["memory_id"])
                size += cost
            if not batch:
                continue
            vectors = await self.provider.embed(
                [memory["text"] for memory in batch],
                budget_class=budget_class,
                authorize=lambda: len(self._revalidate(scope, batch)) == len(batch),
            )
            if dimensions is not None and any(len(vector) != dimensions for vector in vectors):
                raise ValueError("embedding dimensions changed between requests")
            raise_if_cancelling()
            written += await joined_job(
                asyncio.to_thread(self._store, scope, query, batch, vectors)
            )
        return written

    def _rank(self, scope, query, query_vector, limit, token_budget):
        repo = self.repository
        with repo._lock:
            candidates = self._snapshot(scope, query)
            vectors = self._cached(candidates, len(query_vector)) if query_vector else {}
            ranked = []
            for memory in candidates:
                vector = vectors.get(memory["memory_id"])
                similarity = (
                    max(
                        0,
                        min(1, math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))),
                    )
                    if vector
                    else 0
                )
                if query_vector:
                    age_days = max(
                        0,
                        (
                            datetime.fromisoformat(scope[4])
                            - datetime.fromisoformat(memory["game_datetime"])
                        ).total_seconds()
                        / 86400,
                    )
                    base = (
                        memory["importance"] * 2
                        + memory["confidence"]
                        + math.exp(
                            -age_days
                            / (memory["decay_days"] or salience.decay_days(memory["importance"]))
                        )
                    )
                    # BM25 is corpus-dependent and unbounded. Bound its contribution
                    # when combining it with cosine similarity, which is in [-1, 1].
                    relevance = max(0, memory["retrieval_score"] - base)
                    lexical = 4 * relevance / (1 + relevance)
                    memory["retrieval_score"] = base + lexical + 8 * similarity
                memory["retrieval_method"] = "semantic" if vector else "lexical"
                ranked.append(memory)
            ranked.sort(key=lambda memory: (-memory["retrieval_score"], memory["memory_id"]))
            covers = repo.summary_cover(scope[0], scope[2])
            result, used, seen, covered = [], 0, set(), set()
            for memory in ranked:
                key = " ".join(memory["text"].casefold().split())
                cost = repo.token_cost(memory["text"])
                if key in seen or memory["memory_id"] in covered or used + cost > token_budget:
                    continue
                if len(result) >= limit:
                    break
                result.append(memory)
                seen.add(key)
                covered.update(covers.get(memory["memory_id"], ()))
                used += cost
            return result

    async def retrieve_memories(
        self,
        profile_id,
        branch_id,
        actor_id,
        protagonist_id,
        game_datetime,
        query="",
        limit=8,
        *,
        token_budget=512,
        session_id=None,
        budget_class="foreground",
    ):
        if limit < 0 or token_budget < 0:
            raise ValueError("retrieval limits cannot be negative")
        if limit == 0 or token_budget == 0:
            return []
        if self.config.provider == "disabled":
            self.last_status = {"method": "lexical", "reason": "disabled"}
            return await joined_job(
                asyncio.to_thread(
                    self.repository.retrieve_memories,
                    profile_id,
                    branch_id,
                    actor_id,
                    protagonist_id,
                    game_datetime,
                    query,
                    limit,
                    token_budget=token_budget,
                    session_id=session_id,
                )
            )
        scope = (profile_id, branch_id, actor_id, protagonist_id, _date(game_datetime), session_id)
        query_vector = None
        reason = "disabled" if self.config.provider == "disabled" else "empty_query"
        if self.config.provider != "disabled" and query.strip():
            try:
                candidates = await joined_job(asyncio.to_thread(self._snapshot, scope, query))
                if candidates:
                    query_vector = (
                        await self.provider.embed(
                            [query],
                            budget_class=budget_class,
                            authorize=lambda: bool(self._revalidate(scope, candidates)),
                        )
                    )[0]
                    async with self._index_lock:
                        await self._index(
                            scope,
                            query,
                            budget_class=budget_class,
                            maximum=self.config.max_index_per_query,
                            dimensions=len(query_vector),
                        )
                    reason = None
                else:
                    reason = "no_visible_memories"
            except Exception as exc:
                raise_if_cancelling()
                # Never persist exception messages, endpoints, private text, or response bodies.
                reason = type(exc).__name__
                query_vector = None
        raise_if_cancelling()
        result = await joined_job(
            asyncio.to_thread(self._rank, scope, query, query_vector, limit, token_budget)
        )
        self.last_status = {
            "method": "semantic"
            if any(row["retrieval_method"] == "semantic" for row in result)
            else "lexical",
            "reason": reason,
        }
        return result

    def _clear(self, scope):
        # Deletion is profile/actor scoped, including stale vectors outside today's timeline.
        with self.repository._lock, self.repository.connection:
            return self.repository.connection.execute(
                "DELETE FROM memory_vectors WHERE memory_id IN "
                "(SELECT memory_id FROM memories WHERE profile_id=? AND actor_id=?)",
                (scope[0], scope[2]),
            ).rowcount

    def revalidate_memories(
        self,
        profile_id,
        branch_id,
        actor_id,
        protagonist_id,
        game_datetime,
        query="",
        *,
        memories,
        session_id=None,
    ):
        """Recheck supplied ordered results when a consumer resumes after another await."""
        scope = (profile_id, branch_id, actor_id, protagonist_id, _date(game_datetime), session_id)
        return self._revalidate(scope, memories)

    def _revalidate(self, scope, memories):
        repo = self.repository
        profile, branch, actor, protagonist, stamp, session = scope
        protagonist = _protagonist(protagonist)
        with repo._lock:
            try:
                repo._actor_session(profile, actor, session)
                lineage = repo._lineage(profile, branch, stamp)
            except ValueError:
                return []
            result, seen = [], set()
            for memory in memories:
                fresh = repo.get_memory(profile, memory["memory_id"])
                if (
                    fresh is None
                    or fresh["memory_id"] in seen
                    or fresh["actor_id"] != actor
                    or fresh["protagonist_id"] not in (None, protagonist)
                    or fresh["visibility_scope"] != "private"
                    or fresh["branch_id"] not in lineage
                    or fresh["game_datetime"] > lineage[fresh["branch_id"]]
                    or fresh["session_id"] not in (None, session)
                    or (fresh["expires_at"] and fresh["expires_at"] <= stamp)
                    or content_digest(fresh) != content_digest(memory)
                ):
                    continue
                try:
                    sources = repo._sources(
                        profile,
                        fresh["branch_id"],
                        fresh["game_datetime"],
                        fresh["source_event_ids"],
                        fresh["provenance"],
                        fresh["protagonist_id"],
                    )
                    repo._require_observer(actor, sources, fresh["provenance"])
                except (ValueError, KeyError, TypeError):
                    continue
                fresh["confidence"] = min(
                    fresh["confidence"],
                    *(source["confidence"] for source in sources),
                )
                for field in ("retrieval_score", "retrieval_method", "token_cost"):
                    if field in memory:
                        fresh[field] = memory[field]
                result.append(fresh)
                seen.add(fresh["memory_id"])
            return result

    async def rebuild(
        self,
        profile_id,
        branch_id,
        actor_id,
        protagonist_id,
        game_datetime,
        query="",
        *,
        session_id=None,
        budget_class="background",
    ):
        """Discard actor vectors and rebuild the current bounded authorized window."""
        scope = (profile_id, branch_id, actor_id, protagonist_id, _date(game_datetime), session_id)
        async with self._index_lock:
            cleared = await joined_job(asyncio.to_thread(self._clear, scope))
            written, reason = 0, None
            if self.config.provider != "disabled":
                try:
                    written = await self._index(
                        scope,
                        query,
                        budget_class=budget_class,
                        maximum=self.config.max_candidates,
                    )
                except Exception as exc:
                    raise_if_cancelling()
                    reason = type(exc).__name__
                    current = await joined_job(asyncio.to_thread(self._snapshot, scope, query))
                    written = len(await joined_job(asyncio.to_thread(self._cached, current)))
            else:
                reason = "disabled"
            return {"cleared": cleared, "indexed": written, "reason": reason}
