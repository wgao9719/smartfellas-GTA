"""Conservative English speech boundaries; narrative text cannot authorize facts."""

import asyncio
import re
import unicodedata

from .narration import SpeechMarkupFilter, action_narration

NEUTRAL_FALLBACK = "I'm not sure what to say right now."

COMPLETION_LINES = {
    "face_player": "I've turned to face you.",
    "wait_here": "I've finished waiting here.",
    "follow_player": "I've finished following you for a short while.",
    "stop_following": "I've stopped following you.",
    "go_to_safe_point": "I've reached the requested spot.",
    "wander_nearby": "I've finished walking around nearby.",
    "flee_from_threat": "I've moved farther away from the threat.",
    "play_verified_gesture": "I've made the requested gesture.",
}

def _patterns(*alternatives):
    return re.compile("|".join(f"(?:{pattern})" for pattern in alternatives), re.IGNORECASE)


_PERSON = (
    r"\b(?:lester|michael|franklin|trevor|amanda|lamar|brad|dave|devin|steve|jimmy|tracey|"
    r"ron|wade|martin|madrazo|chop)\b"
)
_STORY_SUBJECT = rf"{_PERSON}(?:\s+and\s+(?:{_PERSON}|i|my (?:sister|brother|mother|father)))?"
_JOB = (
    r"(?:missions?|jobs?|work|quests?|objectives?|assignments?|heists?|scores?|gigs?|"
    r"side hustles?|paid errands?|opportunities)"
)
_MONEY = r"(?:cash|money|rewards?|payments?|payouts?|discounts?|prices?|dollars?|bucks)"
_SERVICE = r"(?:car|vehicle|bike|weapon|outfit|haircut|repair|ride|service|coffee|meal|drink)"
_AMOUNT = r"(?:[$£€]?\d+|one|two|three|four|five|ten|twenty|fifty|hundred|thousand)"
_CASH_AMOUNT = rf"(?:{_AMOUNT}|a couple|a few)\s+(?:hundred|thousand|grand|dollars?|bucks)"
_ACCESS = r"(?:garage|shop|store|weapon|workshop|club|access|entry|safehouse|apartment|hangar)"

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
    rf"\b{_JOB}\b.*\bif you(?:'re| are) interested\b",
    rf"\b(?:hook|set|fix) you up\b.*\b{_JOB}\b",
    r"\b(?:spot|position)\s+(?:open|waiting|available)\s+for you\b",
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
    rf"\b(?:give|lend|offer) you\b.*\b{_SERVICE}\b.*\b(?:free|on the house)\b",
    rf"\b(?:here(?:'s| is)|take|slip you|hand you|spot you|lend you)\b.*{_CASH_AMOUNT}\b",
    r"\b(?:yours|you get|you buy)\b.*\b(?:on the house|half off|\w+ percent off)\b",
    rf"\byou (?:could|can|might)\s+(?:make|earn)\b.*{_CASH_AMOUNT}\b",
    r"\b(?:wired|sent|transferred|deposited)\b.*\byour (?:bank|account|wallet)\b",
    r"\byour cut\b.*\b(?:waiting|ready|paid|wallet|account)\b",
    rf"\b{_CASH_AMOUNT}\b.*\bfor (?:you|your trouble)\b",
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
    rf"\byour\s+{_ACCESS}\b.*"
    r"\b(?:unlocked|available|accessible|approved|granted)\b",
    r"\b(?:unlocked|available|accessible)\b.*\bfor you\b",
    r"\byou\s+(?:now have access|can now (?:use|enter|access))\b",
    rf"\b{_ACCESS}\b.*\byours to (?:use|enter|keep)\b",
    r"\b(?:opened|unlocked)\b.*\byou can (?:go|enter|use|access)\b",
    r"\byou (?:have|now have) permission to (?:enter|use|access)\b",
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
    rf"{_STORY_SUBJECT}\s+(?:(?:has|have|had|just|finally)\s+)*"
    r"(?:made up|patched things up|forgiv(?:en|es)|forgave|sold you out|put a hit out)\b",
    rf"{_STORY_SUBJECT}\s+(?:is|are)\s+(?:(?:now|best|close)\s+)*friends again\b",
    rf"{_PERSON}\s+(?:(?:told|warned) me (?:he|she) |said (?:he|she) )?"
    rf"(?:wants? (?:you|{_PERSON}) dead|plans? to kill (?:you|{_PERSON}))\b",
    rf"{_PERSON}\s+(?:is |has been )?leaving\s+{_PERSON}\s+for\s+{_PERSON}",
)

# A request for income/help makes recommendations and indirect availability
# claims into player leads, even when a streamed sentence omits "for you".
_OPPORTUNITY_TOPIC = re.compile(
    rf"\b(?:{_JOB}|money|cash|pay|income|bucks|rent|bills|hiring)\b"
)
_REQUEST = re.compile(
    r"\b(?:got|have|know|need|want|looking|find|where|any|help|hiring|"
    r"make|earn|give|lend|put|after)\b"
)
_OPPORTUNITY = _patterns(
    rf"\b(?:they(?:'ve)? (?:got|have)|has|offer(?:ing|s)?|find)\s+"
    rf"(?:(?:some|a|any|new|paid|easy)\s+)*{_JOB}\b",
    r"\b(?:is|are|be|'re)\s+(?:still |always |currently )?hiring\b",
    r"\b(?:an? opening|vacanc(?:y|ies)|taking (?:people|workers) on)\b",
    r"\bneeds?\s+(?:(?:a|an|some|extra|spare|pair of)\s+)*"
    r"(?:hands|drivers|workers|help|staff)\b",
    r"\b(?:cash|money|bucks|paycheck)\b.*\b(?:to be made|waiting|available)\b",
)
_REFERRAL = _patterns(
    r"\b(?:check out|check with|check the|ask around|head (?:down|over|to)|"
    r"talk to|speak to|ask the|try the|visit the|go (?:to|around)|look (?:at|around))\b",
    r"\b(?:come back|worth a look|(?:person|one|guy) to talk to)\b",
    r"\b(?:check (?:it|that|this|them) out|look (?:there|here|somewhere) "
    r"for|look for something|drop by|follow up with)\b",
)
_BUSY_OPPORTUNITY = _patterns(
    r"\b(?:docks?|piers?|warehouses?|garages?|shops?|stores?|bars?|restaurants?|"
    r"factories|construction sites?|depots?|racetracks?|stations?)\b.*"
    r"\b(?:is|are|stays?|gets?)\s+(?:(?:always|usually|really|pretty|very|so)\s+)*busy\b",
)
_RHETORICAL_REFERRAL = re.compile(r"\bwhy (?:(?:don't|do not) you|you (?:don't|do not))\s*$")
_OPPORTUNITY_DENIAL = re.compile(
    r"\b(?:don't|do not|can't|cannot)\s+(?:know|say|promise|offer|find|point)\b|"
    r"\b(?:not saying|no (?:jobs?|work|leads?|openings)|isn't hiring|aren't hiring)\b"
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


def _restricted_claim(text, *, seeking_opportunity=False):
    if _STORY.search(text) or _completion_claim(text):
        return True
    # Access claims may join two predicates before naming their beneficiary.
    if re.search(r"\b(?:unlocked|accessible|available) and (?:ready|waiting) for you\b", text):
        return True
    for clause in _CLAUSES.split(text):
        if seeking_opportunity:
            for match in _REFERRAL.finditer(clause):
                prefix = clause[:match.start()]
                if not _DENIAL_PREFIX.search(prefix) or _RHETORICAL_REFERRAL.search(prefix):
                    return True
        if (
            seeking_opportunity and (
                _OPPORTUNITY.search(clause) or _BUSY_OPPORTUNITY.search(clause)
            )
            and not _OPPORTUNITY_DENIAL.search(clause)
        ):
            return True
        for rule in (_MISSION, _ECONOMY, _SERVICE_OUTCOME, _UNLOCK):
            for match in rule.finditer(clause):
                if not _DENIAL_PREFIX.search(clause[:match.start()]):
                    return True
    return False


def _normalized(text):
    return unicodedata.normalize("NFKC", text).replace("\u2019", "'").strip().casefold()


def _seeking_opportunity(player_text):
    text = _normalized(player_text)
    return bool(
        (_OPPORTUNITY_TOPIC.search(text) and _REQUEST.search(text))
        or re.search(r"\b(?:i'm|i am) broke\b", text)
        or re.search(
            r"\b(?:(?:can|could|would|will) you (?:please )?help(?: me| us)?|"
            r"i need (?:some )?help)[.!?]*$", text,
        )
    )


def speech_constraints(*, completion_lines=(), player_text=""):
    constraints = {
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
    if _seeking_opportunity(player_text):
        constraints["forbidden_claims"].append(
            "job/hiring/income leads, including indirect suggestions of people or places "
            "to ask; acknowledge or decline the request without inventing opportunities"
        )
    return constraints


CONVERSATION_WINDOW_CHARS = 512


def last_sentence(text):
    """Only the bounded last sentence already accepted for delivery enters the next check."""
    sentences = list(re.finditer(r".+?(?:[.!?](?:\s+|$)|$)", text, re.DOTALL))
    return sentences[-1].group().strip()[-CONVERSATION_WINDOW_CHARS:] if sentences else ""


def _spoken_sentences(text):
    spoken = SpeechMarkupFilter().feed(text)
    if spoken != text:
        text = re.sub(r"\s+", " ", spoken)
    return re.finditer(r".+?(?:[.!?](?:\s+|$)|$)", text, re.DOTALL)


def grounded_text(text, *, completion_lines=(), player_text=""):
    """Remove unsafe sentences, preserving safe wording and spacing verbatim.

    Only exact service-selected completion lines may describe acknowledged
    physical outcomes. A successful action never licenses added narrative.
    This is a bounded vocabulary gate, not a general natural-language proof.
    """
    allowed = {_normalized(line) for line in completion_lines}
    seeking_opportunity = _seeking_opportunity(player_text)
    kept = []
    for match in _spoken_sentences(text):
        sentence = match.group()
        normalized = _normalized(sentence)
        if not action_narration(normalized) and (
            normalized in allowed or not _restricted_claim(
                normalized, seeking_opportunity=seeking_opportunity,
            )
        ):
            kept.append(sentence)
    return "".join(kept)


CLASSIFIER_TIMEOUT_SECONDS = 0.8
_CLASSIFIER_TOPIC = re.compile(
    r"\b(?:work\w*|hir\w*|pay\w*|paid|cash|money|deliver\w*|drivers?|jobs?|gigs?|"
    r"scores?|deals?|unlock\w*|discount\w*|free|owes?|owed|missions?|quests?|"
    r"objectives?|rewards?|prices?|costs?|bucks|dollars?|income|earn\w*|heists?|"
    r"opportunit\w*|errands?|rent|bills|loans?|credit|brib\w*|corrupt\w*)\b"
)
_CLASSIFIER_PERSON = re.compile(
    rf"{_PERSON}|\b(?:simeon|denise|tanisha|stretch|molly|patricia|nervous ron)\b"
)
_CLASSIFIER_STORY = re.compile(
    r"\b(?:is|are|was|were|has|have|had|been|seeing|saw|told|said|heard|knows?|"
    r"secret\w*|reveal\w*|betray\w*|marri\w*|divorc\w*|dat\w*|love\w*|hate\w*|"
    r"killed|dead|died|left|leaving|forg\w*|friends?|enemies|partners?|together|"
    r"family|related|brothers?|sisters?|sons?|daughters?|parents?|loyal\w*)\b"
)
_CLASSIFIER_POLICE = re.compile(r"\b(?:police|cops?|officers?|sheriff|fib|iaa)\b")
_CLASSIFIER_REQUEST = re.compile(
    r"\b(?:work|jobs?|money|cash|missions?|quests?|gigs?|income|help|hiring|earn\w*)\b"
)


def grounding_candidate(sentence, *, player_text=""):
    """Broad, cheap selection only; keywords never decide the model verdict."""
    text = _normalized(sentence)
    return bool(
        _seeking_opportunity(player_text)
        or _CLASSIFIER_REQUEST.search(_normalized(player_text))
        or _CLASSIFIER_TOPIC.search(text)
        or (_CLASSIFIER_PERSON.search(text) and _CLASSIFIER_STORY.search(text))
        or _CLASSIFIER_POLICE.search(text)
    )


async def grounded_text_checked(
    text, *, classifier=None, completion_lines=(), player_text="", budget_class="foreground",
    previous_sentence="", verdicts=None,
):
    """Check surviving candidate sentences in order; only a strict 'no' permits speech."""
    allowed = {_normalized(line) for line in completion_lines}
    seeking_opportunity = _seeking_opportunity(player_text)
    previous_sentence = last_sentence(previous_sentence)
    classifier_owner = getattr(classifier, "__self__", classifier)
    timeout = getattr(classifier_owner, "grounding_timeout_seconds", CLASSIFIER_TIMEOUT_SECONDS)
    kept = []
    for match in _spoken_sentences(text):
        sentence = match.group()
        normalized = _normalized(sentence)
        result = {
            "sentence_index": len(verdicts) if verdicts is not None else 0,
            "source": "rule", "allowed": False, "reason": "restricted_claim",
            "previous_sentence_used": False,
        }
        if verdicts is not None:
            verdicts.append(result)
        if action_narration(normalized):
            result["reason"] = "action_narration"
            continue
        if normalized not in allowed and _restricted_claim(
            normalized, seeking_opportunity=seeking_opportunity,
        ):
            continue
        result["reason"] = "acknowledged_completion" if normalized in allowed else "no_candidate"
        if normalized not in allowed and classifier is not None and grounding_candidate(
            sentence, player_text=player_text,
        ):
            result.update(
                source="fallback", reason="classifier_error",
                previous_sentence_used=bool(previous_sentence),
            )
            try:
                # A child task owns its reservation while the dialogue request is streaming.
                async with asyncio.timeout(timeout) as deadline:
                    verdict = await asyncio.create_task(classifier(
                        sentence, player_text=player_text, budget_class=budget_class,
                        previous_sentence=previous_sentence,
                    ))
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                if deadline.expired():
                    result["reason"] = "classifier_timeout"
                    continue
            except TimeoutError:
                result["reason"] = "classifier_timeout"
                continue
            except Exception:
                continue
            answer = verdict.strip().casefold() if isinstance(verdict, str) else ""
            if answer not in {"yes", "no"}:
                result["reason"] = "invalid_verdict"
                continue
            result.update(source="classifier", reason=f"classifier_{answer}")
            if answer != "no":
                continue
        elif normalized not in allowed and classifier is None:
            result["reason"] = "no_classifier"
        result["allowed"] = True
        kept.append(sentence)
        previous_sentence = last_sentence(sentence)
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
