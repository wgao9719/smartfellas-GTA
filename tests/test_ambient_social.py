"""Ambient scene ownership, delivery, cost, and session-lifetime regressions."""

import asyncio
import json
from datetime import timedelta

import pytest
from test_runtime_social import (  # noqa: F401
    acknowledge,
    envelope,
    heard_memories,
    invite,
    listener,
    turn,
)
from test_runtime_social import (
    social_scene as social_scene,
)

from service.lls.__main__ import fixture_event
from service.lls.ambient_social import decision
from service.lls.budgets import BudgetExceeded, BudgetLedger
from service.lls.gateway_config import ChatterBudget
from service.lls.personas import ambient_persona
from service.lls.protocol import ActorActivity
from service.lls.social import FakeSocialProvider


def make_ambient(runtime, session, actor_id):
    actor = runtime.repo.get_actor(session.request.profile_id, actor_id)
    with runtime.repo.connection:
        runtime.repo.connection.execute(
            "DELETE FROM actors WHERE profile_id=? AND actor_id=?",
            (session.request.profile_id, actor_id),
        )
    persona = ambient_persona(actor_id)
    persona.update(personality="Warm and chatty", mood="Relaxed", appearance="casually dressed")
    actor.update(actor_kind="ambient_ephemeral", session_id=session.id, persona=persona)
    runtime.repo.register_actor(session.request.profile_id, actor)
    session.actors[actor_id]["actor_kind"] = "ambient_ephemeral"


async def test_four_ambient_lines_use_four_calls_and_only_confirmed_hearing(social_scene):
    client, runtime, session, clock = social_scene
    for actor_id in ("npc-a", "npc-b", "npc-c"):
        make_ambient(runtime, session, actor_id)
    contexts = []

    class Recording(FakeSocialProvider):
        async def dialogue(self, context):
            contexts.append(context)
            async for part in super().dialogue(context):
                yield part

    runtime.provider.primary = Recording()
    session.world["weather"] = "rain"
    session.actors["npc-b"]["activity"] = ActorActivity(movement="still").model_dump()
    response = await invite(client, session)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert not contexts
    for index in range(4):
        owner, peer = ("npc-a", "npc-b") if index % 2 == 0 else ("npc-b", "npc-a")
        review = await client.post(
            "/v1/bridge/social/review", json=envelope(session, actor_id=owner)
        )
        assert review.status_code == 200, review.text
        reply = await client.post(
            "/v1/bridge/social/turn", json=envelope(session, actor_id=owner)
        )
        assert reply.status_code == 200, reply.text
        command = session.commands[reply.json()["command_id"]]
        prior = len(heard_memories(runtime, session, peer))
        await acknowledge(client, session, command)
        assert len(heard_memories(runtime, session, peer)) == prior
        evidence = listener(
            session, clock, actor_id=peer,
            runtime_instance_id=session.actors[peer]["runtime_instance_id"],
            position=session.actors[peer]["position"],
        )
        delivery = await client.post("/v1/bridge/social/delivery", json=envelope(
            session, command_id=command["id"], witnesses=[evidence]
        ))
        assert delivery.status_code == 200, delivery.text
        assert set(delivery.json()["heard"]) == {peer}
    assert len(contexts) == 4
    assert all(c["social"]["phase"] == "turn" for c in contexts)
    assert contexts[0]["world"]["weather"] == "rain"
    partner = contexts[0]["social"]["partners"][0]
    assert partner["appearance"] == "casually dressed"
    assert "name" not in partner and "persona" not in partner
    assert not heard_memories(runtime, session, "npc-c")
    await client.post(f"/v1/bridge/sessions/{session.id}/close", json={"runtime_generation": 1})
    assert runtime.repo.get_actor(session.request.profile_id, "npc-a") is None
    assert runtime.repo.list_memories(session.request.profile_id, "npc-a") == []


async def test_delayed_display_can_be_heard_but_not_queue_time(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    result = await turn(client, session)
    command = session.commands[result["command_id"]]
    clock.wall += timedelta(seconds=4)
    clock.value += 4
    session.world["_sampled_at"] = clock.value
    for actor in session.actors.values():
        actor["_sampled_at"] = clock.value
    ack = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json={
        "session_id": session.id, "runtime_generation": 1, "status": "completed",
        "observed": {"delivery": "simulated", "text": command["payload"]["text"],
                     "displayed_at": clock.wall.isoformat()},
    })
    assert ack.status_code == 200, ack.text
    response = await client.post("/v1/bridge/social/delivery", json=envelope(
        session, command_id=command["id"], witnesses=[listener(session, clock)]
    ))
    assert response.status_code == 200, response.text
    assert len(heard_memories(runtime, session, "npc-b")) == 1


@pytest.mark.parametrize("stamp", ["bad", "2026-10-10T12:00:00", None, -1, "future", "past"])
async def test_invalid_display_time_has_no_delivery_side_effects(social_scene, stamp):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    result = await turn(client, session)
    command = session.commands[result["command_id"]]
    if stamp in {"future", "past"}:
        stamp = (clock.wall + timedelta(seconds=1 if stamp == "future" else -1)).isoformat()
    response = await client.post(f"/v1/bridge/commands/{command['id']}/ack", json={
        "session_id": session.id, "runtime_generation": 1, "status": "completed",
        "observed": {"delivery": "simulated", "text": command["payload"]["text"],
                     "displayed_at": stamp},
    })
    assert response.status_code == 422, response.text
    assert not session.delivered_utterances
    assert not command.get("_delivery_recorded")


async def test_revalidation_does_not_cancel_foreground_work(social_scene):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    task = asyncio.create_task(asyncio.Event().wait())
    session.task = task
    session.actors["npc-b"]["area_id"] = "other-block"
    try:
        await runtime.refresh_social(session)
        assert session.social_scenes["social-scene"]["canceled"]
        assert not task.done() and not task.cancelling()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        session.task = None


@pytest.mark.parametrize("target,canceled", [("npc-a", True), ("npc-c", False)])
async def test_player_input_only_interrupts_participating_actor(social_scene, target, canceled):
    client, runtime, session, clock = social_scene
    assert (await invite(client, session)).status_code == 200
    await turn(client, session)
    response = await client.post("/v1/bridge/events:batch", json={
        "session_id": session.id, "events": [fixture_event(
            session.id, 20, "player.utterance_text",
            {"scene_id": "player-scene", "text": "Hello."}, target,
        )],
    })
    assert response.status_code == 200, response.text
    assert session.social_scenes["social-scene"]["canceled"] is canceled


async def test_chatter_budget_is_separate_and_minute_limit_survives_new_ledger(social_scene):
    _, runtime, _, clock = social_scene
    budget = ChatterBudget(max_requests=3, max_estimated_usd=1, max_requests_per_minute=1)
    def reserve(ledger):
        return ledger.reserve(now=clock.wall, budget_class="chatter", provider="local_openai",
                              model="fixture", micro_usd=0, limit=budget)
    reserve(BudgetLedger(runtime.repo))
    with pytest.raises(BudgetExceeded, match="minute"):
        reserve(BudgetLedger(runtime.repo))
    usage = BudgetLedger(runtime.repo).summary(clock.wall)
    assert usage["chatter"]["requests"] == 1
    assert usage["background"]["requests"] == usage["foreground"]["requests"] == 0
    clock.wall += timedelta(seconds=61)
    reserve(BudgetLedger(runtime.repo))


def test_ambient_decisions_respect_activity_boundaries_and_familiarity():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    context = {"actor": {"persona": json.dumps({"personality": "Guarded", "mood": "quiet"})},
               "social": {"phase": "invitation", "experience": {"contacts": {}}}}
    assert decision(context, None, now) == "refuse"
    context["social"]["experience"]["contacts"]["peer"] = {"confirmed_scene_count": 2}
    assert decision(context, None, now) == "accept"
    assert decision(context, ActorActivity(on_phone=True), now) == "refuse"
    context["social"]["phase"] = "participation"
    assert decision(context, ActorActivity(on_phone=True), now) == "withdraw"
    context["social"]["experience"]["own_last_boundary_at"] = now.isoformat()
    assert decision(context, None, now) == "withdraw"


@pytest.mark.parametrize("provider_name", ["disabled", "local_openai"])
async def test_disabled_or_exhausted_ai_uses_authored_smalltalk(social_scene, provider_name):
    import httpx

    from service.lls.ambient_social import BANK
    from service.lls.providers import create_provider
    from service.lls.settings import Settings

    _, runtime, _, clock = social_scene
    calls = []

    def unexpected(request):
        calls.append(request)
        raise AssertionError("zero chatter allowance must prevent HTTP")

    provider = create_provider(
        Settings(provider=provider_name, dialogue_model="fixture"), runtime.repo,
        transport=httpx.MockTransport(unexpected), utc_now=clock.utc_now,
        owner=runtime.provider.gate.owner,
    )
    try:
        for index in range(4):
            context = {"social": {"phase": "turn", "scene_id": "fallback", "turn_index": index}}
            parts = [part async for part in provider.dialogue(context, budget_class="chatter")]
            assert "".join(parts) in [exchange[index] for exchange in BANK]
        assert not calls
        assert provider.usage_summary()["chatter"]["requests"] == 0
    finally:
        await provider.aclose()


async def test_scheduler_counts_only_ambient_dialogue_calls(social_scene):
    from test_social_scheduler import heard, tick

    from service.lls.social_scheduler import SocialScheduler

    client, runtime, session, clock = social_scene
    for actor_id in ("npc-a", "npc-b", "npc-c"):
        make_ambient(runtime, session, actor_id)
    runtime.settings.dev_social_autonomy = True
    runtime.social_scheduler = SocialScheduler(runtime)
    for index in range(4):
        status = await tick(social_scene)
        assert status["reserved_model_calls"] == index + 1
        assert status["queued_turns"] == index + 1
        assert len([s for s in session.social_scenes.values() if not s["canceled"]]) == 1
        await heard(social_scene)
    status = await tick(social_scene)
    assert status["status"] == "completed" and status["reason"] == "turn_limit"
    assert status["reserved_model_calls"] == 4
