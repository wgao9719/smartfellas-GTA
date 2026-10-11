"""Storage acceptance tests exercise reopened databases and timeline branches."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from service.lls.storage import Repository, TimelineAmbiguityError

T0 = "2025-01-01T09:00:00Z"
T1 = "2025-01-01T10:00:00Z"
T2 = "2025-01-01T11:00:00Z"
T3 = "2025-01-01T12:00:00Z"
T4 = "2025-01-01T13:00:00Z"


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "memory.sqlite3") as repository:
        repository.register_actor("save-a", {"actor_id": "avery", "display_name": "Avery"})
        repository.register_actor("save-a", {"actor_id": "bea", "display_name": "Bea"})
        yield repository


def event(
    repo,
    branch,
    event_id="event-1",
    at=T1,
    protagonist="franklin",
    provenance="directly_observed",
    event_type="conversation_completed",
    profile="save-a",
    observers=None,
):
    data = {
        "event_id": event_id,
        "game_datetime": at,
        "protagonist_id": protagonist,
        "event_type": event_type,
        "payload": {"text": "We discussed the broken bicycle."},
        "provenance": provenance,
        "confidence": 0.9,
        "observer_ids": observers or ["avery"],
    }
    repo.record_event(profile, branch, data)
    return data


def memory(
    repo,
    branch,
    event_id="event-1",
    at=T1,
    text="I discussed the broken bicycle with Franklin.",
    actor="avery",
    profile="save-a",
    protagonist="franklin",
    **kwargs,
):
    return repo.add_memory(
        profile, branch, actor, protagonist, at, text, [event_id], "directly_observed", **kwargs
    )


def recall(repo, branch, at=T4, actor="avery", protagonist="franklin", profile="save-a", **kwargs):
    return repo.retrieve_memories(profile, branch, actor, protagonist, at, "bicycle", **kwargs)


def test_reopen_persists_confirmed_writes_wal_fts_and_backup(tmp_path):
    path = tmp_path / "live.sqlite3"
    with Repository(path) as repo:
        assert repo.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        repo.register_actor("save-a", {"actor_id": "avery", "display_name": "Avery"})
        branch = repo.open_timeline("save-a", T0)
        event(repo, branch)
        saved = memory(repo, branch)
        repo.backup(tmp_path / "backup.sqlite3")
    for location in [path, tmp_path / "backup.sqlite3"]:
        with Repository(location) as reopened:
            assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 3
            assert recall(reopened, branch)[0]["memory_id"] == saved["memory_id"]
            assert reopened.get_actor("save-a", "avery")["display_name"] == "Avery"
            assert reopened.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_wal_recovers_committed_memory_after_abrupt_process_exit(tmp_path):
    location = tmp_path / "crash.sqlite3"
    with Repository(location) as repository:
        repository.register_actor("save-a", {"id": "avery"})
        branch = repository.open_timeline("save-a", T0)
        event(repository, branch)
        saved = memory(repository, branch)
    # Leave a real child process transaction uncommitted, without closing SQLite.
    # Recovery must preserve the earlier confirmed write and its FTS terms.
    script = """
import os, sys
from service.lls.storage import Repository
repo = Repository(sys.argv[1])
repo.connection.execute("BEGIN IMMEDIATE")
repo.connection.execute("UPDATE memories SET text='uncommitted scooter'")
os._exit(0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(location)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    with Repository(location) as recovered:
        assert recall(recovered, branch)[0]["memory_id"] == saved["memory_id"]
        assert "broken bicycle" in recovered.get_memory("save-a", saved["memory_id"])["text"]
        assert (
            recovered.connection.execute(
                "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'scooter'"
            ).fetchone()[0]
            == 0
        )


def test_actor_profile_and_protagonist_isolation(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    memory(repo, branch)
    assert recall(repo, branch)
    assert not recall(repo, branch, actor="bea")
    assert not recall(repo, branch, protagonist="michael")
    repo.register_actor("save-b", {"actor_id": "avery", "display_name": "Other Avery"})
    other = repo.open_timeline("save-b", T0)
    assert not recall(repo, other, profile="save-b")
    with pytest.raises(ValueError, match="another protagonist"):
        memory(repo, branch, protagonist="michael")
    with pytest.raises(ValueError, match="another protagonist"):
        memory(repo, branch, protagonist=None)


def test_nested_rollback_filters_every_projection(repo):
    branch = repo.open_timeline("save-a", T0)
    for identifier, at in [("early", T1), ("future", T3)]:
        event(repo, branch, identifier, at)
        memory(repo, branch, identifier, at, text=f"I remember {identifier} bicycle conversation.")
        repo.add_knowledge(
            "save-a",
            branch,
            at,
            identifier,
            [identifier],
            "directly_observed",
            owner_id="avery",
            protagonist_id="franklin",
        )
        assert repo.apply_relationship_event("save-a", branch, "avery", "franklin", identifier)
    fork = repo.open_timeline("save-a", T2)
    assert fork != branch
    assert [row["source_event_ids"] for row in recall(repo, fork)] == [["early"]]
    assert [
        row["text"] for row in repo.retrieve_knowledge("save-a", fork, "avery", "franklin", T4)
    ] == ["early"]
    assert repo.get_relationship("save-a", fork, "avery", "franklin", T4)["familiarity"] == 0.02
    event(repo, fork, "alternate", T3)
    memory(repo, fork, "alternate", T3)
    nested = repo.open_timeline("save-a", T1)
    assert [row["source_event_ids"] for row in recall(repo, nested)] == [["early"]]
    assert len(recall(repo, branch)) == 2  # Future data remains on the old branch.
    with pytest.raises(ValueError, match="absent from this timeline"):
        memory(repo, nested, "future", T4)


def test_event_dedupe_and_memory_dedupe_preserve_immutability(repo):
    branch = repo.open_timeline("save-a", T0)
    data = event(repo, branch)
    assert repo.record_event("save-a", branch, data) is False
    saved = memory(repo, branch)
    assert memory(repo, branch)["memory_id"] == saved["memory_id"]
    data["payload"]["text"] = "Different event"
    with pytest.raises(ValueError, match="immutable"):
        repo.record_event("save-a", branch, data)


def test_hearsay_never_becomes_observation(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch, provenance="reported")
    with pytest.raises(ValueError, match="hearsay"):
        memory(repo, branch)
    saved = repo.add_memory(
        "save-a",
        branch,
        "avery",
        "franklin",
        T1,
        "Franklin told me about a bicycle.",
        ["event-1"],
        "reported",
        confidence=0.5,
    )
    assert saved["provenance"] == "reported"
    with pytest.raises(ValueError, match="hearsay"):
        repo.apply_relationship_event("save-a", branch, "avery", "franklin", "event-1")


def test_ephemeral_identity_and_handle_generation_are_session_scoped(repo):
    branch = repo.start_session("save-a", "session-a", T0)["branch_id"]
    repo.register_actor(
        "save-a", {"id": "random-a", "actor_kind": "ambient_ephemeral", "session_id": "session-a"}
    )
    repo.bind_instance("save-a", "random-a", "session-a", 77, 1)
    assert repo.resolve_instance("save-a", "session-a", 77, 1)["actor_id"] == "random-a"
    assert repo.resolve_instance("save-a", "session-a", 77, 2) is None
    assert repo.resolve_instance("save-a", "session-b", 77, 1) is None
    with pytest.raises(ValueError, match="already belongs"):
        repo.bind_instance("save-a", "avery", "session-a", 77, 1)
    event(repo, branch, observers=["random-a"])
    memory(repo, branch, actor="random-a", session_id="session-a")
    assert not recall(repo, branch, actor="random-a")
    assert not recall(repo, branch, actor="random-a", session_id="session-b")
    assert recall(repo, branch, actor="random-a", session_id="session-a")
    adopted = repo.adopt_actor("save-a", "random-a", "session-a", display_name="New Identity")
    assert adopted["id"] != "random-a"
    assert adopted["actor_kind"] == "spawned_persistent"
    assert not recall(repo, branch, actor=adopted["id"])
    repo.end_session("save-a", "session-a")
    assert repo.get_actor("save-a", "random-a") is None
    assert repo.get_actor("save-a", adopted["id"])
    assert (
        repo.connection.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'bicycle'"
        ).fetchone()[0]
        == 0
    )


def test_relationship_changes_are_grounded_bounded_and_deduped(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    assert repo.apply_relationship_event("save-a", branch, "avery", "franklin", "event-1")
    assert not repo.apply_relationship_event("save-a", branch, "avery", "franklin", "event-1")
    assert not repo.apply_relationship_event("save-a", branch, "bea", "franklin", "event-1")
    assert repo.get_relationship("save-a", branch, "avery", "franklin", T4)["familiarity"] == 0.02
    assert repo.get_relationship("save-a", branch, "avery", "michael", T4)["familiarity"] == 0
    event(repo, branch, "model-claim", event_type="model_says_trust_is_one_hundred")
    assert not repo.apply_relationship_event("save-a", branch, "avery", "franklin", "model-claim")


def test_lexical_ranking_decay_expiry_and_token_budget(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    relevant = memory(repo, branch, text="I repaired a bicycle.", importance=0.1)
    memory(repo, branch, text="I discussed the weather.", importance=1)
    memory(repo, branch, text="I saw a bicycle yesterday.", expires_at=T2)
    result = recall(repo, branch)
    assert result[0]["memory_id"] == relevant["memory_id"]
    assert all("yesterday" not in row["text"] for row in result)
    assert sum(row["token_cost"] for row in result) <= 512
    assert not recall(repo, branch, token_budget=1)
    assert repo.retrieve_memories(
        "save-a", branch, "avery", "franklin", T4, '" OR * : (bicycle) -', token_budget=1000
    )
    assert recall(repo, branch, at="2025-01-11T13:00:00Z")


def test_delete_scrubs_fts_vectors_summaries_sources_and_export(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    saved = memory(repo, branch)
    summary = memory(repo, branch, text="I recall our private bicycle discussion.")
    with repo.connection:
        repo.connection.execute(
            "INSERT INTO memory_vectors VALUES(?,?,?)", (saved["memory_id"], "fixture", b"vector")
        )
        repo.connection.execute(
            "INSERT INTO summary_sources VALUES(?,?)", (summary["memory_id"], saved["memory_id"])
        )
    assert repo.delete_memory("save-a", saved["memory_id"])
    assert not recall(repo, branch)
    assert repo.connection.execute("SELECT count(*) FROM memory_vectors").fetchone()[0] == 0
    assert (
        repo.connection.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'bicycle'"
        ).fetchone()[0]
        == 0
    )
    exported = repo.export_profile("save-a")
    assert not exported["memories"]
    audit = repo.connection.execute("SELECT * FROM scrub_audit").fetchall()
    assert "bicycle" not in json.dumps([tuple(row) for row in audit])


def test_source_deletion_removes_all_derived_state(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    memory(repo, branch)
    repo.add_knowledge(
        "save-a",
        branch,
        T1,
        "Bicycle conversation",
        ["event-1"],
        "directly_observed",
        owner_id="avery",
        protagonist_id="franklin",
    )
    repo.apply_relationship_event("save-a", branch, "avery", "franklin", "event-1")
    assert repo.delete_event("save-a", "event-1")
    assert not recall(repo, branch)
    assert not repo.retrieve_knowledge("save-a", branch, "avery", "franklin", T4)
    assert repo.get_relationship("save-a", branch, "avery", "franklin", T4)["familiarity"] == 0


def test_memory_edit_removes_old_search_terms(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    saved = memory(repo, branch)
    repo.update_memory("save-a", saved["memory_id"], text="I heard about a scooter.")
    assert (
        repo.connection.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'bicycle'"
        ).fetchone()[0]
        == 0
    )
    assert (
        repo.connection.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'scooter'"
        ).fetchone()[0]
        == 1
    )


def test_knowledge_owner_scope_and_protagonist_filters(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    repo.add_knowledge(
        "save-a",
        branch,
        T1,
        "A private conversation",
        ["event-1"],
        "directly_observed",
        owner_id="avery",
        protagonist_id="franklin",
    )
    assert repo.retrieve_knowledge("save-a", branch, "avery", "franklin", T4)
    assert not repo.retrieve_knowledge("save-a", branch, "bea", "franklin", T4)
    assert not repo.retrieve_knowledge("save-a", branch, "avery", "michael", T4)


def test_profile_delete_cascades_without_touching_other_profile(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    memory(repo, branch)
    repo.register_actor("save-b", {"id": "avery"})
    repo.open_timeline("save-b", T0)
    assert repo.delete_profile("save-a")
    assert repo.get_actor("save-a", "avery") is None
    assert repo.get_actor("save-b", "avery") is not None
    assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_save_association_and_invalid_clock_fail_closed(repo):
    repo.create_profile("explicit", save_slot="slot-1")
    with pytest.raises(ValueError, match="save association"):
        repo.create_profile("explicit", save_slot="slot-2")
    with pytest.raises(ValueError, match="full ISO"):
        repo.open_timeline("save-a", "09:00")


def test_reopen_rejects_unknown_future_migration(tmp_path):
    location = tmp_path / "future.sqlite3"
    with sqlite3.connect(location) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        Repository(location)


def test_an_npc_cannot_copy_another_npcs_observation(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    with pytest.raises(ValueError, match="did not observe"):
        memory(repo, branch, actor="bea")
    with pytest.raises(ValueError, match="did not observe"):
        repo.add_knowledge(
            "save-a",
            branch,
            T1,
            "Private bicycle fact",
            ["event-1"],
            "directly_observed",
            owner_id="bea",
            protagonist_id="franklin",
        )


def test_recycled_handle_cannot_reuse_ambient_identity(repo):
    repo.register_actor(
        "save-a", {"id": "random-1", "actor_kind": "ambient_ephemeral", "session_id": "live"}
    )
    repo.register_actor(
        "save-a", {"id": "random-2", "actor_kind": "ambient_ephemeral", "session_id": "live"}
    )
    repo.bind_instance("save-a", "random-1", "live", 77, 1)
    with pytest.raises(ValueError, match="new actor identity"):
        repo.bind_instance("save-a", "random-1", "live", 77, 2)
    repo.bind_instance("save-a", "random-2", "live", 77, 2)
    assert repo.resolve_instance("save-a", "live", 77, 1) is None
    assert repo.resolve_instance("save-a", "live", 77, 2)["actor_id"] == "random-2"


def test_reconciling_persistent_actor_releases_previous_live_instance(repo):
    repo.bind_instance("save-a", "avery", "live", 77, 1)
    repo.bind_instance("save-a", "avery", "live", 88, 2)
    assert repo.resolve_instance("save-a", "live", 77, 1) is None
    assert repo.resolve_instance("save-a", "live", 88, 2)["actor_id"] == "avery"


def ambient(repo, actor_id, session_id="session-a"):
    repo.register_actor(
        "save-a", {"id": actor_id, "actor_kind": "ambient_ephemeral", "session_id": session_id}
    )


def test_released_handle_binds_a_fresh_identity_without_prior_memories(repo):
    branch = repo.start_session("save-a", "session-a", T0)["branch_id"]
    ambient(repo, "first")
    repo.bind_instance("save-a", "first", "session-a", 77, 1)
    event(repo, branch, observers=["first"])
    memory(repo, branch, actor="first", session_id="session-a")
    assert recall(repo, branch, actor="first", session_id="session-a")
    assert repo.release_instance("save-a", "session-a", actor_id="first") == 1
    assert repo.resolve_instance("save-a", "session-a", 77, 1) is None
    ambient(repo, "second")
    repo.bind_instance("save-a", "second", "session-a", 77, 1)
    assert repo.resolve_instance("save-a", "session-a", 77, 1)["actor_id"] == "second"
    assert not recall(repo, branch, actor="second", session_id="session-a")
    # The released identity cannot join this or any other entity again.
    for handle in (77, 88):
        with pytest.raises(ValueError, match="new actor identity"):
            repo.bind_instance("save-a", "first", "session-a", handle, 1)
    assert repo.release_instance("save-a", "session-a", actor_id="second") == 1
    with pytest.raises(ValueError, match="released ambient entity"):
        repo.bind_instance("save-a", "first", "session-a", 77, 1)
    with pytest.raises(ValueError, match="released ambient entity"):
        repo.bind_instance("save-a", "second", "session-a", 77, 1)
    with pytest.raises(ValueError, match="runtime handle generation or an actor"):
        repo.release_instance("save-a", "session-a")


def test_live_handle_conflict_still_raises(repo):
    repo.start_session("save-a", "session-a", T0)
    ambient(repo, "first")
    ambient(repo, "second")
    repo.bind_instance("save-a", "first", "session-a", 77, 1)
    repo.bind_instance("save-a", "first", "session-a", 77, 1)
    with pytest.raises(ValueError, match="already belongs"):
        repo.bind_instance("save-a", "second", "session-a", 77, 1)
    with pytest.raises(ValueError, match="already belongs"):
        repo.bind_instance("save-a", "avery", "session-a", 77, 1)
    assert repo.resolve_instance("save-a", "session-a", 77, 1)["actor_id"] == "first"


def test_persistent_character_keeps_one_live_incarnation_after_release(repo):
    repo.bind_instance("save-a", "avery", "live", 77, 1)
    assert repo.release_instance("save-a", "live", 77, 1) == 1
    assert repo.release_instance("save-a", "live", 77, 1) == 0
    repo.bind_instance("save-a", "avery", "live", 88, 1)
    repo.bind_instance("save-a", "avery", "live", 99, 1)
    live = repo.connection.execute(
        "SELECT handle FROM actor_instances WHERE profile_id='save-a' AND session_id='live' "
        "AND actor_id='avery' AND status='live'"
    ).fetchall()
    assert [row[0] for row in live] == [99]
    with pytest.raises(ValueError, match="already belongs"):
        repo.bind_instance("save-a", "bea", "live", 99, 1)
    repo.bind_instance("save-a", "bea", "live", 77, 1)
    assert repo.resolve_instance("save-a", "live", 77, 1)["actor_id"] == "bea"


def test_derived_confidence_cannot_exceed_source_confidence(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    saved = memory(repo, branch, confidence=1)
    assert saved["confidence"] == 0.9
    assert repo.update_memory("save-a", saved["memory_id"], confidence=1)["confidence"] == 0.9
    card = repo.add_knowledge(
        "save-a",
        branch,
        T1,
        "Private bicycle fact",
        ["event-1"],
        "directly_observed",
        owner_id="avery",
        protagonist_id="franklin",
        confidence=1,
    )
    assert card["confidence"] == 0.9


def test_private_sources_cannot_become_shared_knowledge(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch)
    with pytest.raises(ValueError, match="private source"):
        repo.add_knowledge(
            "save-a",
            branch,
            T1,
            "Private bicycle fact",
            ["event-1"],
            "directly_observed",
            scope="public",
            protagonist_id="franklin",
        )


def test_private_system_fact_does_not_transfer_another_npcs_action_outcome(repo):
    branch = repo.open_timeline("save-a", T0)
    event(repo, branch, provenance="system_fact", event_type="action.completed")
    with pytest.raises(ValueError, match="did not observe"):
        repo.add_memory(
            "save-a",
            branch,
            "bea",
            "franklin",
            T1,
            "I completed Avery's private action.",
            ["event-1"],
            "system_fact",
        )
    with pytest.raises(ValueError, match="did not observe"):
        repo.add_knowledge(
            "save-a",
            branch,
            T1,
            "Avery's private action",
            ["event-1"],
            "system_fact",
            owner_id="bea",
            protagonist_id="franklin",
        )
    with pytest.raises(ValueError, match="private source"):
        repo.add_knowledge(
            "save-a",
            branch,
            T1,
            "Avery's private action",
            ["event-1"],
            "system_fact",
            scope="public",
            protagonist_id="franklin",
        )
    own = repo.add_memory(
        "save-a",
        branch,
        "avery",
        "franklin",
        T1,
        "My action completed.",
        ["event-1"],
        "system_fact",
    )
    assert own["source_event_ids"] == ["event-1"]


def test_authored_public_facts_require_explicit_visibility_and_preserve_provenance(repo):
    branch = repo.open_timeline("save-a", T0)
    source = {
        "event_id": "authored-public-fact",
        "game_datetime": T1,
        "protagonist_id": None,
        "provenance": "system_fact",
        "observer_ids": [],
        "payload": {"text": "A fictional public place name.", "visibility_scope": "public"},
    }
    repo.record_event("save-a", branch, source)
    card = repo.add_knowledge(
        "save-a",
        branch,
        T1,
        source["payload"]["text"],
        [source["event_id"]],
        "system_fact",
        scope="public",
    )
    assert (
        repo.retrieve_knowledge("save-a", branch, "bea", "michael", T2)[0]["card_id"]
        == card["card_id"]
    )
    saved = repo.add_memory(
        "save-a",
        branch,
        "bea",
        None,
        T1,
        source["payload"]["text"],
        [source["event_id"]],
        "system_fact",
    )
    assert saved["provenance"] == "system_fact"
    with pytest.raises(ValueError, match="did not observe"):
        repo.add_memory(
            "save-a",
            branch,
            "bea",
            None,
            T1,
            "I witnessed this myself.",
            [source["event_id"]],
            "directly_observed",
        )


@pytest.mark.parametrize("scope,scope_id", [("region", "pilot-block"), ("group", "gardeners")])
def test_system_fact_shared_scope_requires_explicit_matching_source_policy(repo, scope, scope_id):
    branch = repo.open_timeline("save-a", T0)
    source = {
        "event_id": "scoped-fact",
        "game_datetime": T1,
        "protagonist_id": None,
        "provenance": "system_fact",
        "observer_ids": [],
        "payload": {
            "text": "Scoped authored fact",
            "visibility_scope": scope,
            "scope_id": scope_id,
        },
    }
    repo.record_event("save-a", branch, source)
    repo.add_knowledge(
        "save-a",
        branch,
        T1,
        "Scoped authored fact",
        ["scoped-fact"],
        "system_fact",
        scope=scope,
        scope_id=scope_id,
    )
    assert not repo.retrieve_knowledge("save-a", branch, "bea", "franklin", T2)
    membership = {"region_id": scope_id} if scope == "region" else {"group_ids": (scope_id,)}
    assert repo.retrieve_knowledge("save-a", branch, "bea", "franklin", T2, **membership)
    with pytest.raises(ValueError, match="private source"):
        repo.add_knowledge(
            "save-a",
            branch,
            T1,
            "Wrong scope",
            ["scoped-fact"],
            "system_fact",
            scope=scope,
            scope_id="somewhere-else",
        )


def test_ambiguous_later_reload_requires_an_explicit_branch(repo):
    original = repo.open_timeline("save-a", T0)
    event(repo, original, "original-late", T4)
    alternate = repo.open_timeline("save-a", T1)
    event(repo, alternate, "alternate-late", T2)
    with pytest.raises(TimelineAmbiguityError) as failure:
        repo.open_timeline("save-a", T3)
    assert set(failure.value.candidates) == {original, alternate}
    selected = repo.open_timeline("save-a", T3, parent_branch_id=original)
    assert selected not in {original, alternate}  # Explicit older-save fork.
    assert (
        repo.connection.execute(
            "SELECT parent_id FROM branches WHERE branch_id=?", (selected,)
        ).fetchone()[0]
        == original
    )


REGRESSION = json.loads((Path(__file__).parents[1] / "data" / "memory_regression.json").read_text())


@pytest.fixture(scope="module")
def regression_repository(tmp_path_factory):
    location = tmp_path_factory.mktemp("recall-regression") / "recall.sqlite3"
    with Repository(location) as repository:
        repository.register_actor("save-a", {"id": "avery"})
        repository.register_actor("save-a", {"id": "bea"})
        branch = repository.open_timeline("save-a", T0)
        for source in REGRESSION["events"]:
            repository.record_event("save-a", branch, source)
            repository.add_memory(
                "save-a",
                branch,
                "avery",
                "franklin",
                source["game_datetime"],
                source["payload"]["text"],
                [source["event_id"]],
                source["provenance"],
                confidence=source["confidence"],
            )
    # The fixed-memory suite always queries reopened storage after ten game days.
    with Repository(location) as repository:
        yield repository, branch


@pytest.mark.parametrize("question", REGRESSION["questions"], ids=lambda question: question["id"])
def test_fixed_fifty_question_retrieval_regression(regression_repository, question):
    repository, branch = regression_repository
    found = repository.retrieve_memories(
        "save-a",
        branch,
        question["actor_id"],
        question["protagonist_id"],
        "2025-01-11T13:00:00Z",
        question["query"],
        limit=1,
        token_budget=512,
    )
    expected = question["expected_top_source_event_ids"]
    if not expected:
        assert found == []
    else:
        assert found[0]["source_event_ids"] == expected
        if question["required_qualification"]:
            assert found[0]["text"] == question["required_qualification"]


def pin_world(repo):
    branch = repo.start_session("save-a", "session-a", T0)["branch_id"]
    for actor_id in ("walker", "other"):
        ambient(repo, actor_id)
    event(repo, branch, observers=["walker", "other"])
    memory(repo, branch, actor="walker", session_id="session-a")
    memory(repo, branch, actor="other", session_id="session-a")
    repo.apply_relationship_event(
        "save-a", branch, "walker", "franklin", "event-1", session_id="session-a"
    )
    repo.apply_relationship_event(
        "save-a", branch, "other", "franklin", "event-1", session_id="session-a"
    )
    for owner in ("walker", "other"):
        repo.add_knowledge(
            "save-a", branch, T1, f"{owner} knows the bicycle shop.", ["event-1"],
            "directly_observed", owner_id=owner, protagonist_id="franklin",
            session_id="session-a",
        )
    return branch


def scopes(repo, actor_id):
    query = {
        "memories": "SELECT session_id FROM memories WHERE actor_id=?",
        "relationship_updates": "SELECT session_id FROM relationship_updates WHERE actor_id=?",
        "knowledge_cards": "SELECT session_id FROM knowledge_cards WHERE owner_id=?",
    }
    return {
        table: {row[0] for row in repo.connection.execute(sql, (actor_id,))}
        for table, sql in query.items()
    }


def pin(repo, branch, actor_id="walker", session_id="session-a"):
    return repo.pin_actor(
        "save-a", actor_id, session_id, display_name="Resident 3f9a",
        pinned_at_game_datetime=T2, branch_id=branch,
    )


def test_pin_promotes_in_place_and_survives_session_end(repo):
    branch = pin_world(repo)
    repo.bind_instance("save-a", "walker", "session-a", 77, 1)
    assert repo.get_actor("save-a", "walker")["runtime_binding"] == [77, 1]
    pinned = pin(repo, branch)
    assert (pinned["id"], pinned["actor_kind"], pinned["session_id"]) == (
        "walker", "spawned_persistent", None
    )
    assert pinned["pinned_from_session"] == "session-a"
    assert "runtime_binding" not in pinned
    assert pinned["pinned_at"] == {"game_datetime": "2025-01-01T11:00:00.000000+00:00",
                                   "branch_id": branch}
    assert repo.get_actor("save-a", "walker") == pinned
    assert scopes(repo, "walker") == {
        "memories": {None}, "relationship_updates": {None}, "knowledge_cards": {None}
    }
    assert scopes(repo, "other") == {
        "memories": {"session-a"}, "relationship_updates": {"session-a"},
        "knowledge_cards": {"session-a"},
    }
    assert pin(repo, branch) == pinned
    audits = repo.connection.execute(
        "SELECT COUNT(*) FROM scrub_audit WHERE operation='pin_actor' AND object_id='walker'"
    ).fetchone()[0]
    assert audits == 1
    repo.end_session("save-a", "session-a")
    assert repo.get_actor("save-a", "other") is None
    assert not repo.retrieve_memories("save-a", branch, "other", "franklin", T4, "bicycle")
    later = repo.start_session("save-a", "session-b", T3)["branch_id"]
    assert later == branch
    repo.bind_instance("save-a", "walker", "session-b", 88, 2)
    assert repo.resolve_instance("save-a", "session-b", 88, 2)["actor_id"] == "walker"
    assert recall(repo, branch, actor="walker", session_id="session-b")
    relation = repo.get_relationship(
        "save-a", branch, "walker", "franklin", T4, session_id="session-b"
    )
    assert relation["familiarity"] == 0.02
    cards = repo.retrieve_knowledge(
        "save-a", branch, "walker", "franklin", T4, session_id="session-b"
    )
    assert [card["text"] for card in cards] == ["walker knows the bicycle shop."]
    # Writes after the pin are durable without any session scope.
    event(repo, branch, "event-2", at=T3, observers=["walker"])
    later_memory = memory(repo, branch, "event-2", at=T3, actor="walker", session_id="session-b")
    assert later_memory["session_id"] is None


def test_pin_is_atomic(repo, monkeypatch):
    branch = pin_world(repo)

    def fail(*_):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(repo, "_audit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        pin(repo, branch)
    actor = repo.get_actor("save-a", "walker")
    assert (actor["actor_kind"], actor["session_id"]) == ("ambient_ephemeral", "session-a")
    assert "pinned_from_session" not in actor
    assert scopes(repo, "walker")["memories"] == {"session-a"}


def test_pin_refuses_non_ambient_and_other_session_actors(repo):
    branch = pin_world(repo)
    with pytest.raises(ValueError, match="only a pedestrian from this session"):
        pin(repo, branch, actor_id="avery")
    with pytest.raises(ValueError, match="only a pedestrian from this session"):
        pin(repo, branch, session_id="session-b")
    with pytest.raises(ValueError, match="unknown actor"):
        pin(repo, branch, actor_id="missing")
    with pytest.raises(ValueError, match="display name"):
        repo.pin_actor(
            "save-a", "walker", "session-a", display_name="", pinned_at_game_datetime=T2,
            branch_id=branch,
        )
    assert repo.get_actor("save-a", "walker")["actor_kind"] == "ambient_ephemeral"


def test_forget_applies_only_to_pinned_actors(repo):
    branch = pin_world(repo)
    for actor_id in ("avery", "other"):
        with pytest.raises(ValueError, match="only pinned pedestrians"):
            repo.forget_pinned_actor("save-a", actor_id)
    with pytest.raises(ValueError, match="unknown actor"):
        repo.forget_pinned_actor("save-a", "missing")
    pin(repo, branch)
    repo.forget_pinned_actor("save-a", "walker")
    assert repo.get_actor("save-a", "walker") is None
    assert scopes(repo, "walker") == {
        "memories": set(), "relationship_updates": set(), "knowledge_cards": set()
    }
    operations = {
        row[0]
        for row in repo.connection.execute(
            "SELECT operation FROM scrub_audit WHERE object_id IN ('walker',?)",
            (json.dumps(["walker", "player:franklin", "event-1"], separators=(",", ":")),),
        )
    }
    assert {"pin_actor", "reset_actor", "forget_pinned_actor", "forget_relationship_source"} <= (
        operations
    )
    assert scopes(repo, "other")["memories"] == {"session-a"}
