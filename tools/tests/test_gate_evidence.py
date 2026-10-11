import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "gate_evidence", Path(__file__).parents[1] / "validate_gate_evidence.py"
)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def health_fixture():
    return {
        "status": "ok", "version": "0.1.0", "release_mode": True, "schema_version": 1,
        "provider": "disabled", "speech": "disabled", "game_verified": False,
        "build": {
            "kind": "source_tree", "source_sha256": "b" * 64, "registry_sha256": "c" * 64,
            "build_commit": None, "packaged_build": False,
        },
    }


def index_fixture(tmp_path):
    environment = {name: "fixture-recorded-value" for name in (
        "os", "hardware", "driver", "storefront", "gta_build", "scripthookv_build",
        "shvdn_release", "game_edition", "mode", "compat_layer", "service_host_mode",
        "adapter_sha256", "service_source_sha256",
    )}
    environment.update(
        os="windows", game_edition="legacy", mode="story_mode", compat_layer="none",
        service_host_mode="local", adapter_sha256="a" * 64, service_source_sha256="b" * 64,
    )
    value = {"schema_version": 1, "gate": "G0", "environment": environment,
             "artifacts": {}, "checks": {}}
    for check_id, kinds in GATE.REQUIRED.items():
        refs = []
        for kind in sorted(kinds):
            identifier = check_id + "-" + kind
            filename = identifier + (".json" if kind == "service_health" else ".txt")
            path = tmp_path / filename
            content = (json.dumps(health_fixture())
                       if kind == "service_health" else "Synthetic unit fixture, not GTA proof.")
            path.write_text(content)
            value["artifacts"][identifier] = {
                "path": filename, "sha256": GATE.digest_file(path), "kind": kind,
                "origin": "second_mac" if check_id == "G0-7" else "windows_rig",
            }
            refs.append(identifier)
        value["checks"][check_id] = {
            "scenario": "Synthetic validator format fixture",
            "expected_result": "Validate index structure only",
            "source_paths": ["adapter/LivingNPC.GameAdapter/LivingNpcScript.cs"],
            "inputs": "Sanitized fixture values",
            "procedure": ["Inspect the recorded evidence manually."],
            "result": {"measurements": {
                "duration_seconds": 1200, "interaction_count": 20,
                "bridge_crashes": 0, "bridge_deadlocks": 0,
            }, "sample_count": 20, "failures": []},
            "artifacts": refs,
            "review": {"reviewer": "Fixture reviewer", "recorded_at": "2026-10-10T12:00:00Z",
                       "manual_observations": ["Fixture observation for schema tests only"],
                       "limitations": ["No actual runtime evidence"]},
        }
    return value, tmp_path / "index.json"


def validate(value, path):
    path.write_text(json.dumps(value))
    return GATE.validate(path)


def test_valid_index_never_certifies_game_gate(tmp_path):
    value, path = index_fixture(tmp_path)
    result = validate(value, path)
    assert result["status"] == "evidence_index_valid"
    assert result["g0_passed"] is False
    assert result["manual_gate_decision_required"] is True


@pytest.mark.parametrize("origin", ["simulated_game", "unit_test", "mock", "crossover"])
def test_simulated_origin_cannot_establish_gta_proof(tmp_path, origin):
    value, path = index_fixture(tmp_path)
    next(iter(value["artifacts"].values()))["origin"] = origin
    with pytest.raises(ValueError, match="simulated/unit"):
        validate(value, path)


def test_known_simulation_output_rejects_even_if_origin_is_mislabeled(tmp_path):
    value, path = index_fixture(tmp_path)
    artifact = value["artifacts"]["G0-2-gameplay_recording"]
    artifact["path"] = "simulation.json"
    output = tmp_path / artifact["path"]
    output.write_text(json.dumps({"status": "passed", "game_verified": False}))
    artifact["sha256"] = GATE.digest_file(output)
    with pytest.raises(ValueError, match="unverified runtime"):
        validate(value, path)


@pytest.mark.parametrize("change", ["passed", "boolean", "missing", "short_soak"])
def test_narrative_pass_or_incomplete_evidence_rejects(tmp_path, change):
    value, path = index_fixture(tmp_path)
    if change == "passed":
        value["checks"]["G0-6"]["result"] = {"passed": True}
    elif change == "boolean":
        value["checks"]["G0-6"]["result"]["measurements"] = {"passed": True}
    elif change == "missing":
        value["checks"].pop("G0-7")
    else:
        value["checks"]["G0-6"]["result"]["measurements"]["duration_seconds"] = 60
    with pytest.raises(ValueError):
        validate(value, path)


def test_artifact_tampering_and_escape_reject(tmp_path):
    value, path = index_fixture(tmp_path)
    artifact = next(iter(value["artifacts"].values()))
    target = tmp_path / artifact["path"]
    target.write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        validate(value, path)
    artifact["path"] = "../outside.log"
    with pytest.raises(ValueError, match="inside"):
        validate(value, path)


def test_service_health_must_match_recorded_source_checkpoint(tmp_path):
    value, path = index_fixture(tmp_path)
    value["environment"]["service_source_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="health build digest"):
        validate(value, path)


async def test_actual_current_health_is_supporting_metadata_not_gameplay_proof(tmp_path):
    import httpx

    from service.lls.app import create_app
    from service.lls.settings import Settings

    app = create_app(Settings(database_path=str(tmp_path / "health.sqlite3")))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            response = await client.get("/health")
            response.raise_for_status()
            health = response.json()
    assert health["game_verified"] is False
    value, path = index_fixture(tmp_path)
    artifact = value["artifacts"]["G0-4-service_health"]
    output = tmp_path / artifact["path"]
    output.write_text(json.dumps(health))
    artifact["sha256"] = GATE.digest_file(output)
    value["environment"]["service_source_sha256"] = health["build"]["source_sha256"]
    result = validate(value, path)
    assert result["g0_passed"] is False
    assert result["manual_gate_decision_required"] is True


@pytest.mark.parametrize("kind", ["mac_build", "deployment_receipt"])
def test_build_metadata_can_truthfully_disclaim_game_verification(tmp_path, kind):
    value, path = index_fixture(tmp_path)
    artifact = value["artifacts"]["G0-3-" + kind]
    artifact["path"] = kind + ".json"
    output = tmp_path / artifact["path"]
    metadata = {
        "schema_version": 1, "configuration": "Debug", "game_verified": False,
        "adapter_sha256": "a" * 64, "core_sha256": "d" * 64,
        "files": {"LivingNPC.GameAdapter.dll": "a" * 64},
    }
    output.write_text(json.dumps(metadata))
    artifact["sha256"] = GATE.digest_file(output)
    assert validate(value, path)["g0_passed"] is False


def test_simulation_receipt_cannot_be_relabelled_as_service_health(tmp_path):
    value, path = index_fixture(tmp_path)
    artifact = value["artifacts"]["G0-4-service_health"]
    output = tmp_path / artifact["path"]
    output.write_text(json.dumps({"status": "passed", "mode": "simulated_game",
                                  "game_verified": False}))
    artifact["sha256"] = GATE.digest_file(output)
    with pytest.raises(ValueError, match="service_health"):
        validate(value, path)
