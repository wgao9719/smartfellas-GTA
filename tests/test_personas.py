import json

import pytest
from pydantic import ValidationError

from service.lls.activity import SCENARIOS, describe
from service.lls.context import build_context
from service.lls.display import is_display_safe
from service.lls.personas import (
    _BACKGROUNDS,
    _CLASHES,
    _NAMES,
    _OCCUPATIONS,
    _POOLS,
    MODEL_BY_HASH,
    ambient_persona,
    describe_model,
    names_in_use,
    persona_name,
)
from service.lls.protocol import ActorActivity
from service.lls.roles import MODEL_NAMES, joaat
from service.lls.runtime_pin import default_pin_name


def test_persona_is_a_pure_function_of_the_actor_id():
    model = joaat("a_f_y_runner_01")
    assert ambient_persona("npc-1", model) == ambient_persona("npc-1", model)
    names = {ambient_persona(f"npc-{i}", model)["name"] for i in range(40)}
    personalities = {ambient_persona(f"npc-{i}", model)["personality"] for i in range(40)}
    assert len(names) > 30 and len(personalities) > 15


def test_model_supplies_presentation_age_and_clothing_only():
    persona = ambient_persona("npc-1", joaat("a_f_y_runner_01"))
    assert persona["appearance"] == "young woman in workout clothes"
    assert persona["occupation"] in _OCCUPATIONS["fitness"]
    assert persona["name"].split()[0] in _NAMES["female"] + _NAMES["neutral"]
    uniform = ambient_persona("npc-2", joaat("s_m_y_construct_01"))
    assert uniform["appearance"] == "young man in work clothes"
    assert uniform["occupation"] in _OCCUPATIONS["trades"]
    older = ambient_persona("npc-3", joaat("a_m_o_beach_01"))
    assert older["appearance"] == "older man dressed for the beach"
    assert older["occupation"] in _OCCUPATIONS["beach"] + _OCCUPATIONS["older"]


@pytest.mark.parametrize(
    "model", ["a_m_y_ktown_01", "a_f_m_soucent_01", "a_m_y_gay_01", "a_m_m_hasjew_01"]
)
def test_identity_coded_models_supply_no_traits(model):
    assert describe_model(joaat(model))[2] is None
    persona = ambient_persona("npc-1", joaat(model))
    assert persona["occupation"] in _OCCUPATIONS["general"] + _OCCUPATIONS["older"]
    assert persona["appearance"] in {
        f"{age} {who}" for age in ("young", "middle-aged", "older") for who in ("man", "woman")
    }


def test_unknown_or_missing_model_uses_neutral_names_and_no_appearance():
    for model in (None, joaat("s_m_y_cop_01"), 12345):
        persona = ambient_persona("npc-1", model)
        assert "appearance" not in persona
        assert persona["name"].split()[0] in _NAMES["neutral"]
        assert persona["occupation"] in _OCCUPATIONS["general"]


def test_every_civilian_model_parses_and_every_name_is_a_display_name():
    civilians = [name for name, role in MODEL_NAMES.items() if role == "civilian"]
    assert set(MODEL_BY_HASH.values()) == set(civilians)
    for index, model in enumerate(civilians):
        assert describe_model(joaat(model))[0] in {"man", "woman"}
        persona = ambient_persona(f"actor-{index}", joaat(model))
        assert persona_name({"persona": persona}) == persona["name"]
        assert is_display_safe(persona["name"]) and len(persona["name"]) <= 48
        assert persona["motivations"][0] != persona["motivations"][1]


def traits(persona):
    return {
        persona["occupation"],
        persona["personality"],
        persona["speech_style"],
        persona["quirk"],
        persona["mood"],
        *persona["motivations"],
    }


@pytest.mark.parametrize(
    "model", ["a_m_m_tourist_01", "a_f_m_tourist_01", "a_f_y_tourist_01", "a_f_y_tourist_02"]
)
def test_tourists_are_visitors_with_no_local_life(model):
    _, excludes, adds = _BACKGROUNDS["visitor"]
    added = set(adds["motivations"]) | set(adds["quirks"])
    seen = set()
    for index in range(150):
        persona = ambient_persona(f"tourist-{index}", joaat(model))
        assert persona["long_bio"].startswith("A visitor to Los Santos")
        assert "local" not in persona["long_bio"]
        assert persona["occupation"] in _OCCUPATIONS["tourist"]
        assert not traits(persona) & excludes
        seen |= traits(persona)
    assert seen & added


def test_rural_peds_live_in_blaine_county_and_others_in_los_santos():
    rural = ambient_persona("npc-1", joaat("a_m_m_salton_01"))
    assert rural["long_bio"].startswith("A Blaine County local")
    assert "Cynical about Los Santos but loves it anyway" not in traits(rural)
    for model in ("a_f_y_runner_01", "a_m_y_ktown_01", None):
        assert ambient_persona("npc-1", joaat(model) if model else None)["long_bio"].startswith(
            "A Los Santos local"
        )


def test_only_older_peds_can_be_retired():
    for archetype, occupations in _OCCUPATIONS.items():
        if archetype != "older":
            assert not [job for job in occupations if "retire" in job.lower()], archetype
    for model in ("a_m_y_golfer_01", "a_f_y_golfer_01"):
        for index in range(60):
            assert "etire" not in ambient_persona(f"golfer-{index}", joaat(model))["occupation"]


def test_drawn_traits_never_clash_or_repeat():
    civilians = [name for name, role in MODEL_NAMES.items() if role == "civilian"]
    for index in range(3000):
        persona = ambient_persona(f"actor-{index}", joaat(civilians[index % len(civilians)]))
        drawn = traits(persona)
        assert len(drawn) == 7
        assert not [pair for pair in _CLASHES if pair <= drawn], persona


def test_names_avoid_names_in_use_deterministically():
    model = joaat("a_m_y_business_01")
    natural = ambient_persona("npc-1", model)
    taken = {natural["name"], "Marcus Reyes"}
    redrawn = ambient_persona("npc-1", model, taken)
    assert redrawn["name"] not in taken
    assert redrawn == ambient_persona("npc-1", model, set(taken))
    assert {k: v for k, v in redrawn.items() if k != "name"} == {
        k: v for k, v in natural.items() if k != "name"
    }
    every = {
        f"{first} {last}"
        for first in _NAMES["male"] + _NAMES["neutral"]
        for last in _POOLS["last_names"]
    }
    assert ambient_persona("npc-1", model, every - {"Sam Pham"})["name"] == "Sam Pham"
    # With every name taken, the natural draw is a cosmetic duplicate, not a failure.
    assert ambient_persona("npc-1", model, every)["name"] == natural["name"]


def test_session_of_pedestrians_never_repeats_a_name():
    civilians = [name for name, role in MODEL_NAMES.items() if role == "civilian"]
    stored = []
    for index in range(200):
        persona = ambient_persona(
            f"walker-{index}", joaat(civilians[index % len(civilians)]), names_in_use(stored)
        )
        stored.append({"display_name": "Passerby", "persona": persona})
    assert len({actor["persona"]["name"] for actor in stored}) == 200


def test_names_in_use_reads_display_and_persona_names():
    actors = [
        {"display_name": "Ana Reyes", "persona": {"biography": "Pack character."}},
        {"display_name": "Passerby", "persona": {"name": "Dana Pham"}},
        {"display_name": None, "persona": "not a dict"},
    ]
    assert names_in_use(actors) == {"Ana Reyes", "Passerby", "Dana Pham"}


def test_pin_name_prefers_the_persona_and_falls_back_for_older_actors():
    persona = ambient_persona("npc-1")
    assert default_pin_name("npc-1", {"persona": persona}) == persona["name"]
    fallback = default_pin_name("npc-1", {"persona": {"biography": "A local pedestrian."}})
    assert fallback.startswith("Resident ") and len(fallback) == 13
    assert default_pin_name("npc-1") == fallback
    assert default_pin_name("npc-1", {"persona": {"name": "a<b>"}}) == fallback


@pytest.mark.parametrize(
    ("activity", "phrases"),
    [
        (None, []),
        ({"movement": "unknown"}, []),
        ({"movement": "running", "on_phone": True}, ["running", "talking on a phone"]),
        ({"movement": "still", "scenario": "sitting_bench"}, ["sitting on a bench"]),
        ({"movement": "still"}, ["standing still"]),
        ({"movement": "walking", "scenario": "smoking"}, ["smoking a cigarette", "walking"]),
        (
            {"movement": "still", "scenario": "other", "injured": True},
            ["busy with something", "hurt"],
        ),
    ],
)
def test_activity_phrases(activity, phrases):
    assert describe(activity) == phrases


def test_activity_wire_is_strict_but_degrades_unknown_labels():
    sample = ActorActivity.model_validate({"movement": "sprinting", "scenario": "yoga"})
    assert (sample.movement, sample.scenario) == ("sprinting", "yoga")
    newer = ActorActivity.model_validate({"movement": "crawling", "scenario": "juggling"})
    assert (newer.movement, newer.scenario) == ("unknown", "other")
    with pytest.raises(ValidationError):
        ActorActivity.model_validate({"movement": "walking", "extra": True})
    with pytest.raises(ValidationError):
        ActorActivity.model_validate({"scenario": "x" * 33})
    with pytest.raises(ValidationError):
        ActorActivity.model_validate({"on_phone": "yes"})
    assert "other" in SCENARIOS


def test_context_carries_current_activity_only_when_known():
    values = {
        "actor": {"actor_id": "a", "display_name": "Nearby resident", "persona": {}},
        "snapshot": {"activity": {"movement": "running", "scenario": None}},
        "world": {},
        "memories": [],
        "knowledge": [],
        "relationship": {k: 0 for k in ["familiarity", "trust", "affinity", "fear", "respect"]},
        "input_text": "Hello",
        "eligible": [],
        "protagonist_id": "franklin",
        "game_datetime": "2013-09-17T12:00:00",
        "byte_budget": 4096,
    }
    context, _ = build_context(**values)
    assert context["actor"]["current_activity"] == ["running"]
    values["snapshot"] = {"activity": None}
    context, _ = build_context(**values)
    assert "current_activity" not in json.dumps(context)
