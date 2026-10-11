"""All outbound tests use injected transports. No model or network is required."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest

from service.lls.budgets import BudgetExceeded, BudgetLedger
from service.lls.gateway import NEUTRAL_LINE
from service.lls.gateway_config import DailyBudget, GatewayConfig, LocalFallback, ModelPrice
from service.lls.outbound import OutboundDenied
from service.lls.providers import (
    OpenAIProvider,
    create_provider,
    effective_provider_settings,
    persist_provider_configuration,
)
from service.lls.settings import Settings
from service.lls.storage import Repository

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
PRICE = ModelPrice(input_usd_per_million=1, output_usd_per_million=2)


@pytest.fixture
def repository(tmp_path):
    with Repository(tmp_path / "gateway.sqlite3") as repo:
        yield repo


@pytest.fixture(autouse=True)
def synthetic_credentials(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-cloud-key")


def settings(**changes):
    return Settings(
        provider="openai",
        cloud_opt_in=True,
        dialogue_model="fixture-model",
        ai_gateway=GatewayConfig(prices={"fixture-model": PRICE}),
        **changes,
    )


def configured(repository, handler, *, configuration=None, **options):
    return create_provider(
        configuration or settings(),
        repository,
        transport=httpx.MockTransport(handler),
        utc_now=lambda: NOW,
        **options,
    )


def stream_wire(text="Hello.", usage=None, done=True):
    data = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n"
    if usage is not None:
        data += "data: " + json.dumps({"choices": [], "usage": usage}) + "\n"
    return (data + ("data: [DONE]\n" if done else "")).encode()


async def speak(provider, **kwargs):
    return "".join([part async for part in provider.dialogue({"input": "Hello"}, **kwargs)])


def test_schema_v1_migrates_without_resetting_profile_data(tmp_path):
    path = tmp_path / "old.sqlite3"
    with Repository(path) as repo:
        repo.create_profile("preserved")
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "DROP TABLE ai_requests; DROP TABLE service_settings; PRAGMA user_version=1;"
        )
    with Repository(path) as repo:
        assert repo.export_profile("preserved")["profile"]["profile_id"] == "preserved"
        assert BudgetLedger(repo).summary(NOW)["foreground"]["requests"] == 0
        assert repo.connection.execute("PRAGMA user_version").fetchone()[0] == 5


def reserve(ledger, **changes):
    values = dict(
        now=NOW,
        budget_class="foreground",
        provider="openai",
        model="fixture-model",
        micro_usd=100000,
        limit=DailyBudget(max_requests=10, max_estimated_usd=1),
        price=PRICE,
    )
    values.update(changes)
    return ledger.reserve(**values)


def test_concurrent_process_connections_cannot_overspend(tmp_path):
    path = tmp_path / "atomic.sqlite3"
    with Repository(path) as first, Repository(path) as second:
        barrier = Barrier(2)

        def attempt(repo):
            barrier.wait()
            try:
                reserve(BudgetLedger(repo), limit=DailyBudget(max_requests=1, max_estimated_usd=1))
                return True
            except BudgetExceeded:
                return False

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, (first, second)))
        assert sorted(results) == [False, True]
        assert BudgetLedger(first).summary(NOW)["foreground"]["requests"] == 1


def test_unsettled_and_failed_reservations_survive_reopen_and_profile_erase(tmp_path):
    path = tmp_path / "durable.sqlite3"
    with Repository(path) as repo:
        repo.create_profile("player")
        ledger = BudgetLedger(repo)
        reserve(ledger)
        failed = reserve(ledger)
        ledger.settle(failed)
        repo.delete_profile("player")
    with Repository(path) as repo:
        actual = BudgetLedger(repo).summary(NOW)["foreground"]
        assert actual["requests"] == 2
        assert actual["estimated_micro_usd"] == 200000
        assert actual["pending_reservations"] == actual["uncertain_requests"] == 1


def test_configured_price_math_never_rounds_reservation_down(repository):
    price = ModelPrice(input_usd_per_million=0.1, output_usd_per_million=0.2)
    assert price.estimate_micro_usd(1, 1) == 1
    with pytest.raises(BudgetExceeded):
        reserve(
            BudgetLedger(repository),
            micro_usd=1,
            limit=DailyBudget(max_requests=1, max_estimated_usd=0.0000009),
        )


def test_confirmed_usage_settles_once_and_overages_block_future_requests(repository):
    ledger = BudgetLedger(repository)
    identifier = reserve(ledger)
    ledger.settle(identifier, (10, 5))
    ledger.settle(identifier, (0, 0))
    assert ledger.summary(NOW)["foreground"]["estimated_micro_usd"] == 20
    identifier = reserve(ledger)
    ledger.settle(identifier, (1000001, 1))
    with pytest.raises(BudgetExceeded):
        reserve(ledger)


@pytest.mark.parametrize("usage", [None, (None, 4), (True, 5), (-1, 2), (2, 1.2)])
def test_missing_or_invalid_usage_does_not_refund_reservation(repository, usage):
    ledger = BudgetLedger(repository)
    identifier = reserve(ledger)
    ledger.settle(identifier, usage)
    assert ledger.summary(NOW)["foreground"]["estimated_micro_usd"] == 100000


async def test_consent_and_configured_prices_are_required_before_dispatch(repository):
    calls = []
    configuration = settings()
    configuration.cloud_opt_in = False
    provider = configured(
        repository, lambda request: calls.append(request), configuration=configuration
    )
    assert await speak(provider) == NEUTRAL_LINE
    await provider.set_cloud_consent(True)
    provider.gate.config.prices = {}
    assert await speak(provider) == NEUTRAL_LINE
    assert calls == []
    assert provider.usage_summary()["foreground"]["requests"] == 0


def test_raw_outbound_provider_cannot_bypass_gateway():
    with pytest.raises(TypeError):
        OpenAIProvider(settings())
    with pytest.raises(ValueError, match="gateway"):
        OpenAIProvider(settings(), gate=None)


async def test_complete_reported_usage_settles_configured_estimate(repository):
    calls = []

    def handle(request):
        calls.append(request)
        usage = {"prompt_tokens": 10, "completion_tokens": 3}
        return httpx.Response(200, content=stream_wire(usage=usage))

    provider = configured(repository, handle)
    assert await speak(provider) == "Hello."
    row = provider.usage_summary()["foreground"]
    assert row["estimated_micro_usd"] == 16
    assert row["reported_input_tokens"] == 10
    assert row["reported_output_tokens"] == 3
    assert row["uncertain_requests"] == 0
    assert json.loads(calls[0].content)["stream_options"] == {"include_usage": True}


async def test_interrupted_usage_stream_keeps_full_reservation(repository):
    usage = {"prompt_tokens": 1, "completion_tokens": 1}
    provider = configured(
        repository, lambda _: httpx.Response(200, content=stream_wire(usage=usage, done=False))
    )
    await speak(provider)
    row = provider.usage_summary()["foreground"]
    assert row["estimated_micro_usd"] == PRICE.estimate_micro_usd(16384, 300)
    assert row["uncertain_requests"] == 1


async def test_daily_foreground_background_caps_are_separate_and_reset_only_by_utc_day(repository):
    configuration = settings()
    configuration.ai_gateway.foreground = DailyBudget(max_requests=1, max_estimated_usd=1)
    configuration.ai_gateway.background = DailyBudget(max_requests=1, max_estimated_usd=1)
    provider = configured(
        repository,
        lambda _: httpx.Response(200, content=stream_wire()),
        configuration=configuration,
    )
    assert await speak(provider) == "Hello."
    assert await speak(provider) == NEUTRAL_LINE
    assert await speak(provider, budget_class="background") == "Hello."
    assert await speak(provider, budget_class="background") == NEUTRAL_LINE
    provider.gate.utc_now = lambda: NOW + timedelta(days=1)
    assert await speak(provider) == "Hello."
    assert provider.usage_summary()["foreground"]["requests"] == 1


class HangingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    async def aclose(self):
        self.closed = True


async def test_revocation_cancels_joins_all_remote_streams_and_survives_restart(tmp_path):
    path = tmp_path / "consent.sqlite3"
    streams = [HangingStream(), HangingStream()]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=streams[len(calls) - 1])

    with Repository(path) as repo:
        provider = configured(repo, handle)
        tasks = [asyncio.create_task(speak(provider)) for _ in streams]
        await asyncio.wait_for(asyncio.gather(*(s.started.wait() for s in streams)), 1)
        await provider.set_cloud_consent(False)
        assert all(task.done() and task.cancelled() for task in tasks)
        assert all(stream.closed for stream in streams)
        assert provider.safe_status()["active_remote_requests"] == 0
        assert provider.usage_summary()["foreground"]["uncertain_requests"] == 2
        assert await speak(provider) == NEUTRAL_LINE
        await provider.aclose()
    with Repository(path) as repo:
        provider = configured(repo, handle)  # Environment-style opt-in is still true.
        assert not provider.safe_status()["cloud_consent"]
        assert provider.configuration_summary()["provider"] == "openai"
        assert await speak(provider) == NEUTRAL_LINE
        assert len(calls) == 2


async def test_explicit_local_fallback_is_bounded_and_never_receives_cloud_key(
    repository, monkeypatch
):
    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-use.invalid")
    configuration = settings()
    configuration.ai_gateway.local_fallback = LocalFallback(
        endpoint="http://127.0.0.1:1234/v1", dialogue_model="local-fixture"
    )
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host != "127.0.0.1":
            return httpx.Response(503)
        assert "authorization" not in request.headers
        assert b"synthetic-cloud-key" not in request.content
        return httpx.Response(200, content=stream_wire("Local response."))

    provider = configured(repository, handle, configuration=configuration)
    assert await speak(provider) == "Local response."
    assert [request.url.host for request in calls] == ["api.openai.com", "127.0.0.1"]
    assert provider.usage_summary()["foreground"]["requests"] == 2
    assert provider.usage_summary()["foreground"]["estimated_micro_usd"] > 0


async def test_unconfigured_fallback_and_redirects_never_add_network_destinations(repository):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://unconfigured.invalid/v1"})

    provider = configured(repository, handle)
    assert await speak(provider) == NEUTRAL_LINE
    assert len(calls) == 1
    assert provider.usage_summary()["foreground"]["uncertain_requests"] == 1


async def test_partial_cloud_reply_never_splices_local_output_or_proposes_action(repository):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield stream_wire("Partial.", done=False)
            raise httpx.ReadError("synthetic interruption")

    configuration = settings()
    configuration.ai_gateway.local_fallback = LocalFallback(
        endpoint="http://127.0.0.1:1234/v1", dialogue_model="local-fixture"
    )
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenStream())

    provider = configured(repository, handle, configuration=configuration)
    assert await speak(provider) == "Partial. " + NEUTRAL_LINE
    assert await provider.action({"input": "Wait"}, "Partial.", ["wait_here"]) is None
    assert len(calls) == 1


async def test_schema_repair_reserves_each_attempt_and_respects_request_limit(repository):
    configuration = settings()
    configuration.ai_gateway.foreground = DailyBudget(max_requests=1, max_estimated_usd=1)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "invalid JSON"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    provider = configured(repository, handle, configuration=configuration)
    assert await provider.action({"input": "Wait"}, "Okay", ["wait_here"]) is None
    assert len(calls) == 1
    assert provider.usage_summary()["foreground"]["estimated_micro_usd"] == 11


async def test_circuit_breaker_stops_repeated_cloud_attempts(repository):
    calls = []
    provider = configured(
        repository, lambda request: calls.append(request) or httpx.Response(503), clock=lambda: 1000
    )
    for _ in range(5):
        assert await speak(provider) == NEUTRAL_LINE
    assert len(calls) == 3
    assert provider.safe_status()["last_fallback_reason"] == "circuit_open"


async def test_configuration_status_is_safe_and_disabled_provider_preserves_ledger(repository):
    configuration = settings(bootstrap_token="must-not-appear")
    persist_provider_configuration(repository, configuration)
    ledger = BudgetLedger(repository)
    reserve(ledger)
    ledger.save_settings({"ai_cloud_consent": False})
    effective = effective_provider_settings(Settings(), repository)
    assert effective.provider == "openai"
    assert not effective.cloud_opt_in
    disabled = create_provider(Settings(), repository, use_saved=False, utc_now=lambda: NOW)
    report = disabled.safe_status()
    assert report["usage"]["foreground"]["requests"] == 1
    assert report["configuration"]["provider"] == "disabled"
    assert "synthetic-cloud-key" not in json.dumps(report)
    assert "must-not-appear" not in json.dumps(ledger.settings())
    await disabled.aclose()
    assert ledger.settings()["ai_cloud_consent"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("openai_base_url", "https://user:secret@example.com/v1"),
        ("openai_base_url", "https://example.com/v1?api_key=secret"),
        ("local_base_url", "http://user:secret@127.0.0.1/v1"),
        ("local_base_url", "http://localhost:1234/v1"),
    ],
)
def test_inactive_endpoints_cannot_expose_credentials_in_saved_status(repository, field, value):
    configuration = Settings()
    setattr(configuration, field, value)
    with pytest.raises(ValueError):
        persist_provider_configuration(repository, configuration)
    with pytest.raises(ValueError):
        create_provider(configuration, repository)
    assert BudgetLedger(repository).settings() == {}


async def test_dispatch_has_a_committed_conservative_reservation(repository):
    def handle(request):
        row = repository.connection.execute("SELECT * FROM ai_requests").fetchone()
        assert row["state"] == "reserved"
        assert row["charged_micro_usd"] == PRICE.estimate_micro_usd(16384, 300)
        assert not repository.connection.in_transaction
        return httpx.Response(200, content=stream_wire())

    assert await speak(configured(repository, handle)) == "Hello."


async def test_conflicting_reported_usage_keeps_full_reservation(repository):
    wire = stream_wire(usage={"prompt_tokens": 50, "completion_tokens": 2}, done=False)
    wire += stream_wire(text="", usage={"prompt_tokens": 0, "completion_tokens": 0})
    provider = configured(repository, lambda _: httpx.Response(200, content=wire))
    assert await speak(provider) == "Hello."
    row = provider.usage_summary()["foreground"]
    assert row["estimated_micro_usd"] == PRICE.estimate_micro_usd(16384, 300)
    assert row["uncertain_requests"] == 1


async def test_duplicate_usage_keys_cannot_reset_spent_quota(repository):
    wire = (
        b'data: {"choices":[],"usage":{"prompt_tokens":100,"prompt_tokens":0,'
        b'"completion_tokens":0}}\ndata: [DONE]\n'
    )
    provider = configured(repository, lambda _: httpx.Response(200, content=wire))
    assert await speak(provider) == NEUTRAL_LINE
    assert provider.usage_summary()["foreground"]["estimated_micro_usd"] > 0


async def test_concurrency_limit_rejects_before_reserving_or_dispatching(repository):
    configuration = settings()
    configuration.ai_gateway.max_concurrent_requests = 1
    stream = HangingStream()
    calls = []
    provider = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(200, stream=stream),
        configuration=configuration,
    )
    task = asyncio.create_task(speak(provider))
    await asyncio.wait_for(stream.started.wait(), 1)
    assert await speak(provider) == NEUTRAL_LINE
    assert len(calls) == 1
    assert provider.usage_summary()["foreground"]["requests"] == 1
    await provider.aclose()
    assert task.cancelled()
    assert stream.closed


async def test_local_fallback_failure_ends_in_neutral_with_two_attempts(repository):
    configuration = settings()
    configuration.ai_gateway.local_fallback = LocalFallback(
        endpoint="http://127.0.0.1:1234/v1", dialogue_model="local-fixture"
    )
    calls = []
    provider = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(503),
        configuration=configuration,
    )
    assert await speak(provider) == NEUTRAL_LINE
    assert len(calls) == 2


def test_persisted_configuration_restores_models_caps_and_prices(repository):
    original = settings()
    original.ai_gateway.foreground = DailyBudget(max_requests=3, max_estimated_usd=0.25)
    persist_provider_configuration(repository, original)
    restored = effective_provider_settings(Settings(), repository)
    assert restored.provider == original.provider
    assert restored.dialogue_model == original.dialogue_model
    assert restored.ai_gateway == original.ai_gateway


async def test_nested_request_cannot_replace_active_task_registration(repository):
    provider = configured(repository, lambda _: httpx.Response(503))
    arguments = dict(
        local=False,
        model="fixture-model",
        prompt_bytes=10,
        output_tokens=10,
        budget_class="foreground",
    )
    async with provider.gate.request(**arguments):
        with pytest.raises(OutboundDenied, match="already active"):
            async with provider.gate.request(**arguments):
                pytest.fail("nested request acquired authorization")
        assert provider.safe_status()["active_remote_requests"] == 1
    assert provider.safe_status()["active_remote_requests"] == 0
    assert provider.usage_summary()["foreground"]["requests"] == 1
