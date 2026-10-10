"""Private, retained social sources control adaptation without inferred reputation."""

import json
from datetime import datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest

from service.lls.social_adaptation import SocialAdaptation, decision_memory_text
from service.lls.social_models import SocialStart
from service.lls.social_storage import SocialStore
from service.lls.storage import Repository

GAME = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)


@pytest.fixture
def history(tmp_path):
    with Repository(tmp_path / "adaptation.db") as repo:
        branch = repo.open_timeline("profile", (GAME - timedelta(days=1)).isoformat())
        for actor in ("avery", "bea", "clay"):
            repo.register_actor("profile", {"id": actor, "display_name": actor})
        start = SocialStart(
            scene_id="scene", profile_id="profile", branch_id=branch, session_id="session",
            protagonist_id="franklin", runtime_generation=1, initiator_id="avery",
            participant_bindings={actor: actor + "-1" for actor in ("avery", "bea", "clay")},
            area_id="block", interior_id="street", game_datetime=GAME,
            development_attestation=True,
        )
        yield SimpleNamespace(
            repo=repo, branch=branch, start=start, sequence=count(), store=SocialStore(repo),
            adaptation=SocialAdaptation(repo),
        )


def read(history, *, at=GAME, owners=("avery", "bea", "clay"), **scope):
    return history.adaptation.read(
        scope.get("profile_id", "profile"), scope.get("branch_id", history.branch),
        scope.get("protagonist_id", "franklin"), at, owners,
        session_id=scope.get("session_id", "session"),
    )


def decide(history, choice="refuse", *, owner="bea", scene="scene", at=GAME):
    identifier = "decision-" + str(next(history.sequence))
    start = history.start.model_copy(update={"scene_id": scene})
    history.store.decision(start, owner, choice, identifier, at)
    return identifier


def quote(
    history, *, scene="scene", at=GAME, speaker="avery", listener="bea",
    text="The pier is quiet.", retelling=False,
):
    identifier = "quote-" + str(next(history.sequence))
    start = history.start.model_copy(update={"scene_id": scene})
    source_id = None
    if retelling:
        source_id = identifier + "-prior"
        history.repo.record_event("profile", history.branch, {
            "event_id": source_id, "event_type": "actor.spoke", "game_datetime": at.isoformat(),
            "protagonist_id": "franklin", "observer_ids": [speaker], "provenance": "reported",
            "confidence": 0.7, "payload": {"text": text, "teller_id": "clay"},
        })
        history.repo.add_memory(
            "profile", history.branch, speaker, "franklin", at.isoformat(), "I heard: " + text,
            [source_id], "reported", memory_id=source_id + ":memory",
        )
    utterance = SimpleNamespace(
        actor_id=speaker, utterance_id=identifier, retell_event_id=source_id,
    )
    receipt = SimpleNamespace(event_id=identifier + ":delivered", text=text, sampled_at=at)
    heard = history.store.delivered(
        start, utterance, receipt, [SimpleNamespace(actor_id=listener, confidence=0.9)], at
    )
    return heard[listener]["event_id"]


def event(history, identifier):
    return json.loads(history.repo.connection.execute(
        "SELECT data FROM events WHERE profile_id='profile' AND event_id=?", (identifier,)
    ).fetchone()[0])


def change_source(history, identifier, **changes):
    source = event(history, identifier)
    source["payload"].update(changes)
    with history.repo.connection:
        history.repo.connection.execute(
            "UPDATE events SET data=? WHERE profile_id='profile' AND event_id=?",
            (json.dumps(source), identifier),
        )


def test_own_decision_is_private_scene_boundary_without_directed_resentment(history):
    identifier = decide(history)
    values = read(history)
    assert values["bea"]["last_boundary_at"] == GAME.isoformat(timespec="microseconds")
    assert values["bea"]["recent_boundary_count"] == 1
    assert values["bea"]["own_decision_source_event_ids"] == [identifier]
    assert values["bea"]["peers"] == {}
    for actor in ("avery", "clay"):
        assert values[actor]["last_boundary_at"] is None
        assert values[actor]["peers"] == {}
        assert identifier not in json.dumps(values[actor])
    assert not history.repo.connection.execute("SELECT * FROM relationship_updates").fetchall()


def test_repeated_boundaries_deduplicate_scenes_cap_and_positive_decision_resets(history):
    decide(history, "refuse")
    decide(history, "withdraw")
    assert read(history)["bea"]["recent_boundary_count"] == 1
    for i in range(4):
        decide(history, scene="later-" + str(i))
    assert read(history)["bea"]["recent_boundary_count"] == 3
    decide(history, "accept", scene="willing-again")
    assert read(history)["bea"]["last_boundary_at"] is None
    assert read(history)["bea"]["recent_boundary_count"] == 0
    decide(history, "withdraw", scene="one-new-boundary")
    assert read(history)["bea"]["recent_boundary_count"] == 1


def test_boundary_window_and_weight_use_game_time(history):
    decide(history)
    later = read(history, at=GAME + timedelta(seconds=3601))["bea"]
    assert later["last_boundary_at"] is not None and later["recent_boundary_count"] == 0
    assert read(history, at=GAME + timedelta(days=1))["bea"]["boundary_weight"] == 0.5


def test_confirmed_hearing_counts_encounters_once_without_teaching_speaker_or_bystander(history):
    identifiers = [quote(history) for _ in range(3)]
    one = read(history)["bea"]["peers"]["avery"]
    assert one["confirmed_scene_count"] == 1 and one["familiarity"] == pytest.approx(0.07)
    identifiers.append(quote(history, scene="second"))
    values = read(history)
    peer = values["bea"]["peers"]["avery"]
    assert peer["confirmed_scene_count"] == 2 and peer["familiarity"] == pytest.approx(0.14)
    assert peer["source_event_ids"] == identifiers
    assert len(peer["recent_quotes"]) == 2
    assert peer["recent_quotes"][0]["event_id"] == identifiers[-1]
    assert peer["recent_quotes"][0]["memory_id"] == identifiers[-1] + ":memory"
    assert peer["recent_quotes"][0]["is_retelling"] is False
    assert values["avery"]["peers"] == values["clay"]["peers"] == {}


def test_hearsay_content_never_creates_reputation_or_contact_with_subject(history):
    identifier = quote(history, text="Clay is secretly untrustworthy.", retelling=True)
    values = read(history)
    peer = values["bea"]["peers"]["avery"]
    assert peer["confirmed_scene_count"] == 1
    assert peer["recent_quotes"][0]["is_retelling"] is True
    assert peer["recent_quotes"][0]["text"] == "Clay is secretly untrustworthy."
    assert peer["source_event_ids"] == [identifier]
    assert "clay" not in values["bea"]["peers"]
    assert values["clay"]["peers"] == {}
    assert not {"trust", "fear", "friendship", "affinity", "respect"} & peer.keys()
    assert not history.repo.connection.execute("SELECT * FROM relationship_updates").fetchall()


def test_contact_strength_and_confirmed_count_decay_without_rewriting_evidence(history):
    identifier = quote(history)
    baseline = read(history)["bea"]["peers"]["avery"]
    later = read(history, at=GAME + timedelta(days=7))["bea"]["peers"]["avery"]
    assert later["familiarity"] == pytest.approx(baseline["familiarity"] / 2)
    assert later["confirmed_scene_count"] == 0 and later["recent_quotes"] == []
    assert event(history, identifier)["payload"]["text"] == "The pier is quiet."
    assert read(history, at=GAME + timedelta(days=57))["bea"]["peers"] == {}


def test_memory_specific_decay_and_expiry_are_honored(history):
    identifier = quote(history)
    with history.repo.connection:
        history.repo.connection.execute(
            "UPDATE memories SET decay_days=0.25 WHERE memory_id=?", (identifier + ":memory",)
        )
    later = read(history, at=GAME + timedelta(hours=6))["bea"]["peers"]["avery"]
    assert later["familiarity"] == pytest.approx(0.035)
    with history.repo.connection:
        history.repo.connection.execute(
            "UPDATE memories SET expires_at=? WHERE memory_id=?",
            ((GAME + timedelta(hours=1)).isoformat(), identifier + ":memory"),
        )
    assert read(history, at=GAME + timedelta(hours=2))["bea"]["peers"] == {}


@pytest.mark.parametrize("source_kind", ["decision", "quote"])
@pytest.mark.parametrize("change", ["edit", "memory_delete", "source_delete", "reset"])
def test_correction_deletion_and_reset_remove_effects(history, source_kind, change):
    identifier = decide(history) if source_kind == "decision" else quote(history)
    if change == "edit":
        history.repo.update_memory("profile", identifier + ":memory", text="Corrected account.")
    elif change == "memory_delete":
        history.repo.delete_memory("profile", identifier + ":memory")
    elif change == "source_delete":
        history.repo.delete_event("profile", identifier)
    else:
        history.repo.reset_actor("profile", "bea")
    values = read(history)["bea"]
    assert values["last_boundary_at"] is None and values["peers"] == {}


@pytest.mark.parametrize("unprojected", [False, True])
def test_reset_blocks_same_source_and_anchor_after_reimport(history, unprojected):
    identifier = decide(history)
    source = event(history, identifier)
    if unprojected:
        # Simulate a canonical write whose memory projection has not happened.
        with history.repo.connection:
            history.repo.connection.execute(
                "DELETE FROM memories WHERE memory_id=?", (identifier + ":memory",)
            )
    history.repo.reset_actor("profile", "bea")
    history.repo.add_memory(
        "profile", history.branch, "bea", "franklin", GAME.isoformat(),
        decision_memory_text("refuse"), [identifier], "system_fact",
        memory_id=identifier + ":memory",
    )
    assert read(history)["bea"]["last_boundary_at"] is None
    history.repo.delete_event("profile", identifier)
    history.repo.record_event("profile", history.branch, source)
    history.repo.add_memory(
        "profile", history.branch, "bea", "franklin", GAME.isoformat(),
        decision_memory_text("refuse"), [identifier], "system_fact",
        memory_id=identifier + ":memory",
    )
    assert read(history)["bea"]["last_boundary_at"] is None
    decide(history, scene="fresh")
    assert read(history)["bea"]["recent_boundary_count"] == 1


def test_deleted_speaker_proof_cannot_restore_effect_after_reimport(history):
    identifier = quote(history)
    speaker_id = event(history, identifier)["payload"]["heard_delivery_event_id"]
    source = event(history, speaker_id)
    history.repo.delete_event("profile", speaker_id)
    assert read(history)["bea"]["peers"] == {}
    history.repo.record_event("profile", history.branch, source)
    assert read(history)["bea"]["peers"] == {}


def test_profile_protagonist_and_rewind_are_separate(history):
    decide(history)
    quote(history)
    assert read(history, protagonist_id="michael")["bea"]["peers"] == {}
    assert read(history, protagonist_id="michael")["bea"]["last_boundary_at"] is None
    fork = history.repo.open_timeline(
        "profile", (GAME - timedelta(minutes=1)).isoformat(), parent_branch_id=history.branch
    )
    assert read(history, branch_id=fork)["bea"]["peers"] == {}
    assert read(history, branch_id=fork)["bea"]["last_boundary_at"] is None
    other = history.repo.open_timeline("other", GAME.isoformat())
    history.repo.register_actor("other", {"id": "bea", "display_name": "Unrelated Bea"})
    assert read(history, profile_id="other", branch_id=other)["bea"]["peers"] == {}


def test_reopen_reconstructs_private_state_without_cached_counters(history, tmp_path):
    decide(history)
    quote(history)
    expected = read(history)
    with Repository(tmp_path / "adaptation.db") as reopened:
        actual = SocialAdaptation(reopened).read(
            "profile", history.branch, "franklin", GAME, ("avery", "bea", "clay"),
            session_id="replacement-session",
        )
    assert actual == expected


@pytest.mark.parametrize("field,value", [
    ("decision_owner", "avery"), ("decision_valid", False),
    ("decision_phase", "participation"), ("decision_scope", "friendship"),
    ("participant_ids", ["avery", "clay"]), ("scope_id", "avery"),
])
def test_malformed_or_other_owner_decision_anchor_cannot_adapt(history, field, value):
    identifier = decide(history)
    change_source(history, identifier, **{field: value})
    assert read(history)["bea"]["last_boundary_at"] is None


def test_hearing_requires_matching_exact_canonical_speaker_receipt(history):
    identifier = quote(history)
    speaker_id = event(history, identifier)["payload"]["heard_delivery_event_id"]
    change_source(history, speaker_id, text="Different delivered words.")
    assert read(history)["bea"]["peers"] == {}


def test_source_read_is_bulk_and_bounded_for128_owners(history):
    owners = ["owner-" + str(i) for i in range(128)]
    for owner in owners:
        history.repo.register_actor("profile", {"id": owner, "display_name": owner})
    for i in range(130):
        quote(history, scene="encounter-" + str(i))
    statements = []
    history.repo.connection.set_trace_callback(statements.append)
    try:
        result = read(history, owners=owners)
        own = read(history, owners=["bea"])["bea"]["peers"]["avery"]
    finally:
        history.repo.connection.set_trace_callback(None)
    assert len(result) == 128
    assert len([sql for sql in statements if sql.startswith("WITH candidates AS")]) == 2
    assert own["confirmed_scene_count"] == 64 and own["familiarity"] == 1
    assert len(own["source_event_ids"]) == 8 and len(own["recent_quotes"]) == 2
    with pytest.raises(ValueError):
        read(history, owners=owners + ["one-too-many"])


def test_speaking_and_decisions_cannot_displace_confirmed_hearing(history):
    quote(history, speaker="bea", listener="avery", scene="heard-one")
    quote(history, speaker="bea", listener="avery", scene="heard-two")
    before = read(history)["avery"]["peers"]
    for i in range(130):
        quote(history, speaker="avery", listener="clay", scene="spoken-" + str(i))
        decide(history, "accept", owner="avery", scene="decision-" + str(i))
    assert read(history)["avery"]["peers"] == before


def test_dense_history_for128_owners_fits_default_sql_work_budget(history):
    # In-memory setup isolates the work-budget regression from filesystem speed.
    with Repository() as repo:
        branch = repo.open_timeline("profile", (GAME - timedelta(days=1)).isoformat())
        owners = ["owner-" + str(i) for i in range(128)]
        for owner in owners:
            repo.register_actor("profile", {"id": owner, "display_name": owner})
        store = SocialStore(repo)
        for index, owner in enumerate(owners):
            other = owners[(index + 1) % len(owners)]
            start = history.start.model_copy(update={
                "branch_id": branch, "initiator_id": owner,
                "participant_bindings": {owner: owner + "-1", other: other + "-1"},
            })
            for i in range(100):
                store.decision(
                    start.model_copy(update={"scene_id": "scene-" + str(i)}),
                    owner, "refuse", f"{owner}-decision-{i}", GAME,
                )
        values = SocialAdaptation(repo).read(
            "profile", branch, "franklin", GAME, owners, session_id="session",
        )
        assert len(values) == 128
        for owner in owners:
            assert values[owner]["recent_boundary_count"] == 3
            assert values[owner]["own_decision_source_event_ids"] == [
                f"{owner}-decision-99"
            ]


def test_sql_work_budget_fails_closed_and_restores_connection_handler(history):
    for i in range(30):
        quote(history, scene="encounter-" + str(i))
    history.adaptation = SocialAdaptation(history.repo, sql_step_budget=1000)
    with pytest.raises(ValueError, match="work budget"):
        read(history)
    assert history.repo.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 60
    history.adaptation = SocialAdaptation(history.repo)
    assert read(history)["bea"]["peers"]["avery"]["confirmed_scene_count"] == 30
