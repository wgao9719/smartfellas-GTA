#!/usr/bin/env python3
"""Fetch the pinned public compile reference. This does not install the GTA loader."""

from __future__ import annotations

import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/scripthookvdotnet/scripthookvdotnet/releases/download/v3.6.0/ScriptHookVDotNet.zip"
ZIP_SHA256 = "b6b4b61bce0437e295ebab065ca437c95ed938e0169197726150047861478211"
DLL_SHA256 = "f328a36445c1d3e282e70b2791efc0d516ae3009d0d1461bf076893805a01cd9"


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "adapter/vendor-refs"
    destination.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(URL, headers={"User-Agent": "LivingLosSantos-build/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive = response.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(archive).hexdigest() != ZIP_SHA256:
        raise SystemExit("SHVDN archive checksum mismatch. No files were installed.")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for filename in ("ScriptHookVDotNet3.dll", "ScriptHookVDotNet3.xml"):
            content = bundle.read(filename)
            if filename.endswith(".dll") and hashlib.sha256(content).hexdigest() != DLL_SHA256:
                raise SystemExit("SHVDN DLL checksum mismatch.")
            (destination / filename).write_bytes(content)
    print(f"Verified SHVDN v3.6.0 compile reference: {destination}")


if __name__ == "__main__":
    main()
