"""Deterministic social projections; fixtures do not attest native game events."""

import json

import pytest

from service.lls.storage import RELATIONSHIP_FIELDS, Repository

START = "2025-01-01T08:00:00Z"
EVENT = "2025-01-01T09:00:00Z"
FORK = "2025-01-01T10:00:00Z"
LATER = "2025-01-01T11:00:00Z"
NEXT_DAY = "2025-01-02T09:00:00Z"


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "relationships.sqlite3") as repository:
        repository.register_actor("profile", {"id": "avery", "display_name": "Avery"})
        repository.register_actor("profile", {"id": "bea", "display_name": "Bea"})
        yield repository


def add_event(repo, branch, identifier, kind="courtesy_observed", **changes):
    source = {
        "event_id": identifier,
        "event_type": kind,
        "game_datetime": EVENT,
        "protagonist_id": "franklin",
        "provenance": "directly_observed",
        "observer_ids": ["avery"],
        "confidence": 0.9,
        "payload": {},
        **changes,
    }
    repo.record_event("profile", branch, source)
    return source


def apply(repo, branch, identifier, **changes):
    return repo.apply_relationship_event(
        "profile",
        branch,
        changes.pop("actor_id", "avery"),
        changes.pop("protagonist_id", "franklin"),
        identifier,
        **changes,
    )


def relationship(repo, branch, at=LATER, **changes):
    return repo.get_relationship(
        "profile",
        branch,
        changes.pop("actor_id", "avery"),
        changes.pop("protagonist_id", "franklin"),
        at,
        **changes,
    )


def test_fixed_policy_ignores_model_score_suggestions_in_narrative_payload(repo):
    branch = repo.open_timeline("profile", START)
    add_event(
        repo,
        branch,
        "courtesy",
        payload={
            "model_relationship_scores": {"trust": 1, "fear": 1, "affinity": -1},
            "deltas": {"familiarity": 99},
            "native_faction": "friendly",
        },
    )
    assert apply(repo, branch, "courtesy")
    assert not apply(repo, branch, "courtesy")
    actual = relationship(repo, branch)
    assert {field: actual[field] for field in RELATIONSHIP_FIELDS} == {
        "familiarity": 0,
        "trust": 0.01,
        "affinity": 0.02,
        "fear": 0,
        "respect": 0.01,
    }
    assert actual["scope"] == "conversation_only"
    assert actual["policy_version"] == "1"
    with pytest.raises(TypeError):
        repo.apply_relationship_event(
            "profile", branch, "avery", "franklin", "courtesy", deltas={"trust": 1}
        )


@pytest.mark.parametrize("protagonist", [None, "michael"])
def test_source_must_attribute_the_actual_protagonist(repo, protagonist):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "wrong-protagonist", protagonist_id=protagonist)
    with pytest.raises(ValueError, match="same protagonist"):
        apply(repo, branch, "wrong-protagonist")
    assert relationship(repo, branch)["source_event_ids"] == []


@pytest.mark.parametrize("provenance", ["heard", "reported", "unverified"])
def test_hearsay_and_inference_cannot_drive_relationship_scores(repo, provenance):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "claim", provenance=provenance)
    with pytest.raises(ValueError):
        apply(repo, branch, "claim")
    assert relationship(repo, branch)["trust"] == 0


def test_low_confidence_and_nonwitnesses_do_not_receive_social_updates(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "uncertain", confidence=0.69)
    assert not apply(repo, branch, "uncertain")
    add_event(repo, branch, "private", provenance="system_fact")
    assert not apply(repo, branch, "private", actor_id="bea")
    assert apply(repo, branch, "private")
    assert relationship(repo, branch, protagonist_id="michael")["trust"] == 0


def test_all_five_scores_stay_bounded_under_repeated_grounded_events(repo):
    repo.register_actor(
        "profile",
        {
            "id": "avery",
            "starting_relationship": {
                "familiarity": 0.99,
                "trust": 0.99,
                "affinity": 0.99,
                "fear": 0.99,
                "respect": 0.99,
            },
        },
    )
    branch = repo.open_timeline("profile", START)
    for number in range(4):
        for kind in ["conversation_completed", "courtesy_observed", "boundary_respected"]:
            identifier = f"{number}:{kind}"
            add_event(repo, branch, identifier, kind)
            apply(repo, branch, identifier)
    positive = relationship(repo, branch)
    assert positive["familiarity"] == 1
    assert positive["trust"] == 1
    assert positive["affinity"] == 1
    assert positive["respect"] == 1
    for number in range(80):
        identifier = f"threat:{number:03}"
        add_event(repo, branch, identifier, "threat_observed", game_datetime=LATER)
        apply(repo, branch, identifier)
    negative = relationship(repo, branch)
    assert negative["fear"] == 1
    assert negative["trust"] == -1
    assert -1 <= negative["affinity"] <= 1
    assert all(-1 <= negative[field] <= 1 for field in RELATIONSHIP_FIELDS)


@pytest.mark.parametrize(
    "baseline",
    [
        {"trust": 2},
        {"trust": -2},
        {"fear": -0.1},
        {"familiarity": -0.1},
        {"respect": "1"},
        {"affinity": True},
        {"fear": float("nan")},
        {"native_faction": 1},
        [],
    ],
)
def test_authored_baselines_are_validated_before_persistence(repo, baseline):
    with pytest.raises(ValueError, match="relationship"):
        repo.register_actor("profile", {"id": "invalid", "starting_relationship": baseline})
    assert repo.get_actor("profile", "invalid") is None


def test_mild_irritation_fades_by_game_time_without_deleting_its_source(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "interruption", "conversation_interrupted")
    apply(repo, branch, "interruption")
    assert relationship(repo, branch, EVENT)["affinity"] == -0.01
    assert relationship(repo, branch, NEXT_DAY)["affinity"] == pytest.approx(-0.005)
    later = relationship(repo, branch, "2025-01-11T09:00:00Z")
    assert abs(later["affinity"]) < 0.00001
    assert later["source_event_ids"] == ["interruption"]
    assert repo.export_profile("profile")["events"][0]["event_id"] == "interruption"


def test_declining_an_invitation_does_not_create_resentment(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "declined", "invitation_declined")
    assert apply(repo, branch, "declined")
    assert not apply(repo, branch, "declined")
    values = relationship(repo, branch)
    assert all(values[field] == 0 for field in RELATIONSHIP_FIELDS)
    audit = repo.relationship_history("profile", branch, "avery", "franklin", LATER)
    assert audit[0]["reason"] == "invitation_declined"
    assert audit[0]["deltas"] == {}


def test_audit_preserves_source_provenance_confidence_and_effective_decay(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "interruption", "conversation_interrupted", provenance="system_fact")
    apply(repo, branch, "interruption")
    audit = repo.relationship_history("profile", branch, "avery", "franklin", NEXT_DAY)
    assert len(audit) == 1
    assert audit[0]["source_event_ids"] == ["interruption"]
    assert audit[0]["provenance"] == "system_fact"
    assert audit[0]["confidence"] == 0.9
    assert audit[0]["deltas"] == {"affinity": -0.01}
    assert audit[0]["effective_deltas"] == {"affinity": -0.005}
    assert audit[0]["half_life_days"] == {"affinity": 1}


def test_ancestor_projection_keeps_source_branch_and_rewinds_correctly(repo):
    original = repo.open_timeline("profile", START)
    add_event(repo, original, "early", "conversation_completed")
    add_event(repo, original, "future", "courtesy_observed", game_datetime=LATER)
    assert apply(repo, original, "future")
    alternate = repo.open_timeline("profile", FORK)
    # Lazy projection of an actual earlier source belongs to its own history.
    assert apply(repo, alternate, "early")
    assert not apply(repo, original, "early")
    audit = repo.relationship_history("profile", alternate, "avery", "franklin", LATER)
    assert audit[0]["branch_id"] == original
    assert relationship(repo, alternate)["source_event_ids"] == ["early"]
    assert relationship(repo, original)["source_event_ids"] == ["early", "future"]
    with pytest.raises(ValueError, match="absent from this timeline"):
        apply(repo, alternate, "future")
    earlier = repo.open_timeline("profile", START)
    assert relationship(repo, earlier)["source_event_ids"] == []
    assert repo.delete_event("profile", "early")
    assert relationship(repo, alternate)["familiarity"] == 0
    assert relationship(repo, original)["source_event_ids"] == ["future"]


def test_projection_survives_reopen_and_source_deletion(tmp_path):
    location = tmp_path / "reopen.sqlite3"
    with Repository(location) as repo:
        repo.register_actor("profile", {"id": "avery"})
        branch = repo.open_timeline("profile", START)
        add_event(repo, branch, "courtesy")
        apply(repo, branch, "courtesy")
        expected = relationship(repo, branch)
    with Repository(location) as repo:
        assert relationship(repo, branch) == expected
        assert not apply(repo, branch, "courtesy")
        repo.delete_event("profile", "courtesy")
        assert relationship(repo, branch)["trust"] == 0
        assert repo.relationship_history("profile", branch, "avery", "franklin", LATER) == []


def test_modified_stored_deltas_cannot_become_authoritative_scores(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "courtesy")
    apply(repo, branch, "courtesy")
    with repo.connection:
        repo.connection.execute(
            "UPDATE relationship_updates SET deltas=? WHERE event_id=?",
            (json.dumps({"trust": 1}), "courtesy"),
        )
    with pytest.raises(ValueError, match="grounded policy"):
        relationship(repo, branch)


def test_ephemeral_relationships_remain_in_their_gameplay_session(repo):
    repo.register_actor(
        "profile",
        {
            "id": "temporary",
            "actor_kind": "ambient_ephemeral",
            "session_id": "session-a",
        },
    )
    branch = repo.start_session("profile", "session-a", START)["branch_id"]
    add_event(repo, branch, "ephemeral", observer_ids=["temporary"])
    assert apply(repo, branch, "ephemeral", actor_id="temporary", session_id="session-a")
    with pytest.raises(ValueError, match="gameplay session"):
        relationship(repo, branch, actor_id="temporary", session_id="session-b")
    repo.end_session("profile", "session-a")
    assert repo.export_profile("profile")["relationships"] == []


def test_actor_no_crime_reactions_flag_prevents_threat_score_changes(repo):
    repo.register_actor("profile", {"id": "avery", "flags": {"no_crime_reactions": True}})
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "threat", "threat_observed")
    assert not apply(repo, branch, "threat")
    assert relationship(repo, branch)["fear"] == 0


def test_actor_reset_prevents_replaying_forgotten_relationship_sources(repo):
    branch = repo.open_timeline("profile", START)
    add_event(repo, branch, "courtesy", payload={"text": "A private compliment about a bicycle"})
    assert apply(repo, branch, "courtesy")
    repo.reset_actor("profile", "avery")
    assert relationship(repo, branch)["trust"] == 0
    assert not apply(repo, branch, "courtesy")
    assert relationship(repo, branch)["source_event_ids"] == []
    audit = [tuple(row) for row in repo.connection.execute("SELECT * FROM scrub_audit")]
    assert "bicycle" not in json.dumps(audit)
    add_event(repo, branch, "new-courtesy", game_datetime=LATER)
    assert apply(repo, branch, "new-courtesy")
    assert relationship(repo, branch)["trust"] == 0.01


def test_matching_actor_and_source_ids_do_not_transfer_between_profiles(repo):
    original = repo.open_timeline("profile", START)
    add_event(repo, original, "courtesy")
    assert apply(repo, original, "courtesy")
    repo.register_actor("other-profile", {"id": "avery"})
    other = repo.open_timeline("other-profile", START)
    assert repo.get_relationship("other-profile", other, "avery", "franklin", LATER)["trust"] == 0
    with pytest.raises(ValueError, match="canonical event"):
        repo.apply_relationship_event("other-profile", other, "avery", "franklin", "courtesy")


def test_reset_suppression_survives_reopen_branches_and_source_reimport(tmp_path):
    location = tmp_path / "reset.sqlite3"
    with Repository(location) as repo:
        repo.register_actor("profile", {"id": "avery"})
        repo.register_actor("profile", {"id": "bea"})
        original = repo.open_timeline("profile", START)
        source = add_event(repo, original, "shared-courtesy", observer_ids=["avery", "bea"])
        assert apply(repo, original, "shared-courtesy")
        assert apply(repo, original, "shared-courtesy", actor_id="bea")
        add_event(repo, original, "future", game_datetime=LATER)
        alternate = repo.open_timeline("profile", FORK)
        repo.reset_actor("profile", "avery")

    with Repository(location) as repo:
        for branch in (original, alternate):
            assert not apply(repo, branch, "shared-courtesy")
            assert relationship(repo, branch)["source_event_ids"] == []
            assert relationship(repo, branch, actor_id="bea")["trust"] == 0.01
        assert {event["event_id"] for event in repo.export_profile("profile")["events"]} == {
            "shared-courtesy",
            "future",
        }
        # Explicit source deletion still cannot turn a reset source into a new event.
        assert repo.delete_event("profile", "shared-courtesy")
        repo.record_event("profile", original, source)
        assert not apply(repo, alternate, "shared-courtesy")
        assert apply(repo, alternate, "shared-courtesy", actor_id="bea")
        add_event(repo, alternate, "new-courtesy", game_datetime=LATER)
        assert apply(repo, alternate, "new-courtesy")
        assert relationship(repo, alternate)["trust"] == 0.01
        assert relationship(repo, original)["trust"] == 0


def test_reset_suppression_does_not_cross_profiles(repo):
    original = repo.open_timeline("profile", START)
    source = add_event(repo, original, "courtesy")
    assert apply(repo, original, "courtesy")
    repo.reset_actor("profile", "avery")
    repo.register_actor("other-profile", {"id": "avery"})
    other = repo.open_timeline("other-profile", START)
    repo.record_event("other-profile", other, source)
    assert repo.apply_relationship_event("other-profile", other, "avery", "franklin", "courtesy")
    actual = repo.get_relationship("other-profile", other, "avery", "franklin", LATER)
    assert actual["trust"] == 0.01
    assert not apply(repo, original, "courtesy")
