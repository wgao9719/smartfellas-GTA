#!/usr/bin/env python3
"""Stage a checksum-labeled adapter or atomically deploy it to a local/SSH rig.

SSH requires explicit --ssh-target, --remote-scripts and Windows OpenSSH with SFTP.
No service token or installed loader enters the deployment bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "LivingNPC.GameAdapter.dll",
    "LivingNPC.GameAdapter.pdb",
    "LivingNPC.Core.dll",
    "LivingNPC.Core.pdb",
    "Newtonsoft.Json.dll",
    "LivingNPC.ini.example",
    "adapter-build-identity.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed_path(name: str) -> Path:
    return Path("LivingNPC") / name if name == "LivingNPC.ini.example" else Path(name)


def build_identity(build: Path, configuration: str) -> dict:
    identity = json.loads((build / "adapter-build-identity.json").read_text(encoding="utf-8-sig"))
    if (
        identity.get("schema_version") != 1
        or identity.get("configuration") != configuration
        or not isinstance(identity.get("informational_version"), str)
        or not identity["informational_version"]
        or not isinstance(identity.get("adapter_version"), str)
        or not isinstance(identity.get("source_commit"), str)
        or not isinstance(identity.get("built_at_utc"), str)
        or identity.get("source_dirty") is not None and type(identity["source_dirty"]) is not bool
        or not re.fullmatch(r"[a-fA-F0-9]{64}", str(identity.get("adapter_sha256", "")))
        or identity["adapter_sha256"].lower() != sha256(build / "LivingNPC.GameAdapter.dll")
        or not re.fullmatch(r"[a-fA-F0-9]{64}", str(identity.get("core_sha256", "")))
        or identity["core_sha256"].lower() != sha256(build / "LivingNPC.Core.dll")
    ):
        raise ValueError("Build identity does not match the adapter DLL or configuration.")
    return identity


def stage(build: Path, destination: Path, configuration: str) -> dict:
    if build.resolve() == destination.resolve():
        raise ValueError("The staging directory cannot be the build directory.")
    absent = [name for name in FILES if not (build / name).is_file()]
    if absent:
        raise ValueError("Build output is incomplete: " + ", ".join(absent))
    identity = build_identity(build, configuration)
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(build / name, destination / name)
    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "adapter_version": identity["adapter_version"],
        "informational_version": identity["informational_version"],
        "source_commit": identity["source_commit"],
        "source_dirty": identity["source_dirty"],
        "built_at_utc": identity["built_at_utc"],
        "configuration": configuration,
        "runtime_verification": "UNVERIFIED",
        "files": {name: sha256(destination / name) for name in FILES},
    }
    (destination / "adapter-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def validate_bundle(bundle: Path) -> None:
    manifest = json.loads((bundle / "adapter-build.json").read_text())
    if set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Deployment manifest must cover exactly the approved adapter files.")
    for name in FILES:
        if sha256(bundle / name) != manifest["files"][name]:
            raise ValueError("Deployment checksum mismatch: " + name)
    build_identity(bundle, manifest.get("configuration"))


def deploy_local(bundle: Path, scripts: Path) -> None:
    validate_bundle(bundle)
    source, target = bundle.resolve(), scripts.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("The bundle and scripts directory must be separate directories.")
    if not scripts.is_dir():
        raise ValueError("The existing GTA scripts directory is required.")
    # SHVDN loads every DLL below scripts, so stage beside it on the same volume.
    with tempfile.TemporaryDirectory(prefix="lls-stage-", dir=scripts.parent) as temporary:
        staged = Path(temporary)
        for source in (bundle / name for name in (*FILES, "adapter-build.json")):
            shutil.copy2(source, staged / source.name)
        backup = (
            scripts.parent
            / "LivingNPC-backup"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        backup.mkdir(parents=True)
        installed = []
        try:
            for source in staged.iterdir():
                relative = installed_path(source.name)
                target = scripts / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    old = backup / relative
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, old)
                source.replace(target)
                installed.append(target)
        except OSError:
            for target in reversed(installed):
                old = backup / target.relative_to(scripts)
                if old.exists():
                    shutil.copy2(old, target)
                else:
                    target.unlink(missing_ok=True)
            raise
    print(f"Deployed to {scripts}. Backup: {backup}")


# Uploaded with the bundle and run by path. Values arrive as JSON data, never as code.
REMOTE_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
# .NET hashing avoids Get-FileHash, whose module can fail to load under an inherited PSModulePath.
function Get-Sha256([string]$path) {
  $stream = [System.IO.File]::OpenRead($path)
  try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hex = [System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', ''
      return $hex.ToLowerInvariant()
    } finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
}
try {
  $plan = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $src 'deploy.json')
  $plan = $plan | ConvertFrom-Json
  $dst = $plan.destination
  if (-not (Test-Path -LiteralPath $dst -PathType Container)) {
    throw 'GTA scripts directory does not exist'
  }
  foreach ($entry in $plan.files) {
    $actual = Get-Sha256 (Join-Path $src $entry.source)
    if ($actual -ne $entry.sha256) {
      throw ('Checksum mismatch: ' + $entry.source)
    }
  }
  $backup = Join-Path (Join-Path (Split-Path -Parent $dst) 'LivingNPC-backup') $plan.backup
  New-Item -ItemType Directory -Force -Path $backup | Out-Null
  $done = @()
  $pending = $null
  try {
    foreach ($entry in $plan.files) {
      $out = Join-Path $dst $entry.relative
      $old = Join-Path $backup $entry.relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null
      if (Test-Path -LiteralPath $out) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $old) | Out-Null
        Copy-Item -LiteralPath $out -Destination $old
      }
      $pending = $out + '.incoming'
      Copy-Item -LiteralPath (Join-Path $src $entry.source) -Destination $pending
      Move-Item -LiteralPath $pending -Destination $out -Force
      $pending = $null
      $done += $entry.relative
    }
  } catch {
    if ($pending) { Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue }
    foreach ($name in $done) {
      $old = Join-Path $backup $name
      $out = Join-Path $dst $name
      if (Test-Path -LiteralPath $old) {
        Copy-Item -LiteralPath $old -Destination $out -Force
      } else { Remove-Item -LiteralPath $out }
    }
    throw
  }
  Write-Output ('Deployed adapter. Backup: ' + $backup)
} finally {
  if ((Split-Path -Leaf $src) -match '^lls-upload-\d+$') {
    Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction Continue
  }
}
"""
# cmd.exe, OpenSSH's default shell, limits command lines to 8191 characters. These short
# tokens need no quoting under cmd.exe or a PowerShell DefaultShell.
REMOTE_COMMAND = (
    "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"
)


def remove_upload(batch: Path, target: str, upload: str, names: list[str]) -> None:
    """Best effort after a failed transfer or launch. The script removes its own upload."""
    batch.write_text("".join(f'-rm "{upload}/{name}"\n' for name in names) + f"-rmdir {upload}\n")
    try:
        removed = subprocess.run(["sftp", "-b", str(batch), target], capture_output=True)
    except OSError:
        removed = None
    if removed is None or removed.returncode:
        print(f"Remote upload may remain in the SSH user's home: {upload}", file=sys.stderr)


def deploy_ssh(bundle: Path, target: str, remote_scripts: str) -> None:
    validate_bundle(bundle)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", target):
        raise ValueError("Use an explicit SSH target such as player@100.64.1.2.")
    remote = PureWindowsPath(remote_scripts)
    if not remote.is_absolute() or any(c in remote_scripts for c in "'\"`\n\r$;"):
        raise ValueError("Use an absolute Windows scripts directory without shell metacharacters.")
    nonce = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    upload = f"lls-upload-{nonce}"
    names = (*FILES, "adapter-build.json")
    expected = json.loads((bundle / "adapter-build.json").read_text())["files"]
    plan = {
        "destination": str(remote),
        "backup": nonce,
        "files": [
            {
                "source": name,
                "relative": str(PureWindowsPath(installed_path(name))),
                "sha256": expected.get(name) or sha256(bundle / name),
            }
            for name in names
        ],
    }
    with tempfile.TemporaryDirectory(prefix="lls-deploy-") as temporary:
        local = Path(temporary)
        (local / "deploy.ps1").write_text(REMOTE_SCRIPT, encoding="ascii")
        (local / "deploy.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="ascii")
        sources = [*(bundle / name for name in names), local / "deploy.ps1", local / "deploy.json"]
        # SFTP does not invoke the remote shell. Relative paths start in the SSH user's home.
        batch = local / "upload.sftp"
        batch.write_text(
            f"mkdir {upload}\n"
            + "".join(f'put "{path.as_posix()}" "{upload}/{path.name}"\n' for path in sources)
        )
        try:
            subprocess.run(["sftp", "-b", str(batch), target], check=True)
            subprocess.run(["ssh", target, *REMOTE_COMMAND, f"{upload}/deploy.ps1"], check=True)
        except BaseException:
            remove_upload(local / "cleanup.sftp", target, upload, [path.name for path in sources])
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", choices=("Debug", "Release"), default="Debug")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--stage-dir", type=Path, default=ROOT / "artifacts/adapter")
    parser.add_argument("--scripts-dir", type=Path)
    parser.add_argument("--ssh-target")
    parser.add_argument("--remote-scripts")
    args = parser.parse_args()
    if args.scripts_dir and args.ssh_target:
        parser.error("Select local deployment or SSH deployment.")
    if bool(args.ssh_target) != bool(args.remote_scripts):
        parser.error("SSH deployment requires --ssh-target and --remote-scripts.")
    build = args.build_dir or ROOT / f"adapter/LivingNPC.GameAdapter/bin/{args.configuration}/net48"
    try:
        if args.scripts_dir and args.stage_dir.resolve() == args.scripts_dir.resolve():
            raise ValueError("The staging directory cannot be the scripts directory.")
        manifest = stage(build, args.stage_dir, args.configuration)
        print(json.dumps(manifest, indent=2))
        if args.scripts_dir:
            deploy_local(args.stage_dir, args.scripts_dir)
        if args.ssh_target:
            deploy_ssh(args.stage_dir, args.ssh_target, args.remote_scripts)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Deployment failed: {error}") from error


if __name__ == "__main__":
    main()
