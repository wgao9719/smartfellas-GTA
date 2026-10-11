import pytest
from test_bridge import client as client
from test_bridge import delivery_ack, open_ready, utterance

from service.lls.grounding import NEUTRAL_FALLBACK
from service.lls.providers import FakeProvider


class JobReply(FakeProvider):
    def __init__(self, reply):
        self.reply = reply
        self.action_reply = None

    async def dialogue(self, context):
        for offset in range(0, len(self.reply), 7):
            yield self.reply[offset:offset + 7]

    async def action(self, context, utterance, eligible):
        self.action_reply = utterance
        return None


@pytest.mark.parametrize("player_text,reply,expected", [
    (
        "Got any jobs or missions for me? I need cash.",
        "Hey, looking for some work, huh? This place ain't exactly swarming with jobs. "
        "You might want to check out the docks or the racetrack. "
        "Heard they got some work goin' on there.",
        "Hey, looking for some work, huh? This place ain't exactly swarming with jobs.",
    ),
    (
        "Got any jobs or missions for me? I need cash.",
        "You might want to check out the docks or the racetrack.",
        NEUTRAL_FALLBACK,
    ),
    (
        "What places do you like around here?",
        "You might want to check out the docks or the racetrack.",
        "You might want to check out the docks or the racetrack.",
    ),
    (
        "I need money. Can you help me?",
        "I work at the docks. Money's tight these days. I saw Lamar at the racetrack.",
        "I work at the docks. Money's tight these days. I saw Lamar at the racetrack.",
    ),
])
async def test_player_request_grounds_stream_before_delivery_and_action(
    client, player_text, reply, expected
):
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    runtime.provider = provider = JobReply(reply)
    session = runtime.sessions[sid]
    response, _ = await utterance(client, sid, player_text)
    assert response.status_code == 200
    await session.task
    chunks = [c for c in session.commands.values() if c["kind"] == "dialogue.chunk"]
    spoken = "".join(c["payload"]["text"] for c in chunks).strip()
    assert spoken == expected
    assert provider.action_reply.strip() == expected
    for command in chunks:
        await delivery_ack(client, session, command)
    delivered = "".join(u.text for u in session.delivered_utterances.values()).strip()
    assert delivered == expected
