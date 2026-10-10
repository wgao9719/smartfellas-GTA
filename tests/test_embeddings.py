"""Real embedding HTTP client/gateway contracts with only the HTTP boundary replaced."""

import asyncio
import json

import httpx
import pytest

from service.lls.embeddings import EmbeddingConfig, OpenAIEmbeddingProvider
from service.lls.gateway_config import DailyBudget, GatewayConfig, ModelPrice
from service.lls.outbound import OutboundDenied, OutboundGate
from service.lls.providers import ProviderResponseError
from service.lls.settings import Settings
from service.lls.storage import Repository


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "embedding.sqlite3") as repository:
        yield repository


def configuration(*, remote=False, consent=False, **changes):
    return Settings(
        embedding=EmbeddingConfig(
            provider="openai" if remote else "local_openai",
            model="embedding-fixture",
        ),
        cloud_opt_in=consent,
        ai_gateway=GatewayConfig(
            prices={
                "embedding-fixture": ModelPrice(
                    input_usd_per_million=1,
                    output_usd_per_million=0,
                )
            },
            background=DailyBudget(max_requests=100),
        ),
        **changes,
    )


def response(vectors):
    return httpx.Response(
        200,
        json={
            "data": [{"index": index, "embedding": vector} for index, vector in enumerate(vectors)],
            "usage": {"prompt_tokens": 12, "total_tokens": 12},
        },
    )


async def test_local_request_shape_order_normalization_and_usage(repo, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "must-not-reach-loopback")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url == "http://127.0.0.1:1234/v1/embeddings"
        assert "Authorization" not in request.headers
        assert json.loads(request.content) == {
            "model": "embedding-fixture",
            "input": ["one", "two"],
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [0, 5]}, {"index": 0, "embedding": [2, 0]}],
                "usage": {"prompt_tokens": 12, "total_tokens": 12},
            },
        )

    settings = configuration()
    gate = OutboundGate(settings, repo)
    provider = OpenAIEmbeddingProvider(settings, gate=gate, transport=httpx.MockTransport(handler))
    assert await provider.embed(["one", "two"]) == [[1, 0], [0, 1]]
    receipt = dict(repo.connection.execute("SELECT * FROM ai_requests").fetchone())
    assert receipt["input_tokens"] == 12 and receipt["output_tokens"] == 0
    assert not gate.active and len(calls) == 1
    await gate.aclose()


@pytest.mark.parametrize("denial", ["consent", "price", "budget", "closed"])
async def test_remote_denials_precede_transport(repo, monkeypatch, denial):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    settings = configuration(remote=True, consent=denial != "consent")
    if denial == "price":
        settings.ai_gateway.prices = {}
    if denial == "budget":
        settings.ai_gateway.foreground.max_requests = 0
    gate = OutboundGate(settings, repo)
    if denial == "closed":
        await gate.aclose()
    calls = []
    provider = OpenAIEmbeddingProvider(
        settings,
        gate=gate,
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    with pytest.raises((ValueError, RuntimeError)):
        await provider.embed(["private memory"])
    assert calls == [] and not gate.active
    await gate.aclose()


async def test_cloud_request_requires_explicit_key_and_sends_it_only_to_configured_host(
    repo,
    monkeypatch,
):
    settings = configuration(remote=True, consent=True)
    settings.embedding.base_url = "https://embedding.example.invalid/v1"
    gate = OutboundGate(settings, repo)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.url.host == "embedding.example.invalid"
        return response([[1, 0]])

    provider = OpenAIEmbeddingProvider(settings, gate=gate, transport=httpx.MockTransport(handler))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(OutboundDenied):
        await provider.embed(["one"])
    assert not calls
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    assert await provider.embed(["one"]) == [[1, 0]]
    await gate.aclose()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "embedding": []}],
        [{"index": 0, "embedding": [0, 0]}],
        [{"index": 0, "embedding": [True, 0]}],
        [{"index": 0, "embedding": ["1", 0]}],
        [{"index": True, "embedding": [1, 0]}],
        [{"index": 1, "embedding": [1, 0]}],
        [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [1, 0]}],
        [{"index": 0, "embedding": [1] * 4097}],
        [{"embedding": [1, 0]}],
    ],
)
async def test_malformed_vectors_fail_closed(repo, rows):
    settings = configuration()
    gate = OutboundGate(settings, repo)
    provider = OpenAIEmbeddingProvider(
        settings,
        gate=gate,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": rows})),
    )
    with pytest.raises(ProviderResponseError):
        await provider.embed(["one"])
    assert not gate.active
    await gate.aclose()


@pytest.mark.parametrize(
    "body",
    [
        b'{"data":[{"index":0,"embedding":[NaN,0]}]}',
        b'{"data":[{"index":0,"embedding":[Infinity,0]}]}',
        b'{"data":[],"data":[]}',
    ],
)
async def test_nonfinite_and_duplicate_json_fields_reject(repo, body):
    settings = configuration()
    gate = OutboundGate(settings, repo)
    provider = OpenAIEmbeddingProvider(
        settings,
        gate=gate,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    with pytest.raises(ProviderResponseError):
        await provider.embed(["one"])
    await gate.aclose()


async def test_input_and_response_limits_and_redirects(repo):
    settings = configuration(context_byte_budget=2048, model_response_bytes=1024)
    gate = OutboundGate(settings, repo)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, content=b"x" * 2048)
        return httpx.Response(302, headers={"Location": "https://unapproved.invalid"})

    provider = OpenAIEmbeddingProvider(settings, gate=gate, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderResponseError):
        await provider.embed(["x" * 2048])
    assert not calls
    with pytest.raises(ProviderResponseError):
        await provider.embed(["one"])
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["one"])
    assert len(calls) == 2 and not gate.active
    await gate.aclose()


async def test_inflight_revocation_closes_http_response(repo, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    entered, closed = asyncio.Event(), asyncio.Event()

    class HeldStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    settings = configuration(remote=True, consent=True)
    gate = OutboundGate(settings, repo)
    provider = OpenAIEmbeddingProvider(
        settings,
        gate=gate,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=HeldStream())),
    )
    task = asyncio.create_task(provider.embed(["one"]))
    await asyncio.wait_for(entered.wait(), 2)
    await gate.set_cloud_consent(False)
    assert task.cancelled() and closed.is_set() and not gate.active
    await gate.aclose()


@pytest.mark.parametrize("url", ["http://192.168.1.4/v1", "http://localhost/v1"])
def test_local_embedding_configuration_rejects_lan_and_dns_names(url):
    with pytest.raises(ValueError):
        EmbeddingConfig(provider="local_openai", model="fixture", base_url=url)


@pytest.mark.parametrize(
    "provider,url",
    [
        ("local_openai", "http://127.0.0.1:1235/v1"),
        ("openai", "https://embedding.example.invalid/v1"),
    ],
)
def test_disable_and_reenable_retains_valid_endpoint(provider, url):
    active = EmbeddingConfig(provider=provider, model="fixture", base_url=url)
    wire = active.model_dump()
    wire["provider"] = "disabled"
    disabled = EmbeddingConfig.model_validate(wire)
    assert disabled.base_url == active.base_url
    wire = disabled.model_dump()
    wire["provider"] = provider
    assert EmbeddingConfig.model_validate(wire) == active


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/v1",
        "http://192.168.1.4/v1",
        "https://user:secret@example.invalid/v1",
        "https://example.invalid/v1?key=secret",
    ],
)
def test_disabled_configuration_still_rejects_unsafe_endpoints(url):
    with pytest.raises(ValueError):
        EmbeddingConfig(base_url=url)


async def test_dimensions_are_requested_and_enforced(repo):
    settings = configuration()
    settings.embedding.dimensions = 2
    gate = OutboundGate(settings, repo)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return response([[1, 0, 0]])

    provider = OpenAIEmbeddingProvider(settings, gate=gate, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderResponseError):
        await provider.embed(["one"])
    assert seen[0]["dimensions"] == 2
    await gate.aclose()


async def test_provider_configuration_is_a_snapshot(repo):
    settings = configuration()
    gate = OutboundGate(settings, repo)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return response([[1, 0]])

    provider = OpenAIEmbeddingProvider(settings, gate=gate, transport=httpx.MockTransport(handler))
    original_key = provider.model_key
    settings.embedding.model = "mutated-model"
    await provider.embed(["one"])
    assert seen[0]["model"] == "embedding-fixture"
    assert provider.model_key == original_key
    await gate.aclose()
