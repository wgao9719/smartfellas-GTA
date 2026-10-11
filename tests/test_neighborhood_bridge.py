"""Real service authentication and lifecycle around virtual neighborhood work."""

import asyncio
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from service.lls.__main__ import fixture_event, fixture_session
from service.lls.app import create_app
from service.lls.neighborhood import demo_pack
from service.lls.neighborhood_bridge import DEVELOPMENT_NEIGHBORHOOD_CAPABILITY
from service.lls.settings import Settings

START = datetime(2025, 1, 1, tzinfo=timezone.utc)
PREFIX = "/v1/bridge/neighborhood/"


def envelope(session, **changes):
    return {
        "session_id": session.id,
        "runtime_generation": session.request.runtime_generation,
        "protagonist_id": session.request.protagonist_id,
        "game_datetime": session.game_datetime,
        "neighborhood_id": "authored_demo",
        **changes,
    }


async def world(client, session, *, at=START, **changes):
    event = fixture_event(session.id, session.last_seq + 1, "world.snapshot", {
        "online": False, "mission_active": False, "cutscene_active": False,
        "random_event_active": False, "paused": False, "protagonist_id": "franklin",
        **changes,
    })
    event["game_datetime"] = at.isoformat()
    result = await client.post("/v1/bridge/events:batch", json={
        "session_id": session.id, "events": [event],
    })
    assert result.status_code == 200, result.text


@pytest.fixture
async def bridge(tmp_path):
    path = tmp_path / "authored.json"
    path.write_text(demo_pack(30).model_dump_json(), encoding="utf-8")
    clock = [100.0]
    app = create_app(Settings(
        database_path=str(tmp_path / "runtime.db"), release=False,
        dev_neighborhood=True, neighborhood_pack_path=str(path),
    ), runtime_options={"clock": lambda: clock[0]})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 11)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            body = fixture_session()
            body["game_datetime"] = START.isoformat()
            body["manifest"]["capability_flags"].append(DEVELOPMENT_NEIGHBORHOOD_CAPABILITY)
            opened = await client.post("/v1/bridge/sessions", json=body)
            assert opened.status_code == 200, opened.text
            client.headers["Authorization"] = "Bearer " + opened.json()["token"]
            runtime = app.state.runtime
            session = runtime.sessions[opened.json()["session_id"]]
            await world(client, session)
            yield client, runtime, session, clock


def test_release_rejects_neighborhood_setting():
    with pytest.raises(ValidationError, match="neighborhood"):
        Settings(dev_neighborhood=True)


@pytest.mark.parametrize("gate", ["setting", "capability", "token"])
async def test_closed_gates(bridge, gate):
    client, runtime, session, _ = bridge
    if gate == "setting":
        runtime.settings.dev_neighborhood = False
    elif gate == "capability":
        session.request.manifest.capability_flags.remove(DEVELOPMENT_NEIGHBORHOOD_CAPABILITY)
    else:
        client.headers["Authorization"] = "Bearer wrong"
    response = await client.post(PREFIX + "initialize", json=envelope(session))
    assert response.status_code == (401 if gate == "token" else 403)
    assert runtime.repo.connection.execute(
        "SELECT COUNT(*) FROM events WHERE json_extract(data,'$.event_type') "
        "LIKE 'neighborhood.%'"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("change, status", [
    ({"runtime_generation": 2}, 409),
    ({"protagonist_id": "michael"}, 409),
    ({"game_datetime": (START + timedelta(days=1)).isoformat()}, 409),
    ({"neighborhood_id": "wrong"}, 409),
    ({"session_id": "other-session"}, 401),
    ({"profile_id": "other-profile"}, 422),
    ({"branch_id": "other-branch"}, 422),
])
async def test_identity_and_clock_come_from_authenticated_session(bridge, change, status):
    client, _, session, _ = bridge
    response = await client.post(PREFIX + "initialize", json=envelope(session, **change))
    assert response.status_code == status, response.text


@pytest.mark.parametrize("flag", [
    "online", "mission_active", "cutscene_active", "random_event_active", "paused", "stale",
])
async def test_mutations_require_fresh_eligible_world(bridge, flag):
    client, _, session, clock = bridge
    if flag == "stale":
        clock[0] += 6
    else:
        await world(client, session, **{flag: True})
    response = await client.post(PREFIX + "initialize", json=envelope(session))
    assert response.status_code == 409, response.text
    # Inspection remains useful when advancement is suspended.
    response = await client.post(PREFIX + "snapshot", json=envelope(session))
    assert response.status_code == 200, response.text
    assert not response.json()["configured"]


async def test_explicit_bounded_advancement_and_native_gap(bridge):
    client, runtime, session, _ = bridge
    response = await client.post(PREFIX + "advance", json=envelope(session))
    assert response.status_code == 409
    initialized = await client.post(PREFIX + "initialize", json=envelope(session))
    assert initialized.status_code == 200, initialized.text
    before = initialized.json()
    assert len(before["actors"]) == 30
    assert before["branch_id"] == session.branch_id
    await world(client, session, at=START + timedelta(days=3))
    snapshot = (await client.post(PREFIX + "snapshot", json=envelope(session))).json()
    assert snapshot["state_digest"] == before["state_digest"]
    assert snapshot["backlog_actors"] == 30
    response = await client.post(PREFIX + "advance", json=envelope(
        session, budget={"max_events": 20, "background_events": 20, "max_days": 3},
    ))
    assert response.status_code == 200, response.text
    result = response.json()
    assert 0 < result["events_written"] <= 20
    assert not result["caught_up"]
    assert result["native_operations"] == result["model_calls"] == 0
    assert result["native_verified"] is result["game_verified"] is False
    assert result["automatic_advancement"] is False
    assert result["domain"] == "development_simulation"
    assert not session.commands
    for table in ("actor_instances", "memories", "relationship_updates"):
        assert runtime.repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert runtime.neighborhood.diagnostics()["config_hash"] == result["config_hash"]


async def test_canceled_thread_joins_before_actor_erasure(bridge, monkeypatch):
    client, runtime, session, _ = bridge
    initialized = await client.post(PREFIX + "initialize", json=envelope(session))
    assert initialized.status_code == 200
    await world(client, session, at=START + timedelta(hours=7))
    page = await client.get("/dashboard")
    credentials = json.loads(re.search(r"const credentials = (\{[^\n]+\});", page.text)[1])
    admin_headers = {
        "Authorization": "Bearer " + credentials["token"],
        "X-CSRF-Token": credentials["csrf"], "Origin": "http://127.0.0.1:8765",
    }
    entered, release = threading.Event(), threading.Event()
    original = runtime.neighborhood.engine.advance

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.neighborhood.engine, "advance", blocked)
    task = asyncio.create_task(client.post(PREFIX + "advance", json=envelope(session)))
    reset = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        reset = asyncio.create_task(client.post(
            f"/v1/admin/profiles/{session.request.profile_id}/actors/neighbor-001/reset",
            json={"confirm_actor_id": "neighbor-001"}, headers=admin_headers,
        ))
        await asyncio.sleep(0.02)
        assert not task.done() and not reset.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert reset is not None
    result = await asyncio.wait_for(reset, 3)
    assert result.status_code == 200, result.text
    snapshot = await client.post(PREFIX + "snapshot", json=envelope(session))
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["actors"][0]["status"] == "suppressed"


# pytest exports the node ID as PYTEST_CURRENT_TEST; Windows caps it at 32767 chars.
@pytest.mark.parametrize("contents", ["{}", pytest.param(" " * (256 * 1024 + 1), id="oversize")])
def test_invalid_authored_pack_fails_startup(tmp_path, contents):
    path = tmp_path / "pack.json"
    path.write_text(contents, encoding="utf-8")
    settings = Settings(
        database_path=str(tmp_path / "runtime.db"), release=False,
        dev_neighborhood=True, neighborhood_pack_path=str(path),
    )
    with pytest.raises(ValueError):
        create_app(settings)
    # Failed startup must release its AI owner and database resources.
    path.write_text(demo_pack(30).model_dump_json(), encoding="utf-8")
    app = create_app(settings)
    app.state.runtime.ai_owner.close()
    app.state.repository.close()
