import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from service.lls import roles
from service.lls.__main__ import fixture_actor_snapshot, fixture_ped_facts
from service.lls.protocol import ActorSnapshot, RoleClass

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "ped_role_vectors.json").read_text(encoding="utf-8")
)


def test_table_matches_protocol_and_grants_nothing():
    table = json.loads(roles.TABLE_PATH.read_text(encoding="utf-8"))
    assert table["grants_capabilities"] is False
    assert table["verification"] == "unverified"
    assert set(get_args(RoleClass)) == set(roles.ROLES) == set(roles.ROLE_POLICIES)
    assert {r for r, policy in roles.ROLE_POLICIES.items() if policy != "protected"} == {
        "civilian"
    }
    assert set(roles.LABELS) == set(roles.ROLES)
    legacy = [
        "a_m_y_business_01", "a_m_m_business_01", "a_f_y_business_01",
        "a_m_y_beach_01", "a_f_y_beach_01", "a_m_y_hipster_01",
    ]
    assert all(roles.MODEL_NAMES[name] == "civilian" for name in legacy)
    assert not {"a_m_y_acult_01", "a_m_m_hillbilly_01", "a_m_y_mexthug_01"} & set(roles.MODEL_NAMES)
    assert VECTORS["table_version"] == roles.TABLE_VERSION


def test_joaat_matches_known_game_hashes():
    assert roles.joaat("s_m_y_cop_01") == 1581098148
    assert roles.joaat("player_one") == -1692214353
    assert roles.joaat("COP") == roles.joaat("cop") == 0xA49E591C - 2**32


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_shared_vectors_match_adapter_rules(case):
    assert roles.classify(case["facts"]) == case["expected"]
    if case["facts"] is not None:
        ActorSnapshot.model_validate(fixture_actor_snapshot(ped_facts=case["facts"]))


@pytest.mark.parametrize(
    ("claimed", "facts", "expected"),
    [
        ("civilian", fixture_ped_facts(), ("civilian", "civilian_evidence")),
        ("civilian", fixture_ped_facts("s_m_y_cop_01"), ("police", "model_police")),
        ("police", fixture_ped_facts(), ("police", "model_police")),
        ("gang", fixture_ped_facts(ped_type=26), ("unknown", "ped_type_unlisted")),
        ("civilian", None, ("unknown", "facts_unavailable")),
    ],
)
def test_revalidation_keeps_the_stricter_role(claimed, facts, expected):
    payload = {"role_class": claimed, "role_reason": "model_police", "ped_facts": facts}
    assert roles.revalidate(payload) == expected


@pytest.mark.parametrize(
    "facts",
    [
        {"ped_type": 256},
        {"ped_type": -2},
        {"model_hash": 2**31},
        {"owning_script": "bad script"},
        {"owning_script": "x" * 65},
        {"human": 1},
    ],
)
def test_ped_facts_are_bounded_on_the_wire(facts):
    with pytest.raises(ValidationError):
        ActorSnapshot.model_validate(fixture_actor_snapshot(ped_facts=fixture_ped_facts(**facts)))


def test_snapshot_without_facts_stays_schema_v1_and_classifies_unknown():
    snapshot = ActorSnapshot.model_validate(
        {
            key: value
            for key, value in fixture_actor_snapshot().items()
            if key not in {"role_class", "role_reason", "ped_facts"}
        }
    ).model_dump()
    assert (snapshot["role_class"], snapshot["role_reason"], snapshot["ped_facts"]) == (
        "unknown",
        "unclassified",
        None,
    )
    assert roles.revalidate(snapshot) == ("unknown", "facts_unavailable")
    assert roles.ownership_from_facts(None) == ("unknown", "facts_unavailable")
