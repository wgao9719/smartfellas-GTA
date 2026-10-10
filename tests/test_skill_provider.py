import asyncio
import json

import httpx
import pytest

from service.lls.gateway_config import DailyBudget, GatewayConfig, ModelPrice
from service.lls.providers import create_provider
from service.lls.settings import Settings
from service.lls.skills import compile_skill
from service.lls.storage import Repository

CONTEXT = {"skill_request": {"skill_id": "courtesy", "version": 1}, "previous_trials": []}
SOURCE = {
    "skill_id": "courtesy",
    "version": 1,
    "action_class": "courtesy",
    "max_duration_seconds": 1,
    "steps": [
        {
            "op": "action",
            "proposal": {"action_id": "face_player", "arguments": {"duration_seconds": 0.5}},
        }
    ],
}


def settings(**changes):
    return Settings(
        provider="openai",
        cloud_opt_in=True,
        dialogue_model="fixture",
        ai_gateway=GatewayConfig(
            background=DailyBudget(max_requests=4, max_estimated_usd=1),
            prices={"fixture": ModelPrice(input_usd_per_million=1, output_usd_per_million=2)},
        ),
        **changes,
    )


def response(source=SOURCE, *, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": source if isinstance(source, str) else json.dumps(source)
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 400, "completion_tokens": 100},
        },
    )


async def request_candidate(config, handle, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-candidate-key")
    with Repository() as repository:
        provider = create_provider(config, repository, transport=httpx.MockTransport(handle))
        try:
            candidate = await provider.skill_candidate(CONTEXT)
            return candidate, provider.safe_status()
        finally:
            await provider.aclose()


async def test_structured_candidate_uses_background_gateway_and_requires_compilation(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return response()

    candidate, status = await request_candidate(settings(), handle, monkeypatch)
    compiled = compile_skill(candidate)
    assert len(compiled.operations) == 1
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 1200
    assert "Permissions remain external" in payload["messages"][0]["content"]
    assert status["usage"]["background"]["requests"] == 1
    assert status["usage"]["foreground"]["requests"] == 0
    assert status["usage"]["background"]["reported_input_tokens"] == 400


@pytest.mark.parametrize("denial", ["consent", "budget", "price"])
async def test_candidate_denial_dispatches_no_http(monkeypatch, denial):
    config = settings()
    if denial == "consent":
        config.cloud_opt_in = False
    elif denial == "budget":
        config.ai_gateway.background.max_requests = 0
    else:
        config.ai_gateway.prices.clear()
    requests = []
    candidate, status = await request_candidate(
        config, lambda request: requests.append(request) or response(), monkeypatch
    )
    assert candidate is None
    assert not requests
    assert status["usage"]["background"]["requests"] == 0


@pytest.mark.parametrize(
    "content,finish",
    [
        (SOURCE, "length"),
        ({**SOURCE, "steps": [{"op": "shell", "command": "whoami"}]}, "stop"),
        (json.dumps(SOURCE).replace('"version": 1', '"version": 0, "version": 1'), "stop"),
        ("x" * 16385, "stop"),
    ],
)
async def test_invalid_or_incomplete_candidate_keeps_reservation(monkeypatch, content, finish):
    candidate, status = await request_candidate(
        settings(), lambda _: response(content, finish_reason=finish), monkeypatch
    )
    assert candidate is None
    assert status["usage"]["background"]["requests"] == 1
    assert status["usage"]["background"]["uncertain_requests"] == 1


async def test_fake_candidate_is_explicit_and_disabled_provider_generates_nothing():
    for name in ("disabled", "fake"):
        with Repository() as repository:
            provider = create_provider(Settings(provider=name), repository)
            try:
                candidate = await provider.skill_candidate(CONTEXT)
                assert (candidate is None) is (name == "disabled")
                if candidate:
                    compile_skill(candidate)
                assert provider.usage_summary()["background"]["requests"] == 0
            finally:
                await provider.aclose()


async def test_candidate_provider_cannot_suppress_cancellation():
    with Repository() as repository:
        provider = create_provider(Settings(provider="fake"), repository)
        started = asyncio.Event()

        async def suppressed(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SOURCE

        provider.primary.skill_candidate = suppressed
        try:
            task = asyncio.create_task(provider.skill_candidate(CONTEXT))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await provider.aclose()
