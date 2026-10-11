"""Wire limits shared with the adapter's embedded bridge_limits.json."""

import json
from pathlib import Path

LIMITS_PATH = Path(__file__).resolve().parents[2] / "data" / "bridge_limits.json"
_limits = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
MAX_BODY_BYTES = _limits["max_body_bytes"]
MAX_COMMANDS = _limits["max_commands"]
