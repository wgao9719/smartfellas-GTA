import json

import pytest
from test_admin import client as client
from test_admin import open_profile

from service.lls.gateway import effective_provider_settings
from service.lls.settings import Settings


async def configuration(client, **changes):
    response = await client.get("/v1/admin/provider")
    assert response.status_code == 200
    config = response.json()["configuration"]
    config.pop("cloud_opt_in")
    config.update(changes)
    return config


async def consent_body(client, endpoint):
    status = (await client.get("/v1/admin/provider")).json()
    return {"endpoint": endpoint, "configuration_revision": status["configuration_revision"]}


async def hosted(client):
    config = await configuration(
        client,
        provider="openai",
        dialogue_model="dialogue-test",
        action_model="action-test",
        openai_base_url="https://models.example/v1",
    )
    config["ai_gateway"]["prices"] = {
        name: {"input_usd_per_million": 1, "output_usd_per_million": 2}
        for name in ["dialogue-test", "action-test"]
    }
    return config


@pytest.mark.asyncio
async def test_local_provider_save_preserves_runtime_controls_and_restarts(client):
    speech = client.runtime.settings.speech
    config = await configuration(client, provider="local_openai", dialogue_model="local-test")
    response = await client.put("/v1/admin/provider", json=config)
    assert response.status_code == 200, response.text
    assert response.json()["configuration"]["provider"] == "local_openai"
    assert response.json()["cloud_consent"] is False
    assert client.runtime.settings.speech == speech
    assert client.runtime.settings.database_path == client.repository.path
    restored = effective_provider_settings(
        Settings(database_path=client.repository.path), client.repository
    )
    assert restored.provider == "local_openai"
    assert restored.dialogue_model == "local-test"
    assert restored.cloud_opt_in is False


@pytest.mark.asyncio
async def test_hosted_consent_requires_saved_destination_and_resets_on_edit(client):
    config = await hosted(client)
    saved = await client.put("/v1/admin/provider", json=config)
    assert saved.status_code == 200, saved.text
    assert saved.json()["cloud_consent"] is False
    assert (
        await client.post(
            "/v1/admin/provider/consent", json=await consent_body(client, "https://old.example/v1")
        )
    ).status_code == 409
    enabled = await client.post(
        "/v1/admin/provider/consent", json=await consent_body(client, config["openai_base_url"])
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["cloud_consent"] is True
    config["openai_base_url"] = "https://replacement.example/v1"
    changed = await client.put("/v1/admin/provider", json=config)
    assert changed.status_code == 200
    assert changed.json()["cloud_consent"] is False
    assert changed.json()["usage"]["foreground"]["requests"] == 0


@pytest.mark.asyncio
async def test_revocation_survives_stale_environment_opt_in(client):
    config = await hosted(client)
    await client.put("/v1/admin/provider", json=config)
    await client.post(
        "/v1/admin/provider/consent", json=await consent_body(client, config["openai_base_url"])
    )
    response = await client.post("/v1/admin/provider/revoke", json={})
    assert response.status_code == 200
    assert response.json()["cloud_consent"] is False
    stale = Settings(provider="openai", dialogue_model="old", cloud_opt_in=True)
    restored = effective_provider_settings(stale, client.repository)
    assert restored.provider == "openai"
    assert restored.openai_base_url == config["openai_base_url"]
    assert restored.cloud_opt_in is False
    assert response.json()["active_remote_requests"] == 0


@pytest.mark.asyncio
async def test_consent_requires_model_prices_and_hosted_selection(client):
    assert (
        await client.post(
            "/v1/admin/provider/consent",
            json=await consent_body(client, "https://models.example/v1"),
        )
    ).status_code == 409
    config = await hosted(client)
    del config["ai_gateway"]["prices"]["action-test"]
    assert (await client.put("/v1/admin/provider", json=config)).status_code == 200
    assert (
        await client.post(
            "/v1/admin/provider/consent", json=await consent_body(client, config["openai_base_url"])
        )
    ).status_code == 409
    assert client.runtime.provider.safe_status()["cloud_consent"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": "secret"},
        {"cloud_opt_in": True},
        {"provider": "local_openai", "local_base_url": "http://192.168.1.2:1234/v1"},
        {"openai_base_url": "https://user:secret@models.example/v1"},
        {"openai_base_url": "https://models.example/v1?key=secret"},
        {"local_base_url": "http://user:secret@127.0.0.1:1234/v1"},
        {"provider": "openai", "dialogue_model": ""},
        {"database_path": "arbitrary.sqlite3"},
    ],
)
async def test_provider_rejects_unsafe_or_unrelated_configuration(client, changes):
    before = client.runtime.settings
    config = await configuration(client, **changes)
    response = await client.put("/v1/admin/provider", json=config)
    assert response.status_code == 422, response.text
    assert client.runtime.settings is before


@pytest.mark.asyncio
async def test_provider_routes_require_admin_and_csrf(client):
    session = await open_profile(client)
    response = await client.get(
        "/v1/admin/provider", headers={"Authorization": "Bearer " + session.token}
    )
    assert response.status_code == 401
    config = await hosted(client)
    for path, method, body in [
        ("/provider", "PUT", config),
        ("/provider/consent", "POST", {"endpoint": config["openai_base_url"]}),
        ("/provider/revoke", "POST", {}),
    ]:
        response = await client.request(
            method, "/v1/admin" + path, json=body, headers={"X-CSRF-Token": "wrong"}
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_provider_status_excludes_environment_credentials(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-value")
    config = await hosted(client)
    await client.put("/v1/admin/provider", json=config)
    response = await client.get("/v1/admin/provider")
    assert response.headers["cache-control"] == "no-store"
    assert "provider-secret-value" not in response.text
    assert "bootstrap_token" not in response.text
    assert "provider-secret-value" not in json.dumps(client.runtime.provider.gate.ledger.settings())


@pytest.mark.asyncio
async def test_stale_consent_cannot_approve_new_model_or_budget(client):
    config = await hosted(client)
    await client.put("/v1/admin/provider", json=config)
    reviewed = await consent_body(client, config["openai_base_url"])
    config["ai_gateway"]["foreground"]["max_estimated_usd"] = 2
    await client.put("/v1/admin/provider", json=config)
    assert (await client.post("/v1/admin/provider/consent", json=reviewed)).status_code == 409
    config["dialogue_model"] = "changed-model"
    config["ai_gateway"]["prices"]["changed-model"] = config["ai_gateway"]["prices"][
        "dialogue-test"
    ]
    await client.put("/v1/admin/provider", json=config)
    assert (await client.post("/v1/admin/provider/consent", json=reviewed)).status_code == 409
    assert client.runtime.provider.safe_status()["cloud_consent"] is False


@pytest.mark.asyncio
async def test_embedding_configuration_persists_and_replaces_shared_gate(client):
    old_gate = client.runtime.provider.gate
    config = await configuration(client, embedding={
        "provider": "local_openai", "model": "local-embedding",
        "base_url": "http://127.0.0.1:1235/v1", "dimensions": 64,
    })
    response = await client.put("/v1/admin/provider", json=config)
    assert response.status_code == 200, response.text
    index = client.runtime.memory_index
    assert old_gate.closed
    assert index.provider.gate is client.runtime.provider.gate
    assert index.provider.endpoint == "http://127.0.0.1:1235/v1/embeddings"
    restored = effective_provider_settings(Settings(), client.repository)
    assert restored.embedding == client.runtime.settings.embedding
    assert restored.embedding.dimensions == 64


@pytest.mark.asyncio
async def test_embedding_only_cloud_requires_price_and_reviewed_endpoint(client):
    config = await configuration(client, embedding={
        "provider": "openai", "model": "hosted-embedding",
        "base_url": "https://embeddings.example/v1",
    })
    assert (await client.put("/v1/admin/provider", json=config)).status_code == 200
    assert (await client.post(
        "/v1/admin/provider/consent", json=await consent_body(client, config["openai_base_url"])
    )).status_code == 409
    config["ai_gateway"]["prices"]["hosted-embedding"] = {
        "input_usd_per_million": 1, "output_usd_per_million": 0,
    }
    assert (await client.put("/v1/admin/provider", json=config)).status_code == 200
    reviewed = await consent_body(client, config["openai_base_url"])
    assert (await client.post("/v1/admin/provider/consent", json=reviewed)).status_code == 200
    assert client.runtime.memory_index.provider.gate.cloud_consent
    config["embedding"]["base_url"] = "https://replacement.example/v1"
    assert (await client.put("/v1/admin/provider", json=config)).status_code == 200
    assert not client.runtime.memory_index.provider.gate.cloud_consent
    assert (await client.post("/v1/admin/provider/consent", json=reviewed)).status_code == 409
