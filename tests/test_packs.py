from copy import deepcopy

import pytest

from service.lls.packs import (
    install_pilot_pack,
    load_pilot_pack,
    seed_pilot_fixture,
    validate_pilot_pack,
)
from service.lls.storage import Repository


def test_original_pack_covers_content_contract():
    pack = load_pilot_pack()
    assert len(pack["actors"]) == 12
    assert len({actor["occupation"] for actor in pack["actors"]}) >= 3
    assert pack["runtime_spawn_enabled"] is False
    assert all(actor["voice"]["status"] == "unverified_placeholder" for actor in pack["actors"])
    assert all(actor["runtime_status"] == "unverified_not_spawnable" for actor in pack["actors"])


def test_install_preserves_edits_and_does_not_invent_world_events(tmp_path):
    with Repository(tmp_path / "pack.sqlite3") as repo:
        actors = install_pilot_pack(repo, "pilot")
        edited = deepcopy(actors[0])
        edited["display_name"] = "User's selected name"
        repo.register_actor("pilot", edited)
        install_pilot_pack(repo, "pilot")
        assert len(repo.list_actors("pilot")) == 12
        assert repo.get_actor("pilot", edited["id"])["display_name"] == "User's selected name"
        assert repo.export_profile("pilot")["events"] == []
        assert repo.export_profile("pilot")["memories"] == []


def test_fixture_has_two_witnesses_and_no_omniscient_third_character(tmp_path):
    with Repository(tmp_path / "fixture.sqlite3") as repo:
        install_pilot_pack(repo, "pilot")
        branch = repo.open_timeline("pilot", "2025-01-01T10:00:00Z")
        fixture = seed_pilot_fixture(repo, "pilot", branch)
        seed_pilot_fixture(repo, "pilot", branch)  # replay remains idempotent
        for actor_id in fixture["expected_knowers"]:
            rows = repo.retrieve_memories(
                "pilot", branch, actor_id, "franklin", "2025-01-01T13:00:00Z", "canopy"
            )
            assert len(rows) == 1
            assert "not seen a canopy built" in rows[0]["text"]
        for actor_id in fixture["expected_non_knowers"]:
            assert not repo.retrieve_memories(
                "pilot", branch, actor_id, "franklin", "2025-01-01T13:00:00Z", "canopy"
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "unsupported_action",
        "runtime_spawn",
        "invented_voice",
        "unknown_relationship",
        "omniscience",
    ],
)
def test_pack_rejects_false_runtime_claims(mutation):
    pack = deepcopy(load_pilot_pack())
    if mutation == "unsupported_action":
        pack["actors"][0]["allowed_actions"] = ["invent_money"]
    elif mutation == "runtime_spawn":
        pack["runtime_spawn_enabled"] = True
    elif mutation == "invented_voice":
        pack["actors"][0]["voice"]["status"] = "verified"
    elif mutation == "unknown_relationship":
        pack["actors"][0]["relationships"][0]["actor_id"] = "stranger"
    else:
        pack["knowledge_fixtures"][0]["expected_non_knowers"] = pack["knowledge_fixtures"][0][
            "expected_knowers"
        ]
    with pytest.raises(ValueError):
        validate_pilot_pack(pack)
