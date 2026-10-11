from types import SimpleNamespace

import pytest

from service.lls.grounding import acknowledged_completion_lines, grounded_text


@pytest.mark.parametrize("line", [
    "I moved here from Chicago last year.",
    "I'm waiting for the bus.",
    "Gas costs way too much these days.",
    "Hey Franklin, good to see a friend around here.",
    "I stopped by the store this morning.",
    "They closed the beach because of the storm.",
    "I saved up for months to buy my car.",
    "My car is finally ready for the weekend.",
    "I fixed my bike yesterday.",
    "I opened a little coffee shop downtown.",
    "That movie was a waste of twenty bucks.",
    "I followed the game last night, it was close.",
])
def test_grounding_preserves_reported_ambient_false_positives(line):
    assert grounded_text(line) == line


@pytest.mark.parametrize("line", [
    "I can't offer you a discount, but your haircut is free.",
    "I fixed my bike yesterday and I'm following you now.",
    "I'll follow you, but I've already started waiting here.",
    "Michael, my sister likes coffee and Trevor killed Brad.",
    "Lester has jobs for you.",
    "You get discounts at the shop now.",
    "Your reward is five hundred even though I can't follow you.",
    "I could wait here now that I've turned to face you.",
    "I'm following you after yesterday's trouble.",
    "I haven't waited here since I've turned to face you.",
    "I'll fix your car for free.",
    "I'll give you 500 for the job.",
])
def test_grounding_scopes_assertions_within_mixed_clauses(line):
    assert grounded_text(line) == ""


@pytest.mark.parametrize("line", [
    "Your job sounds tiring.",
    "I don't know your mission, sorry.",
    "Michael said my sister is now your ally.",
    "I love my job, how is the weather for you?",
    "I left my job, and I hope things go well for you.",
    "Your money worries sound rough.",
    "Your payment plan sounds expensive.",
    "I'm at the point where I need another coffee.",
    "I'm at that spot in life where everything feels possible.",
    "I'll give you five minutes to think about it.",
])
def test_grounding_keeps_unrelated_subjects_and_clauses_separate(line):
    assert grounded_text(line) == line


@pytest.mark.parametrize("claim", [
    "I am now following you.",
    "I did it.",
    "I have taken care of it.",
    "All done",
    "The task is done.",
    "Michael is the informant.",
    "Trevor and Michael have reconciled.",
    "You can buy this car for 500.",
    "Your haircut is twenty.",
    "Your garage is now available.",
    "Lester needs you to rob the bank.",
    "Smith, turned to face you.",
])
def test_grounding_refuses_claim_paraphrases(claim):
    assert grounded_text(claim) == ""


@pytest.mark.parametrize("line", [
    "I finished breakfast.",
    "He started working at the docks.",
    "It started raining.",
    "I'll follow you.",
    "I can try turning toward you.",
    "I'm following you online so I can try turning toward you.",
    "Nice weather today. I work at the docks. I already ate.",
])
def test_grounding_preserves_small_talk_and_intentions(line):
    assert grounded_text(line) == line


@pytest.fixture
def completed_action_session():
    return SimpleNamespace(
        id="session-1",
        branch_id="branch-1",
        request=SimpleNamespace(protagonist_id="franklin", runtime_generation=1),
        actors={"npc-1": {"runtime_instance_id": "ped-1"}},
        commands={
            "command-1": {
                "kind": "action.execute",
                "session_id": "session-1",
                "actor_id": "npc-1",
                "payload": {"action_id": "face_player"},
                "state": "completed",
                "runtime_generation": 1,
                "runtime_instance_id": "ped-1",
                "_branch_id": "branch-1",
            },
        },
        action_outcomes=[{
            "event_id": "command-1:completed",
            "status": "completed",
            "provenance": "system_fact",
            "authority": "adapter",
            "session_id": "session-1",
            "actor_id": "npc-1",
            "protagonist_id": "franklin",
            "action_id": "face_player",
        }],
    )


@pytest.mark.parametrize(("target", "field", "value"), [
    ("outcome", "actor_id", "npc-2"),
    ("outcome", "session_id", "session-2"),
    ("outcome", "protagonist_id", "michael"),
    ("outcome", "status", "started"),
    ("outcome", "status", "failed"),
    ("outcome", "authority", "service"),
    ("outcome", "provenance", "reported"),
    ("outcome", "event_id", "missing-command:completed"),
    ("outcome", "action_id", "follow_player"),
    ("command", "kind", "dialogue.chunk"),
    ("command", "actor_id", "npc-2"),
    ("command", "session_id", "session-2"),
    ("command", "runtime_generation", 2),
    ("command", "runtime_instance_id", "ped-2"),
    ("command", "_branch_id", "branch-2"),
    ("command", "state", "started"),
    ("command", "state", "failed"),
])
def test_completion_lines_require_current_correlated_ack(
    completed_action_session, target, field, value,
):
    session = completed_action_session
    record = (
        session.action_outcomes[0] if target == "outcome" else session.commands["command-1"]
    )
    record[field] = value
    assert acknowledged_completion_lines(session, "npc-1") == []


def test_acknowledged_completion_only_allows_its_fixed_line(completed_action_session):
    lines = acknowledged_completion_lines(completed_action_session, "npc-1")
    assert lines == ["I've turned to face you."]
    assert grounded_text(lines[0]) == ""
    assert grounded_text(lines[0], completion_lines=lines) == lines[0]
    assert grounded_text("I've reached the requested spot.", completion_lines=lines) == ""
    assert grounded_text(
        "I've turned to face you and your reward is 500 dollars.", completion_lines=lines
    ) == ""
    assert grounded_text(
        "I've turned to face you. I've reached the requested spot. "
        "Your reward is 500 dollars. Nice weather today.",
        completion_lines=lines,
    ) == "I've turned to face you. Nice weather today."
