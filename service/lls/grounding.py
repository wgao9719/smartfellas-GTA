"""Conservative English speech boundaries; narrative text cannot authorize facts."""

import re
import unicodedata

NEUTRAL_FALLBACK = "I'm not sure what to say right now."

COMPLETION_LINES = {
    "face_player": "I've turned to face you.",
    "wait_here": "I've finished waiting here.",
    "follow_player": "I've started following you.",
    "stop_following": "I've stopped following you.",
    "go_to_safe_point": "I've reached the requested spot.",
    "wander_nearby": "I've started wandering nearby.",
    "flee_from_threat": "I've started moving away from the threat.",
    "play_verified_gesture": "I've made the requested gesture.",
}

# No native mission/economy/story evidence interface exists. These domains
# therefore have no generated assertion allowlist, including quoted claims.
_RESTRICTED = re.compile(
    r"\b(?:missions?|quests?|objectives?|heists?|rewards?|payments?|payouts?|discounts?|"
    r"prices?|costs?|charges?|cash|dollars?|bucks|paid|paying|unlocks?|unlocked|unlocking|"
    r"betray\w*|secrets?|secretly|revelations?|plot|factions?|allegiance)\b|[$£€]|"
    r"\b(?:job|work|task|assignment)s?\s+(?:for you|is to|will be|involves)\b|"
    r"\b(?:give|giving|got|have|offer|offering|new|next|assigned|accepted)\b.*"
    r"\b(?:job|assignment)\b|"
    r"\b(?:pay|owe|earn|earned|receive|received)\b.*\b(?:you|me|money|\d+)\b|"
    r"\b(?:shop|store|repair|haircut|service|ride|weapon|car|outfit)s?\b.*"
    r"\b(?:free|on the house|ready|fixed|repaired|purchased|bought)\b|"
    r"\b(?:free|complimentary)\b.*\b(?:repair|haircut|service|ride|weapon|car)\b|"
    r"\b(?:buy|sell|selling|haircut|repair|car|outfit|weapon|ride)\b.*"
    r"\b(?:for|is|only|just)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|fifteen|twenty|thirty|forty|fifty|hundred|thousand)\b|"
    r"\b(?:on the house|at no charge)\b|"
    r"\b(?:access|entry)\s+(?:is\s+)?(?:granted|approved)|"
    r"\b(?:garage|shop|store|weapon|car|service|mission)\b.*"
    r"\b(?:now available|now accessible|access granted)\b|"
    r"\b(?:truth is|real reason|all along|fib|iaa)\b|"
    r"\b(?:lester|michael|franklin|trevor|amanda|lamar|brad|dave|devin|steve|"
    r"jimmy|tracey|ron|wade|martin|madrazo|chop)\b.*"
    r"\b(?:ally|allies|enemy|enemies|friend|partner|loyal|trust\w*|loves?|hates?|"
    r"married|divorc\w*|left|dead|died|killed|father|mother|brother|sister|"
    r"informant|reconcil\w*|needs? you to|wants? you to|asks? you to)\b|"
    r"\b(?:now|forever|permanent\w*)\b.*"
    r"\b(?:ally|allies|enemy|enemies|partner|married|gang|family)\b",
    re.IGNORECASE,
)

_COMPLETION = re.compile(
    r"\b(?:completed|accomplished|succeeded)\b|"
    r"\bfinished\s+(?:(?:the|your|that|this|my|our)\s+)?"
    r"(?:action|task|job|request|repair|delivery|waiting)\b|"
    r"^(?:done|all done|all set|consider it done|mission accomplished)(?:[.! ,;:]|$)|"
    r"\b(?:task|job|request|it)\s+(?:is\s+)?done\b|"
    r"\b(?:did it|taken care of it|turned to face|started following|started wandering|"
    r"started moving away|started fleeing|started waiting)\b|"
    r"\b(?:i|we|you|he|she|they|it|car|vehicle|door)\b.*"
    r"\b(?:turned|faced|waited|followed|stopped|reached|arrived|moved|parked|"
    r"drove|driven|brought|delivered|repaired|fixed|bought|sold|opened|closed|"
    r"transferred|stole|stolen|killed|rescued|saved|escaped|cleared|"
    r"made the requested gesture)\b|"
    r"\b(?:i'm|i am|we're|we are)\s+(?:(?:already|now)\s+)?"
    r"(?:following|facing|waiting|at the (?:spot|destination))\b|"
    r"\b(?:has|have|is|are|was|were|been)\s+(?:already\s+)?"
    r"(?:repaired|fixed|delivered|paid|moved|parked|opened|closed|rescued|cleared)\b",
    re.IGNORECASE,
)


def _normalized(text):
    return unicodedata.normalize("NFKC", text).replace("\u2019", "'").strip().casefold()


def speech_constraints(*, completion_lines=()):
    return {
        "allowed": ["small talk", "questions", "uncertainty", "refusal", "action intentions"],
        "forbidden_claims": [
            "missions/jobs/objectives", "payments/rewards/discounts/prices", "unlocks",
            "canonical relationship changes", "story revelations", "unacknowledged actions",
        ],
        "acknowledged_completion_lines": list(completion_lines),
    }


def grounded_text(text, *, completion_lines=()):
    """Remove unsafe sentences, preserving safe wording and spacing verbatim.

    Only exact service-selected completion lines may describe acknowledged
    physical outcomes. A successful action never licenses added narrative.
    This is a bounded vocabulary gate, not a general natural-language proof.
    """
    allowed = {_normalized(line) for line in completion_lines}
    kept = []
    for match in re.finditer(r".+?(?:[.!?](?:\s+|$)|$)", text, re.DOTALL):
        sentence = match.group()
        normalized = _normalized(sentence)
        if normalized in allowed or not (
            _RESTRICTED.search(normalized) or _COMPLETION.search(normalized)
        ):
            kept.append(sentence)
    return "".join(kept)


def acknowledged_completion_lines(session, actor_id):
    """Use correlated completed receipts for this incarnation, never model context."""
    actor = session.actors.get(actor_id, {})
    lines = []
    for outcome in session.action_outcomes:
        command_id = outcome["event_id"].removesuffix(":completed")
        command = session.commands.get(command_id, {})
        if (
            outcome.get("status") == "completed"
            and outcome.get("provenance") == "system_fact"
            and outcome.get("authority") != "service"
            and outcome.get("session_id") == session.id
            and outcome.get("actor_id") == actor_id
            and outcome.get("protagonist_id") == session.request.protagonist_id
            and command.get("kind") == "action.execute"
            and command.get("session_id") == session.id
            and command.get("actor_id") == actor_id
            and command.get("payload", {}).get("action_id") == outcome.get("action_id")
            and command.get("state") == "completed"
            and command.get("runtime_generation") == session.request.runtime_generation
            and command.get("runtime_instance_id") == actor.get("runtime_instance_id")
            and command.get("_branch_id") == session.branch_id
            and (line := COMPLETION_LINES.get(outcome.get("action_id")))
            and line not in lines
        ):
            lines.append(line)
    return lines
