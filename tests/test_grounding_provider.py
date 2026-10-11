import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from service.lls.budgets import BudgetExceeded
from service.lls.gateway_config import DailyBudget, GatewayConfig, LocalFallback, ModelPrice
from service.lls.grounding import grounded_text_checked
from service.lls.outbound import OutboundDenied, OutboundGate
from service.lls.providers import OpenAIProvider, ProviderResponseError, create_provider
from service.lls.settings import Settings
from service.lls.storage import Repository


def settings(*, local=True, consent=False):
    return Settings(
        provider="local_openai" if local else "openai",
        cloud_opt_in=consent,
        dialogue_model="fixture",
        ai_gateway=GatewayConfig(
            foreground=DailyBudget(max_requests=4, max_estimated_usd=1),
            background=DailyBudget(max_requests=4, max_estimated_usd=1),
            prices={"fixture": ModelPrice(input_usd_per_million=1, output_usd_per_million=2)},
        ),
    )


def response(content="no", *, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 1},
        },
    )


@pytest.fixture
async def provider_factory():
    created = []

    def create(config, handle):
        repository = Repository()
        gate = OutboundGate(config, repository)
        created.append((gate, repository))
        return OpenAIProvider(config, gate=gate, transport=httpx.MockTransport(handle))

    yield create
    for gate, repository in created:
        await gate.aclose()
        repository.close()


async def test_local_classifier_has_short_deterministic_prompt_and_charges_budget(
    monkeypatch, provider_factory
):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-hosted-key")
    requests = []
    provider = provider_factory(settings(), lambda request: requests.append(request) or response())
    sentence = "The weather is nice today."
    assert await provider.classify_grounding(sentence, player_text="Need cash?") == "no"
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    payload = json.loads(requests[0].content)
    assert payload["model"] == "fixture"
    assert payload["max_tokens"] <= 4
    assert payload["temperature"] == 0
    assert not payload.get("stream", False)
    assert json.loads(payload["messages"][1]["content"]) == {
        "sentence": sentence, "player_text": "Need cash?", "previous_sentence": "",
    }
    prompt = payload["messages"][0]["content"]
    assert "exactly yes or no" in prompt
    assert "story revelation" in prompt
    assert "police corruption" in prompt
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 1
    assert usage["reported_input_tokens"] == 200
    assert usage["reported_output_tokens"] == 1
    assert usage["pending_reservations"] == 0


@pytest.mark.parametrize("denial", ["consent", "budget", "price"])
async def test_classifier_denial_never_dispatches_http(monkeypatch, provider_factory, denial):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-hosted-key")
    config = settings(local=False, consent=denial != "consent")
    if denial == "budget":
        config.ai_gateway.foreground.max_requests = 0
    elif denial == "price":
        config.ai_gateway.prices.clear()
    requests = []
    provider = provider_factory(config, lambda request: requests.append(request) or response())
    with pytest.raises((OutboundDenied, BudgetExceeded)):
        await provider.classify_grounding("Ask him about work.")
    assert not requests
    assert provider.gate.usage_summary()["foreground"]["requests"] == 0


async def test_classifier_bounds_question_and_last_delivered_sentence(provider_factory):
    requests = []
    provider = provider_factory(settings(), lambda request: requests.append(request) or response())
    previous = "Earlier sentence. " + "x" * 600 + "."
    await provider.classify_grounding(
        "Any ideas?", player_text="Need work? " * 100, previous_sentence=previous,
    )
    context = json.loads(json.loads(requests[0].content)["messages"][1]["content"])
    assert len(context["player_text"]) == 512
    assert context["previous_sentence"] == "x" * 511 + "."
    assert context["sentence"] == "Any ideas?"


async def test_classifier_distinguishes_recalled_player_occupation_from_new_offers(
    provider_factory,
):
    requests = []
    provider = provider_factory(settings(), lambda request: requests.append(request) or response())
    sentence = "You told me you were a gardener."
    assert await provider.classify_grounding(
        sentence, player_text="Do you remember my job?",
    ) == "no"
    payload = json.loads(requests[0].content)
    prompt = payload["messages"][0]["content"]
    assert "either speaker's existing identity or occupation" in prompt
    assert "Names alone do not imply a story revelation" in prompt
    assert "New work or income opportunities remain YES" in prompt
    assert json.loads(payload["messages"][1]["content"])["sentence"] == sentence


async def test_local_classifier_waits_for_single_slot_dialogue_generation(provider_factory):
    requests = []
    generation = None
    slot_free = asyncio.Event()

    async def finish_generation():
        await asyncio.sleep(0.9)
        slot_free.set()

    async def handle(request):
        nonlocal generation
        requests.append(request)
        if json.loads(request.content).get("stream"):
            generation = asyncio.create_task(finish_generation())
            return httpx.Response(200, content=(
                'data: {"choices":[{"delta":{"content":"I work long days."}}]}\n'
                'data: {"choices":[],"usage":{"prompt_tokens":200,"completion_tokens":5}}\n'
                "data: [DONE]\n"
            ))
        await slot_free.wait()
        return response("no")

    provider = provider_factory(settings(), handle)
    try:
        async with aclosing(provider.dialogue({"input": "How is your evening?"})) as stream:
            async for chunk in stream:
                assert await grounded_text_checked(
                    chunk, classifier=provider.classify_grounding,
                ) == chunk
    finally:
        if generation is not None:
            await generation
    assert len(requests) == 2
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 2
    assert usage["pending_reservations"] == 0
    assert usage["uncertain_requests"] == 0


@pytest.mark.parametrize("local,model_timeout,expected", [
    (True, 30, 3.0), (True, 1, 1.0), (False, 30, 0.8),
])
def test_classifier_deadline_accounts_for_local_queue_only(
    provider_factory, local, model_timeout, expected,
):
    config = settings(local=local)
    config.model_timeout_seconds = model_timeout
    provider = provider_factory(config, lambda _: response())
    assert provider.grounding_timeout_seconds == expected


@pytest.mark.parametrize("caller_cancels", [False, True])
async def test_local_classifier_cancellation_closes_request_and_reservation(
    provider_factory, caller_cancels,
):
    entered = asyncio.Event()
    canceled = asyncio.Event()

    async def handle(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    config = settings()
    config.model_timeout_seconds = 1
    provider = provider_factory(config, handle)
    verdicts = []
    task = asyncio.create_task(grounded_text_checked(
        "I work long days.", classifier=provider.classify_grounding, verdicts=verdicts,
    ))
    await asyncio.wait_for(entered.wait(), 0.5)
    if caller_cancels:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
    else:
        assert await asyncio.wait_for(task, 1.5) == ""
        assert verdicts[0]["reason"] == "classifier_timeout"
    assert canceled.is_set()
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 1
    assert usage["pending_reservations"] == 0
    assert usage["uncertain_requests"] == 1


async def test_cloud_classifier_scrubs_both_sentence_and_player_input(
    monkeypatch, provider_factory
):
    key = "synthetic-hosted-key"
    monkeypatch.setenv("LLM_API_KEY", key)
    requests = []
    provider = provider_factory(
        settings(local=False, consent=True),
        lambda request: requests.append(request) or response("yes"),
    )
    assert await provider.classify_grounding(
        "Ask person@example.com for work.", player_text=f"I need cash. {key}",
        previous_sentence="My friend is prior@example.com.",
    ) == "yes"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == f"Bearer {key}"
    payload = requests[0].content.decode()
    assert "person@example.com" not in payload
    assert "prior@example.com" not in payload
    assert key not in payload
    assert "[REDACTED]" in payload
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["estimated_micro_usd"] == 202
    assert usage["uncertain_requests"] == 0


@pytest.mark.parametrize("kind", ["error", "timeout", "malformed", "truncated"])
async def test_classifier_failure_is_single_attempt_and_keeps_budget_reservation(
    monkeypatch, provider_factory, kind
):
    requests = []
    cancelled = asyncio.Event()

    async def handle(request):
        requests.append(request)
        if kind == "error":
            return httpx.Response(503)
        if kind == "malformed":
            return httpx.Response(200, json={"choices": []})
        if kind == "truncated":
            return response("no", finish_reason="length")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setenv("LLM_API_KEY", "synthetic-hosted-key")
    provider = provider_factory(settings(local=False, consent=True), handle)
    with pytest.raises((httpx.HTTPStatusError, TimeoutError, ProviderResponseError)):
        await asyncio.wait_for(provider.classify_grounding("They pay cash."), 1.2)
    assert len(requests) == 1
    if kind == "timeout":
        assert cancelled.is_set()
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 1
    assert usage["uncertain_requests"] == 1
    assert usage["pending_reservations"] == 0
    assert usage["estimated_micro_usd"] >= 16384


async def test_classifier_preserves_background_budget_and_raw_answer(provider_factory):
    provider = provider_factory(settings(), lambda _: response("perhaps"))
    assert await provider.classify_grounding("A job?", budget_class="background") == "perhaps"
    usage = provider.gate.usage_summary()
    assert usage["background"]["requests"] == 1
    assert usage["foreground"]["requests"] == 0


@pytest.mark.parametrize("name", ["disabled", "fake"])
async def test_non_model_gateway_has_no_default_classifier(name):
    with Repository() as repository:
        provider = create_provider(Settings(provider=name), repository)
        try:
            assert getattr(provider, "classify_grounding", None) is None
            assert not hasattr(provider.primary, "classify_grounding")
            assert provider.usage_summary()["foreground"]["requests"] == 0
        finally:
            await provider.aclose()


async def test_configured_gateway_can_disable_classifier():
    config = settings()
    config.grounding_classifier_enabled = False
    with Repository() as repository:
        provider = create_provider(config, repository)
        try:
            assert provider.classify_grounding is None
            assert provider.usage_summary()["foreground"]["requests"] == 0
        finally:
            await provider.aclose()


@pytest.mark.parametrize("use_fallback", [False, True])
async def test_classifier_uses_streaming_route_and_independent_budget_request(use_fallback):
    config = settings(local=not use_fallback)
    if use_fallback:
        config.ai_gateway.local_fallback = LocalFallback(
            endpoint="http://127.0.0.1:9001/v1", dialogue_model="fallback"
        )
    requests = []
    active = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        if payload.get("stream"):
            wire = (
                'data: {"choices":[{"delta":{"content":"The price of coffee keeps going up."}}]}\n'
                "data: [DONE]\n"
            )
            return httpx.Response(200, content=wire)
        active.append(len(provider.gate.active))
        return response("no")

    with Repository() as repository:
        provider = create_provider(config, repository, transport=httpx.MockTransport(handle))
        try:
            async with aclosing(provider.dialogue({"input": "Any work?"})) as stream:
                async for chunk in stream:
                    assert chunk == "The price of coffee keeps going up."
                    assert await grounded_text_checked(
                        chunk, classifier=provider.classify_grounding, player_text="Any work?",
                    ) == chunk
            assert len(requests) == 2
            assert active == [2]
            expected_model = "fallback" if use_fallback else "fixture"
            assert {json.loads(request.content)["model"] for request in requests} == {
                expected_model
            }
            assert all(request.url.host == "127.0.0.1" for request in requests)
            usage = provider.usage_summary()["foreground"]
            assert usage["requests"] == 2
            assert usage["pending_reservations"] == 0
        finally:
            await provider.aclose()
