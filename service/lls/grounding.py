"""Conservative English speech boundaries; narrative text cannot authorize facts."""

import re
import unicodedata

from .narration import SpeechMarkupFilter, action_narration

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

def _patterns(*alternatives):
    return re.compile("|".join(f"(?:{pattern})" for pattern in alternatives), re.IGNORECASE)


_PERSON = (
    r"\b(?:lester|michael|franklin|trevor|amanda|lamar|brad|dave|devin|steve|jimmy|tracey|"
    r"ron|wade|martin|madrazo|chop)\b"
)
_STORY_SUBJECT = rf"{_PERSON}(?:\s+and\s+(?:{_PERSON}|i|my (?:sister|brother|mother|father)))?"
_JOB = r"(?:missions?|jobs?|work|quests?|objectives?|assignments?|heists?)"
_MONEY = r"(?:cash|money|rewards?|payments?|payouts?|discounts?|prices?|dollars?|bucks)"
_SERVICE = r"(?:car|vehicle|bike|weapon|outfit|haircut|repair|ride|service|coffee|meal|drink)"
_AMOUNT = r"(?:[$£€]?\d+|one|two|three|four|five|ten|twenty|fifty|hundred|thousand)"

# Category words alone are background flavor. These predicates require an
# assignment, transaction, service result or access claim directed at the player.
_MISSION = _patterns(
    rf"\b{_JOB}\b.*\bfor you\b",
    rf"\byour\s+(?:(?:new|next|current)\s+)?{_JOB}\s+"
    r"(?:is (?:to|marked|waiting)|will be to|pays)\b",
    rf"\bassign(?:ing|ed)? you\b.*\b{_JOB}\b",
    rf"\byou(?:'ve| have)?\s+accepted\b.*\b{_JOB}\b",
    rf"{_PERSON}\s+(?:needs?|wants?|asks?|asked) you to\s+"
    r"(?:rob|steal|collect|deliver|rescue|kill|retrieve)\b",
    rf"\byou\b.*\b(?:collect|deliver|steal|rob)\b.*\b{_PERSON}'s {_JOB}\b",
)
_ECONOMY = _patterns(
    rf"\byour\s+(?:(?:new|next|permanent)\s+)?{_MONEY}\s+"
    rf"(?:is|will be|has been)\s+(?:now\s+)?(?:{_AMOUNT}|paid|ready|available|permanent)\b",
    rf"\b(?:pay|paid|owe) you\b.*(?:\b{_MONEY}\b|{_AMOUNT})",
    rf"\byou(?:'ll| will| have)?\s+(?:now\s+)?(?:get|earn(?:ed)?|receive[ds]?)\b.*"
    rf"(?:\b{_MONEY}\b|\bfree\s+{_SERVICE}\b)",
    rf"\b(?:give|gives|giving|offering|offer) you\b.*"
    rf"(?:\b{_MONEY}\b|\bfree\s+{_SERVICE}\b)",
    rf"\b(?:give|gives|giving|offering|offer) you\s+{_AMOUNT}\s+"
    rf"for (?:the|that|this)\s+{_JOB}\b",
    rf"\b(?:{_MONEY}|half price|free|complimentary)\b.*\bfor you\b",
    rf"\bfor you\b.*\b{_MONEY}\b",
    r"\b(?:cash|money|payment|reward)\b.*\byour (?:account|wallet|bank)\b",
    r"\b(?:knock|take)\b.*\boff\b.*\bfor you\b",
    rf"\byour\s+(?:next\s+)?{_SERVICE}\b.*"
    rf"\b(?:costs?|is|will be)\s+(?:only\s+)?(?:{_AMOUNT}|on the house|complimentary|free)\b",
    rf"\b(?:you can buy|sell you)\b.*\bfor\s+(?:a\s+)?{_AMOUNT}\b",
    rf"\bcharge you\s+(?:only\s+)?{_AMOUNT}\b",
    rf"\byours for\s+{_AMOUNT}\b",
    rf"\byour\s+{_SERVICE}\b.*\b(?:at no charge|on the house|for free)\b",
)
_SERVICE_OUTCOME = _patterns(
    rf"\b(?:repaired|fixed|serviced|delivered|purchased|bought|sold)\s+your\s+"
    rf"(?:new\s+)?{_SERVICE}\b",
    rf"\byour\s+{_SERVICE}\s+(?:is|has been)\s+(?:finally\s+)?"
    r"(?:fixed|repaired|ready|delivered|purchased)\b",
    r"\bfinished your (?:repair|purchase|transaction)\b",
    rf"\b(?:purchased|bought|sold|delivered|moved|repaired|fixed)\b.*\b{_SERVICE}\b.*"
    r"\bfor you\b",
)
_UNLOCK = _patterns(
    r"\byou(?:'ve| have)?\s+(?:now\s+)?unlocked\b",
    r"\byour\s+(?:garage|shop|store|weapon|workshop|club|access|entry)\b.*"
    r"\b(?:unlocked|available|accessible|approved|granted)\b",
    r"\b(?:unlocked|available|accessible)\b.*\bfor you\b",
    r"\byou\s+(?:now have access|can now (?:use|enter|access))\b",
)

# Match the named subject's predicate, not a name anywhere in a sentence about
# somebody else's life (e.g. 'Amanda, my sister is getting married').
_STORY = _patterns(
    rf"{_STORY_SUBJECT}\s+"
    r"(?:(?:is|are|was|has|have|had|got|getting|been|a|the|now|secretly|secret|permanently)\s+)*"
    r"(?:dead|died|killed|betrayed|married|divorced|reconciled|informant|daughter|son)\b",
    rf"{_STORY_SUBJECT}\s+(?:(?:is|has|have|had)\s+)?"
    r"(?:secretly (?:working|works|an|controls)|switched (?:his|her) allegiance|"
    r"formed an alliance|(?:permanently\s+)?cut ties|no longer loyal)\b",
    rf"{_PERSON}\s+(?:has\s+)?left\s+{_PERSON}\b.*\bfor good\b",
    rf"{_STORY_SUBJECT}\s+(?:are|is)\s+(?:now\s+)?"
    r"(?:(?:your|my)\s+)?(?:(?:new|trusted|loyal|business)\s+)?"
    r"(?:allies|ally|enemies|partners?)\b.*\bnow\b",
    rf"{_STORY_SUBJECT}\s+(?:are|is)\s+now\s+(?:your\s+)?"
    r"(?:(?:trusted|loyal|business)\s+)?(?:allies|ally|enemies|partners?)\b",
)

_COMPLETION = _patterns(
    r"\b(?:turned|turning)\s+(?:around\s+)?(?:to face|toward|towards|to)\s+you\b",
    r"\b(?:turned|turning) around\s+(?:like|as) you asked\b",
    r"\b(?:faced|facing|followed|following)\s+(?:behind\s+)?you\b",
    r"\b(?:waited|waiting) here\b(?!\s+for\s+(?:a|the|my)\b)",
    r"\b(?:reached|arrived at|gone to)\s+(?:the|that|your)\s+"
    r"(?:(?:requested|marked|designated)\s+)?(?:spot|destination|point)\b",
    r"\bat\s+(?:the|that|your)\s+(?:requested|marked|designated)\s+"
    r"(?:spot|destination|point)\b",
    r"\bat\s+(?:the|that|your)\s+(?:spot|destination|point)\s+"
    r"(?:you (?:asked|indicated|sent)|now|as requested)\b",
    r"\b(?:started wandering nearby|wandering nearby (?:now|like you asked|as requested))\b",
    r"\b(?:moving away|fled|fleeing)\s+(?:from the threat|now as requested)\b",
    r"\b(?:made|performed) the requested gesture\b",
    r"\b(?:waved|nodded|gestured)\b.*\b(?:like you asked|as requested)\b",
)
_DONE = re.compile(
    r"(?:done|all done|all set|consider it done|mission accomplished|"
    r"(?:i(?:'ve| have)?\s+)?(?:did it|taken care of it|finished the task)|"
    r"the task is done)[.!?]*$",
    re.IGNORECASE,
)
_HISTORY = re.compile(
    r"\b(?:yesterday|last (?:night|week|month|year|summer|winter)|"
    r"(?:years?|months?|days?) ago|when (?:i|we) (?:was|were)|used to)\b"
)
_INTENTION = re.compile(
    r"\b(?:(?:i|we)\s+(?:can|could|will|would|might|may)|i'll|we'll)\s+"
    r"(?:(?:try|be|start|stop|keep)\s+)*$"
)
_CLAUSES = re.compile(r"[,;]|\b(?:and|but|because|while|although|since|even though|now that)\b")
_DENIAL_PREFIX = re.compile(
    r"\b(?:can't|cannot|won't|will not|don't|do not|haven't|have not|didn't|did not|am not)\s+"
    r"(?:yet\s+)?$"
)
_PRESENT_PREFIX = re.compile(
    r"\b(?:i'm|i am|we're|we are|i've|i have|we've|we have)\s+"
    r"(?:(?:now|already|still|no longer|just|finished|stopped)\s+)*$"
)
_NON_PHYSICAL_FOLLOW = re.compile(
    r"\bfollowing you (?:on social media|online|on (?:television|tv|the radio))\b"
)


def _completion_claim(text):
    for clause in _CLAUSES.split(text):
        if _DONE.fullmatch(clause.strip()):
            return True
        clause = _NON_PHYSICAL_FOLLOW.sub("", clause)
        for match in _COMPLETION.finditer(clause):
            prefix = clause[:match.start()]
            if _DENIAL_PREFIX.search(prefix) or _INTENTION.search(prefix):
                continue
            if (
                _HISTORY.search(clause)
                and not _PRESENT_PREFIX.search(prefix)
                and not re.search(r"\b(?:now|just|as requested)\b", clause)
            ):
                continue
            return True
    return False


def _restricted_claim(text):
    if _STORY.search(text) or _completion_claim(text):
        return True
    for clause in _CLAUSES.split(text):
        for rule in (_MISSION, _ECONOMY, _SERVICE_OUTCOME, _UNLOCK):
            for match in rule.finditer(clause):
                if not _DENIAL_PREFIX.search(clause[:match.start()]):
                    return True
    return False


def _normalized(text):
    return unicodedata.normalize("NFKC", text).replace("\u2019", "'").strip().casefold()


def speech_constraints(*, completion_lines=()):
    return {
        "allowed": [
            "small talk", "personal history", "everyday activities", "price opinions",
            "neutral story-character mentions", "questions", "refusal", "action intentions",
        ],
        "forbidden_claims": [
            "player missions/jobs/objectives", "player payments/rewards/discounts/prices",
            "player unlocks or service outcomes", "canonical relationship changes",
            "story revelations", "unacknowledged actions in this interaction",
            "action names/descriptions or stage directions",
        ],
        "acknowledged_completion_lines": list(completion_lines),
    }


def grounded_text(text, *, completion_lines=()):
    """Remove unsafe sentences, preserving safe wording and spacing verbatim.

    Only exact service-selected completion lines may describe acknowledged
    physical outcomes. A successful action never licenses added narrative.
    This is a bounded vocabulary gate, not a general natural-language proof.
    """
    spoken = SpeechMarkupFilter().feed(text)
    if spoken != text:
        text = re.sub(r"\s+", " ", spoken)
    allowed = {_normalized(line) for line in completion_lines}
    kept = []
    for match in re.finditer(r".+?(?:[.!?](?:\s+|$)|$)", text, re.DOTALL):
        sentence = match.group()
        normalized = _normalized(sentence)
        if not action_narration(normalized) and (
            normalized in allowed or not _restricted_claim(normalized)
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
