import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from service.lls.protocol import Ack, ActionProposal, Command, Event, SessionOpen, SessionResponse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/wire_contract.json").read_text(encoding="utf-8"))
MODELS = {
    "session_open": SessionOpen,
    "session_response": SessionResponse,
    "event": Event,
    "proposal": ActionProposal,
    "command": Command,
    "ack": Ack,
}


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda case: case["id"])
def test_shared_wire_contract(case):
    model = MODELS[case["message_type"]]
    if case["valid"]:
        model.model_validate_json(json.dumps(case["payload"]))
    else:
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(case["payload"]))


@pytest.mark.parametrize("message_type", MODELS)
def test_each_message_has_valid_and_several_invalid_vectors(message_type):
    cases = [case for case in FIXTURES["cases"] if case["message_type"] == message_type]
    assert any(case["valid"] for case in cases)
    assert sum(not case["valid"] for case in cases) >= 3
    assert all(case["reason"] for case in cases)


@pytest.mark.parametrize("section", ["7.2", "7.3"])
def test_spec_fixture_preserves_literal_example_and_explains_deviation(section):
    spec = (ROOT / "references" / "SPECS.md").read_text(encoding="utf-8")
    text = spec.split(f"## {section} ", 1)[1].split("\n## ", 1)[0]
    payload = json.loads(re.search(r"```json\n(.*?)\n```", text, re.DOTALL)[1])
    [case] = [case for case in FIXTURES["cases"] if case.get("spec_section") == section]
    assert case["payload"] == payload
    assert not case["valid"] and case["deliberate_deviation"]
    assert case["reason"].startswith("Deliberate deviation:")
    [counterpart] = [
        case for case in FIXTURES["cases"] if case.get("adapted_from_spec_section") == section
    ]
    assert counterpart["valid"] and counterpart["message_type"] == case["message_type"]
