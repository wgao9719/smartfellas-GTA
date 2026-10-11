"""Environment path settings expand ~ and variables, and never follow the working directory."""

import ntpath
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from service.lls import settings as settings_module
from service.lls.settings import Settings

PATHS = (
    "LLS_DATABASE",
    "LLS_NEIGHBORHOOD_PACK",
    "LLS_WHISPER_EXECUTABLE",
    "LLS_WHISPER_MODEL",
    "LLS_PIPER_EXECUTABLE",
    "LLS_PIPER_MODEL",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("LLS_", "LLM_")):
            monkeypatch.delenv(name)


def windows(monkeypatch):
    """Windows path semantics on any host: ntpath expansion and drive-qualified absolutes."""
    monkeypatch.setattr(
        settings_module, "os", SimpleNamespace(name="nt", environ=os.environ, path=ntpath)
    )


def test_snapshot_settings_defaults_and_environment(monkeypatch):
    settings = Settings.from_env()
    assert settings.snapshot_interval_seconds == 3600
    assert settings.snapshot_retention_count == 24
    monkeypatch.setenv("LLS_SNAPSHOT_INTERVAL_SECONDS", "120.5")
    monkeypatch.setenv("LLS_SNAPSHOT_RETENTION_COUNT", "3")
    settings = Settings.from_env()
    assert settings.snapshot_interval_seconds == 120.5
    assert settings.snapshot_retention_count == 3
    monkeypatch.setenv("LLS_SNAPSHOT_INTERVAL_SECONDS", "0")
    assert Settings.from_env().snapshot_interval_seconds == 0


@pytest.mark.parametrize("name,value", [
    ("LLS_SNAPSHOT_INTERVAL_SECONDS", "-1"),
    ("LLS_SNAPSHOT_INTERVAL_SECONDS", "nan"),
    ("LLS_SNAPSHOT_INTERVAL_SECONDS", "inf"),
    ("LLS_SNAPSHOT_RETENTION_COUNT", "0"),
    ("LLS_SNAPSHOT_RETENTION_COUNT", "1001"),
])
def test_invalid_snapshot_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_speech_gates_parse_from_the_environment_and_validate(monkeypatch):
    speech = Settings.from_env().speech
    assert (speech.minimum_dialogue_confidence, speech.silence_dbfs) == (0.3, -60)
    monkeypatch.setenv("LLS_STT_DIALOGUE_CONFIDENCE", "0.45")
    monkeypatch.setenv("LLS_STT_SILENCE_DBFS", "-72.5")
    speech = Settings.from_env().speech
    assert (speech.minimum_dialogue_confidence, speech.silence_dbfs) == (0.45, -72.5)
    for name, value in (
        ("LLS_STT_DIALOGUE_CONFIDENCE", "0.9"),
        ("LLS_STT_DIALOGUE_CONFIDENCE", "nan"),
        ("LLS_STT_SILENCE_DBFS", "-100"),
        ("LLS_STT_SILENCE_DBFS", "-10"),
        ("LLS_STT_SILENCE_DBFS", "quiet"),
    ):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError):
            Settings.from_env()
        monkeypatch.delenv(name)


def test_path_settings_expand_home_and_variables(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LLS_TEST_MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("LLS_DATABASE", "~/LLS/lls.sqlite3")
    monkeypatch.setenv("LLS_NEIGHBORHOOD_PACK", "~/packs/neighborhood.json")
    monkeypatch.setenv("LLS_WHISPER_EXECUTABLE", "${LLS_TEST_MODELS}/whisper-cli")
    monkeypatch.setenv("LLS_WHISPER_MODEL", "$LLS_TEST_MODELS/ggml-base.en.bin")
    monkeypatch.setenv("LLS_PIPER_EXECUTABLE", "~/piper/piper")
    monkeypatch.setenv("LLS_PIPER_MODEL", "${LLS_TEST_MODELS}/voice.onnx")
    settings = Settings.from_env()
    assert Path(settings.database_path) == home / "LLS" / "lls.sqlite3"
    assert Path(settings.neighborhood_pack_path) == home / "packs" / "neighborhood.json"
    assert Path(settings.speech.whisper_executable) == tmp_path / "models" / "whisper-cli"
    assert Path(settings.speech.whisper_model) == tmp_path / "models" / "ggml-base.en.bin"
    assert Path(settings.speech.piper_executable) == home / "piper" / "piper"
    assert Path(settings.speech.piper_model) == tmp_path / "models" / "voice.onnx"


def test_windows_paths_expand_profile_and_percent_variables(monkeypatch):
    windows(monkeypatch)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Player")
    monkeypatch.setenv("LLS_TEST_MODELS", r"D:\Speech Models (local)")
    monkeypatch.setenv("LLS_DATABASE", r"~\LLS\lls.sqlite3")
    monkeypatch.setenv("LLS_WHISPER_MODEL", r"%LLS_TEST_MODELS%\ggml-base.en.bin")
    monkeypatch.setenv("LLS_PIPER_MODEL", r"\\nas\voices\voice.onnx")
    settings = Settings.from_env()
    assert settings.database_path == r"C:\Users\Player\LLS\lls.sqlite3"
    assert settings.speech.whisper_model == r"D:\Speech Models (local)\ggml-base.en.bin"
    assert settings.speech.piper_model == r"\\nas\voices\voice.onnx"


@pytest.mark.parametrize(
    "value",
    [r"~\LLS\lls.sqlite3", r"\LLS\lls.sqlite3", r"C:LLS\lls.sqlite3", r"%LLS_UNSET%\lls.sqlite3"],
    ids=["no-profile", "root-relative", "drive-relative", "unset-variable"],
)
def test_windows_paths_that_depend_on_the_working_directory_fail(monkeypatch, value):
    windows(monkeypatch)
    for name in ("USERPROFILE", "HOMEPATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLS_DATABASE", value)
    with pytest.raises(ValueError, match="LLS_DATABASE must be absolute"):
        Settings.from_env()


@pytest.mark.parametrize("name", PATHS)
def test_relative_path_settings_fail_instead_of_following_the_working_directory(
    monkeypatch, name
):
    monkeypatch.setenv(name, os.path.join("relative", "file"))
    with pytest.raises(ValueError, match=f"{name} must be absolute"):
        Settings.from_env()


def test_unset_variable_is_reported_instead_of_creating_a_literal_directory(monkeypatch):
    monkeypatch.setenv("LLS_DATABASE", "$LLS_UNSET_ROOT/lls.sqlite3")
    with pytest.raises(ValueError, match=r"\$LLS_UNSET_ROOT"):
        Settings.from_env()


def test_empty_paths_stay_unconfigured_and_memory_database_stays_explicit(monkeypatch):
    for name in PATHS[1:]:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LLS_DATABASE", ":memory:")
    settings = Settings.from_env()
    assert settings.database_path == ":memory:"
    assert settings.neighborhood_pack_path == ""
    assert settings.speech.whisper_executable == settings.speech.piper_model == ""


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", None),
        ("  ", None),
        ("7", 7),
        (" 12 ", 12),
        ("²", "²"),
        ("Microphone (USB Audio)", "Microphone (USB Audio)"),
        ("WASAPI:Speakers (Realtek(R) Audio)", "WASAPI:Speakers (Realtek(R) Audio)"),
    ],
)
def test_audio_device_settings_parse_default_index_and_names(monkeypatch, value, expected):
    monkeypatch.setenv("LLS_INPUT_DEVICE", value)
    monkeypatch.setenv("LLS_OUTPUT_DEVICE", value)
    speech = Settings.from_env().speech
    assert speech.input_device == speech.output_device == expected


@pytest.mark.parametrize(
    "environment,expected",
    [
        ({}, (True, False, False)),
        ({"LLS_RELEASE": "0"}, (False, True, True)),
        ({"LLS_RELEASE": "0", "LLS_DEV_SOCIAL": "0"}, (False, False, False)),
        ({"LLS_RELEASE": "0", "LLS_DEV_SOCIAL_AUTONOMY": "0"}, (False, True, False)),
    ],
)
def test_social_startup_defaults_and_opt_out(monkeypatch, environment, expected):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert (settings.release, settings.dev_social, settings.dev_social_autonomy) == expected


def test_social_startup_still_rejects_release_enablement(monkeypatch):
    monkeypatch.setenv("LLS_DEV_SOCIAL", "1")
    with pytest.raises(ValueError, match="release builds refuse development social"):
        Settings.from_env()
