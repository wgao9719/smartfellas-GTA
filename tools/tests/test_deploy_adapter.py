import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
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


LONG_SCRIPTS = r"C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V\scripts"
UPLOADED = (*DEPLOY.FILES, "adapter-build.json", "deploy.ps1", "deploy.json")
REAL_RUN = subprocess.run


def openssh(monkeypatch, home, *, failing=None, remote=None, corrupt=None):
    """Emulate SFTP batches in an SSH home directory. Optionally run the remote command."""
    calls, batches, corrupted = [], [], []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) - 1 == failing:
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(255, command)
            return subprocess.CompletedProcess(command, 255)
        if command[0] == "ssh":
            if corrupt:
                assert corrupted == [corrupt], "Requested upload corruption was not applied"
            if remote is None:
                return subprocess.CompletedProcess(command, 0)
            return REAL_RUN(remote(" ".join(command[2:])), cwd=home, check=kwargs.get("check"))
        batch = Path(command[command.index("-b") + 1]).read_text()
        batches.append(batch)
        for line in batch.splitlines():
            verb, *paths = shlex.split(line)
            if verb == "mkdir":
                (home / paths[0]).mkdir()
            elif verb == "put":
                shutil.copyfile(paths[0], home / paths[1])
                if corrupt and paths[1].endswith("/" + corrupt):
                    (home / paths[1]).write_bytes(b"corrupted in transit")
                    corrupted.append(corrupt)
            elif verb == "-rm" and (home / paths[0]).exists():
                (home / paths[0]).unlink()
            elif verb == "-rmdir" and (home / paths[0]).exists():
                (home / paths[0]).rmdir()
        return subprocess.CompletedProcess(command, 0)

    home.mkdir(exist_ok=True)
    monkeypatch.setattr(DEPLOY.subprocess, "run", run)
    return calls, batches


def uploaded(home, calls):
    upload = calls[1][-1].split("/")[0]
    return upload, json.loads((home / upload / "deploy.json").read_text(encoding="ascii"))


def test_fake_openssh_rejects_unapplied_corruption(tmp_path, monkeypatch):
    _, staged, _ = bundle(tmp_path)
    openssh(monkeypatch, tmp_path / "home", corrupt="LivingNPC.pdb")
    with pytest.raises(AssertionError, match="Requested upload corruption was not applied"):
        DEPLOY.deploy_ssh(staged, "player@100.64.1.2", LONG_SCRIPTS)


def test_ssh_deploy_uses_exact_file_mapping_without_overwriting_ini(tmp_path, monkeypatch):
    _, staged, _ = bundle(tmp_path)
    calls, _ = openssh(monkeypatch, tmp_path / "home")
    DEPLOY.deploy_ssh(staged, "player@100.64.1.2", r"C:\GTA\scripts\\")
    upload, plan = uploaded(tmp_path / "home", calls)
    mapping = {entry["source"]: entry["relative"] for entry in plan["files"]}
    assert mapping == {
        **{name: name for name in (*DEPLOY.FILES, "adapter-build.json")},
        "LivingNPC.ini.example": "LivingNPC\\LivingNPC.ini.example",
    }
    assert plan["destination"] == r"C:\GTA\scripts" and plan["backup"] == upload[11:]
    for entry in plan["files"]:
        content = (tmp_path / "home" / upload / entry["source"]).read_bytes()
        assert content == (staged / entry["source"]).read_bytes()
        assert entry["sha256"] == DEPLOY.sha256(staged / entry["source"])
    assert sorted(path.name for path in (tmp_path / "home" / upload).iterdir()) == sorted(UPLOADED)


def test_ssh_deploy_command_stays_short_and_unquoted_for_long_game_paths(tmp_path, monkeypatch):
    _, staged, _ = bundle(tmp_path)
    calls, batches = openssh(monkeypatch, tmp_path / "home")
    DEPLOY.deploy_ssh(staged, "player@100.64.1.2", LONG_SCRIPTS)
    assert [call[:2] for call in calls] == [["sftp", "-b"], ["ssh", "player@100.64.1.2"]]
    upload, plan = uploaded(tmp_path / "home", calls)
    remote = calls[1][2:]
    # cmd.exe, Windows OpenSSH's default shell, rejects command lines over 8191 characters.
    assert len(" ".join(remote)) < 200
    assert remote == [*DEPLOY.REMOTE_COMMAND, f"{upload}/deploy.ps1"]
    assert remote[-2:] == ["-File", f"{upload}/deploy.ps1"]
    # No token needs quoting or escaping under cmd.exe, PowerShell, or a POSIX shell.
    assert all(re.fullmatch(r"[A-Za-z0-9._/-]+", token) for token in remote)
    assert re.fullmatch(r"lls-upload-[0-9]{20}", upload)
    # Spaces and parentheses travel as JSON data, never through a shell or script source.
    assert plan["destination"] == LONG_SCRIPTS
    script = (tmp_path / "home" / upload / "deploy.ps1").read_text(encoding="ascii")
    assert script == DEPLOY.REMOTE_SCRIPT and "Program Files" not in script
    assert not any("Program Files" in token for call in calls for token in call)
    assert "Program Files" not in batches[0]


@pytest.mark.parametrize("failing", [0, 1], ids=["upload", "launch"])
def test_failed_ssh_deploy_removes_remote_upload(tmp_path, monkeypatch, failing):
    _, staged, _ = bundle(tmp_path)
    home = tmp_path / "home"
    calls, batches = openssh(monkeypatch, home, failing=failing)
    with pytest.raises(subprocess.CalledProcessError):
        DEPLOY.deploy_ssh(staged, "player@100.64.1.2", LONG_SCRIPTS)
    assert [call[0] for call in calls] == (["sftp", "ssh", "sftp"] if failing else ["sftp", "sftp"])
    upload = batches[-1].splitlines()[-1].split()[-1]
    assert batches[-1].splitlines() == [f'-rm "{upload}/{name}"' for name in UPLOADED] + [
        f"-rmdir {upload}"
    ]
    assert not list(home.iterdir())


def test_remote_script_reads_values_as_data_and_removes_only_its_upload():
    script = DEPLOY.REMOTE_SCRIPT
    assert script.isascii() and "$src = $PSScriptRoot" in script
    assert "ConvertFrom-Json" in script and "Invoke-Expression" not in script
    cleanup = script[script.rindex("} finally {") :]
    assert "-match '^lls-upload-\\d+$'" in cleanup
    assert "Remove-Item -LiteralPath $src -Recurse -Force" in cleanup


def powershell_remote(shell):
    """Run the SSH command as Windows OpenSSH would through its configured DefaultShell."""
    if shell == "cmd":
        return lambda command: ["cmd.exe", "/c", command]
    return lambda command: ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]


@pytest.mark.skipif(os.name != "nt", reason="runs the deployment script with Windows PowerShell")
@pytest.mark.parametrize("shell", ["cmd", "powershell"])
def test_windows_default_shells_run_deploy_script_and_remove_upload(tmp_path, monkeypatch, shell):
    _, staged, _ = bundle(tmp_path)
    scripts = tmp_path / "Program Files (x86)" / "Grand Theft Auto V" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "LivingNPC.Core.dll").write_bytes(b"old core")
    openssh(monkeypatch, tmp_path / "home", remote=powershell_remote(shell))
    DEPLOY.deploy_ssh(staged, "player@100.64.1.2", str(scripts))
    for name in (*DEPLOY.FILES, "adapter-build.json"):
        target = scripts / DEPLOY.installed_path(name)
        assert target.read_bytes() == (staged / name).read_bytes()
    (backup,) = (scripts.parent / "LivingNPC-backup").iterdir()
    assert (backup / "LivingNPC.Core.dll").read_bytes() == b"old core"
    assert not list((tmp_path / "home").iterdir()) and not list(scripts.rglob("*.incoming"))


@pytest.mark.skipif(os.name != "nt", reason="runs the deployment script with Windows PowerShell")
@pytest.mark.parametrize("shell", ["cmd", "powershell"])
@pytest.mark.parametrize("corrupt", [*DEPLOY.FILES, "adapter-build.json"])
def test_windows_deploy_script_rejects_corrupt_upload(tmp_path, monkeypatch, capfd, shell, corrupt):
    _, staged, scripts = bundle(tmp_path)
    for name in DEPLOY.FILES:
        (scripts / DEPLOY.installed_path(name)).parent.mkdir(exist_ok=True)
        (scripts / DEPLOY.installed_path(name)).write_bytes(b"old:" + name.encode())
    openssh(monkeypatch, tmp_path / "home", remote=powershell_remote(shell), corrupt=corrupt)
    with pytest.raises(subprocess.CalledProcessError) as error:
        DEPLOY.deploy_ssh(staged, "player@100.64.1.2", str(scripts))
    assert error.value.returncode == 1
    output = capfd.readouterr()
    assert "Checksum mismatch: " + corrupt in output.err
    assert "Deployed adapter" not in output.out
    for name in DEPLOY.FILES:
        assert (scripts / DEPLOY.installed_path(name)).read_bytes() == b"old:" + name.encode()
    assert not (scripts / "adapter-build.json").exists() and not list(scripts.rglob("*.incoming"))
    assert not (scripts.parent / "LivingNPC-backup").exists()
    assert not list((tmp_path / "home").iterdir())


@pytest.mark.skipif(os.name != "nt", reason="runs the deployment script with Windows PowerShell")
@pytest.mark.parametrize("shell", ["cmd", "powershell"])
def test_windows_deploy_script_rolls_back_locked_dll(tmp_path, monkeypatch, shell):
    _, staged, scripts = bundle(tmp_path)
    for name in DEPLOY.FILES:
        (scripts / DEPLOY.installed_path(name)).parent.mkdir(exist_ok=True)
        (scripts / DEPLOY.installed_path(name)).write_bytes(b"old:" + name.encode())
    openssh(monkeypatch, tmp_path / "home", remote=powershell_remote(shell))
    # Python opens files without FILE_SHARE_DELETE, like a loader holding the DLL.
    with (scripts / "LivingNPC.Core.dll").open("rb"):
        with pytest.raises(subprocess.CalledProcessError):
            DEPLOY.deploy_ssh(staged, "player@100.64.1.2", str(scripts))
    (backup,) = (scripts.parent / "LivingNPC-backup").iterdir()
    for name in ("LivingNPC.GameAdapter.dll", "LivingNPC.GameAdapter.pdb", "LivingNPC.Core.dll"):
        assert (backup / name).read_bytes() == b"old:" + name.encode()
    for name in DEPLOY.FILES:
        assert (scripts / DEPLOY.installed_path(name)).read_bytes() == b"old:" + name.encode()
    assert not (scripts / "adapter-build.json").exists() and not list(scripts.rglob("*.incoming"))
    assert not list((tmp_path / "home").iterdir())


def test_local_staging_keeps_partial_copies_outside_the_loader_tree(tmp_path, monkeypatch):
    """SHVDN loads every DLL below scripts, including an interrupted staging copy."""
    _, staged, scripts = bundle(tmp_path)
    (scripts / "LivingNPC.GameAdapter.dll").write_bytes(b"old")
    staged_dlls, original = [], DEPLOY.shutil.copy2

    def copy(source, target):
        result = original(source, target)
        if Path(target).parent.name.startswith("lls-stage-"):
            assert Path(target).parent.parent == scripts.parent
            staged_dlls.append(sorted(p.name for p in scripts.rglob("*.dll")))
        return result

    monkeypatch.setattr(DEPLOY.shutil, "copy2", copy)
    DEPLOY.deploy_local(staged, scripts)
    assert staged_dlls == [["LivingNPC.GameAdapter.dll"]] * (len(DEPLOY.FILES) + 1)
    assert not list(scripts.parent.glob("lls-stage-*"))
    assert (scripts / "LivingNPC.GameAdapter.dll").read_bytes().startswith(b"new:")
