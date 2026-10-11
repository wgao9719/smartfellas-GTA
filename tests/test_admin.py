import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from service.lls.__main__ import fixture_actor_snapshot, fixture_session
from service.lls.admin import create_admin_router
from service.lls.protocol import SessionOpen
from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.storage import Repository

ORIGIN = "http://127.0.0.1:8765"
T0 = "2025-01-01T09:00:00Z"
T1 = "2025-01-01T10:00:00Z"
T2 = "2025-01-01T11:00:00Z"


@pytest.fixture
async def client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "admin.sqlite3"), provider="fake")
    repository = Repository(settings.database_path)
    runtime = Runtime(settings, repository)
    application = FastAPI()
    application.include_router(create_admin_router(settings, runtime, repository))
    transport = httpx.ASGITransport(app=application, client=("127.0.0.1", 4000))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        client.runtime = runtime
        client.repository = repository
        client.application = application
        page = await client.get("/dashboard")
        bootstrap = re.search(r"const credentials = (\{[^\n]+\});", page.text)
        assert bootstrap, page.text
        client.credentials = json.loads(bootstrap.group(1))
        client.headers.update(
            {
                "Authorization": "Bearer " + client.credentials["token"],
                "X-CSRF-Token": client.credentials["csrf"],
                "Origin": ORIGIN,
            }
        )
        yield client
    await runtime.shutdown()
    repository.close()


def seed(client, profile="pilot", actor="avery"):
    repo = client.repository
    repo.register_actor(profile, {"id": actor, "display_name": "Avery", "persona": {"bio": "Hi"}})
    branch = repo.open_timeline(profile, T0)
    repo.record_event(
        profile,
        branch,
        {
            "event_id": "event-1",
            "game_datetime": T1,
            "protagonist_id": "franklin",
            "provenance": "heard",
            "confidence": 0.7,
            "observer_ids": [actor],
            "payload": {"text": "A private bicycle conversation"},
        },
    )
    memory = repo.add_memory(
        profile,
        branch,
        actor,
        "franklin",
        T1,
        "I heard about a bicycle.",
        ["event-1"],
        "heard",
    )
    return branch, memory


async def open_profile(client, profile="pilot", branch=None):
    body = fixture_session()
    body.update(profile_id=profile, game_datetime=T2, parent_branch_id=branch)
    return await client.runtime.open(SessionOpen.model_validate(body))


@pytest.mark.asyncio
async def test_dashboard_is_local_no_cache_and_has_nonce_csp(client):
    page = await client.get("/dashboard")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    assert "localStorage" not in page.text
    assert "Bridge session token" not in page.text
    assert "__ADMIN_BOOTSTRAP__" not in page.text
    nonce = re.search(r'<script nonce="([^"]+)"', page.text).group(1)
    assert "'nonce-" + nonce + "'" in page.headers["content-security-policy"]
    assert 'role="status"' in page.text
    assert "Type the exact identifier" in page.text
    remote = httpx.ASGITransport(app=client.application, client=("100.101.102.103", 1234))
    async with httpx.AsyncClient(transport=remote, base_url=ORIGIN) as other:
        assert (await other.get("/dashboard")).status_code == 403


@pytest.mark.asyncio
async def test_admin_identity_is_separate_from_bridge_and_csrf_is_required(client):
    session = await open_profile(client)
    assert session.token != client.credentials["token"]
    assert (
        await client.get("/v1/admin/overview", headers={"Authorization": "Bearer " + session.token})
    ).status_code == 401
    body = {"confirm_scope": "all_profiles"}
    for headers in [
        {"X-CSRF-Token": "wrong"},
        {"Origin": "http://hostile.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ]:
        assert (
            await client.post("/v1/admin/backups", json=body, headers=headers)
        ).status_code == 403
    client.headers.pop("Origin")
    assert (await client.post("/v1/admin/backups", json=body)).status_code == 403
    client.headers["Origin"] = ORIGIN
    assert (
        await client.post(
            "/v1/admin/backups", content=json.dumps(body), headers={"Content-Type": "text/plain"}
        )
    ).status_code == 415


@pytest.mark.asyncio
async def test_actor_inspector_memory_edit_and_export_do_not_cross_profiles(client):
    _, memory = seed(client)
    seed(client, "other")
    overview = (await client.get("/v1/admin/overview")).json()
    assert {profile["profile_id"] for profile in overview["profiles"]} == {"pilot", "other"}
    assert "token" not in json.dumps(overview)
    actors = (await client.get("/v1/admin/profiles/pilot/actors")).json()["actors"]
    assert actors[0]["persona"] == {"bio": "Hi"}
    history = (await client.get("/v1/admin/profiles/pilot/memories?actor_id=avery")).json()
    assert history["scope"] == "administrative_all_branches"
    assert history["memories"][0]["memory_id"] == memory["memory_id"]
    response = await client.patch(
        f"/v1/admin/profiles/pilot/memories/{memory['memory_id']}",
        json={"text": "I heard a corrected scooter account.", "confidence": 1, "importance": 0.8},
    )
    assert response.status_code == 200
    assert response.json()["confidence"] == 0.7
    assert (
        await client.patch(
            f"/v1/admin/profiles/other/memories/{memory['memory_id']}",
            json={"text": "wrong profile"},
        )
    ).status_code == 404
    exported = (await client.get("/v1/admin/profiles/pilot/export")).json()
    assert exported["memories"][0]["text"] == "I heard a corrected scooter account."
    assert exported["profile"]["profile_id"] == "pilot"
    assert exported["events"][0]["payload"]["text"] == "A private bicycle conversation"


@pytest.mark.asyncio
async def test_memory_delete_requires_confirmation_and_scrubs_runtime_text(client):
    branch, memory = seed(client)
    session = await open_profile(client, branch=branch)
    command = {
        "kind": "dialogue.chunk",
        "state": "queued",
        "payload": {"text": "A private cached reply", "scene_id": "fixture"},
    }
    session.commands["fixture"] = command
    session.trace.append({"accidental_snippet": "A private cached reply"})
    path = f"/v1/admin/profiles/pilot/memories/{memory['memory_id']}"
    assert (
        await client.request("DELETE", path, json={"confirm_memory_id": "wrong"})
    ).status_code == 409
    response = await client.request("DELETE", path, json={"confirm_memory_id": memory["memory_id"]})
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "derived_memory_ids": []}
    assert command["payload"]["text"] == ""
    assert command["state"] == "canceled"
    assert not session.trace
    assert client.repository.get_memory("pilot", memory["memory_id"]) is None


@pytest.mark.asyncio
async def test_memory_delete_reports_the_characters_derived_lines(client):
    branch, memory = seed(client)
    repo = client.repository
    repo.record_event("pilot", branch, {
        "event_id": "event-2", "game_datetime": T2, "protagonist_id": "franklin",
        "provenance": "heard", "confidence": 0.7, "observer_ids": ["avery"],
        "payload": {"text": "I repeated the bicycle story."},
    })
    later = repo.add_memory(
        "pilot", branch, "avery", "franklin", T2, "I repeated the bicycle story.",
        ["event-2"], "heard",
    )
    repo.link_memory_derivations("pilot", "avery", [("event-1", "event-2")])
    path = f"/v1/admin/profiles/pilot/memories/{memory['memory_id']}"
    response = await client.request("DELETE", path, json={"confirm_memory_id": memory["memory_id"]})
    assert response.json() == {"deleted": True, "derived_memory_ids": [later["memory_id"]]}
    assert repo.get_memory("pilot", later["memory_id"]) is None


@pytest.mark.asyncio
async def test_profile_erase_waits_for_session_lock_and_closes_active_sessions(client):
    branch, _ = seed(client)
    seed(client, "other")
    session = await open_profile(client, branch=branch)
    await session.lock.acquire()
    request = asyncio.create_task(
        client.request("DELETE", "/v1/admin/profiles/pilot", json={"confirm_profile_id": "pilot"})
    )
    try:
        await asyncio.sleep(0.02)
        assert not request.done()
        assert client.repository.get_actor("pilot", "avery") is not None
    finally:
        session.lock.release()
    response = await asyncio.wait_for(request, 2)
    assert response.status_code == 200
    assert session.closed
    assert client.repository.get_actor("pilot", "avery") is None
    assert client.repository.get_actor("other", "avery") is not None
    assert (await client.get("/v1/admin/profiles/pilot/export")).status_code == 404


@pytest.mark.asyncio
async def test_actor_reset_keeps_persona_and_requires_exact_actor_confirmation(client):
    branch, memory = seed(client)
    session = await open_profile(client, branch=branch)
    session.action_outcomes.append({"actor_id": "avery", "reason": "private old reason"})
    session.delivery_turns["old-turn"] = {"private": "old delivery"}
    path = "/v1/admin/profiles/pilot/actors/avery/reset"
    assert (await client.post(path, json={"confirm_actor_id": "bea"})).status_code == 409
    assert (await client.post(path, json={"confirm_actor_id": "avery"})).status_code == 200
    assert client.repository.get_memory("pilot", memory["memory_id"]) is None
    assert client.repository.get_actor("pilot", "avery")["persona"] == {"bio": "Hi"}
    assert not session.action_outcomes
    assert not session.delivery_turns


@pytest.mark.asyncio
async def test_backup_is_local_fixed_destination_and_can_be_reopened(client):
    _, memory = seed(client)
    assert (
        await client.post(
            "/v1/admin/backups", json={"confirm_scope": "all_profiles", "destination": "/tmp/evil"}
        )
    ).status_code == 422
    response = await client.post("/v1/admin/backups", json={"confirm_scope": "all_profiles"})
    assert response.status_code == 201
    path = Path(response.json()["path"])
    assert path.parent == Path(client.repository.path).parent / "backups"
    with Repository(path) as reopened:
        assert reopened.get_memory("pilot", memory["memory_id"]) is not None


@pytest.mark.asyncio
async def test_timeline_selection_is_profile_scoped_and_used_on_next_connection(client):
    branch, _ = seed(client)
    other, _ = seed(client, "other")
    session = await open_profile(client, branch=branch)
    branches = (await client.get("/v1/admin/profiles/pilot/branches")).json()["branches"]
    assert branches[0]["branch_id"] == branch
    path = "/v1/admin/profiles/pilot/timeline"
    assert (
        await client.post(path, json={"confirm_profile_id": "pilot", "branch_id": other})
    ).status_code == 404
    response = await client.post(path, json={"confirm_profile_id": "pilot", "branch_id": branch})
    assert response.status_code == 200
    assert session.closed
    assert client.runtime.selected_branches["pilot"] == branch
    next_session = await open_profile(client)
    assert next_session.branch_id == branch
    assert "pilot" not in client.runtime.selected_branches


async def pin_session(client, profile="pilot"):
    body = fixture_session()
    body.update(profile_id=profile, game_datetime=T2)
    body["manifest"]["capability_flags"] = ["fake_game", "actor_pin"]
    session = await client.runtime.open(SessionOpen.model_validate(body))
    stamp = client.runtime.clock()
    session.world = {
        "online": False, "mission_active": False, "cutscene_active": False,
        "random_event_active": False, "paused": False, "protagonist_id": "franklin",
        "_sampled_at": stamp,
    }
    client.repository.register_actor(
        profile,
        {
            "actor_id": "walker", "actor_kind": "ambient_ephemeral", "session_id": session.id,
            "display_name": "Nearby resident", "control_policy": "ambient",
        },
    )
    session.actors["walker"] = {
        **fixture_actor_snapshot(runtime_instance_id="walker-1", name="Nearby resident"),
        "actor_kind": "ambient_ephemeral",
        "_sampled_at": stamp,
    }
    return session


async def pin_diagnostics(client, session):
    actors = (await client.get(f"/v1/admin/sessions/{session.id}/diagnostics")).json()["actors"]
    return next((a["pin_eligible"], a["pin_reason"]) for a in actors if a["actor_id"] == "walker")


@pytest.mark.asyncio
async def test_pin_requires_token_csrf_confirmation_and_eligibility(client):
    session = await pin_session(client)
    path = f"/v1/admin/sessions/{session.id}/actors/walker/pin"
    body = {"confirm_actor_id": "walker"}
    unauthorized = {"Authorization": "Bearer wrong"}
    assert (await client.post(path, json=body, headers=unauthorized)).status_code == 401
    assert (await client.post(path, json=body, headers={"X-CSRF-Token": "x"})).status_code == 403
    assert (await client.post(path, json={"confirm_actor_id": "bea"})).status_code == 409
    for name in ["", "x" * 49, "<b>Bold</b>", "tab\there", " padded"]:
        response = await client.post(path, json={**body, "display_name": name})
        assert response.status_code == 422
    missing = "/v1/admin/sessions/missing/actors/walker/pin"
    assert (await client.post(missing, json=body)).status_code == 404
    session.actors["walker"]["control_policy"] = "protected"
    assert await pin_diagnostics(client, session) == (
        False, "This pedestrian is protected and cannot be pinned."
    )
    refused = await client.post(path, json=body)
    assert refused.status_code == 409 and "protected" in refused.json()["detail"]
    assert client.repository.get_actor("pilot", "walker")["actor_kind"] == "ambient_ephemeral"
    session.actors["walker"]["control_policy"] = "ambient"
    assert await pin_diagnostics(client, session) == (True, None)
    response = await client.post(path, json={**body, "display_name": "Corner Regular"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "pin_requested", "actor_id": "walker", "display_name": "Corner Regular",
        "actor_kind": "spawned_persistent",
    }
    stored = client.repository.get_actor("pilot", "walker")
    assert (stored["actor_kind"], stored["display_name"]) == (
        "spawned_persistent", "Corner Regular"
    )
    assert [c["kind"] for c in session.commands.values()][-2:] == ["scene.cancel", "actor.pin"]
    assert await pin_diagnostics(client, session) == (False, "Already pinned in this session.")


@pytest.mark.asyncio
async def test_forget_refuses_pack_actors_and_removes_pinned_ones(client):
    seed(client)
    session = await pin_session(client)
    pin_path = f"/v1/admin/sessions/{session.id}/actors/walker/pin"
    assert (await client.post(pin_path, json={"confirm_actor_id": "walker"})).status_code == 200

    async def forget(actor, confirm=None):
        return await client.request(
            "DELETE",
            f"/v1/admin/profiles/pilot/actors/{actor}",
            json={"confirm_actor_id": confirm or actor},
        )

    assert (await forget("avery")).status_code == 409
    assert (await forget("walker", "avery")).status_code == 409
    assert (await forget("missing")).status_code == 404
    response = await forget("walker")
    assert response.status_code == 200
    assert response.json() == {"status": "forgotten", "actor_id": "walker"}
    assert client.repository.get_actor("pilot", "walker") is None
    assert client.repository.get_actor("pilot", "avery") is not None
    assert "walker" not in session.actors and session.pins["walker"]["state"] == "forgotten"
