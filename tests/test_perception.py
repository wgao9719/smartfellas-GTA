from datetime import datetime, timedelta, timezone

import pytest

from service.lls.perception import DeliveredUtterance, Incident, Witness, witnesses_for

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def incident(**changes):
    return Incident(
        **{
            "sampled_at": NOW,
            "event_id": "event",
            "event_type": "player.weapon_drawn",
            "position": {"x": 0, "y": 0, "z": 0},
            "area_id": "block-a",
            "interior_id": "outside",
            "text": "The player drew a weapon.",
            "modality": "visual",
            "confidence": 0.9,
            **changes,
        }
    )


def witness(**changes):
    return Witness(
        **{
            "sampled_at": NOW,
            "actor_id": "npc",
            "runtime_instance_id": "instance",
            "position": {"x": 2, "y": 0, "z": 0},
            "area_id": "block-a",
            "interior_id": "outside",
            "exists": True,
            "alive": True,
            "line_of_sight": True,
            "within_view": True,
            "can_hear": True,
            "confidence": 0.8,
            **changes,
        }
    )


def observe(event=None, actor=None, bindings=None):
    return witnesses_for(
        event or incident(),
        [actor or witness()],
        NOW,
        {"npc": "instance"} if bindings is None else bindings,
    )


def test_visual_evidence_retains_binding_and_caps_confidence():
    result = observe()[0]
    assert result.provenance == "directly_observed"
    assert result.runtime_instance_id == "instance"
    assert result.confidence == 0.8
    assert result.distance_meters == 2


@pytest.mark.parametrize(
    "change",
    [
        {"line_of_sight": None},
        {"line_of_sight": False},
        {"within_view": None},
        {"area_id": None},
        {"interior_id": None},
        {"interior_id": "shop"},
        {"alive": False},
        {"exists": False},
        {"confidence": 0.2},
        {"runtime_instance_id": "recycled"},
        {"sampled_at": NOW - timedelta(seconds=3)},
        {"sampled_at": NOW + timedelta(seconds=1)},
        {"position": {"x": 41, "y": 0, "z": 0}},
    ],
)
def test_unseen_unknown_stale_or_unbound_actor_gets_no_evidence(change):
    assert not observe(actor=witness(**change))


def test_hearing_whisper_range_and_unknown_sensor():
    normal = incident(modality="audible", event_type="player.spoke", text="Hello.")
    assert observe(normal)[0].provenance == "heard"
    assert not observe(normal, witness(can_hear=None))
    assert not observe(
        normal.model_copy(update={"whisper": True}), witness(position={"x": 4, "y": 0, "z": 0})
    )
    assert not observe(normal, witness(position={"x": 21, "y": 0, "z": 0}))


def test_heard_report_never_becomes_eyewitness_truth():
    report = incident(
        modality="report",
        event_type="actor.spoke",
        text="Lee said a car crashed.",
        source_actor_id="lee",
        reported_event_id="unknown-crash",
    )
    receipt = DeliveredUtterance(
        event_id="event",
        actor_id="lee",
        runtime_instance_id="lee-instance",
        sampled_at=NOW,
        text=report.text,
    )
    bindings = {"npc": "instance", "lee": "lee-instance"}
    heard = witnesses_for(
        report, [witness()], NOW, bindings, delivered_utterances={"event": receipt}
    )[0]
    assert heard.provenance == "reported"
    assert heard.teller_id == "lee"
    assert heard.reported_event_id == "unknown-crash"
    assert not observe(report)
    assert not witnesses_for(
        report, [witness()], NOW, {"npc": "instance"}, delivered_utterances={"event": receipt}
    )
    altered = report.model_copy(update={"text": "A different claim was never delivered."})
    assert not witnesses_for(
        altered, [witness()], NOW, bindings, delivered_utterances={"event": receipt}
    )
    assert not observe(report.model_copy(update={"source_actor_id": None}))


def test_stale_incident_and_duplicate_witness_refused():
    assert not observe(incident(sampled_at=NOW - timedelta(seconds=3)))
    with pytest.raises(ValueError, match="duplicate"):
        witnesses_for(incident(), [witness(), witness()], NOW, {"npc": "instance"})
    assert not observe(bindings={})
