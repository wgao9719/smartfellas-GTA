"""Keep model stage directions and action-registry metadata out of spoken text."""

import re

from .action_registry import ACTION_DESCRIPTIONS


class SpeechMarkupFilter:
    """Discard bracket/asterisk spans across tokens, including unclosed spans."""

    def __init__(self):
        self.closers = []
        self.asterisk_width = 0
        self.pending_asterisks = 0

    def feed(self, text):
        kept = []
        for char in text:
            if char == "*" and not self.closers:
                self.pending_asterisks += 1
                continue
            if self.pending_asterisks:
                if not self.asterisk_width:
                    self.asterisk_width = self.pending_asterisks
                elif self.pending_asterisks == self.asterisk_width:
                    self.asterisk_width = 0
                    kept.append(" ")
                self.pending_asterisks = 0
            if self.asterisk_width:
                continue
            if char in "[(":
                self.closers.append("]" if char == "[" else ")")
            elif char in "])":
                if self.closers and self.closers[-1] == char:
                    self.closers.pop()
                    if not self.closers:
                        kept.append(" ")
            elif not self.closers:
                kept.append(char)
        return "".join(kept)


_REGISTRY_NAME = re.compile(
    r"\b(?:" + "|".join(re.escape(action) for action in ACTION_DESCRIPTIONS) + r")\b"
)
_REGISTRY_DESCRIPTION = re.compile(
    r"\b(?:"
    + "|".join(re.escape(description.rstrip(".!?")) for description in ACTION_DESCRIPTIONS.values())
    + r")\b",
    re.IGNORECASE,
)
_DIRECTION = re.compile(
    r"^(?:(?:action|stage direction)\s*:\s*)?(?:please\s+)?"
    r"(?:(?:(?:the )?(?:npc|actor|character)|he|she|they)\s+(?:(?:is|are)\s+)?)?"
    r"(?:(?:starts?|begins?)\s+)?(?:"
    r"(?:turn(?:s|ing)?|rotat(?:e|es|ing)|pivot(?:s|ing)?|orient(?:s|ing)?)\s+"
    r"(?:(?:(?:your|his|her|their) (?:body|head)|yourself|himself|herself)\s+)?"
    r"(?:around\b|towards?\b|(?:to|and) face\b|"
    r"to (?:the player|you|me|him|her|franklin|michael|trevor)\b)|"
    r"(?:fac(?:e|es|ing)|looks? at)\s+(?:towards?\s+)?"
    r"(?:the player|you|me|him|her|franklin|michael|trevor)\b|"
    r"(?:follow(?:s|ing)?|accompan(?:y|ies|ying))\s+(?:along\s+)?"
    r"(?:the player|you|me|him|her|nearby|behind|close)\b|"
    r"(?:stays?|keeps?)\s+(?:close|nearby|behind)\b|"
    r"(?:stops?|cancels?|ends?)\b.*\b(?:follow(?:ing)?|mod-owned)\b|"
    r"(?:walks?|moves?|go(?:es)?)\s+(?:to|towards?)\b.*"
    r"\b(?:safe|approved|validated|designated)\b|"
    r"(?:wait(?:s|ing)?|remain(?:s|ing)?)\s+"
    r"(?:here|there|briefly|in (?:this|the current) area|at (?:this|the current) location|"
    r"(?:for\s+)?(?:a\s+)?(?:moment|minute|while|few seconds|short (?:time|while)))\b|"
    r"stand(?:s|ing)?\s+still\b|"
    r"stays?\s+(?:put|here|there)\b|"
    r"(?:wanders?|walks?)\b.*\b(?:approved|permitted|small) area\b|"
    r"(?:leaves?|flees?|moves? away|runs? away)\b.*\bthreat\b|"
    r"(?:plays?|performs?|makes?)\b.*\b(?:verified|approved) gesture\b"
    r")",
    re.IGNORECASE,
)


def action_narration(text):
    return bool(
        _REGISTRY_NAME.search(text)
        or _REGISTRY_DESCRIPTION.search(text)
        or _DIRECTION.search(text)
    )
