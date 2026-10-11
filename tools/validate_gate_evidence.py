#!/usr/bin/env python3
"""Check a G0 evidence index and artifact digests, never certify GTA observations."""

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

REQUIRED = {
    "G0-1": {"baseline_frame_times"},
    "G0-2": {"loaded_modules", "gameplay_recording"},
    "G0-3": {"mac_build", "deployment_receipt", "adapter_log", "gameplay_recording"},
    "G0-4": {"service_health", "request_trace", "network_configuration"},
    "G0-5": {"audio_control_trace", "gameplay_recording", "database_check"},
    "G0-6": {"trial_ledger", "performance_capture", "restart_trace"},
    "G0-7": {"independent_reproduction", "deployment_receipt", "adapter_log"},
}
ARTIFACT_KINDS = set.union(*REQUIRED.values())
ORIGINS = {"windows_rig", "mac_build", "second_mac"}
SUPPORTING_KINDS = {"service_health", "mac_build", "deployment_receipt"}


def exact(value, fields, name):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(fields))}")


def text(value, name):
    if not isinstance(value, str) or not value.strip() or value.upper() in {
        "UNVERIFIED", "UNKNOWN", "TODO", "PASS", "PASSED", "TRUE",
    }:
        raise ValueError(f"{name} requires a concrete recorded value")


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate evidence JSON key")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def reject_simulation(value):
    if isinstance(value, dict):
        if value.get("game_verified") is False or value.get("native_verified") is False:
            raise ValueError("simulated or unverified runtime output cannot establish a GTA gate")
        if str(value.get("mode", "")).startswith("simulated"):
            raise ValueError("simulated output cannot establish a GTA gate")
        if value.get("gta_build") in {"simulated", "UNVERIFIED"}:
            raise ValueError("unverified game build cannot establish a GTA gate")
        for child in value.values():
            reject_simulation(child)
    elif isinstance(value, list):
        for child in value:
            reject_simulation(child)


def supporting_json(kind, value):
    """Metadata identifies a service/build. Its game_verified=false is correct."""
    if not isinstance(value, dict):
        raise ValueError("supporting metadata must be an object")
    if kind == "service_health":
        exact(value, {
            "status", "version", "build", "release_mode", "schema_version", "provider",
            "speech", "game_verified",
        }, "service_health")
        build = value["build"]
        exact(build, {
            "kind", "source_sha256", "registry_sha256", "build_commit", "packaged_build",
        }, "service_health.build")
        if (
            value["status"] != "ok" or value["schema_version"] != 1
            or type(value["release_mode"]) is not bool or value["game_verified"] is not False
            or value["provider"] not in {"disabled", "fake", "openai", "anthropic", "local_openai"}
            or value["speech"] not in {"disabled", "configured"}
            or build["kind"] != "source_tree" or build["packaged_build"] is not False
            or build["build_commit"] is not None
            or any(not re.fullmatch(r"[a-f0-9]{64}", str(build[name]))
                   for name in ("source_sha256", "registry_sha256"))
        ):
            raise ValueError("service health must identify its actual source checkpoint")
        text(value["version"], "service version")
    elif kind == "deployment_receipt":
        hashes = value.get("files")
        if (
            value.get("schema_version") != 1
            or value.get("configuration") not in {"Debug", "Release"}
            or not isinstance(hashes, dict) or not hashes
            or any(not re.fullmatch(r"[a-fA-F0-9]{64}", str(item)) for item in hashes.values())
        ):
            raise ValueError("deployment metadata requires a checksummed build manifest")
    else:
        if value.get("configuration") not in {"Debug", "Release"} or any(
            not re.fullmatch(r"[a-fA-F0-9]{64}", str(value.get(name, "")))
            for name in ("adapter_sha256", "core_sha256")
        ):
            raise ValueError("JSON mac_build metadata requires both built DLL digests")
def validate(path):
    index = load_json(path)
    exact(index, {"schema_version", "gate", "environment", "artifacts", "checks"}, "index")
    if type(index["schema_version"]) is not int or index["schema_version"] != 1:
        raise ValueError("unsupported evidence schema")
    if index["gate"] != "G0":
        raise ValueError("this validator covers G0 only")
    environment = index["environment"]
    exact(environment, {
        "os", "hardware", "driver", "storefront", "gta_build", "scripthookv_build",
        "shvdn_release", "game_edition", "mode", "compat_layer", "service_host_mode",
        "adapter_sha256", "service_source_sha256",
    }, "environment")
    for name, value in environment.items():
        text(value, "environment." + name)
    for name, expected in {
        "os": "windows", "game_edition": "legacy", "mode": "story_mode",
        "compat_layer": "none", "service_host_mode": "local",
    }.items():
        if environment[name] != expected:
            raise ValueError(f"G0 final reference evidence requires {name}={expected}")
    for name in ("adapter_sha256", "service_source_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", environment[name]):
            raise ValueError("invalid environment build digest")
    artifacts = index["artifacts"]
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= 100:
        raise ValueError("evidence requires 1 to 100 artifacts")
    root = path.parent.resolve()
    resolved = {}
    for identifier, artifact in artifacts.items():
        exact(artifact, {"path", "sha256", "kind", "origin"}, "artifact")
        if artifact["kind"] not in ARTIFACT_KINDS or artifact["origin"] not in ORIGINS:
            raise ValueError("simulated/unit evidence cannot establish a GTA gate")
        relative = artifact["path"]
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("artifact path must be relative to the evidence index")
        candidate = (root / relative).resolve()
        if (
            root not in candidate.parents
            or not candidate.is_file()
            or candidate.stat().st_size == 0
        ):
            raise ValueError("artifact must be a nonempty file inside the evidence directory")
        if digest_file(candidate) != artifact["sha256"]:
            raise ValueError("artifact checksum mismatch: " + identifier)
        with candidate.open("rb") as stream:
            looks_json = stream.read(1024).lstrip(b"\xef\xbb\xbf \r\n\t")[:1] in {b"{", b"["}
        if candidate.suffix.lower() == ".json" or looks_json:
            payload = load_json(candidate)
            if artifact["kind"] in SUPPORTING_KINDS:
                supporting_json(artifact["kind"], payload)
            else:
                reject_simulation(payload)
        resolved[identifier] = candidate
    checks = index["checks"]
    exact(checks, REQUIRED, "checks")
    for identifier, check in checks.items():
        exact(check, {
            "scenario", "expected_result", "source_paths", "inputs", "procedure", "result",
            "artifacts", "review",
        }, identifier)
        for name in ("scenario", "expected_result", "inputs"):
            text(check[name], identifier + "." + name)
        for name in ("source_paths", "procedure"):
            if not isinstance(check[name], list) or not check[name]:
                raise ValueError(identifier + " requires " + name)
            for value in check[name]:
                text(value, identifier + "." + name)
        exact(check["result"], {"measurements", "sample_count", "failures"}, "result")
        result = check["result"]
        if type(result["sample_count"]) is not int or result["sample_count"] < 1:
            raise ValueError("result requires a positive measured sample_count")
        if not isinstance(result["measurements"], dict) or not result["measurements"]:
            raise ValueError("result requires recorded measurements, never a pass boolean")
        if any(type(value) not in {int, float} or not math.isfinite(value)
               for value in result["measurements"].values()):
            raise ValueError("measurements must be finite numbers, never pass booleans")
        if identifier == "G0-1" and result["measurements"].get("duration_seconds", 0) < 300:
            raise ValueError("G0-1 requires at least 300 baseline seconds")
        if identifier == "G0-6" and (
            result["measurements"].get("duration_seconds", 0) < 1200
            or result["measurements"].get("interaction_count", 0) < 20
            or result["measurements"].get("bridge_crashes") != 0
            or result["measurements"].get("bridge_deadlocks") != 0
        ):
            raise ValueError("G0-6 requires the full interaction and soak measurements")
        if not isinstance(result["failures"], list) or result["failures"]:
            raise ValueError("unresolved failures prevent a complete G0 evidence index")
        refs = check["artifacts"]
        if not isinstance(refs, list) or not refs or any(ref not in artifacts for ref in refs):
            raise ValueError("check contains missing artifact references")
        if not REQUIRED[identifier] <= {artifacts[ref]["kind"] for ref in refs}:
            raise ValueError(identifier + " lacks required artifact kinds")
        origin = "second_mac" if identifier == "G0-7" else "windows_rig"
        if not any(artifacts[ref]["origin"] == origin for ref in refs):
            raise ValueError(identifier + " lacks its required runtime origin")
        review = check["review"]
        exact(review, {"reviewer", "recorded_at", "manual_observations", "limitations"}, "review")
        text(review["reviewer"], "reviewer")
        if datetime.fromisoformat(review["recorded_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("review timestamp requires a timezone")
        if not isinstance(review["manual_observations"], list) or not review["manual_observations"]:
            raise ValueError("manual visual/native observations must remain explicit")
        for observation in review["manual_observations"]:
            text(observation, "manual observation")
        if not isinstance(review["limitations"], list):
            raise ValueError("review limitations must be a list")
    health_refs = [ref for ref in checks["G0-4"]["artifacts"]
                   if artifacts[ref]["kind"] == "service_health"]
    for ref in health_refs:
        health = load_json(resolved[ref])
        supporting_json("service_health", health)
        if health.get("build", {}).get("source_sha256") != environment["service_source_sha256"]:
            raise ValueError("service health build digest does not match this evidence")
    return {
        "status": "evidence_index_valid",
        "gate": "G0",
        "artifacts_checked": len(artifacts),
        "checks_indexed": len(checks),
        "g0_passed": False,
        "manual_gate_decision_required": True,
        "scope": "Index structure and file integrity only. Observations and budgets need review.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.index), indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SystemExit("Evidence index rejected: " + str(exc)) from exc


if __name__ == "__main__":
    main()
