import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "deploy_adapter", Path(__file__).parents[1] / "deploy_adapter.py"
)
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


def bundle(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    for name in DEPLOY.FILES:
        (build / name).write_bytes(("new:" + name).encode())
    (build / "adapter-build-identity.json").write_text(json.dumps({
        "schema_version": 1,
        "adapter_version": "fixture",
        "informational_version": "fixture+build-commit",
        "source_commit": "build-commit",
        "source_dirty": True,
        "configuration": "Debug",
        "built_at_utc": "2026-10-10T12:00:00Z",
        "adapter_sha256": DEPLOY.sha256(build / "LivingNPC.GameAdapter.dll"),
        "core_sha256": DEPLOY.sha256(build / "LivingNPC.Core.dll"),
    }))
    staged = tmp_path / "bundle"
    DEPLOY.stage(build, staged, "Debug")
    scripts = tmp_path / "game" / "scripts"
    scripts.mkdir(parents=True)
    return build, staged, scripts


def test_deploy_copies_only_manifest_files_and_backs_up_outside_scripts(tmp_path):
    _, staged, scripts = bundle(tmp_path)
    (staged / "stale.dll").write_text("never deploy")
    (scripts / "LivingNPC.GameAdapter.dll").write_bytes(b"old")
    DEPLOY.deploy_local(staged, scripts)
    assert not (scripts / "stale.dll").exists()
    assert not (scripts / "LivingNPC-backup").exists()
    backups = list((scripts.parent / "LivingNPC-backup").iterdir())
    assert (backups[0] / "LivingNPC.GameAdapter.dll").read_bytes() == b"old"
    assert (scripts / "LivingNPC.GameAdapter.dll").read_bytes().startswith(b"new:")


def test_locked_destination_rolls_back_already_replaced_files(tmp_path, monkeypatch):
    _, staged, scripts = bundle(tmp_path)
    for name in DEPLOY.FILES:
        (scripts / name).write_bytes(("old:" + name).encode())
    original = Path.replace

    def locked(source, target):
        if Path(target) == scripts / "LivingNPC.Core.dll":
            raise PermissionError("Simulated Windows DLL lock")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError):
        DEPLOY.deploy_local(staged, scripts)
    for name in DEPLOY.FILES:
        assert (scripts / name).read_bytes() == ("old:" + name).encode()
    assert not (scripts / "adapter-build.json").exists()


def test_tampered_bundle_is_rejected_before_destination_changes(tmp_path):
    _, staged, scripts = bundle(tmp_path)
    (staged / "LivingNPC.Core.dll").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        DEPLOY.deploy_local(staged, scripts)
    assert not list(scripts.iterdir())


def test_stage_cannot_overwrite_build(tmp_path):
    build, _, _ = bundle(tmp_path)
    with pytest.raises(ValueError, match="build directory"):
        DEPLOY.stage(build, build, "Debug")


def test_deploy_rejects_same_directory(tmp_path):
    _, staged, _ = bundle(tmp_path)
    with pytest.raises(ValueError, match="separate directories"):
        DEPLOY.deploy_local(staged, staged)


def test_manifest_cannot_authorize_extra_dll(tmp_path):
    _, staged, scripts = bundle(tmp_path)
    path = staged / "adapter-build.json"
    manifest = json.loads(path.read_text())
    manifest["files"]["arbitrary.dll"] = "0000"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="approved"):
        DEPLOY.deploy_local(staged, scripts)


def test_ini_template_uses_canonical_directory_and_preserves_existing_settings(tmp_path):
    _, staged, scripts = bundle(tmp_path)
    data = scripts / "LivingNPC"
    data.mkdir()
    (data / "LivingNPC.ini").write_text("private configuration")
    (data / "existing.log").write_text("preserved evidence")
    DEPLOY.deploy_local(staged, scripts)
    assert (data / "LivingNPC.ini.example").read_bytes() == (
        staged / "LivingNPC.ini.example"
    ).read_bytes()
    assert not (scripts / "LivingNPC.ini.example").exists()
    assert (data / "LivingNPC.ini").read_text() == "private configuration"
    assert (data / "existing.log").read_text() == "preserved evidence"


def test_stage_uses_build_identity_instead_of_current_checkout(tmp_path):
    _, staged, _ = bundle(tmp_path)
    manifest = json.loads((staged / "adapter-build.json").read_text())
    assert manifest["informational_version"] == "fixture+build-commit"
    assert manifest["source_commit"] == "build-commit"
    assert manifest["built_at_utc"] == "2026-10-10T12:00:00Z"


def test_stage_rejects_stale_build_identity_before_copying(tmp_path):
    build, _, _ = bundle(tmp_path)
    (build / "LivingNPC.GameAdapter.dll").write_bytes(b"different build")
    destination = tmp_path / "uncreated"
    with pytest.raises(ValueError, match="Build identity"):
        DEPLOY.stage(build, destination, "Debug")
    assert not destination.exists()


def test_stage_rejects_configuration_mismatch(tmp_path):
    build, _, _ = bundle(tmp_path)
    with pytest.raises(ValueError, match="configuration"):
        DEPLOY.stage(build, tmp_path / "uncreated", "Release")


def test_stage_rejects_core_from_a_different_build(tmp_path):
    build, _, _ = bundle(tmp_path)
    (build / "LivingNPC.Core.dll").write_bytes(b"different build")
    with pytest.raises(ValueError, match="Build identity"):
        DEPLOY.stage(build, tmp_path / "uncreated", "Debug")


def test_ssh_deploy_uses_exact_file_mapping_without_overwriting_ini(tmp_path, monkeypatch):
    import base64

    _, staged, _ = bundle(tmp_path)
    calls = []
    monkeypatch.setattr(DEPLOY.subprocess, "run", lambda command, **kwargs: calls.append(command))
    DEPLOY.deploy_ssh(staged, "player@100.64.1.2", r"C:\GTA\scripts")
    script = base64.b64decode(calls[-1][-1]).decode("utf-16le")
    assert "Relative='LivingNPC\\LivingNPC.ini.example'" in script
    assert "Relative='LivingNPC.ini'" not in script
    assert "Get-ChildItem -LiteralPath $src -File" not in script
