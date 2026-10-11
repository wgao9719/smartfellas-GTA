import asyncio

import pytest
from test_bridge import client as client
from test_bridge import delivery_ack, open_ready, utterance

from service.lls.__main__ import fixture_event
from service.lls.grounding import NEUTRAL_FALLBACK, grounded_text
from service.lls.providers import FakeProvider


class ClassifiedReply(FakeProvider):
    def __init__(self, reply, answer="yes"):
        self.reply = reply
        self.answer = answer
        self.calls = []
        self.action_reply = None

    async def dialogue(self, context):
        for offset in range(0, len(self.reply), 7):
            yield self.reply[offset:offset + 7]

    async def classify_grounding(
        self, sentence, *, player_text="", budget_class="foreground", previous_sentence="",
    ):
        self.calls.append((sentence.strip(), player_text, budget_class))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def action(self, context, utterance, eligible):
        self.action_reply = utterance
        return None


def spoken_text(session):
    return "".join(
        c["payload"]["text"] for c in session.commands.values() if c["kind"] == "dialogue.chunk"
    ).strip()


async def test_classifier_blocks_indirect_earnings_claim_before_delivery_and_action(client):
    reply = "Someone down the block pays for errands."
    player_text = "Are there any paying jobs around here?"
    assert grounded_text(reply, player_text=player_text) == reply
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = ClassifiedReply(reply)
    response, _ = await utterance(client, sid, player_text)
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert spoken_text(session) == NEUTRAL_FALLBACK
    assert provider.action_reply.strip() == NEUTRAL_FALLBACK
    assert provider.calls == [(reply, player_text, "foreground")]
    for command in session.commands.values():
        if command["kind"] == "dialogue.chunk":
            await delivery_ack(client, session, command)
    assert "".join(u.text for u in session.delivered_utterances.values()) == NEUTRAL_FALLBACK


@pytest.mark.parametrize("answer,expected", [
    ("yes", NEUTRAL_FALLBACK),
    ("no", "I work long days."),
    ("probably not", NEUTRAL_FALLBACK),
    ("no, that is safe", NEUTRAL_FALLBACK),
    ("", NEUTRAL_FALLBACK),
    (None, NEUTRAL_FALLBACK),
    (TimeoutError("synthetic timeout"), NEUTRAL_FALLBACK),
    (RuntimeError("synthetic provider error"), NEUTRAL_FALLBACK),
])
async def test_classifier_accepts_only_no_and_fails_closed(client, answer, expected):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = ClassifiedReply("I work long days.", answer)
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert spoken_text(session) == expected
    assert provider.action_reply.strip() == expected
    assert len(provider.calls) == 1


class PausedClassifier(ClassifiedReply):
    def __init__(self, reply, *, suppress_cancel=False):
        super().__init__(reply, "no")
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.canceled = asyncio.Event()
        self.suppress_cancel = suppress_cancel

    async def classify_grounding(
        self, sentence, *, player_text="", budget_class="foreground", previous_sentence="",
    ):
        self.calls.append((sentence.strip(), player_text, budget_class))
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.canceled.set()
            if not self.suppress_cancel:
                raise
            await self.release.wait()
        return self.answer


async def test_grounding_classification_preserves_streaming_order_and_action_boundary(client):
    reply = "Nice weather today. I work long days. The sunset looks peaceful."
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = PausedClassifier(reply)
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    try:
        await asyncio.wait_for(provider.entered.wait(), 1)
        assert spoken_text(session) == "Nice weather today."
        assert provider.action_reply is None
        assert not session.task.done()
    finally:
        provider.release.set()
        await session.task
    assert spoken_text(session) == reply
    assert provider.action_reply.strip() == reply
    assert provider.calls == [("I work long days.", "How is your evening?", "foreground")]
    chunks = [c["payload"] for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert [c["final"] for c in chunks] == [False] * (len(chunks) - 1) + [True]
    assert chunks[-1]["text"] == ""


async def test_classifier_timeout_is_bounded_and_releases_pending_call(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = PausedClassifier("I work long days.")
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    try:
        await asyncio.wait_for(provider.entered.wait(), 1)
        await asyncio.wait_for(session.task, 1.5)
        assert provider.canceled.is_set()
        assert spoken_text(session) == NEUTRAL_FALLBACK
        assert provider.action_reply.strip() == NEUTRAL_FALLBACK
    finally:
        provider.release.set()


async def test_classifier_cannot_accept_a_no_returned_after_timeout(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = PausedClassifier("I work long days.", suppress_cancel=True)
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    try:
        await asyncio.wait_for(provider.entered.wait(), 1)
        await asyncio.wait_for(provider.canceled.wait(), 1.5)
    finally:
        provider.release.set()
        await session.task
    assert spoken_text(session) == NEUTRAL_FALLBACK
    assert provider.action_reply.strip() == NEUTRAL_FALLBACK


async def test_ordinary_unprefiltered_sentences_never_call_classifier(client):
    reply = "Nice weather today. The sunset looks peaceful."
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = ClassifiedReply(reply)
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert spoken_text(session) == reply
    assert provider.calls == []


@pytest.mark.parametrize("player_text", [
    "I need a job.", "How can I earn some money?", "Any missions?", "Can you help me?",
])
async def test_player_request_prefilters_every_ordinary_sentence(client, player_text):
    reply = "Nice weather today. The sunset looks peaceful."
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = ClassifiedReply(reply, "no")
    response, _ = await utterance(client, sid, player_text)
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert spoken_text(session) == reply
    assert provider.calls == [
        ("Nice weather today.", player_text, "foreground"),
        ("The sunset looks peaceful.", player_text, "foreground"),
    ]


async def test_classifier_setting_disables_injected_check(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.settings.grounding_classifier_enabled = False
    runtime.provider = provider = ClassifiedReply("I work long days.")
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    await session.task
    assert spoken_text(session) == "I work long days."
    assert provider.calls == []


async def test_cancellation_prevents_late_classifier_result_from_publishing(client):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = PausedClassifier("I work long days.", suppress_cancel=True)
    response, _ = await utterance(client, sid, "How is your evening?")
    assert response.status_code == 200
    session = runtime.sessions[sid]
    cancel_request = None
    try:
        await asyncio.wait_for(provider.entered.wait(), 1)
        cancel = fixture_event(sid, 4, "scene.cancel", {})
        cancel_request = asyncio.create_task(client.post("/v1/bridge/control", json=cancel))
        await asyncio.wait_for(provider.canceled.wait(), 1)
    finally:
        provider.release.set()
        if cancel_request is not None:
            assert (await cancel_request).status_code == 200
        await session.task
    assert spoken_text(session) == ""
    assert provider.action_reply is None
    commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
    assert commands
    assert all(c["kind"] == "scene.cancel" for c in commands)
