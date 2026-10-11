"""Measure the sentence gate on the fixed corpus without a provider or network.

Optionally compare trusted repository code at --baseline REV. Rates count each
ordinary line once, failing it if either neutral or job-request context alters it.
Latencies are per gate call, including intent detection, not model/turn timings.
"""

import argparse
import inspect
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.grounding import grounded_text  # noqa: E402


def measure(gate, corpus, iterations):
    contextual = set(corpus["contextual_adversarial"])
    player_text = corpus["player_utterance"]
    supports_context = "player_text" in inspect.signature(gate).parameters

    def apply(line, request):
        return gate(line, player_text=request) if supports_context else gate(line)

    ordinary = corpus["ordinary"]
    adversarial = corpus["adversarial"]
    false_positives = sum(
        any(apply(line, request) != line for request in ("", player_text))
        for line in ordinary
    )
    false_negatives = sum(
        bool(apply(line, player_text if line in contextual else ""))
        for line in adversarial
    )
    report = {
        "false_positives": f"{false_positives}/{len(ordinary)}",
        "false_negatives": f"{false_negatives}/{len(adversarial)}",
        "provider_calls": 0,
    }
    groups = {
        "ordinary_neutral": [(line, "") for line in ordinary],
        "ordinary_job_request": [(line, player_text) for line in ordinary],
        "adversarial": [
            (line, player_text if line in contextual else "") for line in adversarial
        ],
    }
    for label, cases in groups.items():
        for line, request in cases:
            apply(line, request)
        samples = []
        for _ in range(iterations):
            for line, request in cases:
                start = time.perf_counter_ns()
                apply(line, request)
                samples.append((time.perf_counter_ns() - start) / 1_000_000)
        samples.sort()
        report[label] = {
            "calls": len(samples),
            "mean_ms": round(statistics.fmean(samples), 6),
            "p50_ms": round(statistics.median(samples), 6),
            "p95_ms": round(samples[int(len(samples) * 0.95)], 6),
            "p99_ms": round(samples[int(len(samples) * 0.99)], 6),
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--baseline", help="Trusted git revision to compare, read-only")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    corpus = json.loads((ROOT / "tests/fixtures/grounding_corpus.json").read_text("utf-8"))
    report = {"iterations": args.iterations}
    if args.baseline:
        source = subprocess.run(
            ["git", "show", f"{args.baseline}:service/lls/grounding.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
        baseline = ModuleType("service.lls._grounding_baseline")
        baseline.__package__ = "service.lls"
        exec(compile(source, "<grounding-baseline>", "exec"), baseline.__dict__)
        report["baseline_revision"] = args.baseline
        report["baseline"] = measure(baseline.grounded_text, corpus, args.iterations)
    report["current"] = measure(grounded_text, corpus, args.iterations)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
