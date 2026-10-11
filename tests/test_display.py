import json
import random
import unicodedata
from pathlib import Path

import pytest
from pydantic import ValidationError

from service.lls.display import DISPLAY_MAX_CHARS, DISPLAY_RULE, display_text, is_display_safe
from service.lls.protocol import Command, DialogueChunk, StatusMessage

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "display_text.json").read_text(encoding="utf-8")
)
ALPHABET = [
    *"~<>/=_'\". nrsabB1",
    *"\t\n\r\x00\x07\x1f\x7f\x85",
    *"\u00a0\u2000\u3000\u2028\u2029\u00ad\u200b\u200d\u202e\u2066\u2069\u2060\ufeff\u180e\u061c",
    *"e\u0301\u0338\u00e9\ue000\ud800\U0001f600\U000e0001",
    "~n~", "~r~", "<C>", "</C>", "<b x='1'>", "~HUD_COLOUR_RED~",
]


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda case: repr(case["input"])[:40])
def test_shared_fixtures_define_display_rule(case):
    assert FIXTURES["display_rule"] == DISPLAY_RULE == 1
    assert display_text(case["input"]) == case["display"]
    assert is_display_safe(case["display"])
    assert is_display_safe(case["input"]) == (case["input"] == case["display"])


def test_random_text_never_keeps_markup_or_invisible_characters_and_is_stable():
    generator = random.Random(20261010)
    for _ in range(4000):
        text = "".join(generator.choice(ALPHABET) for _ in range(generator.randint(0, 48)))
        shown = display_text(text)
        assert not set(shown) & set("~<>")
        assert all(unicodedata.category(c) not in {"Cc", "Cf", "Co", "Cs"} for c in shown)
        assert all(c == " " or not c.isspace() for c in shown) and "  " not in shown
        assert display_text(shown) == shown and is_display_safe(shown)
        maximum = generator.randint(1, 24)
        clipped = display_text(text, maximum)
        assert len(clipped) <= maximum and is_display_safe(clipped)


def test_clipping_prefers_word_boundaries():
    assert display_text("alpha beta gamma", 12) == "alpha beta"
    assert display_text("alpha beta gamma", 10) == "alpha beta"
    assert display_text("abcdefghij", 4) == "abcd"
    assert display_text("~r~" + "word " * 200, DISPLAY_MAX_CHARS).endswith("word")
    assert len(display_text("word " * 200, DISPLAY_MAX_CHARS)) <= DISPLAY_MAX_CHARS
    assert not is_display_safe(None) and not is_display_safe(b"bytes")


def chunk(**fields):
    return {
        "text": "Hello there. ",
        "speaker": "Neighbor",
        "scene_id": "scene",
        "turn_id": "turn",
        "chunk_index": 0,
        "final": False,
        **fields,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "Hi ~r~there~s~"),
        ("text", "<C>Now</C>!"),
        ("text", "Line\nbreak"),
        ("text", "bidi \u202eevil"),
        ("text", "two  spaces"),
        ("text", "cafe\u0301"),
        ("speaker", "Neighbor<C>"),
        ("speaker", "~r~Neighbor"),
    ],
)
def test_dialogue_schema_rejects_text_that_skipped_normalization(field, value):
    DialogueChunk.model_validate(chunk())
    with pytest.raises(ValidationError, match="display rule"):
        DialogueChunk.model_validate(chunk(**{field: value}))
    command = {
        "id": "cmd",
        "seq": 1,
        "kind": "dialogue.chunk",
        "session_id": "ses",
        "actor_id": "npc",
        "runtime_generation": 1,
        "runtime_instance_id": "ped",
        "expires_at": "2026-10-10T12:00:00+00:00",
        "idempotency_key": "effect",
        "payload": chunk(**{field: value}),
    }
    with pytest.raises(ValidationError):
        Command.model_validate(command)


def test_status_schema_rejects_unsafe_message():
    StatusMessage.model_validate({"state": "ready", "message": "Living Los Santos: ready."})
    for message in ("~r~Alert", "Two\nlines", "<b>Bold</b>"):
        with pytest.raises(ValidationError):
            StatusMessage.model_validate({"state": "ready", "message": message})
