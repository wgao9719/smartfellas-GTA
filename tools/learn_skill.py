"""Demonstrate persistent skill revision with explicit fake providers and simulated receipts."""

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.lls.providers import create_provider  # noqa: E402
from service.lls.settings import Settings  # noqa: E402
from service.lls.skill_learning import SkillLearner, SkillLearningConfig  # noqa: E402
from service.lls.skill_store import SkillStore  # noqa: E402
from service.lls.skills import (  # noqa: E402
    PrimitiveReceipt,
    SkillEnvironment,
    SkillIdentity,
    SkillInterpreter,
)
from service.lls.storage import Repository  # noqa: E402


async def demo():
    at = "2025-01-01T10:00:00Z"
    with tempfile.TemporaryDirectory(prefix="lls-learning-") as folder:
        path = Path(folder) / "simulation.sqlite3"
        with Repository(path) as repo:
            branch = repo.open_timeline("simulation", at)
            for actor in ("avery", "bea"):
                repo.register_actor("simulation", {"id": actor, "display_name": actor})
            identity = SkillIdentity(
                "simulation", branch, "franklin", "simulation", 1, "avery", "ped-simulated"
            )
            repo.record_event(
                "simulation",
                branch,
                {
                    "event_id": "simulated_need",
                    "game_datetime": at,
                    "protagonist_id": "franklin",
                    "event_type": "development.simulated_need",
                    "observer_ids": ["avery"],
                    "provenance": "system_fact",
                    "confidence": 1,
                    "payload": {"text": "Please face me and wait a moment."},
                },
            )
            store = SkillStore(repo)
            provider = create_provider(Settings(provider="fake", release=False), repo)
            learner = SkillLearner(
                store,
                provider,
                is_current=lambda expected: expected == identity,
                config=SkillLearningConfig(enabled=True, release=False),
            )
            operations = []

            def environment(expected):
                sampled = monotonic()
                return SkillEnvironment(
                    identity,
                    {
                        "_sampled_at": sampled,
                        "exists": True,
                        "alive": True,
                        "busy": False,
                        "in_combat": False,
                        "distance": 2,
                        "ownership": "ambient",
                        "control_policy": "ambient",
                        "mission_entity": False,
                        "can_move": True,
                    },
                    {
                        "_sampled_at": sampled,
                        "online": False,
                        "mission_active": False,
                        "cutscene_active": False,
                        "random_event_active": False,
                        "paused": False,
                    },
                    ("face_player", "wait_here"),
                )

            async def execute(expected, operation_id, proposal, deadline):
                operations.append(proposal.action_id)
                return PrimitiveReceipt(operation_id=operation_id, status="completed")

            async def cancel(expected, operation_id):
                pass

            async def trial(candidate):
                return await store.record_trial(
                    candidate["record_id"],
                    identity,
                    at,
                    interpreter=SkillInterpreter(enabled=True),
                    environment=environment,
                    execute=execute,
                    cancel=cancel,
                )

            try:
                request = {
                    "skill_id": "courteous_pause",
                    "version": 1,
                    "source_event_ids": ["simulated_need"],
                }
                first = await learner.generate(identity, at, request)
                refused = await trial(first)
                revised = await learner.generate(
                    identity, at, {**request, "version": 2, "revises": refused["record_id"]}
                )
                completed = await trial(revised)
                adopted = store.adopt(identity, at, completed["record_id"], ["simulated_need"])
                usage = provider.usage_summary()
            finally:
                await provider.aclose()
        with Repository(path) as reopened:
            store = SkillStore(reopened)
            selected = store.select(identity, at, action_class="conversational_courtesy")
            isolated = store.select(replace(identity, actor_id="bea"), at) is None
            report = {
                "mode": "simulated_persistent_skill_learning",
                "candidate_provider": "fake",
                "model_generated": False,
                "game_verified": False,
                "need_observed_in_game": False,
                "first_trial": refused["result"],
                "revised_trial": completed["result"],
                "adopted_version": adopted["version"],
                "selected_after_reopen": selected["version"] if selected else None,
                "actor_private_repertoire": isolated,
                "simulated_operations": operations,
                "cloud_requests": usage["background"]["requests"],
            }
            if (
                refused["result"]["status"] != "refused"
                or completed["result"]["status"] != "completed"
                or selected is None
                or selected["version"] != 2
                or not isolated
            ):
                raise RuntimeError("persistent learning simulation failed")
            return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", required=True, action="store_true")
    parser.parse_args()
    print(json.dumps(asyncio.run(demo()), indent=2))
