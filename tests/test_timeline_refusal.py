"""Session refusals distinguish a history choice from a save/profile mismatch."""

import json
import re

from service.lls.__main__ import fixture_session
from tests.test_bridge import client as client


async def test_ambiguous_session_reports_reason_and_dashboard_choice_allows_retry(client):
    body = fixture_session()
    repo = client.app.state.repository
    profile = body["profile_id"]
    repo.create_profile(profile, save_slot=body["save_slot"])
    original = repo.open_timeline(profile, "2013-09-17T09:00:00")
    repo.open_timeline(profile, "2013-09-17T13:00:00")
    alternate = repo.open_timeline(profile, "2013-09-17T10:00:00")
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "timeline_ambiguous"
    assert set(response.json()["branch_candidates"]) == {original, alternate}
    assert client.app.state.runtime.sessions == {}

    page = await client.get("/dashboard")
    bootstrap = re.search(r"const credentials = (\{[^\n]+\});", page.text)
    assert bootstrap
    credentials = json.loads(bootstrap.group(1))
    selected = await client.post(
        f"/v1/admin/profiles/{profile}/timeline",
        json={"confirm_profile_id": profile, "branch_id": alternate},
        headers={
            "Authorization": "Bearer " + credentials["token"],
            "X-CSRF-Token": credentials["csrf"],
            "Origin": "http://127.0.0.1:8765",
        },
    )
    assert selected.status_code == 200
    retry = await client.post("/v1/bridge/sessions", json=body)
    assert retry.status_code == 200
    assert retry.json()["branch_id"] == alternate


async def test_save_profile_mismatch_does_not_report_timeline_ambiguity(client):
    body = fixture_session()
    client.app.state.repository.create_profile(body["profile_id"], save_slot="another-slot")
    response = await client.post("/v1/bridge/sessions", json=body)
    assert response.status_code == 409
    assert response.json().get("reason_code") != "timeline_ambiguous"
    assert "another save slot" in response.json()["detail"]
