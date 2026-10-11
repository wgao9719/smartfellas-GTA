"""Policy fixtures do not claim native GTA sensor attestation."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from service.lls.perception import Evidence
from service.lls.triggers import TriggerContext, TriggerEngine, TriggerPack, load_trigger_pack


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


def context(clock, **changes):
    return TriggerContext(
        **{
            "profile_id": "pilot",
            "session_id": "session-a",
            "timeline_branch": "branch-a",
            "protagonist_id": "franklin",
            "game_datetime": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "scene_id": "local",
            "epoch": 0,
            "runtime_generation": 1,
            "area_id": "pilot-commercial-block",
            "actor_id": "avery",
            "runtime_instance_id": "ped-1",
            "role": "bicycle_repairer",
            "control_policy": "ambient",
            "evidence_created_at": clock(),
            "foreground_dialogue": False,
            "player_present": True,
            "alive": True,
            "idle": True,
            "allowed_actions": ("face_player", "wait_here"),
            **changes,
        }
    )


def evidence(**changes):
    return Evidence(
        **{
            "event_id": "event-1",
            "event_type": "actor.greeted",
            "actor_id": "avery",
            "runtime_instance_id": "ped-1",
            "provenance": "heard",
            "text": "Franklin said hello.",
            "confidence": 0.9,
            "distance_meters": 3,
            **changes,
        }
    )


def rule(identifier="greeting", **changes):
    return {
        "id": identifier,
        "match": {"event_types": ["actor.greeted"]},
        "priority": 50,
        "cooldown_seconds": 1,
        "effect": {"type": "speak_to_player", "template": "Hello there."},
        **changes,
    }


def engine(clock, rules=None, limits=None, random_value=lambda: 0.1):
    pack = TriggerPack.model_validate(
        {
            "pack_id": "fixture",
            "rules": rules or [rule()],
            "limits": {
                "local_remark_interval_seconds": 5,
                "actor_remark_cooldown_seconds": 10,
                "area_remark_cooldown_seconds": 7,
                **(limits or {}),
            },
        }
    )
    return TriggerEngine(pack, clock=clock, random_value=random_value)


def test_pilot_pack_is_valid_original_content_with_all_effect_classes():
    pack = load_trigger_pack(Path(__file__).parents[1] / "data" / "pilot_triggers.yaml")
    assert {rule.effect.type for rule in pack.rules} == {
        "speak_to_player",
        "thought",
        "knowledge_write",
        "suggested_action",
    }
    assert pack.limits.local_remark_interval_seconds == 45
    assert TriggerEngine(pack).diagnostics()["native_perception_verified"] is False


@pytest.mark.parametrize(
    "body",
    [
        "pack_id: bad\nrules: []\nrun: rm -rf /\n",
        "pack_id: bad\npack_id: shadow\nrules: []\n",
        "pack_id: bad\nrules: &loop [*loop]\n",
        "!!python/object/apply:os.system ['echo forbidden']",
        # pytest exports the node ID as PYTEST_CURRENT_TEST; Windows caps it at 32767 chars.
        pytest.param("pack_id: bad\nrules: []\n" + "#" * 65536, id="oversize"),
    ],
)
def test_yaml_rejects_code_unknown_keys_duplicates_aliases_and_oversize(tmp_path, body):
    path = tmp_path / "bad.yaml"
    path.write_text(body)
    with pytest.raises((ValueError, yaml.YAMLError)):
        load_trigger_pack(path)


@pytest.mark.parametrize(
    "change",
    [
        {"priority": True},
        {"probability": "1"},
        {"condition": "__import__('os')"},
        {"match": {"event_types": ["global.observer_assertion"]}},
        {"conditions": [{"field": "__class__", "op": "eq", "value": "x"}]},
        {"conditions": [{"field": "confidence", "op": "eq", "value": True}]},
        {"conditions": [{"field": "confidence", "op": "gt", "value": "high"}]},
        {"effect": {"type": "speak_to_player", "template": "{evidence.__class__}"}},
        {"effect": {"type": "speak_to_player", "template": "{evidence!r}"}},
        {"effect": {"type": "knowledge_write", "template": "I witnessed a secret murder."}},
        {"effect": {"type": "suggested_action", "action": {"action_id": "give_money"}}},
        {
            "effect": {
                "type": "suggested_action",
                "action": {"action_id": "face_player"},
                "capabilities": ["give_money"],
            }
        },
    ],
)
def test_rules_cannot_evaluate_code_invent_facts_or_widen_capabilities(clock, change):
    with pytest.raises(ValidationError):
        engine(clock, [rule(**change)])


def test_actor_and_area_scope_and_declarative_conditions(clock):
    conditions = [
        {"field": "confidence", "op": "gte", "value": 0.8},
        {"field": "distance_meters", "op": "lte", "value": 5},
        {"field": "provenance", "op": "in", "value": ["heard", "directly_observed"]},
        {"field": "role", "op": "eq", "value": "bicycle_repairer"},
        {"field": "idle", "op": "eq", "value": True},
    ]
    trigger = engine(
        clock,
        [
            rule(
                conditions=conditions,
                scope={"actor_ids": ["avery"], "area_ids": ["pilot-commercial-block"]},
            )
        ],
    )
    assert trigger.enqueue(evidence(confidence=0.5), context(clock)) == 0
    assert trigger.enqueue(evidence(), context(clock, area_id="elsewhere")) == 0
    assert trigger.enqueue(evidence(actor_id="bea"), context(clock, actor_id="bea")) == 0
    assert trigger.enqueue(evidence(), context(clock)) == 1
    effect = trigger.drain([context(clock)])[0]
    assert effect.actor_id == "avery"
    assert effect.source_event_ids == ("event-1",)


def test_global_observer_assertions_and_mismatched_bindings_never_trigger(clock):
    trigger = engine(clock)
    with pytest.raises(TypeError, match="actor-local Evidence"):
        trigger.enqueue({"observer_ids": ["avery"]}, context(clock))
    assert trigger.enqueue(evidence(actor_id="bea"), context(clock)) == 0
    assert trigger.enqueue(evidence(runtime_instance_id="old-ped"), context(clock)) == 0
    with pytest.raises(TypeError, match="typed trusted"):
        trigger.enqueue(evidence(), {"actor_id": "avery"})


def test_one_local_remark_uses_priority_across_actors(clock):
    trigger = engine(
        clock,
        [
            rule("low", scope={"actor_ids": ["avery"]}, priority=10),
            rule("high", scope={"actor_ids": ["bea"]}, priority=90),
        ],
    )
    avery, bea = context(clock), context(clock, actor_id="bea", runtime_instance_id="ped-2")
    trigger.enqueue(evidence(), avery)
    trigger.enqueue(evidence(actor_id="bea", runtime_instance_id="ped-2"), bea)
    output = trigger.drain([avery, bea])
    assert len(output) == 1
    assert output[0].rule_id == "high"
    assert output[0].actor_id == "bea"


def test_actor_area_and_global_interval_cooldowns_are_independent(clock):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    assert len(trigger.drain([context(clock)])) == 1
    # New actor, new area still cannot bypass the local foreground budget.
    clock.advance(4)
    other = context(clock, actor_id="bea", runtime_instance_id="ped-2", area_id="other")
    assert (
        trigger.enqueue(evidence(event_id="2", actor_id="bea", runtime_instance_id="ped-2"), other)
        == 0
    )
    clock.advance(2)
    # Global interval elapsed, but the original area cooldown has not.
    same_area = context(clock, actor_id="bea", runtime_instance_id="ped-2")
    assert (
        trigger.enqueue(
            evidence(event_id="3", actor_id="bea", runtime_instance_id="ped-2"), same_area
        )
        == 0
    )
    # The original actor cannot move to another area to bypass its own cooldown.
    assert trigger.enqueue(evidence(event_id="4"), context(clock, area_id="other")) == 0
    clock.advance(5)
    assert trigger.enqueue(evidence(event_id="5"), context(clock)) == 1
    assert len(trigger.drain([context(clock)])) == 1


def test_foreground_speech_suppresses_enqueue_and_drops_queued_remarks(clock):
    trigger = engine(clock)
    assert trigger.enqueue(evidence(), context(clock, foreground_dialogue=True)) == 0
    assert trigger.enqueue(evidence(event_id="2"), context(clock)) == 1
    assert trigger.drain([context(clock, foreground_dialogue=True)]) == []
    assert trigger.drain([context(clock)]) == []
    # Even if a candidate has stale foreground metadata, another current
    # actor's foreground flag suppresses speech in the same scene.
    assert trigger.enqueue(evidence(event_id="3"), context(clock)) == 1
    assert (
        trigger.drain([context(clock), context(clock, actor_id="bea", foreground_dialogue=True)])
        == []
    )


def test_memory_effects_preserve_report_sources_and_do_not_become_public(clock):
    trigger = engine(
        clock,
        [
            rule("thought", effect={"type": "thought"}),
            rule("knowledge", effect={"type": "knowledge_write"}),
        ],
    )
    source = evidence(provenance="reported", teller_id="bea", reported_event_id="rumor-event")
    assert trigger.enqueue(source, context(clock, foreground_dialogue=True)) == 2
    output = trigger.drain([context(clock, foreground_dialogue=True)])
    assert len(output) == 2
    for item in output:
        assert item.provenance == "reported"
        assert item.confidence == 0.9
        assert item.teller_id == "bea"
        assert item.reported_event_id == "rumor-event"
        assert item.visibility_scope == "actor"
        assert item.text == "I heard a report from bea: Franklin said hello."
        assert item.source_event_ids == ("event-1",)


def test_speech_does_not_forward_reports_or_interpret_source_code(clock):
    trigger = engine(
        clock, [rule(effect={"type": "speak_to_player", "template": "You mentioned: {evidence}"})]
    )
    source = evidence(
        provenance="reported", teller_id="bea", text="{__import__('os')} is just text."
    )
    assert trigger.enqueue(source, context(clock)) == 0
    assert trigger.drain([context(clock)]) == []
    source = evidence(event_id="direct-hearing", text="{__import__('os')} is just text.")
    trigger.enqueue(source, context(clock))
    effect = trigger.drain([context(clock)])[0]
    assert "{__import__('os')}" in effect.text
    assert effect.provenance == "heard"


def test_suggested_actions_require_current_capability_and_remain_proposals(clock):
    action_rule = rule(effect={"type": "suggested_action", "action": {"action_id": "face_player"}})
    trigger = engine(clock, [action_rule])
    assert trigger.enqueue(evidence(), context(clock, allowed_actions=())) == 0
    assert trigger.enqueue(evidence(event_id="2"), context(clock)) == 1
    assert trigger.drain([context(clock, allowed_actions=())]) == []
    assert trigger.enqueue(evidence(event_id="3"), context(clock)) == 1
    output = trigger.drain([context(clock)])[0]
    assert output.action.action_id == "face_player"
    assert output.requires_authorization
    assert output.consent is False
    assert output.text is None


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_instance_id": "new-ped"},
        {"runtime_generation": 2},
        {"timeline_branch": "rolled-back"},
        {"protagonist_id": "michael"},
        {"epoch": 1},
        {"area_id": "elsewhere"},
        {"alive": False},
        {"control_policy": "protected"},
        {"player_present": False},
        {"idle": False},
    ],
)
def test_queued_effects_revalidate_current_state(clock, changes):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    assert trigger.drain([context(clock, **changes)]) == []


def test_stale_evidence_cannot_be_refreshed_by_new_context(clock):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    clock.advance(2.1)
    assert trigger.drain([context(clock)]) == []
    assert trigger.enqueue(evidence(event_id="2"), context(clock, evidence_created_at=100)) == 0


def test_cancel_scene_rejects_old_epochs_and_prevents_event_replay(clock):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    trigger.cancel_scene("session-a", "local")
    assert trigger.drain([context(clock)]) == []
    assert trigger.enqueue(evidence(event_id="2"), context(clock)) == 0
    assert trigger.enqueue(evidence(), context(clock, epoch=1)) == 0
    assert trigger.enqueue(evidence(event_id="3"), context(clock, epoch=1)) == 1
    assert len(trigger.drain([context(clock, epoch=1)])) == 1


def test_cancel_session_drops_queued_reactions_and_rejects_late_sources(clock):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    trigger.cancel_session("session-a")
    assert trigger.drain([context(clock)]) == []
    assert trigger.enqueue(evidence(event_id="2"), context(clock, epoch=10)) == 0
    assert trigger.enqueue(evidence(event_id="3"), context(clock, session_id="session-b")) == 1


def test_probability_is_deterministic_and_duplicate_events_cannot_reroll(clock):
    rolls = iter([0.8, 0.2])
    trigger = engine(clock, [rule(probability=0.5)], random_value=lambda: next(rolls))
    assert trigger.enqueue(evidence(), context(clock)) == 0
    assert trigger.enqueue(evidence(), context(clock)) == 0
    assert trigger.enqueue(evidence(event_id="2"), context(clock)) == 1
    assert len(trigger.drain([context(clock)])) == 1


def test_queue_and_state_remain_bounded_under_event_flood(clock):
    trigger = engine(
        clock,
        [rule(effect={"type": "thought"})],
        limits={
            "max_queue": 4,
            "max_state_entries": 16,
        },
    )
    for number in range(1000):
        trigger.enqueue(evidence(event_id=f"event-{number}"), context(clock))
    stats = trigger.diagnostics()
    assert stats["queued"] == 4
    assert stats["dedupe_entries"] == 16
    assert stats["scene_entries"] <= 16
    assert stats["counters"]["capacity"] > 0
    clock.advance(121)
    assert trigger.diagnostics()["queued"] == 0
    assert trigger.diagnostics()["dedupe_entries"] == 0


def test_queue_retains_highest_priority_when_capacity_is_full(clock):
    trigger = engine(
        clock,
        [
            rule("low", priority=10, effect={"type": "thought"}),
            rule("high", priority=90, effect={"type": "knowledge_write"}),
        ],
        limits={"max_queue": 1},
    )
    trigger.enqueue(evidence(), context(clock))
    output = trigger.drain([context(clock)])
    assert len(output) == 1
    assert output[0].rule_id == "high"


def test_cancellation_tombstones_are_bounded_and_fail_closed(clock):
    trigger = engine(clock, limits={"max_state_entries": 16})
    for number in range(100):
        trigger.cancel_session(f"old-{number}")
    stats = trigger.diagnostics()
    assert stats["canceled_sessions"] == 16
    assert stats["disabled"] is True
    assert trigger.enqueue(evidence(), context(clock)) == 0


def test_clock_regression_is_rejected(clock):
    trigger = engine(clock)
    trigger.enqueue(evidence(), context(clock))
    clock.advance(-1)
    with pytest.raises(ValueError, match="monotonic"):
        trigger.drain([context(clock)])
