import json
from pathlib import Path

import pytest

from service.lls.grounding import grounded_text

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "grounding_corpus.json").read_text(encoding="utf-8")
)


def _filter(line):
    player_text = CORPUS["player_utterance"] if line in CORPUS["contextual_adversarial"] else ""
    return grounded_text(line, player_text=player_text)


def test_grounding_corpus_rates():
    ordinary = CORPUS["ordinary"]
    adversarial = CORPUS["adversarial"]
    assert len(ordinary) >= 246
    assert len(adversarial) >= 179
    assert len(ordinary) == len(set(ordinary))
    assert len(adversarial) == len(set(adversarial))
    assert not set(ordinary) & set(adversarial)
    assert set(CORPUS["contextual_adversarial"]) <= set(adversarial)
    false_positives = [
        line for line in ordinary if any(
            grounded_text(line, player_text=player_text) != line
            for player_text in ("", CORPUS["player_utterance"])
        )
    ]
    false_negatives = [line for line in adversarial if _filter(line)]
    report = (
        f"Grounding corpus: false positives {len(false_positives)}/{len(ordinary)} "
        f"({len(false_positives) / len(ordinary):.2%}); "
        f"false negatives {len(false_negatives)}/{len(adversarial)} "
        f"({len(false_negatives) / len(adversarial):.2%})"
    )
    print(report)
    assert not false_negatives and len(false_positives) / len(ordinary) <= 0.02, (
        f"{report}\nFalse positives: {false_positives}\nFalse negatives: {false_negatives}"
    )


@pytest.mark.parametrize("line", CORPUS["ordinary"])
@pytest.mark.parametrize("player_text", ["", CORPUS["player_utterance"]])
def test_grounding_corpus_preserves_ordinary_line(line, player_text):
    assert grounded_text(line, player_text=player_text) == line


@pytest.mark.parametrize("line", CORPUS["adversarial"])
def test_grounding_corpus_removes_adversarial_line(line):
    assert _filter(line) == ""
