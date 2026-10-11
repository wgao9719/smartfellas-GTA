"""Actor-scoped, bounded model context and text-free diagnostic metadata."""

import json

from .action_registry import ACTION_DESCRIPTIONS
from .grounding import speech_constraints


def clip_utf8(text, maximum):
    return str(text).encode("utf-8")[:maximum].decode("utf-8", errors="ignore")


def build_context(
    *,
    actor,
    snapshot,
    world,
    memories,
    knowledge,
    relationship,
    input_text,
    eligible,
    protagonist_id,
    game_datetime,
    byte_budget,
    action_outcomes=(),
    completion_lines=(),
    input_volume="normal",
):
    def record(item, identity):
        return {
            identity: item[identity],
            "text": item["text"],
            "provenance": item["provenance"],
            "confidence": item["confidence"],
            "source_event_ids": item["source_event_ids"],
            "game_datetime": item["game_datetime"],
        }

    persona = json.dumps(actor.get("persona", {}), ensure_ascii=False)
    context = {
        "actor": {
            "actor_id": actor["actor_id"],
            "display_name": actor["display_name"],
            "persona": clip_utf8(persona, 4000),
        },
        "protagonist_id": protagonist_id,
        "input": input_text,
        "input_volume": input_volume,
        "world": {
            **{k: v for k, v in world.items() if not k.startswith("_")},
            "game_datetime": game_datetime,
            "area_id": snapshot.get("area_id"),
        },
        "memories": [record(m, "memory_id") for m in memories[:8]],
        "knowledge": [record(k, "card_id") for k in knowledge[:8]],
        "relationship": {
            k: relationship[k] for k in ("familiarity", "trust", "affinity", "fear", "respect")
        },
        "eligible_actions": {action: ACTION_DESCRIPTIONS[action] for action in eligible},
        "action_outcomes": list(action_outcomes),
        "speech_constraints": speech_constraints(completion_lines=completion_lines),
        "context_truncated": (
            len(persona.encode("utf-8")) > 4000 or len(memories) > 8 or len(knowledge) > 8
        ),
    }

    def size():
        return len(json.dumps(context, ensure_ascii=False).encode("utf-8"))

    while size() > byte_budget:
        context["context_truncated"] = True
        if len(context["action_outcomes"]) > 2:
            context["action_outcomes"].pop(0)
        elif context["memories"]:
            context["memories"].pop()
        elif context["knowledge"]:
            context["knowledge"].pop()
        elif len(context["actor"]["persona"].encode("utf-8")) > 256:
            original = context["actor"]["persona"]
            context["actor"]["persona"] = clip_utf8(original, len(original.encode("utf-8")) // 2)
        elif len(context["input"].encode("utf-8")) > 128:
            original = context["input"]
            context["input"] = clip_utf8(original, len(original.encode("utf-8")) // 2)
        elif context["action_outcomes"]:
            context["action_outcomes"].pop(0)
        else:
            raise ValueError("configured context budget cannot hold required metadata")
    trace = {
        "memory_ids": [m["memory_id"] for m in context["memories"]],
        "knowledge_ids": [k["card_id"] for k in context["knowledge"]],
        "relationship_event_ids": relationship.get("source_event_ids", [])[:32],
        "action_outcome_ids": [o["event_id"] for o in context["action_outcomes"]],
        "context_bytes": size(),
        "context_truncated": context["context_truncated"],
    }
    return context, trace
