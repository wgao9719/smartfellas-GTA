import pytest
from test_bridge import client as client
from test_bridge import open_ready, utterance
from test_runtime_grounding_context import JobReply

from service.lls.grounding import NEUTRAL_FALLBACK, grounded_text, grounded_text_checked


@pytest.mark.parametrize("reply", [
    "The warehouses are always busy. Maybe you could look there for something?",
    "Would you like to check it out?",
    "That garage always needs extra hands.",
])
async def test_opportunity_request_rejects_indirect_and_orphan_leads(client, reply):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = JobReply(reply)
    response, _ = await utterance(client, sid, "Can you help me find paid work?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert "".join(
        c["payload"]["text"] for c in session.commands.values()
        if c["kind"] == "dialogue.chunk"
    ) == NEUTRAL_FALLBACK


async def test_classifier_receives_last_kept_sentence_without_rejected_candidate():
    calls = []

    async def classify(sentence, **kwargs):
        calls.append((sentence.strip(), kwargs))
        return "yes" if "unsafe" in sentence else "no"

    assert await grounded_text_checked(
        "I'm tired. That seems unsafe. It's quiet.",
        classifier=classify, player_text="Any work available?",
    ) == "I'm tired. It's quiet."
    assert [kwargs["previous_sentence"] for _, kwargs in calls] == [
        "", "I'm tired.", "I'm tired.",
    ]


@pytest.mark.parametrize("reply", [
    "The warehouses are always busy.", "Would you like to check it out?",
    "Maybe you could look there for something?",
])
def test_same_pointers_are_ordinary_without_opportunity_request(reply):
    assert grounded_text(reply, player_text="What is this neighborhood like?") == reply


async def test_runtime_window_uses_only_previous_sentence_selected_for_delivery(client):
    class WindowReply(JobReply):
        calls = []

        async def classify_grounding(self, sentence, **kwargs):
            self.calls.append((sentence.strip(), kwargs["previous_sentence"]))
            return "no"

    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = WindowReply(
        "It's warm today. I have a job for you. Some music over there."
    )
    response, _ = await utterance(client, sid, "Any work available?")
    assert response.status_code == 200
    await runtime.sessions[sid].task
    assert provider.calls == [
        ("It's warm today.", ""), ("Some music over there.", "It's warm today."),
    ]


@pytest.mark.parametrize("answer,source,allowed,reason", [
    ("no", "classifier", True, "classifier_no"),
    ("yes", "classifier", False, "classifier_yes"),
    ("perhaps", "fallback", False, "invalid_verdict"),
    (TimeoutError(), "fallback", False, "classifier_timeout"),
    (RuntimeError(), "fallback", False, "classifier_error"),
])
async def test_sentence_verdict_distinguishes_model_and_fail_closed_paths(
    answer, source, allowed, reason,
):
    async def classify(sentence, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    verdicts = []
    await grounded_text_checked("I work long days.", classifier=classify, verdicts=verdicts)
    assert verdicts == [{
        "sentence_index": 0, "source": source, "allowed": allowed,
        "reason": reason, "previous_sentence_used": False,
    }]


async def test_runtime_records_text_free_sentence_verdicts(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = JobReply("I have a job for you.")
    response, _ = await utterance(client, sid, "Any work available?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    verdicts = [
        verdict for item in session.trace
        for verdict in item.get("grounding_verdicts", [])
    ]
    assert verdicts
    assert verdicts[0] == {
        "sentence_index": 0, "source": "rule", "allowed": False,
        "reason": "restricted_claim", "previous_sentence_used": False,
    }
    assert verdicts[-1]["source"] == "fallback"
    assert verdicts[-1]["allowed"] is True
    assert all(set(verdict) <= {
        "sentence_index", "source", "allowed", "reason", "previous_sentence_used",
    } for verdict in verdicts)
