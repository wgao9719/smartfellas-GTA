"""Session opening requires a chosen save association before memory attachment."""

from contextlib import asynccontextmanager

import httpx
import pytest
from test_bridge import client as client
from test_storage import T0, T1, T2, T3, T4, event, memory, recall

from service.lls.__main__ import fixture_session
from service.lls.app import create_app
from service.lls.settings import Settings


def session_body(**changes):
    return {
        **fixture_session(),
        "profile_id": "save-a",
        "save_slot": "slot-a",
        "game_datetime": T0,
        **changes,
    }


async def open_session(client, **changes):
    response = await client.post("/v1/bridge/sessions", json=session_body(**changes))
    assert response.status_code == 200, response.text
    return client.app.state.runtime.sessions[response.json()["session_id"]]


def remember(repo, branch, identifier="encounter", at=T1):
    repo.register_actor("save-a", {"id": "avery", "display_name": "Avery"})
    event(repo, branch, identifier, at)
    saved = memory(
        repo, branch, identifier, at, text=f"I remember the {identifier} bicycle encounter."
    )
    repo.add_knowledge(
        "save-a", branch, at, identifier, [identifier], "directly_observed",
        owner_id="avery", protagonist_id="franklin",
    )
    repo.apply_relationship_event("save-a", branch, "avery", "franklin", identifier)
    return saved


@pytest.mark.parametrize("missing", ["profile_id", "save_slot", "profile_confirmed"])
async def test_first_load_missing_selection_warns_without_creating_state(client, missing):
    body = session_body()
    del body[missing]
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 409
    assert "before attaching memories" in response.json()["detail"]
    assert client.app.state.runtime.profile_warning == response.json()["detail"]
    assert client.app.state.runtime.sessions == {}
    assert client.app.state.repository.list_profiles() == []


async def test_first_explicit_selection_creates_associated_root(client):
    session = await open_session(client)
    repo = client.app.state.repository
    profile = repo.list_profiles()[0]
    assert profile["profile_id"] == "save-a"
    assert profile["save_slot"] == "slot-a"
    assert profile["active_branch"] == session.branch_id
    assert repo.list_branches("save-a")[0]["parent_id"] is None


@pytest.mark.parametrize("field", ["profile_id", "save_slot"])
async def test_blank_manual_selection_cannot_attach_memories(client, field):
    response = await client.post(
        "/v1/bridge/sessions", json=session_body(**{field: "   "})
    )
    assert response.status_code == 409
    assert client.app.state.runtime.sessions == {}
    assert client.app.state.repository.list_profiles() == []


async def test_legacy_profile_needs_confirmation_before_association(client):
    repo = client.app.state.repository
    branch = repo.open_timeline("save-a", T0)
    saved = remember(repo, branch)
    before = repo.export_profile("save-a")
    response = await client.post(
        "/v1/bridge/sessions",
        json=session_body(game_datetime=T4, profile_confirmed=False),
    )
    assert response.status_code == 409
    assert repo.export_profile("save-a") == before
    assert client.app.state.runtime.sessions == {}

    session = await open_session(client, game_datetime=T4)
    assert session.branch_id == branch
    assert repo.list_profiles()[0]["save_slot"] == "slot-a"
    assert recall(repo, session.branch_id)[0]["memory_id"] == saved["memory_id"]
    assert client.app.state.runtime.profile_warning is None


@asynccontextmanager
async def restarted_client(path):
    app = create_app(Settings(database_path=str(path), provider="fake"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as connection:
            connection.app = app
            yield connection


async def test_same_save_continues_after_service_and_database_reopen(tmp_path):
    path = tmp_path / "association.sqlite3"
    async with restarted_client(path) as connection:
        session = await open_session(connection)
        branch = session.branch_id
        saved = remember(connection.app.state.repository, branch)
    async with restarted_client(path) as connection:
        repo = connection.app.state.repository
        response = await connection.post(
            "/v1/bridge/sessions",
            json=session_body(game_datetime=T4, profile_confirmed=False),
        )
        assert response.status_code == 409
        assert connection.app.state.runtime.sessions == {}
        session = await open_session(connection, game_datetime=T4)
        assert session.branch_id == branch
        assert repo.list_profiles()[0]["save_slot"] == "slot-a"
        assert recall(repo, session.branch_id)[0]["memory_id"] == saved["memory_id"]


@pytest.mark.parametrize("explicit_parent", [False, True])
async def test_different_slot_at_equal_time_cannot_attach_existing_profile(client, explicit_parent):
    session = await open_session(client, game_datetime=T1)
    repo = client.app.state.repository
    saved = remember(repo, session.branch_id)
    before = repo.export_profile("save-a")
    response = await client.post(
        "/v1/bridge/sessions",
        json=session_body(
            game_datetime=T1, save_slot="slot-b",
            parent_branch_id=session.branch_id if explicit_parent else None,
        ),
    )
    assert response.status_code == 409
    assert "another save slot" in response.json()["detail"]
    assert repo.export_profile("save-a") == before
    assert list(client.app.state.runtime.sessions) == [session.id]
    assert recall(repo, session.branch_id)[0]["memory_id"] == saved["memory_id"]

    other = await open_session(client, profile_id="save-b", save_slot="slot-b", game_datetime=T1)
    repo.register_actor("save-b", {"id": "avery", "display_name": "Other Avery"})
    assert other.branch_id != session.branch_id
    assert repo.list_branches("save-b")[0]["parent_id"] is None
    assert not recall(repo, other.branch_id, profile="save-b")
    assert repo.export_profile("save-a") == before


async def test_confirmed_earlier_save_keeps_existing_rewind_cutoff(client):
    original = await open_session(client)
    repo = client.app.state.repository
    early = remember(repo, original.branch_id, "early", T1)
    remember(repo, original.branch_id, "future", T3)
    rewind = await open_session(client, game_datetime=T2)
    assert rewind.branch_id != original.branch_id
    assert [row["memory_id"] for row in recall(repo, rewind.branch_id)] == [early["memory_id"]]
    assert [
        row["text"]
        for row in repo.retrieve_knowledge("save-a", rewind.branch_id, "avery", "franklin", T4)
    ] == ["early"]
    assert repo.get_relationship(
        "save-a", rewind.branch_id, "avery", "franklin", T4
    )["familiarity"] == 0.02
    assert len(recall(repo, original.branch_id)) == 2


@pytest.mark.parametrize("protagonist", ["michael", "trevor"])
async def test_confirmed_protagonist_switch_preserves_save_and_privacy(client, protagonist):
    franklin = await open_session(client)
    repo = client.app.state.repository
    saved = remember(repo, franklin.branch_id)
    switched = await open_session(client, game_datetime=T4, protagonist_id=protagonist)
    assert switched.branch_id == franklin.branch_id
    assert repo.list_profiles()[0]["save_slot"] == "slot-a"
    assert not recall(repo, switched.branch_id, protagonist=protagonist)
    assert not repo.retrieve_knowledge("save-a", switched.branch_id, "avery", protagonist, T4)
    assert repo.get_relationship(
        "save-a", switched.branch_id, "avery", protagonist, T4
    )["familiarity"] == 0
    returned = await open_session(client, game_datetime=T4)
    assert returned.branch_id == franklin.branch_id
    assert recall(repo, returned.branch_id)[0]["memory_id"] == saved["memory_id"]
