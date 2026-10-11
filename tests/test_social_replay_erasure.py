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
    assert line.carrier_event_ids == (sources[-1],)
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
            with pytest.raises(ValueError, match="forgotten words"):
                scene.engine.store.delivered(
                    scene.start, line, receipt(line),
                    [witness(scene, actor) for actor in ("avery", "clay")], GAME,
                )
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
    assert line.carrier_event_ids == ("source",)
    heard = (await deliver(scene, line, ("bea",)))["heard"]["bea"]["event_id"]
    spoken = scene.engine.store.event_id("session", receipt(line).event_id, "avery")
    hearing = memories(scene, "bea")
    assert scene.repo.delete_memory("profile", source["memory_id"])
    assert scene.repo.get_memory("profile", spoken + ":memory") is None
    assert speech_memories(scene, "avery") == []
    assert memories(scene, "bea") == hearing
    # The retained canonical speaker event still proves Bea's own hearing.
    assert contacts(scene, "bea")["avery"]["source_event_ids"] == [heard]


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
