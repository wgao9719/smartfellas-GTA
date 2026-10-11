"""Measure the QA exchanges through the service with a fake game and local model.

Each replicate gets a temporary database and one fresh ambient NPC. Requests use
the actual in-process HTTP bridge, provider gateway, grounding, delivery ACKs,
and memory storage. No listening socket, running service settings, or GTA is used.
Delivered text is retained for semantic review; keywords alone do not determine
attribution correctness or whether a surviving sentence is a job lead.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.__main__ import (  # noqa: E402
    fixture_actor_snapshot,
    fixture_event,
    fixture_session,
)
from service.lls.app import create_app  # noqa: E402
from service.lls.settings import Settings  # noqa: E402
from tools.benchmark_grounding_model import latency  # noqa: E402

ATTRIBUTION = (
    "Hi, I'm Franklin. I fix cars for a living.",
    "What's my name?",
    "What do I do for work?",
)
LEADS = (
    "Got any jobs or missions for me? I need cash.",
    "Know where I can make some quick money?",
    "Who's hiring around here?",
)


async def run_case(settings, prompts):
    """Run a fresh isolated case, acknowledging every real delivered subtitle."""
    app = create_app(settings)
    runtime = app.state.runtime
    turns = []
    generated_fragments = []
    dialogue = runtime.provider.primary.dialogue

    async def observed_dialogue(context, **kwargs):
        async with aclosing(dialogue(context, **kwargs)) as stream:
            async for fragment in stream:
                generated_fragments.append(fragment)
                yield fragment

    runtime.provider.primary.dialogue = observed_dialogue
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 45000)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            async def post(path, body):
                response = await client.post(path, json=body)
                response.raise_for_status()
                return response.json()

            request = fixture_session()
            request.update(profile_id="model-measurement", capabilities=[])
            opened = await post("/v1/bridge/sessions", request)
            sid = opened["session_id"]
            client.headers["Authorization"] = "Bearer " + opened["token"]
            session = runtime.sessions[sid]
            sequence, cursor = 0, 0
            for prompt in prompts:
                generated_fragments.clear()
                events = []
                for kind, payload, actor_id in (
                    ("world.snapshot", {
                        "online": False, "mission_active": False, "cutscene_active": False,
                        "random_event_active": False, "paused": False,
                        "protagonist_id": "franklin",
                    }, None),
                    ("actor.snapshot", fixture_actor_snapshot(name="Nearby resident"), "npc-1"),
                    ("player.utterance_text", {
                        "text": prompt, "scene_id": f"measurement-{sequence + 3}",
                    }, "npc-1"),
                ):
                    sequence += 1
                    events.append(fixture_event(sid, sequence, kind, payload, actor_id))
                before = len(session.trace)
                start = time.perf_counter_ns()
                await post("/v1/bridge/events:batch", {"session_id": sid, "events": events})
                if session.task is None:
                    raise RuntimeError("The service did not start the requested turn")
                await asyncio.wait_for(session.task, timeout=90)
                generated_ms = (time.perf_counter_ns() - start) / 1_000_000
                response = await client.get(
                    "/v1/bridge/commands", params={"after": cursor, "wait_seconds": 0},
                )
                response.raise_for_status()
                page = response.json()
                cursor = page["cursor"]
                commands = page["commands"]
                if not commands or any(c["kind"] != "dialogue.chunk" for c in commands):
                    raise RuntimeError("Expected only subtitle commands for the passive fake NPC")
                chunks = []
                for command in commands:
                    payload = command["payload"]
                    await post(f"/v1/bridge/commands/{command['id']}/ack", {
                        "session_id": sid, "runtime_generation": 1, "status": "completed",
                        "observed": {"delivery": "simulated", "text": payload["text"]},
                    })
                    if payload["text"]:
                        chunks.append(payload["text"])
                elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
                diagnostics = await client.get("/v1/diagnostics")
                diagnostics.raise_for_status()
                traces = diagnostics.json()["trace"][before:]
                turns.append({
                    "player_text": prompt,
                    "generated_text": "".join(generated_fragments),
                    "delivered_chunks": chunks,
                    "delivered_text": "".join(chunks).strip(),
                    "generation_and_grounding_ms": generated_ms,
                    "through_delivery_ack_ms": elapsed_ms,
                    "trace": traces,
                })
            usage = runtime.provider.gate.usage_summary()
            await post(f"/v1/bridge/sessions/{sid}/close", {"runtime_generation": 1})
    return {"turns": turns, "budget_usage": usage}


async def measure(args):
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url, "model": args.model, "replicates": args.replicates,
        "method": (
            "Sequential fresh temporary databases, one Nearby resident ambient NPC per case, "
            "Franklin protagonist; three exact attribution turns on the same actor, and each "
            "of three exact QA job requests independently. Uses in-process HTTP bridge with "
            "real local provider and default provider sampling, actual sentence grounding and "
            "simulated successful subtitle ACKs. No port bound, no GTA/native validation, no "
            "running QA service or provider settings changed. Times include service processing; "
            "ACK wall time also includes fake delivery bookkeeping, not visible game latency. "
            "A transparent iterator wrapper records generated model text separately from "
            "delivered text; it does not prefetch or change the gateway or classifier path. "
            "Each trace records grounding verdicts; delivered text requires semantic review."
        ),
        "sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                ROOT / "service/lls/context.py", ROOT / "service/lls/providers.py",
                ROOT / "service/lls/grounding.py", ROOT / "service/lls/runtime.py",
                Path(__file__),
            )
        },
        "records": [],
        "semantic_review": None,
    }
    cases = [("attribution", ATTRIBUTION)] + [
        (f"lead_{index + 1}", (prompt,)) for index, prompt in enumerate(LEADS)
    ]
    for kind, prompts in cases:
        for replicate in range(1, args.replicates + 1):
            with tempfile.TemporaryDirectory(prefix="lls-service-measurement-") as temporary:
                settings = Settings(
                    database_path=str(Path(temporary) / "measurement.sqlite3"),
                    provider="local_openai", local_base_url=args.base_url,
                    dialogue_model=args.model,
                )
                record = await run_case(settings, prompts)
            report["records"].append({"kind": kind, "replicate": replicate, **record})
            # Preserve completed cases if a later model/server call interrupts measurement.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({
                "completed": f"{kind}:{replicate}",
                "delivered": [turn["delivered_text"] for turn in record["turns"]],
            }), flush=True)
    report["latency"] = {
        kind: {
            metric: latency([
                turn[metric] for record in report["records"] if record["kind"] == kind
                for turn in record["turns"]
            ])
            for metric in ("generation_and_grounding_ms", "through_delivery_ack_ms")
        }
        for kind, _ in cases
    }
    report["grounding_verdicts"] = {
        kind: dict(Counter(
            f"{verdict['source']}:{verdict['reason']}:"
            f"{'allowed' if verdict['allowed'] else 'blocked'}"
            for record in report["records"] if record["kind"] == kind
            for turn in record["turns"] for trace in turn["trace"]
            for verdict in trace.get("grounding_verdicts", [])
        ))
        for kind, _ in cases
    }
    report["turn_statuses"] = dict(Counter(
        trace["status"] for record in report["records"] for turn in record["turns"]
        for trace in turn["trace"] if "status" in trace
    ))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8091/v1")
    parser.add_argument("--model", default="qwen2.5-7b-instruct")
    parser.add_argument("--replicates", type=int, choices=range(1, 101), default=10)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/service-model-benchmark.json",
    )
    args = parser.parse_args()
    report = asyncio.run(measure(args))
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "artifact": str(args.output), "latency": report["latency"],
        "grounding_verdicts": report["grounding_verdicts"],
        "turn_statuses": report["turn_statuses"],
    }, indent=2))


if __name__ == "__main__":
    main()
