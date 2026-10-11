import json
from dataclasses import replace

import pytest
from test_admin import T0, T1
from test_admin import client as client
from test_skill_store import candidate, definition, observation
from test_skill_store import state as state


def seed_characters(repository, profile="pilot"):
    for actor in ("avery", "bea"):
        repository.register_actor(profile, {
            "id": actor, "display_name": actor.title(), "persona": {"bio": actor + " biography"},
        })
    branch = repository.open_timeline(profile, T0)
    memories = {}
    for event_id, observers in (("avery-only", ["avery"]), ("bea-only", ["bea"]),
                                ("shared", ["avery", "bea"])):
        repository.record_event(profile, branch, {
            "event_id": event_id, "game_datetime": T1, "protagonist_id": "franklin",
            "event_type": "conversation_completed", "provenance": "directly_observed",
            "confidence": 1, "observer_ids": observers,
            "payload": {"text": profile + " " + event_id + " raw conversation"},
        })
        for actor in observers:
            memories[actor, event_id] = repository.add_memory(
                profile, branch, actor, "franklin", T1,
                profile + " " + actor + " " + event_id + " memory", [event_id],
                "directly_observed",
            )
            repository.add_knowledge(
                profile, branch, T1, profile + " " + actor + " " + event_id + " fact",
                [event_id], "directly_observed", owner_id=actor, protagonist_id="franklin",
            )
            repository.apply_relationship_event(profile, branch, actor, "franklin", event_id)
    return branch, memories


async def test_character_export_is_scoped_to_profile_actor_and_observed_events(client):
    repo = client.repository
    _, memories = seed_characters(repo)
    seed_characters(repo, "other")
    response = await client.get("/v1/admin/profiles/pilot/actors/avery/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    exported = response.json()
    assert exported["profile"] == {"profile_id": "pilot"}
    assert [actor["id"] for actor in exported["actors"]] == ["avery"]
    assert {memory["memory_id"] for memory in exported["memories"]} == {
        memories["avery", event_id]["memory_id"] for event_id in ("avery-only", "shared")
    }
    assert {event["event_id"] for event in exported["events"]} == {"avery-only", "shared"}
    assert {card["owner_id"] for card in exported["knowledge_cards"]} == {"avery"}
    assert {row["actor_id"] for row in exported["relationships"]} == {"avery"}
    serialized = json.dumps(exported)
    for excluded in ("other", "bea biography", "bea-only", "pilot bea shared memory",
                     "pilot bea shared fact"):
        assert excluded not in serialized


async def test_character_export_requires_admin_and_known_profile_actor(client):
    seed_characters(client.repository)
    path = "/v1/admin/profiles/pilot/actors/avery/export"
    assert (await client.get(path, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/v1/admin/profiles/missing/actors/avery/export")).status_code == 404
    assert (await client.get("/v1/admin/profiles/pilot/actors/missing/export")).status_code == 404
    page = await client.get("/dashboard")
    assert 'id="export-actor"' in page.text
    assert "Show character export" in page.text


async def test_character_export_respects_private_scope_and_includes_its_public_sources(client):
    repo = client.repository
    branch, _ = seed_characters(repo)
    for event_id, payload, observers in (
        ("private-record", {"visibility_scope": "actor", "scope_id": "bea",
                            "private": {"text": "Bea nested secret"}}, ["avery", "bea"]),
        ("public-record", {"visibility_scope": "public", "text": "The shop opens at noon"}, []),
    ):
        repo.record_event("pilot", branch, {
            "event_id": event_id, "game_datetime": T1, "provenance": "system_fact",
            "confidence": 1, "payload": {**payload, "observer_ids": observers},
        })
    repo.add_memory(
        "pilot", branch, "avery", None, T1, "The shop opens at noon", ["public-record"],
        "system_fact",
    )
    exported = (await client.get("/v1/admin/profiles/pilot/actors/avery/export")).json()
    assert "public-record" in {event["event_id"] for event in exported["events"]}
    assert "Bea nested secret" not in json.dumps(exported)
    assert "private-record" not in {event["event_id"] for event in exported["events"]}


async def test_conversation_erasure_rejects_a_different_private_owner(client):
    repo = client.repository
    branch, _ = seed_characters(repo)
    repo.record_event("pilot", branch, {
        "event_id": "private-record", "game_datetime": T1, "provenance": "heard",
        "observer_ids": ["avery", "bea"],
        "payload": {"visibility_scope": "actor", "scope_id": "bea", "text": "Bea private"},
    })
    before = repo.export_profile("pilot")
    response = await client.request(
        "DELETE", "/v1/admin/profiles/pilot/actors/avery/conversations/private-record",
        json={"confirm_actor_id": "avery", "confirm_event_id": "private-record"},
    )
    assert response.status_code == 404
    assert repo.export_profile("pilot") == before


def test_character_export_includes_only_its_skill_records_versions_and_sources(state):
    observation(state.repo, state.identity, "shared", observer_ids=["avery", "bea"])
    own = candidate(state, sources=["shared"])
    other = candidate(
        state, source=definition(name="bea_private_skill"), sources=["shared"],
        identity=replace(state.identity, actor_id="bea", runtime_instance_id="bea-1"),
    )
    exported = state.repo.export_actor("profile", "avery")
    assert [row["record_id"] for row in exported["skill_records"]] == [own["record_id"]]
    assert {row["record_id"] for row in exported["skill_sources"]} == {own["record_id"]}
    assert [row["skill_id"] for row in exported["skill_versions"]] == ["greeting"]
    assert other["record_id"] not in json.dumps(exported)
    assert "bea_private_skill" not in json.dumps(exported)


async def test_character_reset_removes_private_source_and_preserves_other_experiences(client):
    repo = client.repository
    _, memories = seed_characters(repo)
    seed_characters(repo, "other")
    other_before = repo.export_profile("other")
    response = await client.post(
        "/v1/admin/profiles/pilot/actors/avery/reset", json={"confirm_actor_id": "avery"},
    )
    assert response.status_code == 200
    serialized = json.dumps(repo.export_profile("pilot"))
    assert "pilot avery-only raw conversation" not in serialized
    assert "pilot shared raw conversation" not in serialized
    assert "pilot bea shared memory" in serialized
    assert "pilot avery shared memory" not in serialized
    assert repo.get_actor("pilot", "avery")["persona"] == {"bio": "avery biography"}
    assert repo.get_memory("pilot", memories["bea", "shared"]["memory_id"]) is not None
    assert repo.export_profile("other") == other_before
    exported = (await client.get("/v1/admin/profiles/pilot/actors/avery/export")).json()
    for key in ("events", "memories", "knowledge_cards", "relationships", "skill_records"):
        assert exported[key] == []


async def test_character_conversation_erasure_is_confirmed_and_scoped(client):
    repo = client.repository
    _, memories = seed_characters(repo)
    seed_characters(repo, "other")
    other_before = repo.export_profile("other")
    path = "/v1/admin/profiles/pilot/actors/avery/conversations/shared"
    body = {"confirm_actor_id": "avery", "confirm_event_id": "shared"}
    assert (await client.request("DELETE", path, json={
        **body, "confirm_event_id": "wrong",
    })).status_code == 409
    response = await client.request("DELETE", path, json=body)
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert repo.get_memory("pilot", memories["avery", "shared"]["memory_id"]) is None
    assert repo.get_memory("pilot", memories["bea", "shared"]["memory_id"]) is not None
    assert "pilot shared raw conversation" not in json.dumps(repo.export_profile("pilot"))
    assert repo.export_profile("other") == other_before
    missing = path.removesuffix("shared") + "bea-only"
    pilot_before = repo.export_profile("pilot")
    assert (await client.request("DELETE", missing, json={
        **body, "confirm_event_id": "bea-only",
    })).status_code == 404
    assert repo.export_profile("pilot") == pilot_before
    assert (await client.post(
        "/v1/admin/profiles/missing/actors/avery/reset", json={"confirm_actor_id": "avery"},
    )).status_code == 404
    assert repo.export_profile("other") == other_before


async def test_erased_conversation_cannot_recreate_its_relationship(client):
    repo = client.repository
    branch, _ = seed_characters(repo)
    assert repo.erase_conversation("pilot", "avery", "shared")
    assert not repo.apply_relationship_event("pilot", branch, "avery", "franklin", "shared")


async def test_erased_unprojected_conversation_cannot_create_a_relationship(client):
    repo = client.repository
    branch, _ = seed_characters(repo)
    with repo.connection:
        repo.connection.execute("DELETE FROM relationship_updates")
    assert repo.erase_conversation("pilot", "avery", "shared")
    assert not repo.apply_relationship_event("pilot", branch, "avery", "franklin", "shared")


async def test_erased_conversation_cannot_recreate_its_knowledge_snippet(client):
    repo = client.repository
    branch, _ = seed_characters(repo)
    assert repo.erase_conversation("pilot", "avery", "avery-only")
    with pytest.raises(ValueError, match="erased|forgotten|expired"):
        repo.add_knowledge(
            "pilot", branch, T1, "pilot avery-only raw conversation", ["avery-only"],
            "directly_observed", owner_id="avery", protagonist_id="franklin",
        )


async def test_memory_erasure_traverses_an_unprojected_derivation_event(client):
    repo = client.repository
    branch, memories = seed_characters(repo)
    for event_id in ("unprojected", "later-recall"):
        repo.record_event("pilot", branch, {
            "event_id": event_id, "game_datetime": T1, "protagonist_id": "franklin",
            "provenance": "directly_observed", "observer_ids": ["avery"],
            "payload": {"text": "Derived private conversation"},
        })
    later = repo.add_memory(
        "pilot", branch, "avery", "franklin", T1, "Derived private conversation",
        ["later-recall"], "directly_observed",
    )
    repo.link_memory_derivations("pilot", "avery", [
        ("avery-only", "unprojected"), ("unprojected", "later-recall"),
    ])
    assert repo.delete_memory("pilot", memories["avery", "avery-only"]["memory_id"])
    assert repo.get_memory("pilot", later["memory_id"]) is None
