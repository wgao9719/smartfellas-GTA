import json
from pathlib import Path

import pytest

from service.lls.action_registry import ACTION_DESCRIPTIONS
from service.lls.grounding import grounded_text

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "action_narration_corpus.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("reply", CORPUS["model_replies"])
def test_local_model_reply_keeps_only_spoken_words(reply):
    assert grounded_text(reply["text"]).strip() == reply["spoken"]


@pytest.mark.parametrize("direction", [
    *ACTION_DESCRIPTIONS, *ACTION_DESCRIPTIONS.values(), *CORPUS["directions"],
])
def test_action_narration_never_becomes_speech(direction):
    assert grounded_text(direction).strip() == ""


@pytest.mark.parametrize("line", CORPUS["ordinary"])
def test_action_narration_filter_preserves_conversation(line):
    assert grounded_text(line) == line
