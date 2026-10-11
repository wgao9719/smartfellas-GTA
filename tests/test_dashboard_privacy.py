"""Exercise dashboard response races without adding a browser dependency."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_dashboard_privacy_response_races():
    script = Path(__file__).with_name("dashboard_privacy_check.cjs")
    result = subprocess.run(
        [shutil.which("node"), "--test", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
