import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_admin import client as client
from test_admin_provider import configuration, consent_body
from test_providers import Stream

from service.lls.anthropic_provider import (
    AnthropicProvider,
    api_schema,
    merged_turns,
)
from service.lls.gateway import GatewayProvider
from service.lls.gateway_config import GatewayConfig, LocalFallback, ModelPrice
from service.lls.outbound import OutboundDenied, OutboundGate
from service.lls.providers import OpenAIProvider, ProviderResponseError
from service.lls.settings import Settings
from service.lls.storage import Repository

MODEL = "claude-sonnet-5"


@pytest.fixture
def claude_factory(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-test-key-0123456789abcdef")
    repositories = []

    def create(handler, **settings):
        repo = Repository(":memory:")
        repositories.append(repo)
        values = {"provider": "anthropic", "dialogue_model": MODEL, "cloud_opt_in": True}
        configured = Settings(**{**values, **settings})
        configured.ai_gateway = GatewayConfig(
            prices={MODEL: ModelPrice(input_usd_per_million=4, output_usd_per_million=20)}
        )
        return AnthropicProvider(
            configured,
            gate=OutboundGate(configured, repo),
            transport=httpx.MockTransport(handler),
        )

    yield create
    for repo in repositories:
        repo.close()


def event(kind, **fields):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n".encode()


def message_start(input_tokens=120):
    return event(
        "message_start",
        message={"type": "message", "role": "assistant", "content": [],
                 "usage": {"input_tokens": input_tokens, "output_tokens": 1}},
    )


def text_block(index, *parts):
    return [
        event("content_block_start", index=index, content_block={"type": "text", "text": ""}),
        *[
            event("content_block_delta", index=index,
                  delta={"type": "text_delta", "text": part})
            for part in parts
        ],
        event("content_block_stop", index=index),
    ]


def finish(stop_reason="end_turn", output_tokens=40):
    return [
        event("message_delta", delta={"stop_reason": stop_reason},
              usage={"output_tokens": output_tokens}),
        event("message_stop"),
    ]


def reply(text, stop_reason="end_turn", usage=None):
    """A complete non-streaming Messages API response with an omitted thinking block."""
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": MODEL,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 80, "output_tokens": 12},
    })


async def speak(provider, context=None):
    return "".join([part async for part in provider.dialogue(context or {"input": "hello"})])


def keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(keys(item) for item in value))
    return set()


def test_settings_require_model_https_and_default_to_medium_effort():
    with pytest.raises(ValidationError, match="explicit model"):
        Settings(provider="anthropic")
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(provider="anthropic", dialogue_model=MODEL,
                 anthropic_base_url="http://api.example/v1")
    with pytest.raises(ValidationError):
        Settings(provider="anthropic", dialogue_model=MODEL, anthropic_effort="adaptive")
    configured = Settings(provider="anthropic", dialogue_model=MODEL)
    assert configured.anthropic_effort == "medium"
    assert configured.cloud_opt_in is False


def test_gateway_file_supplies_prices_and_limits(tmp_path, monkeypatch):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({
        "prices": {MODEL: {"input_usd_per_million": 4, "output_usd_per_million": 20}},
        "dialogue_output_tokens": 2048,
    }))
    monkeypatch.setenv("LLS_AI_GATEWAY_FILE", str(path))
    monkeypatch.setenv("LLS_DATABASE", ":memory:")
    loaded = Settings.from_env()
    assert loaded.ai_gateway.prices[MODEL].output_usd_per_million == 20
    assert loaded.ai_gateway.dialogue_output_tokens == 2048
    monkeypatch.delenv("LLS_AI_GATEWAY_FILE")
    assert Settings.from_env().ai_gateway.prices == {}


def test_rig_gateway_file_prices_the_default_model_with_thinking_room():
    path = Path(__file__).parents[1] / "tools" / "windows-rig" / "claude-gateway.json"
    config = GatewayConfig.model_validate_json(path.read_text(encoding="utf-8"))
    assert MODEL in config.prices
    assert config.dialogue_output_tokens >= 1024 and config.action_output_tokens >= 512
    assert config.memory_output_tokens >= 512
    assert config.background.max_requests == 0
    script = path.with_name("start-service.ps1").read_text(encoding="utf-8")
    assert "[string]$Model = '" + MODEL + "'" in script
    # The key comes from the environment or the git-ignored .env, never the script.
    assert "secrets\\LLM_API_KEY.txt" in script and "sk-ant-" not in script


async def test_dialogue_streams_only_text_and_settles_reported_usage(claude_factory):
    requests = []
    wire = [
        message_start(),
        event("content_block_start", index=0,
              content_block={"type": "thinking", "thinking": ""}),
        event("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": ""}),
        event("content_block_delta", index=0, delta={"type": "signature_delta", "signature": "s"}),
        event("content_block_stop", index=0),
        event("ping"),
        *text_block(1, "Morning. ", "Large black again?"),
        *finish(),
    ]
    stream = Stream(wire)

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    provider = claude_factory(handle)
    context = {
        "actor": {"display_name": "Malik Reed"},
        "protagonist_id": "franklin",
        "recent_conversation": [
            {"memory_id": "m1", "role": "player", "text": "The player said: Morning."},
            {"memory_id": "m2", "role": "npc", "text": "I said: Large, black?"},
        ],
        "input": "Hey. Another one, please.",
    }
    assert await speak(provider, context) == "Morning. Large black again?"
    assert stream.closed
    request = requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test-key-0123456789abcdef"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == MODEL and body["stream"] is True
    assert body["output_config"] == {"effort": "medium"}
    assert "temperature" not in body and "top_p" not in body
    assert body["system"].startswith('You are "Malik Reed"')
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][-1]["content"].endswith("Another one, please.")
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 1 and usage["pending_reservations"] == 0
    assert (usage["reported_input_tokens"], usage["reported_output_tokens"]) == (120, 40)
    assert usage["estimated_micro_usd"] == 120 * 4 + 40 * 20
    rows = provider.gate.ledger.repository.connection.execute(
        "SELECT provider, state FROM ai_requests"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("anthropic", "settled")]


async def test_dialogue_requires_consent_and_credentials(claude_factory, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream([message_start(), *text_block(0, "Hi."),
                                                  *finish()]))

    provider = claude_factory(handle, cloud_opt_in=False)
    with pytest.raises(OutboundDenied, match="consent"):
        await speak(provider)
    provider = claude_factory(handle)
    monkeypatch.delenv("LLM_API_KEY")
    with pytest.raises(RuntimeError, match="credentials"):
        await speak(provider)
    assert requests == []


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        ([message_start(), event("error", error={"type": "overloaded_error",
                                                  "message": "Overloaded"})],
         "overloaded_error"),
        ([message_start(), *text_block(0, "Cut off")], "completion marker"),
        ([*text_block(0, "No start.")], "invalid model stream"),
        ([message_start(),
          event("content_block_start", index=0, content_block={"type": "tool_use"})],
         "invalid model stream"),
    ],
)
async def test_stream_failures_close_response(claude_factory, wire, message):
    stream = Stream(wire)
    provider = claude_factory(lambda _: httpx.Response(200, stream=stream))
    with pytest.raises(ProviderResponseError, match=message):
        await speak(provider)
    assert stream.closed
    assert provider.gate.usage_summary()["foreground"]["uncertain_requests"] == 1


async def test_refusal_is_reported_after_settling(claude_factory):
    wire = [message_start(), *text_block(0, "I can't."), *finish("refusal")]
    provider = claude_factory(lambda _: httpx.Response(200, stream=Stream(wire)))
    with pytest.raises(ProviderResponseError, match="refused"):
        await speak(provider)
    assert provider.gate.usage_summary()["foreground"]["uncertain_requests"] == 0


async def test_reply_limit_and_cancellation_close_stream(claude_factory):
    stream = Stream([message_start(), *text_block(0, "界" * 500)], hang=True)
    provider = claude_factory(lambda _: httpx.Response(200, stream=stream),
                              max_reply_chars=100)
    with pytest.raises(ProviderResponseError, match="reply character limit"):
        await speak(provider)
    assert stream.closed
    stream = Stream([message_start(), *text_block(0, "First.")], hang=True)
    provider = claude_factory(lambda _: httpx.Response(200, stream=stream))
    task = asyncio.create_task(speak(provider))
    await asyncio.wait_for(stream.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_cloud_scrubber_removes_credentials_before_sending(claude_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream([message_start(), *text_block(0, "Okay."),
                                                  *finish()]))

    provider = claude_factory(handle)
    await speak(provider, {"input": "my key is sk-ant-test-key-0123456789abcdef"})
    assert "sk-ant-test-key" not in requests[0].content.decode()


async def test_action_uses_structured_output_and_validates_bounds_locally(claude_factory):
    requests = []
    replies = [
        {"proposal": {"action_id": "wait_here", "arguments": {"duration_seconds": 99}}},
        {"proposal": {"action_id": "wait_here", "arguments": {"duration_seconds": 2}}},
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        return reply(json.dumps(replies[len(requests) - 1]))

    provider = claude_factory(handle, action_model="claude-haiku-5-5")
    provider.gate.config.prices["claude-haiku-5-5"] = ModelPrice(
        input_usd_per_million=1, output_usd_per_million=5
    )
    proposal = await provider.action({"input": "Wait here."}, "Sure, I'll wait.", ["wait_here"])
    assert proposal.action_id == "wait_here"
    assert proposal.arguments.duration_seconds == 2
    assert len(requests) == 2
    first = requests[0]
    assert first["model"] == "claude-haiku-5-5" and "stream" not in first
    schema = first["output_config"]["format"]["schema"]
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert schema["type"] == "object" and schema["required"] == ["proposal"]
    assert not keys(schema) & {"minimum", "maximum", "minLength", "maxLength"}
    # The prompt keeps the bounds the grammar cannot enforce.
    assert '"maximum": 30' in first["system"] and '"wait_here"' in first["system"]
    assert [message["role"] for message in requests[1]["messages"]] == [
        "user", "assistant", "user"
    ]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_incomplete_action_selection_returns_no_action(claude_factory, stop_reason):
    requests = []

    def handle(request):
        requests.append(request)
        return reply("", stop_reason)

    provider = claude_factory(handle)
    assert await provider.action({"input": "Follow me."}, "Okay.", ["follow_player"]) is None
    assert len(requests) == 1


async def test_unoffered_action_is_rejected(claude_factory):
    content = json.dumps({"proposal": {"action_id": "follow_player", "arguments": {}}})
    provider = claude_factory(lambda _: reply(content))
    assert await provider.action({"input": "Wait."}, "Okay.", ["wait_here"]) is None


async def test_grounding_verdict_uses_enum_schema_without_temperature(claude_factory):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return reply('{"verdict": "no"}')

    provider = claude_factory(handle)
    verdict = await provider.classify_grounding(
        "How'd the interview go?", player_text="Hey. Another one, please."
    )
    assert verdict == "no"
    body = requests[0]
    assert "temperature" not in body
    assert body["max_tokens"] == 512
    assert body["output_config"]["format"]["schema"]["properties"]["verdict"]["enum"] == [
        "yes", "no"
    ]
    assert provider.grounding_timeout_seconds == 3.0


@pytest.mark.parametrize("response", [reply("", "max_tokens"), reply('{"verdict": "maybe"}')])
async def test_incomplete_or_invalid_grounding_verdict_raises(claude_factory, response):
    provider = claude_factory(lambda _: response)
    with pytest.raises(ProviderResponseError):
        await provider.classify_grounding("Ask at the docks for paid work.")


async def test_memory_summary_uses_structured_output_and_local_bounds(claude_factory):
    requests = []
    replies = [
        '{"text": "A regular ordered a large black coffee before his interview.", '
        '"importance": 0.6}',
        '{"text": "Too important.", "importance": 3}',
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        return reply(replies[len(requests) - 1])

    provider = claude_factory(handle)
    provider.gate.config.background.max_requests = 10
    provider.gate.config.background.max_estimated_usd = 1
    context = {"memories": [{"text": "The player said: Large coffee, black."}],
               "base_importance": 0.5}
    summary = await provider.memory_summary(context)
    assert summary == {
        "text": "A regular ordered a large black coffee before his interview.",
        "importance": 0.6,
    }
    schema = requests[0]["output_config"]["format"]["schema"]
    assert schema["required"] == ["text", "importance"]
    assert not keys(schema) & {"minimum", "maximum", "minLength", "maxLength"}
    assert requests[0]["max_tokens"] == provider.gate.config.memory_output_tokens
    with pytest.raises(ProviderResponseError, match="invalid memory summary"):
        await provider.memory_summary(context)


async def test_effort_can_be_omitted_for_models_without_it(claude_factory):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return reply('{"verdict": "yes"}')

    provider = claude_factory(handle, anthropic_effort="")
    await provider.classify_grounding("Ask at the docks for paid work.")
    assert "effort" not in requests[0]["output_config"]


def test_schema_copy_keeps_property_names_and_merges_turns():
    schema = {"type": "object", "properties": {"maximum": {"type": "number", "maximum": 3}}}
    assert api_schema(schema) == {"type": "object", "properties": {"maximum": {"type": "number"}}}
    turns = merged_turns([
        {"role": "user", "content": "context"},
        {"role": "user", "content": "Franklin (the player): hi"},
        {"role": "assistant", "content": "Hey."},
    ])
    assert turns == [
        {"role": "user", "content": "context\n\nFranklin (the player): hi"},
        {"role": "assistant", "content": "Hey."},
    ]


def test_gateway_routes_claude_with_explicit_local_fallback():
    repo = Repository(":memory:")
    try:
        configured = Settings(provider="anthropic", dialogue_model=MODEL)
        configured.ai_gateway = GatewayConfig(local_fallback=LocalFallback(
            endpoint="http://127.0.0.1:8091/v1", dialogue_model="qwen2.5-7b-instruct"
        ))
        gateway = GatewayProvider(configured, repo)
        assert isinstance(gateway.primary, AnthropicProvider)
        assert isinstance(gateway.fallback, OpenAIProvider) and gateway.fallback.local
        assert gateway.configuration_summary()["anthropic_base_url"] == (
            "https://api.anthropic.com/v1"
        )
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_admin_consent_names_the_claude_endpoint(client):
    config = await configuration(client, provider="anthropic", dialogue_model=MODEL)
    config["ai_gateway"]["prices"] = {
        MODEL: {"input_usd_per_million": 4, "output_usd_per_million": 20}
    }
    saved = await client.put("/v1/admin/provider", json=config)
    assert saved.status_code == 200, saved.text
    assert saved.json()["configuration"]["provider"] == "anthropic"
    wrong = await client.post(
        "/v1/admin/provider/consent",
        json=await consent_body(client, config["openai_base_url"]),
    )
    assert wrong.status_code == 409
    enabled = await client.post(
        "/v1/admin/provider/consent",
        json=await consent_body(client, "https://api.anthropic.com/v1"),
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["cloud_consent"] is True


async def test_social_dialogue_uses_private_npc_context_and_social_instructions(claude_factory):
    from service.lls.providers import SOCIAL_SYSTEM

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream([
            message_start(), *text_block(0, "Hello."), *finish(),
        ]))

    provider = claude_factory(handle)
    context = {"social": {"phase": "turn", "decision_owner": "npc-a"},
               "actor": {"display_name": "Passerby"}, "input": "Speak to the other NPC."}
    assert await speak(provider, context) == "Hello."
    payload = json.loads(requests[0].content)
    assert payload["system"] == SOCIAL_SYSTEM
    assert json.loads(payload["messages"][0]["content"]) == context


async def test_action_prompt_includes_observed_target_ids(claude_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return reply('{"proposal": null}')

    provider = claude_factory(handle)
    observed = {"targets": [{"target_id": "visible-car", "kind": "vehicle"}]}
    assert await provider.action(
        {"input": "Get in that car.", "action_context": observed}, "All right.",
        ["enter_vehicle"],
    ) is None
    context = json.loads(json.loads(requests[0].content)["messages"][0]["content"])
    assert context["action_context"] == observed
    assert "free seat" in context["actions"]["enter_vehicle"]
