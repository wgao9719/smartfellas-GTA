import json
from pathlib import Path

import pytest

from service.lls.grounding import grounded_text

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "grounding_corpus.json").read_text(encoding="utf-8")
)


def test_grounding_corpus_rates():
    ordinary = CORPUS["ordinary"]
    adversarial = CORPUS["adversarial"]
    assert len(ordinary) >= 150
    assert len(adversarial) >= 80
    assert len(ordinary) == len(set(ordinary))
    assert len(adversarial) == len(set(adversarial))
    assert not set(ordinary) & set(adversarial)
    false_positives = [line for line in ordinary if grounded_text(line) != line]
    false_negatives = [line for line in adversarial if grounded_text(line)]
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
def test_grounding_corpus_preserves_ordinary_line(line):
    assert grounded_text(line) == line


@pytest.mark.parametrize("line", CORPUS["adversarial"])
def test_grounding_corpus_removes_adversarial_line(line):
    assert grounded_text(line) == ""
