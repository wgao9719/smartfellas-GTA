import json

import httpx
import pytest
from test_social import NOW, invite
from test_social import scene as scene

from service.lls.gateway_config import DailyBudget, GatewayConfig, LocalFallback
from service.lls.grounding import NEUTRAL_FALLBACK
from service.lls.providers import create_provider
from service.lls.settings import Settings


@pytest.mark.parametrize("answer,expected", [
    ("no", "I work long days."), ("yes", NEUTRAL_FALLBACK),
])
async def test_social_classifier_uses_worker_local_fallback_and_background_budget(
    scene, monkeypatch, answer, expected
):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-social-grounding-key")
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        if not payload.get("stream"):
            return httpx.Response(200, json={"choices": [{
                "message": {"content": answer}, "finish_reason": "stop",
            }]})
        context = json.loads(payload["messages"][1]["content"])
        text = (
            '{"decision":"accept"}' if context["social"]["phase"] == "invitation"
            else "I work long days."
        )
        event = json.dumps({"choices": [{"delta": {"content": text}}]})
        return httpx.Response(200, content=f"data: {event}\n\ndata: [DONE]\n")

    settings = Settings(
        provider="openai", dialogue_model="hosted-fixture", cloud_opt_in=False,
        ai_gateway=GatewayConfig(
            foreground=DailyBudget(max_requests=0),
            background=DailyBudget(max_requests=5),
            local_fallback=LocalFallback(
                endpoint="http://127.0.0.1:9001/v1", dialogue_model="local-fixture",
            ),
        ),
    )
    await scene.provider.aclose()
    provider = create_provider(
        settings, scene.repo, transport=httpx.MockTransport(handle), utc_now=lambda: NOW,
    )
    scene.engine.replace_provider(provider)
    try:
        assert (await invite(scene))["status"] == "active"
        utterance = await scene.engine.generate_turn("scene", "avery")
        assert utterance.text == expected
        assert len(requests) == 5  # Three invitations, one speech turn, one classifier.
        assert all(request.url.host == "127.0.0.1" for request in requests)
        assert all("authorization" not in request.headers for request in requests)
        payloads = [json.loads(request.content) for request in requests]
        assert all(payload["model"] == "local-fixture" for payload in payloads)
        assert json.loads(payloads[-1]["messages"][1]["content"])["sentence"] == (
            "I work long days."
        )
        assert not payloads[-1].get("stream")
        usage = provider.usage_summary()
        assert usage["background"]["requests"] == 5
        assert usage["foreground"]["requests"] == 0
        assert usage["background"]["pending_reservations"] == 0
    finally:
        await provider.aclose()
