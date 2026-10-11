"""Fingerprint service source at startup without claiming a packaged release."""

import hashlib
from pathlib import Path

from .action_registry import REGISTRY_SHA256


def source_identity(package=None):
    package = Path(package or Path(__file__).parent)
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html"}:
            name = path.relative_to(package).as_posix().encode()
            data = path.read_bytes()
            digest.update(len(name).to_bytes(4, "big") + name)
            digest.update(len(data).to_bytes(8, "big") + data)
    return {
        "kind": "source_tree",
        "source_sha256": digest.hexdigest(),
        "registry_sha256": REGISTRY_SHA256,
        "build_commit": None,
        "packaged_build": False,
    }
