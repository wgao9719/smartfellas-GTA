import asyncio
import json
from dataclasses import replace

import pytest

from service.lls.skills import (
    PrimitiveReceipt,
    SkillEnvironment,
    SkillIdentity,
    SkillInterpreter,
    compile_skill,
)

IDENTITY = SkillIdentity("pilot", "branch", "franklin", "session", 1, "avery", "ped-instance")


def action(name="face_player", **arguments):
    return {"op": "action", "proposal": {"action_id": name, "arguments": arguments}}


def definition(*steps, name="greeting", version=1, duration=2.0, distance=0.0):
    return {
        "skill_id": name,
        "version": version,
        "action_class": "conversational_courtesy",
        "max_duration_seconds": duration,
        "max_distance_meters": distance,
        "steps": list(steps) or [action(duration_seconds=1.0)],
    }


class Fixture:
    def __init__(self):
        self.identity = IDENTITY
        self.actor = {
            "_sampled_at": 100.0,
            "exists": True,
            "alive": True,
            "busy": False,
            "in_combat": False,
            "distance": 2,
            "control_policy": "ambient",
            "ownership": "ambient",
            "mission_entity": False,
            "can_move": True,
        }
        self.world = {
            "_sampled_at": 100.0,
            "online": False,
            "mission_active": False,
            "cutscene_active": False,
            "random_event_active": False,
            "paused": False,
        }
        self.capabilities = ("face_player", "wait_here", "follow_player")
        self.consent = False
        self.executed = []
        self.canceled = []

    def environment(self, identity):
        return SkillEnvironment(
            self.identity, self.actor, self.world, self.capabilities, self.consent
        )

    async def execute(self, identity, operation_id, proposal, deadline):
        self.executed.append((identity, operation_id, proposal.action_id))
        return PrimitiveReceipt(operation_id=operation_id, status="completed")

    async def cancel(self, identity, operation_id):
        self.canceled.append((identity, operation_id))

    async def run(self, skill, **options):
        return await SkillInterpreter(enabled=True, clock=lambda: 100).run(
            skill,
            IDENTITY,
            environment=self.environment,
            execute=options.pop("execute", self.execute),
            cancel=options.pop("cancel", self.cancel),
            **options,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"python": "import os; os.system('whoami')"},
        {"steps": [{"op": "shell", "command": "whoami"}]},
        {"steps": [{"op": "http", "url": "https://example.com"}]},
        {"steps": [{"op": "while", "condition": True}]},
        {"steps": [action("give_money", amount=100)]},
        {"steps": [action("flee_from_threat")]},
        {"steps": [action(duration_seconds=float("inf"))]},
        {"steps": [action(duration_seconds=1, native="TASK_ANYTHING")]},
        {"max_duration_seconds": 31},
        {"action_class": "friendship; grant_authority"},
    ],
)
def test_generated_program_cannot_add_authority_or_execution_forms(change):
    source = definition()
    source.update(change)
    with pytest.raises(ValueError):
        compile_skill(source)


def test_compiler_expands_calls_and_checks_cumulative_duration():
    child = definition(name="brief_greeting")
    call = {"op": "call", "skill_id": "brief_greeting", "version": 1}
    library = {("brief_greeting", 1): child}
    compiled = compile_skill(definition(call, call), library)
    assert len(compiled.operations) == 2
    assert len(compiled.dependencies) == 1
    with pytest.raises(ValueError, match="composed duration"):
        compile_skill(definition(call, call, duration=1), library)


def test_compiler_rejects_recursion_expansion_and_distance_overreach():
    call = {"op": "call", "skill_id": "greeting", "version": 1}
    recursive = definition(call)
    with pytest.raises(ValueError, match="recursion"):
        compile_skill(recursive, {("greeting", 1): recursive})
    child = definition(*([action(duration_seconds=0.1)] * 16), name="many", duration=2)
    many = {"op": "call", "skill_id": "many", "version": 1}
    with pytest.raises(ValueError, match="operation limit"):
        compile_skill(definition(many, many, many, duration=30), {("many", 1): child})
    with pytest.raises(ValueError, match="distance"):
        compile_skill(definition(action("follow_player", max_follow_meters=10), distance=5))


def test_source_size_and_caller_mutation_cannot_change_compiled_code():
    source = definition()
    compiled = compile_skill(source)
    source["steps"][0]["proposal"]["action_id"] = "flee_from_threat"
    assert json.loads(compiled.source)["steps"][0]["proposal"]["action_id"] == "face_player"
    with pytest.raises(ValueError, match="byte limit"):
        compile_skill(" " * 16385)


def test_duplicate_json_keys_and_composed_follow_are_rejected():
    source = json.dumps(definition()).replace('"version": 1', '"version": 0, "version": 1')
    with pytest.raises(ValueError, match="invalid declarative"):
        compile_skill(source)
    child = definition(action("follow_player", max_follow_meters=1), duration=1, distance=1)
    parent = definition(
        {"op": "call", "skill_id": "greeting", "version": 1},
        name="parent",
        duration=30,
        distance=30,
    )
    with pytest.raises(ValueError, match="scoped duration"):
        compile_skill(parent, {("greeting", 1): child})


@pytest.mark.asyncio
async def test_default_disabled_and_release_capabilities_produce_no_execution():
    fixture = Fixture()
    compiled = compile_skill(definition())
    disabled = await SkillInterpreter(clock=lambda: 100).run(
        compiled,
        IDENTITY,
        environment=fixture.environment,
        execute=fixture.execute,
        cancel=fixture.cancel,
    )
    assert disabled.status == "rejected"
    fixture.capabilities = ()
    refused = await fixture.run(compiled)
    assert refused.reason == "capability_unavailable"
    assert not fixture.executed


@pytest.mark.asyncio
async def test_each_step_rechecks_world_and_actor_binding():
    fixture = Fixture()
    skill = compile_skill(definition(action(duration_seconds=0.5), action(duration_seconds=0.5)))

    async def scripted_takeover(*args):
        receipt = await fixture.execute(*args)
        fixture.world["mission_active"] = True
        return receipt

    result = await fixture.run(skill, execute=scripted_takeover)
    assert result.status == "refused"
    assert result.completed_operations == 1
    assert len(fixture.executed) == 1
    fixture.world["mission_active"] = False
    fixture.identity = replace(IDENTITY, runtime_instance_id="replacement-ped")
    result = await fixture.run(skill)
    assert result.reason == "actor_binding_changed"
    assert len(fixture.executed) == 1


@pytest.mark.asyncio
async def test_classes_and_guards_do_not_bypass_consent():
    fixture = Fixture()
    source = definition(action("follow_player", max_follow_meters=5), distance=5)
    source["action_class"] = "approved_friendship"
    result = await fixture.run(compile_skill(source))
    assert result.reason == "consent_required"
    assert not fixture.executed
    guarded = definition({"op": "require", "condition": "consent"}, action(duration_seconds=1))
    result = await fixture.run(compile_skill(guarded))
    assert result.status == "refused"
    fixture.consent = True
    result = await fixture.run(compile_skill(guarded))
    assert result.status == "completed"
    assert result.game_verified is False


@pytest.mark.asyncio
async def test_dependency_changes_and_forged_operations_require_revalidation():
    fixture = Fixture()
    child = definition(name="child")
    source = definition({"op": "call", "skill_id": "child", "version": 1})
    library = {("child", 1): child}
    compiled = compile_skill(source, library)
    child["steps"] = [action("wait_here", duration_seconds=1)]
    result = await fixture.run(compiled, library=library)
    assert result.status == "rejected"
    direct = compile_skill(definition())
    forged = replace(direct, operations=(json.dumps(action("wait_here", duration_seconds=1)),))
    assert (await fixture.run(forged)).status == "rejected"
    assert (await fixture.run(direct, registry_fingerprint="new-registry")).status == "rejected"
    assert not fixture.executed


@pytest.mark.asyncio
async def test_operation_receipts_are_bound_and_repeated_runs_use_unique_ids():
    fixture = Fixture()
    compiled = compile_skill(definition())
    await fixture.run(compiled)
    await fixture.run(compiled)
    assert fixture.executed[0][1] != fixture.executed[1][1]

    async def wrong_receipt(*args):
        return PrimitiveReceipt(operation_id="someone-elses-operation", status="completed")

    result = await fixture.run(compiled, execute=wrong_receipt)
    assert result.reason == "primitive_receipt_mismatch"
    assert len(fixture.canceled) == 1


@pytest.mark.asyncio
async def test_constructed_receipt_cannot_bypass_finite_number_validation():
    fixture = Fixture()

    async def invalid_receipt(_, operation_id, proposal, deadline):
        return PrimitiveReceipt.model_construct(
            operation_id=operation_id, status="completed", distance_meters=float("nan")
        )

    with pytest.raises(ValueError):
        await fixture.run(compile_skill(definition()), execute=invalid_receipt)
    assert len(fixture.canceled) == 1


@pytest.mark.asyncio
async def test_executor_cannot_suppress_cancellation_and_run_the_next_operation():
    fixture = Fixture()
    started = asyncio.Event()
    operations = []

    async def suppress_cancellation(_, operation_id, proposal, deadline):
        operations.append(operation_id)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return PrimitiveReceipt(operation_id=operation_id, status="completed")

    skill = compile_skill(definition(action(duration_seconds=0.5), action(duration_seconds=0.5)))
    task = asyncio.create_task(fixture.run(skill, execute=suppress_cancellation))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(operations) == 1
    assert len(fixture.canceled) == 1


@pytest.mark.asyncio
async def test_cancellation_joins_cleanup_and_preserves_cancellation_after_cleanup_error():
    fixture = Fixture()
    entered, cleanup_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await asyncio.Event().wait()

    async def cancel(*args):
        cleanup_started.set()
        await release.wait()
        raise OSError("late driver failure")

    task = asyncio.create_task(
        fixture.run(compile_skill(definition()), execute=execute, cancel=cancel)
    )
    await entered.wait()
    task.cancel()
    await cleanup_started.wait()
    assert not task.done()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_failed_candidate_can_be_revised_without_altering_previous_code():
    fixture = Fixture()
    first = compile_skill(
        definition({"op": "require", "condition": "mod_following"}, action(duration_seconds=1))
    )
    trial = await fixture.run(first)
    assert trial.reason == "precondition_mod_following"
    revised = compile_skill(
        definition(
            action(duration_seconds=0.5), action("wait_here", duration_seconds=0.5), version=2
        )
    )
    retrial = await fixture.run(revised)
    assert retrial.status == "completed"
    assert retrial.completed_operations == 2
    assert first.fingerprint != revised.fingerprint
    assert (await fixture.run(first)).reason == "precondition_mod_following"
