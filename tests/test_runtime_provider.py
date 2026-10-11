import asyncio

import httpx
import pytest
from test_bridge import client as client
from test_bridge import open_ready, utterance

from service.lls.ai_owner import AIControlInUse
from service.lls.providers import FakeProvider, create_provider, effective_provider_settings
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.storage import Repository


class PendingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.opened = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.opened.set()
        yield b'data: {"choices":[{"delta":{"content":"Remote reply. "}}]}\n'
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def test_runtime_provider_replacement_and_revocation_join_mock_remote_request(
    client, monkeypatch
):
    monkeypatch.setenv("LLM_API_KEY", "mock-hosted-credential")
    sid, _ = await open_ready(client)
    runtime = client.app.state.runtime
    session = runtime.sessions[sid]
    stream = PendingStream()
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "127.0.0.1":
            return httpx.Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"Local reply."}}]}\ndata: [DONE]\n',
            )
        return httpx.Response(200, stream=stream)

    def factory(settings, repository, **kwargs):
        return create_provider(
            settings, repository, transport=httpx.MockTransport(handle), **kwargs
        )

    runtime.provider_factory = factory
    values = runtime.settings.model_dump()
    values.update(provider="openai", dialogue_model="test-hosted", cloud_opt_in=False)
    values["ai_gateway"]["prices"] = {
        "test-hosted": {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0}
    }
    await runtime.configure_provider(Settings.model_validate(values))
    assert requests == []
    blocked = "".join([part async for part in runtime.provider.dialogue({"input": "Hello"})])
    assert blocked
    assert requests == []
    await runtime.set_cloud_consent(True)
    response, _ = await utterance(client, sid)
    assert response.status_code == 200
    await asyncio.wait_for(stream.opened.wait(), 1)
    assert len(requests) == 1
    await asyncio.wait_for(runtime.set_cloud_consent(False), 1)
    assert stream.closed
    assert session.task.done()
    assert not any(
        c["kind"] == "action.execute" and c["state"] == "queued" for c in session.commands.values()
    )
    status = runtime.provider.safe_status()
    assert status["cloud_consent"] is False
    assert status["active_remote_requests"] == 0
    assert runtime.settings.provider == "openai"
    restored = effective_provider_settings(
        Settings(
            database_path=runtime.settings.database_path,
            provider="openai",
            dialogue_model="stale-env-model",
            cloud_opt_in=True,
        ),
        runtime.repo,
    )
    assert restored.provider == "openai" and restored.dialogue_model == "test-hosted"
    assert restored.cloud_opt_in is False
    values = runtime.settings.model_dump()
    values.update(provider="local_openai", dialogue_model="test-local")
    old = runtime.provider
    await runtime.configure_provider(Settings.model_validate(values))
    assert old.safe_status()["closed"]
    assert (
        "".join([part async for part in runtime.provider.dialogue({"input": "Hello"})])
        == "Local reply."
    )
    assert "authorization" not in requests[-1].headers
    assert runtime.provider.safe_status()["cloud_consent"] is False


async def test_runtime_lease_covers_custom_provider_and_releases_after_shutdown(tmp_path):
    repository = Repository(str(tmp_path / "owner.db"))
    settings = Settings(database_path=repository.path, provider="fake")
    runtime = Runtime(settings, repository, provider=FakeProvider())
    try:
        with pytest.raises(AIControlInUse):
            Runtime(settings, repository, provider=FakeProvider())
        await runtime.shutdown()
        assert runtime.ai_owner.closed
        replacement = Runtime(settings, repository, provider=FakeProvider())
        await replacement.shutdown()
    finally:
        await runtime.shutdown()
        repository.close()


async def test_runtime_constructor_failure_releases_control_lease(tmp_path):
    repository = Repository(str(tmp_path / "failed-owner.db"))
    settings = Settings(database_path=repository.path, provider="fake")

    def broken_factory(*args, **kwargs):
        raise ValueError("test provider construction failed")

    try:
        with pytest.raises(ValueError, match="test provider construction failed"):
            Runtime(settings, repository, provider_factory=broken_factory)
        runtime = Runtime(settings, repository, provider=FakeProvider())
        await runtime.shutdown()
    finally:
        repository.close()


async def test_provider_replacement_keeps_runtime_control_lease(tmp_path, monkeypatch):
    repository = Repository(str(tmp_path / "replacement-owner.db"))
    settings = Settings(database_path=repository.path, provider="fake")
    runtime = Runtime(settings, repository)
    closed, release = asyncio.Event(), asyncio.Event()
    old_provider = runtime.provider
    old_close = old_provider.aclose

    async def blocked_close():
        await old_close()
        closed.set()
        await release.wait()

    monkeypatch.setattr(old_provider, "aclose", blocked_close)
    values = settings.model_dump()
    values["provider"] = "disabled"
    replacing = asyncio.create_task(runtime.configure_provider(Settings.model_validate(values)))
    try:
        await asyncio.wait_for(closed.wait(), 1)
        assert old_provider.gate.closed
        assert not runtime.ai_owner.closed
        with pytest.raises(AIControlInUse):
            Runtime(settings, repository)
        release.set()
        await replacing
        assert runtime.provider.name == "disabled"
        assert runtime.provider.gate.owner is runtime.ai_owner
        assert not runtime.ai_owner.closed
    finally:
        release.set()
        await asyncio.gather(replacing, return_exceptions=True)
        await runtime.shutdown()
        repository.close()
