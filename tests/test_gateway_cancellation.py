"""Admission and cleanup cancellation never start another model request."""

import asyncio
from threading import Event

import httpx
import pytest
from test_gateway import (
    PRICE,
    HangingStream,
    configured,
    settings,
    speak,
    stream_wire,
)
from test_gateway import (
    repository as repository,
)
from test_gateway import (
    synthetic_credentials as synthetic_credentials,
)

from service.lls.gateway import NEUTRAL_LINE
from service.lls.gateway_config import LocalFallback
from service.lls.outbound import AIControlOwner


def with_fallback():
    configuration = settings()
    configuration.ai_gateway.local_fallback = LocalFallback(
        endpoint="http://127.0.0.1:1234/v1", dialogue_model="local-fixture"
    )
    return configuration


@pytest.mark.parametrize("reserve_fails", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_canceled_admission_joins_worker_without_dispatch_or_fallback(
    repository, monkeypatch, reserve_fails, cleanup_fails
):
    calls = []
    provider = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(200, content=stream_wire()),
        configuration=with_fallback(),
    )
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    original_reserve = provider.gate.ledger.reserve

    def reserve(**kwargs):
        identifier = None if reserve_fails else original_reserve(**kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        if reserve_fails:
            raise OSError("synthetic reservation failure after cancellation")
        return identifier

    def fail_cleanup(*args):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(provider.gate.ledger, "reserve", reserve)
    if cleanup_fails:
        monkeypatch.setattr(provider.gate.ledger, "settle", fail_cleanup)
    task = asyncio.create_task(speak(provider))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Joining must also survive repeated caller cancellation.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert calls == []
        assert provider.gate.active == {}
        assert provider.last_fallback_reason is None
        usage = provider.usage_summary()["foreground"]
        assert usage["requests"] == (0 if reserve_fails else 1)
        if not reserve_fails and not cleanup_fails:
            assert usage["pending_reservations"] == 0
            assert usage["uncertain_requests"] == 1
            assert usage["estimated_micro_usd"] == PRICE.estimate_micro_usd(16384, 300)
    finally:
        release.set()
        await provider.aclose()


async def test_canceled_stream_keeps_cancellation_when_settlement_fails(repository, monkeypatch):
    stream, calls = HangingStream(), []
    provider = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(200, stream=stream),
        configuration=with_fallback(),
    )

    def settle(*args):
        raise OSError("synthetic settlement failure")

    monkeypatch.setattr(provider.gate.ledger, "settle", settle)
    task = asyncio.create_task(speak(provider))
    try:
        await asyncio.wait_for(stream.started.wait(), 2)
        before_cancel = len(calls)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert len(calls) == before_cancel == 1
        assert stream.closed
        assert provider.gate.active == {}
        assert provider.last_fallback_reason is None
    finally:
        await provider.aclose()


async def test_cancel_during_settlement_preserves_cancellation_when_worker_fails(
    repository, monkeypatch
):
    calls = []
    provider = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(200, content=stream_wire()),
        configuration=with_fallback(),
    )
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()

    def settle(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        raise OSError("synthetic settlement failure after cancellation")

    monkeypatch.setattr(provider.gate.ledger, "settle", settle)
    task = asyncio.create_task(speak(provider))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        before_cancel = len(calls)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert len(calls) == before_cancel == 1
        assert provider.turn_routes[task] is None
        assert provider.gate.active == {}
    finally:
        release.set()
        await provider.aclose()


async def test_action_result_is_not_returned_after_provider_suppresses_cancellation(
    repository, monkeypatch
):
    provider = configured(repository, lambda _: pytest.fail("unexpected request"))
    entered = asyncio.Event()

    async def action(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return object()

    monkeypatch.setattr(provider.primary, "action", action)
    task = asyncio.create_task(provider.action({"input": "Face me"}, "Yes", ["face_player"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("truncated", [False, True])
async def test_incomplete_dialogue_cannot_dispatch_action_or_local_fallback(repository, truncated):
    calls = []
    configuration = with_fallback()
    configuration.max_reply_chars = 100
    text = "x" * 200 if truncated else "I might"
    provider = configured(
        repository,
        lambda request: calls.append(request)
        or httpx.Response(200, content=stream_wire(text, done=truncated)),
        configuration=configuration,
    )
    try:
        assert await speak(provider) == text[:100] + " " + NEUTRAL_LINE
        assert await provider.action({"input": "Face me"}, text, ["face_player"]) is None
        assert len(calls) == 1
        assert calls[0].url.host == "api.openai.com"
        assert provider.usage_summary()["foreground"]["uncertain_requests"] == 1
    finally:
        await provider.aclose()


async def test_shared_owner_revocation_joins_other_gateway_stream(repository):
    owner = AIControlOwner(repository)
    stream, calls = HangingStream(), []
    first = configured(
        repository,
        lambda request: calls.append(request) or httpx.Response(200, stream=stream),
        owner=owner,
    )
    second = configured(repository, lambda _: pytest.fail("unexpected request"), owner=owner)
    task = asyncio.create_task(speak(first))
    try:
        await asyncio.wait_for(stream.started.wait(), 2)
        await second.set_cloud_consent(False)
        assert task.cancelled()
        assert stream.closed
        assert await speak(first) == NEUTRAL_LINE
        assert len(calls) == 1
        await first.aclose()
        assert not owner.closed
    finally:
        await first.aclose()
        await second.aclose()
        owner.close()
