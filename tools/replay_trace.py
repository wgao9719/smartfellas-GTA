"""Replay a sanitized fixture without GTA, live data, or model calls."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.lls.replay import load_bundle, run_replay  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(run_replay(load_bundle(args.bundle)))
    except (ValueError, OSError) as exc:
        # Validation errors may include narrative text. Do not echo their inputs.
        parser.exit(2, f"Replay failed: {type(exc).__name__}. Check the bundle schema.\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        1
        if result["missing_responses"] or result["unsettled_turn"] or result["timed_out_turns"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
