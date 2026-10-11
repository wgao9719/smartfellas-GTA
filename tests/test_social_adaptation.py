"""Private, retained social sources control adaptation without inferred reputation."""

import json
import re
from datetime import datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest

from service.lls.social_adaptation import (
    SocialAdaptation,
    _empty,
    _history_sql,
    _unit,
    decision_memory_text,
)
from service.lls.social_models import SocialStart
from service.lls.social_storage import SocialStore
from service.lls.storage import Repository, _date, _protagonist

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


def decide(history, choice="refuse", *, owner="bea", scene="scene", at=GAME, **changes):
    identifier = "decision-" + str(next(history.sequence))
    start = history.start.model_copy(update={"scene_id": scene, **changes})
    history.store.decision(start, owner, choice, identifier, at)
    return identifier


def quote(
    history, *, scene="scene", at=GAME, speaker="avery", listener="bea",
    text="The pier is quiet.", retelling=False, **changes,
):
    identifier = "quote-" + str(next(history.sequence))
    start = history.start.model_copy(update={"scene_id": scene, **changes})
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
        actor_id=speaker, utterance_id=identifier, retell_event_id=source_id, carrier_event_ids=(),
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
    windows = [sql for sql in statements if sql.startswith("SELECT m.actor_id,")]
    # Two walks per owner, decisions then hearing, through one statement shape.
    assert len(windows) == 2 * 129
    assert len({re.sub(r"'[^']*'", "?", sql) for sql in windows}) == 1
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
    history.adaptation = SocialAdaptation(history.repo, sql_steps_per_owner=1000)
    with pytest.raises(ValueError, match="work budget"):
        read(history)
    assert history.repo.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 60
    history.adaptation = SocialAdaptation(history.repo)
    assert read(history)["bea"]["peers"]["avery"]["confirmed_scene_count"] == 30


def legacy_read(history, *, at=GAME, owners=("avery", "bea", "clay"), **scope):
    """The pre-v4 single ranked scan, kept as an oracle for the bounded walks."""
    profile_id, repo = scope.get("profile_id", "profile"), history.repo
    stamp = _date(at.isoformat())
    current = datetime.fromisoformat(stamp)
    lineage = repo._lineage(profile_id, scope.get("branch_id", history.branch), stamp)
    rows = repo.connection.execute(
        "WITH candidates AS (SELECT m.actor_id,m.memory_id,m.text,m.provenance,"
        "m.confidence AS memory_confidence,m.decay_days,e.event_id,e.data,"
        "e.game_datetime,e.branch_id,e.rowid AS source_order,"
        "speaker.data AS speaker_data,speaker.branch_id AS speaker_branch,"
        "speaker.game_datetime AS speaker_time,"
        "ROW_NUMBER() OVER (PARTITION BY m.actor_id,"
        "(json_extract(e.data,'$.event_type')='social.quote_delivered') "
        "ORDER BY e.game_datetime DESC,e.rowid DESC) AS source_rank "
        "FROM memories m INDEXED BY memory_actor "
        "CROSS JOIN actors a ON a.profile_id=m.profile_id AND a.actor_id=m.actor_id "
        "CROSS JOIN memory_sources s ON s.profile_id=m.profile_id AND s.memory_id=m.memory_id "
        "CROSS JOIN events e ON e.profile_id=s.profile_id AND e.event_id=s.event_id "
        "LEFT JOIN events speaker ON speaker.profile_id=e.profile_id "
        "AND speaker.event_id=json_extract(e.data,'$.payload.heard_delivery_event_id') "
        "WHERE m.profile_id=? AND m.actor_id IN (" + ",".join("?" for _ in owners) + ") "
        "AND m.protagonist_id=? AND e.protagonist_id=m.protagonist_id "
        "AND m.visibility_scope='private' AND m.branch_id=e.branch_id "
        "AND m.game_datetime=e.game_datetime AND m.game_datetime>=? AND ("
        + " OR ".join("(m.branch_id=? AND m.game_datetime<=?)" for _ in lineage) + ") "
        "AND (m.session_id IS NULL OR m.session_id=?) "
        "AND (a.actor_kind!='ambient_ephemeral' OR a.session_id=?) "
        "AND (m.expires_at IS NULL OR m.expires_at>?) "
        "AND m.memory_id=e.event_id||':memory' "
        "AND (SELECT COUNT(*) FROM memory_sources ms WHERE ms.memory_id=m.memory_id)=1 "
        "AND json_extract(e.data,'$.event_type') IN "
        "('social.accept','social.refuse','social.continue','social.withdraw',"
        "'social.quote_delivered') "
        "AND ((m.provenance='reported' AND json_extract(e.data,'$.provenance')='reported' "
        "AND json_extract(e.data,'$.event_type')='social.quote_delivered' "
        "AND speaker.event_id IS NOT NULL) "
        "OR (m.provenance='system_fact' AND json_extract(e.data,'$.provenance')='system_fact' "
        "AND json_extract(e.data,'$.event_type')!='social.quote_delivered')) "
        "AND NOT EXISTS (SELECT 1 FROM scrub_audit scrub WHERE scrub.profile_id=m.profile_id "
        "AND ((scrub.operation='delete_memory' AND scrub.object_id=m.memory_id) "
        "OR (scrub.operation='delete_event' AND scrub.object_id IN (e.event_id,speaker.event_id)) "
        "OR (scrub.operation='forget_social_source' "
        "AND scrub.object_id=json_array(m.actor_id,e.event_id)))) "
        ") SELECT * FROM candidates WHERE source_rank<=? ORDER BY game_datetime,source_order",
        (
            profile_id, *owners, _protagonist(scope.get("protagonist_id", "franklin")),
            _date((current - timedelta(days=56)).isoformat()),
            *(item for pair in lineage.items() for item in pair),
            *[scope.get("session_id", "session")] * 2, stamp, 64,
        ),
    ).fetchall()
    result = {owner: _empty() for owner in owners}
    decisions, contacts = {owner: [] for owner in owners}, {owner: {} for owner in owners}
    for row in rows:
        source, owner = json.loads(row["data"]), row["actor_id"]
        payload = source.get("payload", {})
        age = (current - datetime.fromisoformat(row["game_datetime"])).total_seconds()
        if (
            not SocialAdaptation._private_source(source, payload, owner)
            or age < 0 or not _unit(row["memory_confidence"])
        ):
            continue
        if source.get("event_type") == "social.quote_delivered":
            contact = SocialAdaptation._contact(row, source, payload, age)
            if contact:
                contacts[owner].setdefault(contact[0], []).append(contact[1])
        elif SocialAdaptation._decision(row, source, payload):
            decisions[owner].append((row, payload, age))
    for owner in owners:
        SocialAdaptation._boundaries(result[owner], decisions[owner])
        result[owner]["peers"] = {
            peer: SocialAdaptation._familiarity(items) for peer, items in contacts[owner].items()
        }
    return result


def seed_mixed_history(history):
    """Same-instant ties past the window limit, corrections, erasure, rewind, and scope."""
    repo, minutes = history.repo, lambda value: GAME - timedelta(minutes=value)
    for i in range(70):
        decide(history, scene=f"tie-{i}", at=minutes(30))
    for i in range(66):
        quote(history, scene=f"heard-{i}", at=minutes(20))
    quote(history, scene="retold", at=minutes(12), retelling=True)
    decide(history, "withdraw", scene="late", at=minutes(10))
    clay = [
        decide(history, choice, owner="clay", scene=f"clay-{i}", at=minutes(at))
        for i, (choice, at) in enumerate(
            [("accept", 50), ("refuse", 40), ("continue", 35), ("withdraw", 25), ("refuse", 25)]
        )
    ]
    repo.update_memory("profile", clay[2] + ":memory", text="Corrected account.")
    heard = [
        quote(history, speaker="bea", listener="clay", scene=f"clay-heard-{i}", at=minutes(45 - i))
        for i in range(6)
    ]
    repo.update_memory("profile", heard[0] + ":memory", text="Corrected account.")
    repo.delete_memory("profile", heard[1] + ":memory")
    repo.delete_event("profile", heard[2])
    repo.delete_event("profile", event(history, heard[3])["payload"]["heard_delivery_event_id"])
    with repo.connection:
        repo.connection.execute(
            "UPDATE memories SET expires_at=? WHERE memory_id=?",
            ((GAME + timedelta(hours=1)).isoformat(), heard[4] + ":memory"),
        )
        repo.connection.execute(
            "UPDATE memories SET decay_days=0.5 WHERE memory_id=?", (heard[5] + ":memory",)
        )
    for i in range(3):
        quote(history, speaker="clay", listener="avery", scene=f"avery-{i}", at=minutes(15 + i))
    repo.delete_memory("profile", decide(history, owner="avery", at=minutes(9)) + ":memory")
    repo.delete_event("profile", decide(history, owner="avery", at=minutes(8)))
    decide(history, "accept", owner="avery", scene="avery-kept", at=minutes(7))
    # Reset, then reimport the same canonical source and its anchor.
    repo.register_actor("profile", {"id": "dana", "display_name": "dana"})
    dana = {"participant_bindings": {"dana": "dana-1", "avery": "avery-1"}}
    refusal = decide(history, owner="dana", scene="dana-refusal", at=minutes(6), **dana)
    quote(history, listener="dana", scene="dana-heard", at=minutes(6))
    source = event(history, refusal)
    repo.reset_actor("profile", "dana")
    for reimport in (False, True):
        if reimport:
            repo.delete_event("profile", refusal)
            repo.record_event("profile", history.branch, source)
        repo.add_memory(
            "profile", history.branch, "dana", "franklin", minutes(6).isoformat(),
            decision_memory_text("refuse"), [refusal], "system_fact", memory_id=refusal + ":memory",
        )
    decide(history, "withdraw", owner="dana", scene="dana-fresh", at=minutes(4), **dana)
    decide(history, owner="bea", scene="michael", at=minutes(1), protagonist_id="michael")
    quote(history, speaker="bea", listener="avery", at=minutes(1), protagonist_id="michael")
    fork = repo.open_timeline("profile", minutes(15).isoformat(), parent_branch_id=history.branch)
    decide(history, "accept", scene="forked", at=minutes(5), branch_id=fork)
    quote(history, speaker="bea", listener="clay", scene="forked", at=minutes(5), branch_id=fork)
    return fork


def test_bounded_read_matches_reference_scan(history):
    fork = seed_mixed_history(history)
    owners = ("avery", "bea", "clay", "dana", "ghost")
    scopes = [
        {}, {"at": GAME - timedelta(minutes=27)}, {"at": GAME + timedelta(hours=2)},
        {"at": GAME + timedelta(days=3)}, {"at": GAME + timedelta(days=56, minutes=-25)},
        {"protagonist_id": "michael"}, {"branch_id": fork},
        {"branch_id": fork, "at": GAME + timedelta(hours=1)}, {"session_id": "other-session"},
    ]
    for scope in scopes:
        assert read(history, owners=owners, **scope) == legacy_read(history, owners=owners, **scope)
    current = read(history, owners=owners)
    assert current["bea"]["peers"]["avery"]["confirmed_scene_count"] == 64
    assert current["bea"]["recent_boundary_count"] == 3
    assert current["clay"]["peers"]["bea"]["confirmed_scene_count"] == 2
    assert current["dana"]["own_decision_source_event_ids"] != []
    assert current["ghost"] == _empty()


def test_edited_anchor_no_longer_occupies_a_history_window_slot(history):
    identifiers = [
        quote(history, scene=f"encounter-{i}", at=GAME - timedelta(minutes=65 - i))
        for i in range(65)
    ]
    history.repo.update_memory(
        "profile", identifiers[-1] + ":memory", text="I corrected my recollection."
    )
    # The reference scan spent one of its 64 hearing slots on the invalid edit.
    assert legacy_read(history)["bea"]["peers"]["avery"]["confirmed_scene_count"] == 63
    peer = read(history)["bea"]["peers"]["avery"]
    assert peer["confirmed_scene_count"] == 64
    assert identifiers[-1] not in peer["source_event_ids"]


def windowed_history(*, deep):
    """Four owners with full 64+64 windows. Deep history adds rows no walk should visit."""
    repo = Repository()
    branch = repo.open_timeline("profile", (GAME - timedelta(days=80)).isoformat())
    owners = ("o0", "o1", "o2", "o3")
    for actor in (*owners, "x0", "x1"):
        repo.register_actor("profile", {"id": actor, "display_name": actor})
    store, sequence = SocialStore(repo), count()

    def start(owner, other, protagonist):
        return SocialStart(
            scene_id=f"scene-{next(sequence)}", profile_id="profile", branch_id=branch,
            session_id="session", protagonist_id=protagonist, runtime_generation=1,
            initiator_id=owner, participant_bindings={owner: "1", other: "2"},
            area_id="block", interior_id="street", game_datetime=GAME,
            development_attestation=True,
        )

    def decide(owner, at, protagonist="franklin"):
        scene = start(owner, "x1" if owner == "x0" else "x0", protagonist)
        store.decision(scene, owner, "refuse", scene.scene_id, at)
        return scene.scene_id + ":memory"

    def say(speaker, listener, at, protagonist="franklin"):
        scene = start(speaker, listener, protagonist)
        return store.delivered(
            scene,
            SimpleNamespace(
                actor_id=speaker, utterance_id=scene.scene_id, retell_event_id=None,
                carrier_event_ids=(),
            ),
            SimpleNamespace(event_id=scene.scene_id + ":delivered", text="Hi.", sampled_at=at),
            [SimpleNamespace(actor_id=listener, confidence=0.9)], at,
        )[listener]["event_id"]

    def unrelated(owner, at, provenance, text):
        identifier = f"unrelated-{next(sequence)}"
        repo.record_event("profile", branch, {
            "event_id": identifier, "event_type": "action.completed",
            "game_datetime": at.isoformat(), "protagonist_id": "franklin",
            "provenance": provenance, "observer_ids": [owner],
        })
        repo.add_memory(
            "profile", branch, owner, "franklin", at.isoformat(), text, [identifier], provenance,
            memory_id=identifier + ":memory",
        )

    for index, owner in enumerate(owners):
        for minute in range(64):
            decide(owner, GAME - timedelta(minutes=minute))
            say(owners[index - 1], owner, GAME - timedelta(minutes=minute, seconds=30))
    if not deep:
        return repo, branch, owners
    # In all: 900 older decisions, 900 heard and 900 spoken lines, 300 erased anchors
    # interleaved with the windows, and 200 other memories in the windowed provenances.
    for index, owner in enumerate(owners):
        peer = owners[index - 1]
        for i in range(225):
            decide(owner, GAME - timedelta(days=2, minutes=307 * i))
            say(peer, owner, GAME - timedelta(days=2, minutes=307 * i))
        for i in range(75):
            at = GAME - timedelta(seconds=49 * i + 5)
            if i % 2:
                repo.delete_memory("profile", decide(owner, at))
            else:
                repo.delete_event("profile", say(peer, owner, at))
        for i in range(25):
            at = GAME - timedelta(seconds=149 * i + 7)
            unrelated(owner, at, "system_fact", "The game reported my wave request as completed.")
            unrelated(owner, at, "reported", f"I heard a report from {peer}: The pier is busy.")
        for i in range(16):
            decide(owner, GAME - timedelta(minutes=i, seconds=10), "michael")
            say(peer, owner, GAME - timedelta(minutes=i, seconds=20), "michael")
            decide(owner, GAME - timedelta(days=60, minutes=i))
            say(peer, owner, GAME - timedelta(days=60, minutes=i))
    for i in range(100):
        decide("x0", GAME - timedelta(minutes=i, seconds=15))
        say("x1", "x0", GAME - timedelta(minutes=i, seconds=45))
    repo.reset_actor("profile", "x0")
    repo.reset_actor("profile", "x1")
    with repo.connection:
        repo.connection.executemany(
            "INSERT INTO scrub_audit(profile_id,operation,object_id,created_at) VALUES(?,?,?,?)",
            [("elsewhere", "delete_memory", f"memory-{i}", "2026-01-01") for i in range(20_000)],
        )
    return repo, branch, owners


def test_history_work_is_independent_of_depth_and_erasure_volume():
    measured = {}
    for deep in (False, True):
        repo, branch, owners = windowed_history(deep=deep)
        with repo:
            reader = SocialAdaptation(repo)
            values = reader.read("profile", branch, "franklin", GAME, owners, session_id="session")
            measured[deep] = reader.last_sql_steps, values
            if deep:
                # A full deep read needs less than half of the default per-owner budget.
                assert SocialAdaptation(repo, sql_steps_per_owner=32_000).read(
                    "profile", branch, "franklin", GAME, owners, session_id="session"
                ) == values
    (shallow_steps, shallow), (deep_steps, deep) = measured[False], measured[True]
    assert deep == shallow
    assert shallow["o0"]["recent_boundary_count"] == 3
    assert shallow["o0"]["peers"]["o3"]["confirmed_scene_count"] == 64
    assert 0 < deep_steps <= 1.1 * shallow_steps + 2000


def test_history_window_plan_uses_owner_indexes(history):
    stamp = GAME.isoformat()
    plan = [
        row["detail"]
        for row in history.repo.connection.execute(
            "EXPLAIN QUERY PLAN " + _history_sql(2),
            ("profile", "bea", "player:franklin", "reported", "2024-11-06", stamp,
             "branch", stamp, "parent", stamp, "session", stamp, 64),
        )
    ]
    assert "USING INDEX memory_social_window" in plan[0]
    assert sum("USING COVERING INDEX scrub_lookup" in line for line in plan) == 3
    assert not any(line.startswith("SCAN") or "TEMP B-TREE" in line for line in plan)
