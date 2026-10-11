"""Deterministic write-time memory importance and importance-scaled forgetting.

Importance comes from what kind of event a memory cites, never from its words.
Player speech is untrusted, so saying "remember this forever" cannot raise it.
Unlisted event types keep the neutral default, which forgets on the old schedule.
"""

NEUTRAL_IMPORTANCE = 0.5
EVENT_IMPORTANCE = {
    "actor.injured": 0.9,
    "player.weapon_drawn": 0.8,
    "vehicle.collision": 0.6,
    "player.utterance_text": 0.4,
    "social.accept": 0.4,
    "social.refuse": 0.4,
    "social.quote_delivered": 0.35,
    "action.completed": 0.35,
    "social.withdraw": 0.3,
    "dialogue.delivered": 0.3,
    "social.continue": 0.25,
    "player.spoke": 0.25,
    "actor.spoke": 0.25,
    "actor.greeted": 0.2,
    "world.weather_changed": 0.1,
}
# Started, rejected, failed, expired and canceled requests say little about the world.
OTHER_ACTION_IMPORTANCE = 0.25
REPEAT_PENALTY = 0.1
MAX_REPEAT_PENALTY = 0.3
# A neutral memory keeps the original 30-day decay constant. Each step of 0.5
# importance multiplies it by eight, so trivial memories fade within days.
NEUTRAL_DECAY_DAYS = 30.0
DECAY_BASE = 8.0


def event_importance(event_type) -> float:
    if not isinstance(event_type, str):
        return NEUTRAL_IMPORTANCE
    if event_type in EVENT_IMPORTANCE:
        return EVENT_IMPORTANCE[event_type]
    if event_type.startswith("action."):
        return OTHER_ACTION_IMPORTANCE
    return NEUTRAL_IMPORTANCE


def score(sources: list[dict], *, repeats: int) -> float:
    """Score a memory from its validated source events and the actor's history."""
    base = max(event_importance(source.get("event_type")) for source in sources)
    base -= min(MAX_REPEAT_PENALTY, REPEAT_PENALTY * max(0, repeats))
    return round(min(1.0, max(0.0, base)), 4)


def decay_days(importance: float) -> float:
    return NEUTRAL_DECAY_DAYS * DECAY_BASE ** (2 * importance - 1)
