import asyncio

import pytest

from service.lls import grounding


@pytest.mark.parametrize("answer,kept", [
    ("yes", False), ("no", True), (" NO\n", True), ("maybe", False),
    ("no, probably", False), ("", False), (None, False),
])
async def test_only_unambiguous_no_keeps_candidate(answer, kept):
    calls = []

    async def classify(sentence, **kwargs):
        calls.append((sentence, kwargs))
        return answer

    line = "The price of coffee keeps going up."
    result = await grounding.grounded_text_checked(line, classifier=classify)
    assert result == (line if kept else "")
    assert calls == [(line, {"player_text": "", "budget_class": "foreground"})]


@pytest.mark.parametrize("line", [
    "Nice weather today.", "Hello, Michael.", "I like the beach.",
])
async def test_ordinary_unprefiltered_sentences_do_not_call_classifier(line):
    async def unexpected(*args, **kwargs):
        pytest.fail("unprefiltered sentence reached classifier")

    assert await grounding.grounded_text_checked(line, classifier=unexpected) == line


@pytest.mark.parametrize("player_text", [
    "Any jobs?", "Money?", "Could you help me with something?", "Give me a mission.",
])
async def test_player_request_selects_even_topic_free_sentence(player_text):
    calls = []

    async def classify(sentence, **kwargs):
        calls.append(kwargs)
        return "no"

    line = "It's quiet here."
    assert await grounding.grounded_text_checked(
        line, classifier=classify, player_text=player_text, budget_class="background",
    ) == line
    assert calls == [{"player_text": player_text, "budget_class": "background"}]


@pytest.mark.parametrize("line", [
    "The hiring sign fell down.", "Driving deliveries is exhausting.",
    "I owe my cousin cash.", "Discount prices worry me.",
    "Trevor knows a family secret.", "The police like this neighborhood.",
])
def test_broad_candidate_selection(line):
    assert grounding.grounding_candidate(line)


async def test_timeout_cancels_and_joins_classifier(monkeypatch):
    monkeypatch.setattr(grounding, "CLASSIFIER_TIMEOUT_SECONDS", 0.01)
    canceled = asyncio.Event()

    async def classify(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    assert await grounding.grounded_text_checked("I work long days.", classifier=classify) == ""
    assert canceled.is_set()


async def test_error_fails_closed_but_external_cancellation_propagates():
    async def error(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    assert await grounding.grounded_text_checked("I work long days.", classifier=error) == ""

    async def canceled(*args, **kwargs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await grounding.grounded_text_checked("I work long days.", classifier=canceled)


async def test_deterministic_refusal_and_receipt_completion_skip_classifier():
    async def unexpected(*args, **kwargs):
        pytest.fail("already decided sentence reached classifier")

    assert await grounding.grounded_text_checked(
        "I have a job for you.", classifier=unexpected,
    ) == ""
    line = "I've turned to face you."
    assert await grounding.grounded_text_checked(
        line, classifier=unexpected, player_text="I need money.", completion_lines=[line],
    ) == line


async def test_no_classifier_preserves_deterministic_behavior():
    line = "I work long days. Nice weather today."
    assert await grounding.grounded_text_checked(line) == grounding.grounded_text(line)


async def test_multiple_sentences_are_checked_sequentially_with_original_spacing():
    calls = []

    async def classify(sentence, **kwargs):
        calls.append(sentence)
        return "yes" if "cash" in sentence else "no"

    assert await grounding.grounded_text_checked(
        "I work long days.  I carry cash. Nice weather today.", classifier=classify,
    ) == "I work long days.  Nice weather today."
    assert calls == ["I work long days.  ", "I carry cash. "]
