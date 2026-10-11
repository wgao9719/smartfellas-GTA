"""Display rule v1: the exact subtitle text, shared with adapter/LivingNPC.Core/DisplayText.cs.

Both sides turn ~n~ into a space, delete GTA tokens, tags, and any remaining ~ < >,
turn control and line or paragraph separators into spaces, delete invisible format
characters, and collapse whitespace without trimming. Only Python applies NFC and
deletes every Cf, Co, and Cs code point, so service output always passes the adapter.
"""

import re
import unicodedata

DISPLAY_RULE = 1
DISPLAY_MAX_CHARS = 500
_TOKEN = re.compile(r"~[A-Za-z0-9_]{1,40}~")
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:[\t-\r ][^<>]*)?>")
_MARKUP = re.compile(r"[~<>]")
_SPACE = re.compile(r"\s+")
_REMOVED = {"Cf", "Co", "Cs"}


def display_text(text, maximum=None):
    """Apply the rule, then clip at a word boundary when a producer can exceed ``maximum``."""
    text = unicodedata.normalize("NFC", text).replace("~n~", " ")
    text = _MARKUP.sub("", _TAG.sub("", _TOKEN.sub("", text)))
    text = "".join(
        " " if kind == "Cc" or char in "\u2028\u2029" else char
        for char in text
        if (kind := unicodedata.category(char)) not in _REMOVED
    )
    # Deleting markup can expose a combining sequence, so NFC runs last as well.
    text = unicodedata.normalize("NFC", _SPACE.sub(" ", text))
    if maximum is not None and len(text) > maximum:
        cut = text[: maximum + 1]
        space = cut.rfind(" ")
        text = display_text(cut[:space] if space > 0 else text[:maximum])
    return text


def is_display_safe(text):
    return isinstance(text, str) and display_text(text) == text
