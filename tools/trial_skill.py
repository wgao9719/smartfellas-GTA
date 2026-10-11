"""Compile a finite skill and run a simulated trial. Never calls GTA or a model."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.lls.skills import (  # noqa: E402
    PrimitiveReceipt,
    SkillEnvironment,
    SkillIdentity,
    SkillInterpreter,
    compile_skill,
)


async def trial(source):
    compiled = compile_skill(source)
    identity = SkillIdentity("simulation", "simulation", "franklin", "simulation", 1, "npc", "ped")
    operations = []

    def environment(_):
        actor = {
            "_sampled_at": monotonic(),
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
        world = {
            "_sampled_at": monotonic(),
            "online": False,
            "mission_active": False,
            "cutscene_active": False,
            "random_event_active": False,
            "paused": False,
        }
        return SkillEnvironment(identity, actor, world, ("face_player", "wait_here"))

    async def execute(_, operation_id, proposal, deadline):
        operations.append({"operation_id": operation_id, "action_id": proposal.action_id})
        return PrimitiveReceipt(operation_id=operation_id, status="completed")

    async def cancel(_, operation_id):
        operations.append({"operation_id": operation_id, "canceled": True})

    result = await SkillInterpreter(enabled=True).run(
        compiled, identity, environment=environment, execute=execute, cancel=cancel
    )
    return {**asdict(result), "simulated_operations": operations}


def demo_definitions():
    first = {
        "skill_id": "courteous_pause",
        "version": 1,
        "action_class": "conversational_courtesy",
        "max_duration_seconds": 2,
        "steps": [
            {"op": "require", "condition": "mod_following"},
            {
                "op": "action",
                "proposal": {"action_id": "face_player", "arguments": {"duration_seconds": 1}},
            },
        ],
    }
    revised = {
        **first,
        "version": 2,
        "steps": [
            first["steps"][1],
            {
                "op": "action",
                "proposal": {"action_id": "wait_here", "arguments": {"duration_seconds": 1}},
            },
        ],
    }
    return [first, revised]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path, help="JSON skill definition, at most 16 KiB")
    mode.add_argument(
        "--demo", action="store_true", help="simulate two authored candidate versions"
    )
    args = parser.parse_args()
    if args.source:
        with args.source.open("rb") as stream:
            source = stream.read(16385)
        if len(source) > 16384:
            parser.error("skill source exceeds 16 KiB")
        sources = [source.decode("utf-8")]
    else:
        sources = demo_definitions()
    trials = [await trial(source) for source in sources]
    print(
        json.dumps(
            {
                "mode": "simulated_skill_trial",
                "game_verified": False,
                "model_generated": False,
                "repertoire_persisted": False,
                "trials": trials,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
