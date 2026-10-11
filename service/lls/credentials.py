"""Load the hosted model key from a git-ignored file when the environment has none."""

import os
import sys
from pathlib import Path

KEY_VARIABLE = "LLM_API_KEY"
DEFAULT_KEY_FILE = Path(__file__).resolve().parents[2] / "secrets" / "LLM_API_KEY.txt"
KEY_TEMPLATE = (
    "# Paste your Anthropic API key on the line below, then save this file.\n"
    "# This file is git-ignored: it stays on this computer and is never committed.\n"
    "\n"
)


def key_file(environ):
    configured = environ.get("LLS_API_KEY_FILE", "")
    if not configured:
        return DEFAULT_KEY_FILE
    path = Path(os.path.expandvars(os.path.expanduser(configured)))
    if not path.is_absolute():
        raise ValueError(f"LLS_API_KEY_FILE must be absolute after expansion: {str(path)!r}")
    return path


def read_key(path):
    """Return the first non-comment line, tolerating BOMs, quotes and a KEY= prefix."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith(KEY_VARIABLE + "="):
            value = value.split("=", 1)[1].strip()
        return value.strip("\"'")
    return ""


def load_api_key(environ=None, *, hosted=False, path=None):
    """Put the file's key into the environment; an existing environment key always wins.

    When a hosted provider is selected and no key exists anywhere, create the commented
    template so the user only has to paste the key. Returns where the key came from.
    """
    environ = os.environ if environ is None else environ
    if environ.get(KEY_VARIABLE):
        return "environment"
    path = key_file(environ) if path is None else path
    key = read_key(path)
    if key:
        environ[KEY_VARIABLE] = key
        return "file"
    if hosted:
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(KEY_TEMPLATE, encoding="utf-8")
            except OSError:
                pass
        # Name the file, never a key value.
        print(f"No API key found. Paste your key into {path} and restart.", file=sys.stderr)
    return None
