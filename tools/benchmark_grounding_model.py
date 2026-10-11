"""Report real local-classifier grounding quality and latency, without tuning.

The checked-in corpus remains the regression set. The separate holdout is read
only here, for reporting. One sequential pass uses an isolated temporary budget
ledger; no running service configuration, database, or environment is changed.
"""

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.lls.gateway_config import DailyBudget, GatewayConfig  # noqa: E402
from service.lls.grounding import grounded_text, grounded_text_checked  # noqa: E402
from service.lls.outbound import OutboundGate  # noqa: E402
from service.lls.providers import OpenAIProvider  # noqa: E402
from service.lls.settings import Settings  # noqa: E402
from service.lls.storage import Repository  # noqa: E402


def latency(samples):
    """Nearest-rank p95, with exact median for p50; milliseconds throughout."""
    if not samples:
        return {"samples": 0, "p50_ms": None, "p95_ms": None}
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


def rate(numerator, denominator):
    return {
        "count": numerator,
        "total": denominator,
        "percent": round(100 * numerator / denominator, 3) if denominator else None,
    }


class ObservedClassifier:
    def __init__(self, provider):
        self.provider = provider
        self.grounding_timeout_seconds = provider.grounding_timeout_seconds
        self.calls = []

    async def __call__(self, sentence, *, player_text, budget_class, previous_sentence=""):
        call = {"sentence": sentence, "previous_sentence": previous_sentence}
        self.calls.append(call)
        start = time.perf_counter_ns()
        try:
            answer = await self.provider.classify_grounding(
                sentence, player_text=player_text, budget_class=budget_class,
                previous_sentence=previous_sentence,
            )
            normalized = answer.strip().lower() if isinstance(answer, str) else None
            call["outcome"] = normalized if normalized in {"yes", "no"} else "malformed"
            return answer
        except (TimeoutError, asyncio.CancelledError):
            # The enclosing sentence deadline cancels the in-flight classifier.
            call["outcome"] = "timeout"
            raise
        except Exception as exc:
            call["outcome"] = "provider_error"
            call["error_type"] = type(exc).__name__
            raise
        finally:
            call["elapsed_ms"] = (time.perf_counter_ns() - start) / 1_000_000


def cases_for(corpus, holdout):
    contextual = set(corpus["contextual_adversarial"])
    for label, fixture in (("corpus", corpus), ("holdout", holdout)):
        for index, line in enumerate(fixture["ordinary"]):
            contexts = ("", fixture["player_utterance"]) if label == "corpus" else (
                fixture["player_utterance"],
            )
            for request in contexts:
                yield {
                    "set": label, "kind": "ordinary", "index": index, "text": line,
                    "player_text": request,
                    "context": "job_request" if request else "neutral",
                    "source": "independent_control" if label == "holdout" else "corpus",
                }
        for index, entry in enumerate(fixture["adversarial"]):
            line = entry["text"] if isinstance(entry, dict) else entry
            request = fixture["player_utterance"] if (
                label == "holdout" or line in contextual
            ) else ""
            if isinstance(entry, dict):
                request = entry.get("player_utterance", request)
            yield {
                "set": label, "kind": "adversarial", "index": index, "text": line,
                "player_text": request, "context": "job_request" if request else "neutral",
                "source": entry["source"] if isinstance(entry, dict) else "corpus",
            }


def summarize(records):
    ordinary = [row for row in records if row["kind"] == "ordinary"]
    adversarial = [row for row in records if row["kind"] == "adversarial"]
    ordinary_ids = {row["index"] for row in ordinary}
    summary = {}
    for field, label in (("deterministic", "deterministic"), ("checked", "checked")):
        false_positives = {
            row["index"] for row in ordinary if row[field] != row["text"]
        }
        misses = [row for row in adversarial if row[field]]
        summary[label] = {
            "false_positive_rate": rate(len(false_positives), len(ordinary_ids)),
            "miss_rate": rate(len(misses), len(adversarial)),
            "false_positive_lines": list(dict.fromkeys(
                row["text"] for row in ordinary if row[field] != row["text"]
            )),
            "missed_lines": [row["text"] for row in misses],
        }
    calls = [call for row in records for call in row["classifier_calls"]]
    summary["gate_calls"] = len(records)
    summary["classifier_calls"] = len(calls)
    outcomes = Counter(call["outcome"] for call in calls)
    summary["classifier_outcomes"] = {
        outcome: outcomes[outcome]
        for outcome in ("yes", "no", "malformed", "timeout", "provider_error")
    }
    summary["verdicts"] = dict(Counter(
        f"{verdict['source']}:{verdict['reason']}:{'allowed' if verdict['allowed'] else 'blocked'}"
        for row in records for verdict in row["verdicts"]
    ))
    summary["ordinary_context_false_positive_rate"] = {
        context: rate(
            sum(row["checked"] != row["text"] for row in ordinary if row["context"] == context),
            sum(row["context"] == context for row in ordinary),
        )
        for context in sorted({row["context"] for row in ordinary})
    }
    summary["latency"] = {
        "deterministic": latency([row["deterministic_ms"] for row in records]),
        "checked": latency([row["checked_ms"] for row in records]),
        "added": latency([row["added_ms"] for row in records]),
        "classifier": latency([call["elapsed_ms"] for call in calls]),
        "by_group": {
            f"{kind}_{context}": {
                "checked": latency([
                    row["checked_ms"] for row in records
                    if row["kind"] == kind and row["context"] == context
                ]),
                "added": latency([
                    row["added_ms"] for row in records
                    if row["kind"] == kind and row["context"] == context
                ]),
            }
            for kind, context in sorted({(row["kind"], row["context"]) for row in records})
        },
    }
    return summary


async def measure(args):
    fixture_paths = {
        "corpus": ROOT / "tests/fixtures/grounding_corpus.json",
        "holdout": ROOT / "tests/fixtures/grounding_holdout.json",
    }
    fixtures = {label: json.loads(path.read_text("utf-8")) for label, path in fixture_paths.items()}
    settings = Settings(
        provider="local_openai", local_base_url=args.base_url, dialogue_model=args.model,
        ai_gateway=GatewayConfig(foreground=DailyBudget(max_requests=100000)),
    )
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url, "model": args.model, "passes": 1,
        "classifier_timeout_seconds": None,
        "method": (
            "Sequential, no warmup. Corpus ordinary lines run in neutral and job contexts; "
            "each line counts once and is a false positive if either context changes it. "
            "Corpus adversarial lines use their prescribed contexts. Every holdout line uses "
            "the user's money request unless an entry specifies its exact QA question. "
            "Multisentence entries use the gate's previous delivered sentence window. "
            "Any nonempty adversarial output is a miss. "
            "Added latency is checked wall time minus a paired deterministic call. "
            "Classifier timing includes client setup, gateway accounting and cancellation "
            "cleanup; output deadlines do not guarantee exact wall time under OS load. "
            "p95 uses nearest rank. This is gate latency, not dialogue/turn latency."
        ),
        "holdout_provenance": fixtures["holdout"]["provenance"],
        "sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                *fixture_paths.values(), ROOT / "service/lls/grounding.py",
                ROOT / "service/lls/providers.py", Path(__file__),
            ]
        },
        "records": [],
    }
    with tempfile.TemporaryDirectory(prefix="lls-grounding-benchmark-") as temporary:
        with Repository(Path(temporary) / "budgets.sqlite3") as repository:
            gate = OutboundGate(settings, repository)
            provider = OpenAIProvider(settings, gate=gate)
            classifier = ObservedClassifier(provider)
            report["classifier_timeout_seconds"] = classifier.grounding_timeout_seconds
            try:
                for case in cases_for(fixtures["corpus"], fixtures["holdout"]):
                    start = time.perf_counter_ns()
                    deterministic = grounded_text(case["text"], player_text=case["player_text"])
                    deterministic_ms = (time.perf_counter_ns() - start) / 1_000_000
                    call_start = len(classifier.calls)
                    verdicts = []
                    start = time.perf_counter_ns()
                    checked = await grounded_text_checked(
                        case["text"], player_text=case["player_text"], classifier=classifier,
                        verdicts=verdicts,
                    )
                    checked_ms = (time.perf_counter_ns() - start) / 1_000_000
                    report["records"].append({
                        **case, "deterministic": deterministic, "checked": checked,
                        "deterministic_ms": deterministic_ms, "checked_ms": checked_ms,
                        "added_ms": checked_ms - deterministic_ms,
                        "classifier_calls": classifier.calls[call_start:],
                        "verdicts": verdicts,
                    })
                report["budget_usage"] = gate.usage_summary()
            finally:
                await gate.aclose()
    report["summary"] = {
        label: summarize([row for row in report["records"] if row["set"] == label])
        for label in fixtures
    }
    report["summary"]["holdout_verbatim_unsafe"] = summarize([
        row for row in report["records"] if row["source"] == "verbatim_user_holdout"
    ])
    report["summary"]["holdout_qa2_unsafe"] = summarize([
        row for row in report["records"] if row["source"] == "qa2_verbatim_user_holdout"
    ])
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8091/v1")
    parser.add_argument("--model", default="qwen2.5-7b-instruct")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/grounding-model-benchmark.json",
    )
    args = parser.parse_args()
    report = asyncio.run(measure(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(args.output), **report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
