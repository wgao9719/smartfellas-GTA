import json

from service.lls.context import build_context


def context_inputs():
    return {
        "actor": {"actor_id": "a", "display_name": "Avery", "persona": {"goal": "Help."}},
        "snapshot": {"area_id": "current"},
        "world": {"online": False, "_sampled_at": 123},
        "memories": [],
        "knowledge": [],
        "relationship": {k: 0.1 for k in ["familiarity", "trust", "affinity", "fear", "respect"]},
        "input_text": "Hello",
        "eligible": ["face_player"],
        "protagonist_id": "franklin",
        "game_datetime": "2013-09-17T12:00:00",
        "byte_budget": 2048,
    }


def test_unicode_context_is_bounded_and_marks_all_truncation():
    values = context_inputs()
    values["actor"]["persona"] = {"biography": "界" * 10000}
    values["input_text"] = "é" * 4000
    record = {
        "text": "private memory " * 100,
        "provenance": "reported",
        "confidence": 0.5,
        "source_event_ids": ["source-1"],
        "game_datetime": values["game_datetime"],
    }
    values["memories"] = [{**record, "memory_id": f"m{i}"} for i in range(9)]
    values["knowledge"] = [{**record, "card_id": f"k{i}"} for i in range(9)]
    values["action_outcomes"] = [
        {"event_id": f"o{i}", "status": "rejected", "reason": "界" * 500} for i in range(8)
    ]
    context, trace = build_context(**values)
    serialized = json.dumps(context, ensure_ascii=False)
    assert len(serialized.encode()) <= 2048
    assert context["context_truncated"]
    assert trace["context_truncated"]
    assert "_sampled_at" not in context["world"]
    assert "private memory" not in json.dumps(trace)
    assert trace["context_bytes"] == len(serialized.encode())


def test_initial_persona_and_record_limit_clipping_is_visible():
    values = context_inputs()
    values["byte_budget"] = 12000
    values["actor"]["persona"] = {"biography": "x" * 5000}
    context, trace = build_context(**values)
    assert context["context_truncated"]
    assert trace["context_truncated"]
    assert len(context["actor"]["persona"].encode()) == 4000


def test_small_context_preserves_values_and_source_metadata():
    values = context_inputs()
    values["relationship"]["source_event_ids"] = ["relationship-source"]
    context, trace = build_context(**values)
    assert not context["context_truncated"]
    assert context["relationship"]["trust"] == 0.1
    assert context["world"]["area_id"] == "current"
    assert context["protagonist_id"] == "franklin"
    assert trace["relationship_event_ids"] == ["relationship-source"]
