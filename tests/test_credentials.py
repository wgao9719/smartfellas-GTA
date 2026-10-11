from pathlib import Path

import pytest

from service.lls.credentials import DEFAULT_KEY_FILE, KEY_TEMPLATE, load_api_key

REPO = Path(__file__).parents[1]


def test_default_key_file_lives_in_the_ignored_secrets_folder():
    assert DEFAULT_KEY_FILE == REPO / "secrets" / "LLM_API_KEY.txt"
    rules = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "secrets/*" in rules and "!secrets/README.md" in rules
    assert (REPO / "secrets" / "README.md").is_file()


def test_environment_key_wins_over_the_file(tmp_path):
    path = tmp_path / "LLM_API_KEY.txt"
    path.write_text("file-key\n", encoding="utf-8")
    environ = {"LLM_API_KEY": "environment-key"}
    assert load_api_key(environ, path=path) == "environment"
    assert environ["LLM_API_KEY"] == "environment-key"


@pytest.mark.parametrize(
    "content",
    [
        KEY_TEMPLATE + "pasted-key\n",
        "﻿pasted-key",
        '  LLM_API_KEY="pasted-key"  \n',
        "# comment\n\n'pasted-key'\nignored-second-line\n",
    ],
)
def test_file_key_is_loaded_from_the_first_uncommented_line(tmp_path, content):
    path = tmp_path / "LLM_API_KEY.txt"
    path.write_text(content, encoding="utf-8")
    environ = {}
    assert load_api_key(environ, path=path) == "file"
    assert environ == {"LLM_API_KEY": "pasted-key"}


def test_hosted_start_without_a_key_creates_the_template_and_names_only_the_file(
    tmp_path, capsys
):
    path = tmp_path / "secrets" / "LLM_API_KEY.txt"
    environ = {}
    assert load_api_key(environ, hosted=True, path=path) is None
    assert path.read_text(encoding="utf-8") == KEY_TEMPLATE
    assert "LLM_API_KEY" not in environ
    assert str(path) in capsys.readouterr().err
    # An unedited template is still no key, and is never overwritten.
    path.write_text(KEY_TEMPLATE + "\n", encoding="utf-8")
    assert load_api_key(environ, hosted=True, path=path) is None
    assert path.read_text(encoding="utf-8") == KEY_TEMPLATE + "\n"


def test_local_or_disabled_start_creates_no_file(tmp_path):
    path = tmp_path / "secrets" / "LLM_API_KEY.txt"
    assert load_api_key({}, path=path) is None
    assert not path.exists()


def test_key_file_location_can_be_overridden_with_an_absolute_path(tmp_path):
    path = tmp_path / "elsewhere.txt"
    path.write_text("override-key", encoding="utf-8")
    environ = {"LLS_API_KEY_FILE": str(path)}
    assert load_api_key(environ) == "file"
    assert environ["LLM_API_KEY"] == "override-key"
    with pytest.raises(ValueError, match="absolute"):
        load_api_key({"LLS_API_KEY_FILE": "relative.txt"})
