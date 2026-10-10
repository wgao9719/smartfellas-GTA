"""Erasure markers prevent social delivery retries from restoring private memory."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from test_social import GAME, invite, receipt, speech_memories, witness
from test_social import scene as scene

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
