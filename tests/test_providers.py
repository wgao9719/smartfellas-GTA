import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError
from test_context import context_inputs

from service.lls.context import build_context
from service.lls.gateway_config import GatewayConfig, ModelPrice
from service.lls.outbound import OutboundGate
from service.lls.providers import OpenAIProvider, ProviderResponseError
from service.lls.settings import Settings
from service.lls.storage import Repository


@pytest.fixture
def provider_factory():
    repositories = []

    def create(settings, **kwargs):
        repo = Repository(":memory:")
        repositories.append(repo)
        settings.ai_gateway = GatewayConfig(
            prices={
                settings.dialogue_model: ModelPrice(
                    input_usd_per_million=1, output_usd_per_million=2
                ),
            }
        )
        return OpenAIProvider(settings, gate=OutboundGate(settings, repo), **kwargs)

    yield create
    for repo in repositories:
        repo.close()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, hang=False):
        self.chunks = chunks
        self.hang = hang
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        self.started.set()
        if self.hang:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def frame(content):
    return (
        "data: "
        + json.dumps({"choices": [{"delta": {"content": content}}]}, ensure_ascii=False)
        + "\r\n"
    ).encode()


def local(**kwargs):
    return Settings(provider="local_openai", dialogue_model="local-dialogue", **kwargs)


async def dialogue(provider):
    return "".join([text async for text in provider.dialogue({"input": "hello"})])


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:1234/v1",
        "http://192.168.1.2/v1",
        "https://example.com/v1",
        "http://0.0.0.0/v1",
        "http://127.0.0.1.example.com/v1",
        "http://user:password@127.0.0.1/v1",
        "http://127.0.0.1/v1?key=secret",
        "http://127.0.0.1/v1#secret",
        "ftp://127.0.0.1/v1",
    ],
)
def test_local_provider_requires_literal_loopback_endpoint(endpoint):
    with pytest.raises(ValidationError):
        local(local_base_url=endpoint)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:1234/v1", "http://[::1]:1234/v1"])
def test_local_provider_accepts_explicit_loopback_without_cloud_consent(endpoint):
    assert not local(local_base_url=endpoint).cloud_opt_in
    with pytest.raises(ValidationError, match="explicit model"):
        Settings(provider="local_openai")


async def test_local_stream_handles_fragmented_unicode_without_forwarding_cloud_key(
    monkeypatch, provider_factory
):
    monkeypatch.setenv("LLM_API_KEY", "private-hosted-key")
    wire = frame("Hello 世界. ") + frame("Café.") + b"data: [DONE]\n"
    stream = Stream([wire[i : i + 1] for i in range(len(wire))])
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    provider = provider_factory(local(), transport=httpx.MockTransport(handle))
    assert await dialogue(provider) == "Hello 世界. Café."
    assert "authorization" not in requests[0].headers
    assert "private-hosted-key" not in requests[0].content.decode()
    assert str(requests[0].url) == "http://127.0.0.1:1234/v1/chat/completions"
    assert stream.closed


async def test_hosted_provider_requires_consent_and_credentials(monkeypatch, provider_factory):
    assert not Settings(provider="openai", dialogue_model="hosted").cloud_opt_in
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            provider="openai",
            dialogue_model="hosted",
            cloud_opt_in=True,
            openai_base_url="http://example.com/v1",
        )
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream([frame("Hosted."), b"data: [DONE]\n"]))

    provider = provider_factory(
        Settings(provider="openai", dialogue_model="hosted", cloud_opt_in=True),
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="credentials"):
        await dialogue(provider)
    assert requests == []
    monkeypatch.setenv("LLM_API_KEY", "hosted-test-key")
    assert await dialogue(provider) == "Hosted."
    assert requests[0].headers["authorization"] == "Bearer hosted-test-key"


async def test_local_provider_refuses_redirects(provider_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://remote.example/v1"})

    provider = provider_factory(local(), transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        await dialogue(provider)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("wire", "settings", "message"),
    [
        ([b":" + b"x" * 1024], {"model_response_bytes": 1024}, "response exceeded"),
        ([b"data: " + b"x" * 16384], {}, "event line exceeded"),
        ([b"data: {broken}\n"], {}, "invalid model stream"),
        ([b'data: {"choices":[{"delta":{"content":42}}]}\n'], {}, "invalid model stream"),
    ],
)
async def test_model_stream_limits_close_response(wire, settings, message, provider_factory):
    stream = Stream(wire)
    provider = provider_factory(
        local(**settings),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )
    with pytest.raises(ProviderResponseError, match=message):
        await dialogue(provider)
    assert stream.closed


async def test_reply_character_limit_closes_stream_before_waiting_for_more(provider_factory):
    stream = Stream([frame("界" * 500)], hang=True)
    provider = provider_factory(
        local(max_reply_chars=100),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )
    fragments = []
    with pytest.raises(ProviderResponseError, match="reply character limit"):
        async for fragment in provider.dialogue({"input": "hello"}):
            fragments.append(fragment)
    assert "".join(fragments) == "界" * 100
    assert stream.closed


async def test_provider_cancellation_closes_network_stream(provider_factory):
    stream = Stream([frame("First.")], hang=True)
    provider = provider_factory(
        local(), transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    )
    task = asyncio.create_task(dialogue(provider))
    await asyncio.wait_for(stream.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_provider_whole_call_timeout_closes_stream(provider_factory):
    stream = Stream([], hang=True)
    provider = provider_factory(
        local(model_timeout_seconds=1),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )
    with pytest.raises(TimeoutError):
        await dialogue(provider)
    assert stream.closed


async def test_action_uses_separate_model_only_offered_schemas_and_one_repair(provider_factory):
    requests = []
    replies = [
        {"action_id": "follow_player", "arguments": {}},
        {"action_id": "wait_here", "arguments": {"duration_seconds": 2}},
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        content = json.dumps(replies[len(requests) - 1])
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = provider_factory(
        local(action_model="local-actions"), transport=httpx.MockTransport(handle)
    )
    proposal = await provider.action({"input": "Wait."}, "I can wait.", ["wait_here"])
    assert proposal.action_id == "wait_here"
    assert proposal.arguments.duration_seconds == 2
    assert len(requests) == 2
    assert {request["model"] for request in requests} == {"local-actions"}
    system = requests[0]["messages"][0]["content"]
    assert '"wait_here"' in system
    assert '"follow_player"' not in system
    assert '"additionalProperties": false' in system
    assert len(requests[1]["messages"]) == 4


async def test_dialogue_prompt_excludes_action_metadata_and_requires_spoken_words(provider_factory):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=Stream([frame("Hi."), b"data: [DONE]\n"]))

    provider = provider_factory(local(), transport=httpx.MockTransport(handle))
    context = {
        "input": "Hi there.",
        "eligible_actions": {"face_player": "Turn toward the player."},
        "action_outcomes": [{"action_id": "face_player", "status": "started"}],
        "speech_constraints": {"acknowledged_completion_lines": []},
    }
    assert "".join([part async for part in provider.dialogue(context)]) == "Hi."
    request = requests[0]
    prompt = json.loads(request["messages"][1]["content"])
    assert "eligible_actions" not in prompt
    assert "action_outcomes" not in prompt
    assert "stage directions" in request["messages"][0]["content"]
    assert "Turn toward the player." not in json.dumps(request)
    assert "face_player" in context["eligible_actions"]
    assert request["stream"] is True


async def test_dialogue_serializes_same_actor_history_as_named_chat_turns(provider_factory):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=Stream([frame("You are Franklin."), b"data: [DONE]\n"]))

    values = context_inputs()
    values.update(byte_budget=8000, input_text="What's my name?")
    record = {
        "provenance": "heard", "confidence": 1, "game_datetime": values["game_datetime"],
        "exchange_id": "intro", "source_event_ids": ["intro"],
    }
    introduction = "Hi, I'm Franklin. I fix cars for a living."
    values["recent_conversation"] = [
        {**record, "memory_id": "m1", "role": "player", "text": "The player said: " + introduction},
        {**record, "memory_id": "m2", "role": "npc", "text": "I said: Hi there.",
         "source_event_ids": ["reply-1"], "provenance": "directly_observed"},
        {**record, "memory_id": "m3", "role": "npc", "text": "I said: What brings you by?",
         "source_event_ids": ["reply-2"], "provenance": "directly_observed"},
    ]
    values["memories"] = [{
        **record, "memory_id": "old", "source_event_ids": ["older"],
        "text": "The player said: I like the beach.",
    }]
    context, _ = build_context(**values)
    original = deepcopy(context)
    provider = provider_factory(local(), transport=httpx.MockTransport(handle))
    assert "".join([part async for part in provider.dialogue(context)]) == "You are Franklin."
    messages = requests[0]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "user", "assistant", "user"]
    assert messages[0]["content"].startswith('You are "Avery", the NPC')
    assert "The player is the protagonist named Franklin." in messages[0]["content"]
    assert messages[2]["content"] == "Franklin (the player): " + introduction
    assert messages[3]["content"] == "Hi there. What brings you by?"
    assert messages[4]["content"] == "Franklin (the player): What's my name?"
    supplied = json.loads(messages[1]["content"])
    assert "input" not in supplied
    assert all("text" not in item for item in supplied["recent_conversation"])
    assert supplied["recent_conversation"][0]["source_event_ids"] == ["intro"]
    assert supplied["recent_conversation"][0]["provenance"] == "heard"
    assert supplied["memories"][0]["text"] == "Franklin told you: I like the beach."
    assert introduction not in messages[1]["content"]
    assert "The player said:" not in json.dumps(messages)
    assert "I said:" not in json.dumps(messages)
    assert context == original


async def test_dialogue_budget_includes_history_before_any_provider_request(provider_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream([b"data: [DONE]\n"]))

    provider = provider_factory(
        local(context_byte_budget=2048), transport=httpx.MockTransport(handle),
    )
    context = {
        "actor": {"display_name": "Avery"}, "protagonist_id": "franklin", "input": "Hi.",
        "recent_conversation": [{"role": "player", "text": "The player said: " + "\u754c" * 800}],
    }
    with pytest.raises(ProviderResponseError, match="context exceeds"):
        _ = [part async for part in provider.dialogue(context)]
    assert requests == []


async def test_two_invalid_action_proposals_produce_no_action(provider_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action_id":"shell"}'}}]},
        )

    provider = provider_factory(local(), transport=httpx.MockTransport(handle))
    assert await provider.action({"input": "Run code"}, "No.", ["wait_here"]) is None
    assert len(requests) == 2
    assert await provider.action({"input": "Wait"}, "No.", []) is None
    assert len(requests) == 2


async def test_oversized_action_response_and_context_fail_closed(provider_factory):
    stream = Stream([b"x" * 1025])
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    provider = provider_factory(
        local(model_response_bytes=1024, context_byte_budget=2048),
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(ProviderResponseError, match="response exceeded"):
        await provider.action({"input": "Wait"}, "Okay.", ["wait_here"])
    assert stream.closed
    with pytest.raises(ProviderResponseError, match="context exceeds"):
        await provider.action({"input": "界" * 2048}, "Okay.", ["wait_here"])
    assert len(requests) == 1
