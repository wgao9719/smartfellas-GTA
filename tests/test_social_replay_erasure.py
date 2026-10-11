"""Erasure markers prevent social delivery retries from restoring private memory."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from test_social import (
    GAME,
    deliver,
    invite,
    previous_contact,
    receipt,
    source_memory,
    speech_memories,
    witness,
)
from test_social import scene as scene

from service.lls.grounding import NEUTRAL_FALLBACK
from service.lls.social import SocialError
from service.lls.social_adaptation import SocialAdaptation
from service.lls.social_storage import SocialStore
from service.lls.storage import Repository


@pytest.fixture
async def delivery(scene):
    await invite(scene)
    utterance = await scene.engine.generate_turn(scene.start.scene_id, "avery")
    delivered = receipt(utterance)
    evidence = [witness(scene, actor) for actor in ("bea", "clay")]
    ids = {
        actor: scene.engine.store.event_id(scene.start.session_id, delivered.event_id, actor)
        for actor in ("avery", "bea", "clay")
    }
    return SimpleNamespace(
        utterance=utterance, receipt=delivered, evidence=evidence, ids=ids,
        args=(scene.start, utterance, delivered, evidence, GAME),
    )


def forget(repo, owner, event_id, operation):
    if operation == "reset":
        repo.reset_actor("profile", owner)
    elif operation == "memory":
        assert repo.delete_memory("profile", event_id + ":memory")
    else:
        assert repo.delete_event("profile", event_id)


def ordinary_context(scene, actor):
    return scene.engine.store.context(
        scene.start, actor, scene.env, phase="turn", heard=[], retelling=None, byte_budget=8192
    )


@pytest.mark.parametrize("operation", ["reset", "memory", "event"])
async def test_listener_erasure_blocks_replay_and_normal_prompt_memory(scene, delivery, operation):
    assert set(scene.engine.store.delivered(*delivery.args)) == {"bea", "clay"}
    forget(scene.repo, "bea", delivery.ids["bea"], operation)
    for _ in range(2):
        assert set(scene.engine.store.delivered(*delivery.args)) == {"clay"}
        assert scene.repo.get_memory("profile", delivery.ids["bea"] + ":memory") is None
        assert delivery.utterance.text not in json.dumps(ordinary_context(scene, "bea"))
        assert delivery.utterance.text in json.dumps(ordinary_context(scene, "clay"))
    assert speech_memories(scene, "bea") == []
    assert len(speech_memories(scene, "clay")) == 1
    if operation == "event":
        assert scene.repo.connection.execute(
            "SELECT 1 FROM events WHERE profile_id='profile' AND event_id=?",
            (delivery.ids["bea"],),
        ).fetchone() is None
    with Repository(scene.repo.path) as reopened:
        assert set(SocialStore(reopened).delivered(*delivery.args)) == {"clay"}
        assert reopened.get_memory("profile", delivery.ids["bea"] + ":memory") is None


@pytest.mark.parametrize("operation", ["reset", "memory"])
async def test_speaker_private_erasure_preserves_independent_listener_memories(
    scene, delivery, operation
):
    scene.engine.store.delivered(*delivery.args)
    forget(scene.repo, "avery", delivery.ids["avery"], operation)
    assert set(scene.engine.store.delivered(*delivery.args)) == {"bea", "clay"}
    assert speech_memories(scene, "avery") == []
    assert len(speech_memories(scene, "bea")) == len(speech_memories(scene, "clay")) == 1
    assert scene.repo.get_memory("profile", delivery.ids["avery"] + ":memory") is None


@pytest.mark.parametrize("operation", ["reset", "memory"])
async def test_erased_speaker_proof_cannot_seed_a_new_listener(scene, delivery, operation):
    args = (*delivery.args[:3], delivery.evidence[:1], GAME)
    assert set(scene.engine.store.delivered(*args)) == {"bea"}
    hearing = memories(scene, "bea")
    forget(scene.repo, "avery", delivery.ids["avery"], operation)
    for _ in range(2):
        assert set(scene.engine.store.delivered(*delivery.args)) == {"bea"}
        assert memories(scene, "bea") == hearing
        assert speech_memories(scene, "clay") == []
    with Repository(scene.repo.path) as reopened:
        assert set(SocialStore(reopened).delivered(*delivery.args)) == {"bea"}
        assert reopened.get_memory("profile", delivery.ids["clay"] + ":memory") is None


async def test_deleted_speaker_proof_blocks_all_delivery_replay(scene, delivery):
    scene.engine.store.delivered(*delivery.args)
    assert scene.repo.delete_event("profile", delivery.ids["avery"])
    # A partial retry must not create a projection for a newly supplied listener.
    assert scene.repo.delete_memory("profile", delivery.ids["bea"] + ":memory")
    assert scene.engine.store.delivered(*delivery.args) == {}
    assert scene.repo.get_memory("profile", delivery.ids["avery"] + ":memory") is None
    assert scene.repo.get_memory("profile", delivery.ids["bea"] + ":memory") is None
    assert scene.repo.connection.execute(
        "SELECT 1 FROM events WHERE profile_id='profile' AND event_id=?",
        (delivery.ids["avery"],),
    ).fetchone() is None


@pytest.mark.parametrize("operation", ["reset", "memory", "event"])
async def test_decision_erasure_blocks_replay_and_normal_prompt_memory(scene, operation):
    event_id = "private-refusal"
    arguments = (scene.start, "bea", "refuse", event_id, GAME)
    scene.engine.store.decision(*arguments)
    text = scene.repo.get_memory("profile", event_id + ":memory")["text"]
    forget(scene.repo, "bea", event_id, operation)
    for _ in range(2):
        scene.engine.store.decision(*arguments)
        assert scene.repo.get_memory("profile", event_id + ":memory") is None
        assert text not in json.dumps(ordinary_context(scene, "bea"))
    if operation == "event":
        assert scene.repo.connection.execute(
            "SELECT 1 FROM events WHERE profile_id='profile' AND event_id=?", (event_id,)
        ).fetchone() is None
    with Repository(scene.repo.path) as reopened:
        SocialStore(reopened).decision(*arguments)
        assert reopened.get_memory("profile", event_id + ":memory") is None


async def test_raw_source_reset_prevents_first_projection_after_partial_failure(scene, delivery):
    original = scene.repo.add_memory

    def interrupted(*args, **kwargs):
        if kwargs.get("memory_id") == delivery.ids["bea"] + ":memory":
            raise OSError("projection was interrupted")
        return original(*args, **kwargs)

    scene.repo.add_memory = interrupted
    try:
        with pytest.raises(OSError, match="projection was interrupted"):
            scene.engine.store.delivered(*delivery.args)
    finally:
        scene.repo.add_memory = original
    # No memory existed to leave a delete_memory marker. The raw source marker
    # must still prevent the first projection from reviving erased knowledge.
    scene.repo.reset_actor("profile", "bea")
    assert set(scene.engine.store.delivered(*delivery.args)) == {"clay"}
    assert scene.repo.get_memory("profile", delivery.ids["bea"] + ":memory") is None


async def test_speaker_reset_does_not_allow_changed_canonical_proof(scene, delivery):
    scene.engine.store.delivered(*delivery.args)
    scene.repo.reset_actor("profile", "avery")
    changed = delivery.receipt.model_copy(update={"text": "A changed private statement."})
    with pytest.raises(ValueError, match="canonical events are immutable"):
        scene.engine.store.delivered(
            scene.start, delivery.utterance, changed, delivery.evidence, GAME
        )
    assert scene.repo.get_memory("profile", delivery.ids["avery"] + ":memory") is None


async def test_reset_cannot_interleave_between_suppression_check_and_projection(
    scene, delivery, monkeypatch
):
    entered, release, reset_started = threading.Event(), threading.Event(), threading.Event()
    original = scene.repo.add_memory

    def blocked(*args, **kwargs):
        if kwargs.get("memory_id") == delivery.ids["bea"] + ":memory":
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    def reset():
        reset_started.set()
        scene.repo.reset_actor("profile", "bea")

    monkeypatch.setattr(scene.repo, "add_memory", blocked)
    write = asyncio.create_task(asyncio.to_thread(scene.engine.store.delivered, *delivery.args))
    erase = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        erase = asyncio.create_task(asyncio.to_thread(reset))
        assert await asyncio.to_thread(reset_started.wait, 1)
        await asyncio.sleep(0.01)
        assert not erase.done()
        release.set()
        await asyncio.gather(write, erase)
        assert scene.repo.get_memory("profile", delivery.ids["bea"] + ":memory") is None
        assert set(scene.engine.store.delivered(*delivery.args)) == {"clay"}
    finally:
        release.set()
        await asyncio.gather(write, *([erase] if erase else []), return_exceptions=True)


def memories(scene, actor):
    return scene.repo.list_memories("profile", actor)


def indexed_owners(scene, words):
    """Owners of full-text entries for these words; deleted rows would appear as None."""
    return sorted(
        str(row[0])
        for row in scene.repo.connection.execute(
            "SELECT m.actor_id FROM memory_fts f LEFT JOIN memories m ON m.rowid=f.rowid "
            "WHERE memory_fts MATCH ?",
            (words,),
        )
    )


def contacts(scene, actor):
    return SocialAdaptation(scene.repo).read(
        "profile", scene.branch, "franklin", GAME, [actor], session_id="session"
    )[actor]["peers"]


async def topic_line(scene):
    """Bea heard Avery in two earlier scenes, then raises that topic herself."""
    sources = [previous_contact(scene, number) for number in (1, 2)]
    scene.start.initiator_id = "bea"
    await invite(scene)
    line = await scene.engine.generate_turn("scene", "bea")
    assert line.text == "avery, you said: I enjoy quiet streets. What do you think now?"
    assert set(line.carrier_event_ids) == {sources[-1], "social_decision_0"}
    return sources, line


async def test_forgetting_heard_words_removes_speakers_derived_topic_line(scene):
    sources, line = await topic_line(scene)
    await deliver(scene, line, ("avery", "clay"))
    listeners = {actor: memories(scene, actor) for actor in ("avery", "clay")}
    contact = contacts(scene, "clay")
    assert contact["bea"]["confirmed_scene_count"] == 1
    assert "bea" in indexed_owners(scene, "quiet AND streets")
    for source in sources:
        assert scene.repo.delete_memory("profile", source + ":memory")
    for replay in range(2):
        if replay:
            assert set(scene.engine.store.delivered(
                scene.start, line, receipt(line),
                [witness(scene, actor) for actor in ("avery", "clay")], GAME,
            )) == {"avery", "clay"}
        assert not any("quiet streets" in row["text"] for row in memories(scene, "bea"))
        # Avery keeps its own two lines and its hearing; Clay keeps its hearing.
        assert indexed_owners(scene, "quiet AND streets") == ["avery"] * 3 + ["clay"]
        assert "quiet streets" not in json.dumps(ordinary_context(scene, "bea"))
        assert {actor: memories(scene, actor) for actor in ("avery", "clay")} == listeners
        assert contacts(scene, "clay") == contact


async def test_forgetting_in_scene_line_removes_only_later_replies(scene):
    scene.engine.config.max_turns = 6
    await invite(scene)
    lines = []
    for speaker in ("avery", "bea", "clay", "avery", "bea"):
        line = await scene.engine.generate_turn("scene", speaker)
        listeners = [actor for actor in ("avery", "bea", "clay") if actor != speaker]
        heard = (await deliver(scene, line, listeners))["heard"]
        spoken = scene.engine.store.event_id("session", receipt(line).event_id, speaker)
        lines.append(SimpleNamespace(utterance=line, memory_id=spoken + ":memory", heard=heard))
    first, heard_line, later = lines[1], lines[2], lines[4]  # Bea, then Clay, then Bea.
    forgotten = heard_line.heard["bea"]["event_id"]
    assert forgotten in later.utterance.carrier_event_ids
    assert forgotten not in first.utterance.carrier_event_ids
    before = {actor: memories(scene, actor) for actor in ("avery", "bea", "clay")}
    assert scene.repo.delete_memory("profile", forgotten + ":memory")
    assert scene.repo.get_memory("profile", later.memory_id) is None
    assert scene.repo.get_memory("profile", first.memory_id) is not None
    removed = {forgotten + ":memory", later.memory_id}
    assert memories(scene, "bea") == [
        row for row in before["bea"] if row["memory_id"] not in removed
    ]
    for actor in ("avery", "clay"):
        assert memories(scene, actor) == before[actor]


async def test_forgetting_retold_source_keeps_listener_hearing(scene):
    source = source_memory(scene, "avery")
    await invite(scene)
    line = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert set(line.carrier_event_ids) == {"source", "social_decision_0"}
    heard = (await deliver(scene, line, ("bea",)))["heard"]["bea"]["event_id"]
    spoken = scene.engine.store.event_id("session", receipt(line).event_id, "avery")
    hearing = memories(scene, "bea")
    assert scene.repo.delete_memory("profile", source["memory_id"])
    assert scene.repo.get_memory("profile", spoken + ":memory") is None
    assert speech_memories(scene, "avery") == []
    assert memories(scene, "bea") == hearing
    # The retained canonical speaker event still proves Bea's own hearing.
    assert contacts(scene, "bea")["avery"]["source_event_ids"] == [heard]


@pytest.mark.parametrize("forgotten", ["source", "social_decision_0"])
async def test_filtered_retelling_erasure_scrubs_speaker_and_preserves_hearing(scene, forgotten):
    source_memory(
        scene, "avery", memory_id="source:memory",
        source_text="Lester has a mission for you. Nice weather today.",
    )
    await invite(scene)
    line = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert line.text == "Nice weather today."
    assert line.retell_event_id is None
    heard = (await deliver(scene, line, ("bea",)))["heard"]["bea"]["event_id"]
    spoken = scene.engine.store.event_id("session", receipt(line).event_id, "avery")
    hearing = scene.repo.export_actor("profile", "bea")
    assert scene.repo.delete_memory("profile", forgotten + ":memory")
    assert scene.repo.get_memory("profile", spoken + ":memory") is None
    assert line.text not in json.dumps(ordinary_context(scene, "avery"))
    assert indexed_owners(scene, "weather") == ["bea"]
    events = {row["event_id"]: row for row in scene.repo.export_profile("profile")["events"]}
    assert events[spoken]["content_redacted"]
    assert line.text not in json.dumps(events[spoken])
    if forgotten == "source":
        assert events["source"]["content_redacted"]
        assert "Lester" not in json.dumps(scene.repo.export_actor("profile", "avery"))
    assert events[heard]["payload"]["reported_event_id"] is None
    assert events[heard]["payload"]["retelling_lineage"] == []
    # Replaying erased proof preserves existing hearing, without adding a new listener.
    assert set(scene.engine.store.delivered(
        scene.start, line, receipt(line), [witness(scene, "bea"), witness(scene, "clay")], GAME,
    )) == {"bea"}
    assert scene.repo.export_actor("profile", "bea") == hearing
    assert speech_memories(scene, "clay") == []


@pytest.mark.parametrize("forgotten", ["source", "social_decision_0"])
async def test_filtered_retelling_rechecks_carriers_before_delivery(scene, forgotten):
    source_memory(
        scene, "avery", memory_id="source:memory",
        source_text="Lester has a mission for you. Nice weather today.",
    )
    await invite(scene)
    line = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert line.text == "Nice weather today."
    assert line.retell_event_id is None
    assert scene.repo.delete_memory("profile", forgotten + ":memory")
    with pytest.raises(SocialError, match="forgotten words"):
        await deliver(scene, line, ("bea",))
    assert indexed_owners(scene, "weather") == []


async def test_wholly_filtered_retelling_fallback_is_independent_of_erased_sources(scene):
    source = source_memory(scene, "avery", source_text="Lester has a mission for you.")
    await invite(scene)
    line = await scene.engine.generate_turn("scene", "avery", retell_event_id="source")
    assert line.text == NEUTRAL_FALLBACK
    assert line.retell_event_id is None
    assert line.carrier_event_ids == ()
    assert scene.repo.delete_memory("profile", source["memory_id"])
    assert scene.repo.delete_memory("profile", "social_decision_0:memory")
    assert (await deliver(scene, line, ("bea",)))["heard"]["bea"]["text"] == NEUTRAL_FALLBACK


async def test_delivery_refuses_reply_from_forgotten_words(scene):
    sources, line = await topic_line(scene)
    assert scene.repo.delete_memory("profile", sources[-1] + ":memory")
    with pytest.raises(SocialError, match="forgotten words"):
        await deliver(scene, line, ("avery", "clay"))
    assert scene.repo.connection.execute(
        "SELECT COUNT(*) FROM events WHERE json_extract(data,'$.payload.utterance_id')=?",
        (line.utterance_id,),
    ).fetchone()[0] == 0
    assert not any("bea told me" in row["text"] for row in scene.repo.list_memories("profile"))
    assert not scene.repo.connection.execute(
        "SELECT 1 FROM memory_derivations WHERE actor_id='bea'"
    ).fetchall()


async def recalled_paraphrase(scene):
    source = source_memory(scene, "avery")
    await invite(scene)

    async def paraphrase(context):
        assert any(
            source["memory_id"] == memory["memory_id"] for memory in context["memories"]
        )
        assert context["social"]["retell_source_id"] is None
        assert context["social"]["topic_continuation"] is None
        assert context["social"]["heard"] == []
        yield "That waterfront sounds peaceful."

    scene.provider.primary.dialogue = paraphrase
    line = await scene.engine.generate_turn("scene", "avery")
    return source, line


async def test_forgetting_general_recall_removes_paraphrase_only_for_speaker(scene):
    source, line = await recalled_paraphrase(scene)
    await deliver(scene, line, ("bea", "clay"))
    listeners = {actor: memories(scene, actor) for actor in ("bea", "clay")}
    spoken = scene.engine.store.event_id("session", receipt(line).event_id, "avery")
    assert scene.repo.delete_memory("profile", source["memory_id"])
    assert scene.repo.get_memory("profile", spoken + ":memory") is None
    assert "source" in line.carrier_event_ids
    assert "waterfront" not in json.dumps(ordinary_context(scene, "avery"))
    assert indexed_owners(scene, "waterfront") == ["bea", "clay"]
    assert {actor: memories(scene, actor) for actor in ("bea", "clay")} == listeners
    assert set(scene.engine.store.delivered(
        scene.start, line, receipt(line),
        [witness(scene, actor) for actor in ("bea", "clay")], GAME,
    )) == {"bea", "clay"}


async def test_delivery_refuses_paraphrase_after_general_recall_is_forgotten(scene):
    source, line = await recalled_paraphrase(scene)
    assert scene.repo.delete_memory("profile", source["memory_id"])
    with pytest.raises(SocialError, match="forgotten words"):
        await deliver(scene, line, ("bea",))
    assert indexed_owners(scene, "waterfront") == []


async def test_trimmed_recall_does_not_create_a_derivation(scene):
    source = source_memory(scene, "avery")
    await invite(scene)
    actor = scene.repo.get_actor("profile", "avery")
    actor["persona"]["background"] = "A long authored biography. " * 180
    scene.repo.register_actor("profile", actor)
    scene.engine.config.context_byte_budget = 4096

    async def reply(context):
        scene.recorder.contexts.append(context)
        yield "A fresh conversation."

    scene.provider.primary.dialogue = reply
    line = await scene.engine.generate_turn("scene", "avery")
    context = scene.recorder.contexts[-1]
    assert context["context_truncated"]
    assert context["memories"] == []
    assert "source" not in line.carrier_event_ids
    assert scene.repo.delete_memory("profile", source["memory_id"])
    assert set((await deliver(scene, line))["heard"]) == {"bea"}


@pytest.mark.parametrize("operation", ["reset", "memory", "event"])
async def test_next_turn_drops_forgotten_cached_hearing(scene, delivery, operation):
    result = await scene.engine.acknowledge_delivery(
        "scene", delivery.utterance.utterance_id, delivery.receipt, delivery.evidence
    )
    forgotten = result["heard"]["bea"]["event_id"]
    forget(scene.repo, "bea", forgotten, operation)
    line = await scene.engine.generate_turn("scene", "bea")
    context = scene.recorder.contexts[-1]
    assert delivery.utterance.text not in json.dumps(context)
    assert forgotten not in line.carrier_event_ids
    assert context["social"]["heard"] == []
    assert scene.engine.scenes["scene"].heard["bea"] == []


async def test_raw_retention_preserves_durable_social_hearing_and_decisions(scene, delivery):
    scene.engine.store.delivered(*delivery.args)
    owners = ["avery", "bea", "clay"]
    adaptation = SocialAdaptation(scene.repo)
    before = adaptation.read(
        "profile", scene.branch, "franklin", GAME, owners, session_id="session"
    )
    retained = {actor: memories(scene, actor) for actor in owners}
    assert before["bea"]["peers"]["avery"]["confirmed_scene_count"] == 1
    assert before["avery"]["own_decision_source_event_ids"]
    assert scene.repo.expire_raw_events("9999-01-01T00:00:00+00:00") == 6
    assert adaptation.read(
        "profile", scene.branch, "franklin", GAME, owners, session_id="session"
    ) == before
    assert {actor: memories(scene, actor) for actor in owners} == retained
    for row in scene.repo.connection.execute("SELECT data FROM events"):
        assert delivery.utterance.text not in row["data"]
