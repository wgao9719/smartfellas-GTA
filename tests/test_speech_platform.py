"""Windows speech boundaries with fake OS, PortAudio, and launcher layers on any host."""

import argparse
import asyncio
import ctypes
import json
import sys
import wave
from types import SimpleNamespace

import pytest

from service.lls import speech, speech_process

SECRETS = {
    "LLM_API_KEY": "sk-secret",
    "OPENAI_API_KEY": "sk-other",
    "LLS_BRIDGE_TOKEN": "bridge-secret",
    "LLS_DATABASE": "/private/lls.sqlite3",
    "GITHUB_TOKEN": "ghp-secret",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "DB_PASSWORD": "password",
    "HTTPS_PROXY": "http://proxy.invalid:8080",
    "http_proxy": "http://proxy.invalid:8080",
    "NO_PROXY": "localhost",
}
WINDOWS = {
    "SystemRoot": r"C:\Windows",
    "windir": r"C:\Windows",
    "SystemDrive": "C:",
    "TEMP": r"C:\Users\Player\AppData\Local\Temp",
    "TMP": r"C:\Users\Player\AppData\Local\Temp",
    "Path": r"C:\Windows\system32;C:\Windows",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "ComSpec": r"C:\Windows\system32\cmd.exe",
    "USERPROFILE": r"C:\Users\Player",
    "LOCALAPPDATA": r"C:\Users\Player\AppData\Local",
    "APPDATA": r"C:\Users\Player\AppData\Roaming",
    "ProgramData": r"C:\ProgramData",
    "ProgramFiles": r"C:\Program Files",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "CommonProgramFiles": r"C:\Program Files\Common Files",
    "NUMBER_OF_PROCESSORS": "8",
    "PROCESSOR_ARCHITECTURE": "AMD64",
}


def wav(path, *, rate=16000, channels=1, seconds=0.1):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return path


def capture_launcher(monkeypatch, effect=None):
    calls = []

    async def run_owned(argv, *, stdin, timeout, env, max_output):
        calls.append((list(map(str, argv)), env))
        if effect is not None:
            effect(env)
        return b""

    monkeypatch.setattr(speech_process, "run_owned", run_owned)
    return calls


async def test_speech_programs_receive_no_secrets_from_the_service(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LC_SECRET_TOKEN", "locale-named-secret")
    calls = capture_launcher(monkeypatch)
    await speech.run_program(["whisper-cli", "-f", "audio.wav"], env={"LLS_SAPI_TEXT": "Hello"})
    ((argv, env),) = calls
    assert argv == ["whisper-cli", "-f", "audio.wav"]
    assert not set(SECRETS) & set(env) and "LC_SECRET_TOKEN" not in env
    assert env["LLS_SAPI_TEXT"] == "Hello" and "PATH" in env


async def test_real_child_process_environment_excludes_secrets(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    output = await speech.run_program(
        [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"]
    )
    names = {name.upper() for name in json.loads(output)}
    assert not {name.upper() for name in SECRETS} & names
    assert "PATH" in names


def test_windows_child_environment_keeps_runtime_variables_only(monkeypatch):
    environ = {**WINDOWS, **SECRETS, "PSModulePath": r"C:\Program Files\PowerShell\7\Modules"}
    monkeypatch.setattr(speech, "os", SimpleNamespace(name="nt", environ=environ))
    assert speech.child_environment({"LLS_SAPI_WAV": r"C:\Temp\speech.wav"}) == {
        **WINDOWS,
        "LLS_SAPI_WAV": r"C:\Temp\speech.wav",
    }


def test_posix_child_environment_keeps_path_home_temp_and_locale(monkeypatch):
    kept = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/player",
        "TMPDIR": "/tmp/",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "LC_CTYPE": "UTF-8",
    }
    environ = {**kept, **SECRETS, "SSH_AUTH_SOCK": "/tmp/agent", "PYTHONPATH": "/tmp/code"}
    monkeypatch.setattr(speech, "os", SimpleNamespace(name="posix", environ=environ))
    assert speech.child_environment() == kept


def kernel32(directory, calls=None):
    def get_system_directory(buffer, size):
        if calls is not None:
            calls.append(size)
        buffer.value = directory
        return len(directory) if directory else 0

    return SimpleNamespace(GetSystemDirectoryW=get_system_directory)


def test_windows_powershell_comes_from_the_real_system_directory(monkeypatch):
    calls = []
    system = kernel32(r"D:\Windows\system32", calls)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: system, raising=False)
    # Neither a planted PATH entry nor an altered SystemRoot variable selects the interpreter.
    monkeypatch.setenv("PATH", r"C:\Users\Player\Downloads")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Users\Player\FakeWindows")
    expected = r"D:\Windows\system32\WindowsPowerShell\v1.0\powershell.exe"
    assert speech.windows_powershell() == expected
    assert calls == [260]
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: kernel32(""), raising=False)
    with pytest.raises(speech.SpeechUnavailable, match="system directory"):
        speech.windows_powershell()


async def test_windows_sapi_pins_wav_format_and_passes_text_only_as_data(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "os", SimpleNamespace(name="nt", environ={**WINDOWS, **SECRETS}))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **kwargs: kernel32(r"C:\Windows\system32"), raising=False
    )
    text = "Hello'; Remove-Item C:\\ -Recurse; $env:LLM_API_KEY \"quoted\""
    calls = capture_launcher(
        monkeypatch, lambda env: wav(env["LLS_SAPI_WAV"], rate=22050, seconds=0.2)
    )
    info = await speech.WindowsSapi().synthesize(text, tmp_path / "reply.wav")
    ((argv, env),) = calls
    assert argv[0] == r"C:\Windows\system32\WindowsPowerShell\v1.0\powershell.exe"
    assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    script = argv[4]
    assert "SpeechAudioFormatInfo" in script and "22050" in script
    assert "AudioBitsPerSample]::Sixteen" in script and "AudioChannel]::Mono" in script
    assert "SetOutputToWaveFile($env:LLS_SAPI_WAV,$format)" in script
    assert text not in " ".join(argv) and "Remove-Item" not in script
    assert env["LLS_SAPI_TEXT"] == text and not set(SECRETS) & set(env)
    assert env["SystemRoot"] == r"C:\Windows"
    assert (info.sample_rate, info.channels) == (22050, 1)
    assert speech.inspect_wav(tmp_path / "reply.wav") == info


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class PortAudioError(Exception):
    pass


WINDOWS_HOSTAPIS = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")


def endpoints(*rows):
    """PortAudio's device list: (name, host API index, input channels, output channels)."""
    return [
        {
            "name": name,
            "index": index,
            "hostapi": api,
            "max_input_channels": inputs,
            "max_output_channels": outputs,
            "default_samplerate": 48000.0,
        }
        for index, (name, api, inputs, outputs) in enumerate(rows)
    ]


def windows_endpoints():
    """One microphone and one speaker, listed once per Windows host API under one name."""
    rows = []
    for api in range(len(WINDOWS_HOSTAPIS)):
        rows += [("Microphone (USB Audio)", api, 1, 0), ("Speakers (Realtek(R) Audio)", api, 0, 2)]
    return endpoints(*rows)


class WasapiSettings:
    def __init__(self, exclusive=False, auto_convert=False, explicit_sample_format=False):
        self.exclusive, self.auto_convert = exclusive, auto_convert


def portaudio(monkeypatch, devices, hostapis=WINDOWS_HOSTAPIS, *, fail=None):
    """A fake sounddevice module whose streams deliver PCM like a working driver."""
    state = SimpleNamespace(streams=[], queries=0)

    def query_devices():
        state.queries += 1
        return list(devices)

    def query_hostapis():
        return tuple({"name": name} for name in hostapis)

    class Stream:
        def __init__(self, **kwargs):
            if fail is not None:
                raise fail
            self.kwargs = kwargs
            state.streams.append(self)

        def start(self):
            if "finished_callback" not in self.kwargs:
                self.kwargs["callback"](bytes(320), 160, None, False)
                return
            buffer = bytearray(1 << 20)
            try:
                self.kwargs["callback"](buffer, len(buffer), None, False)
            except CallbackStop:
                self.kwargs["finished_callback"]()

        def stop(self):
            pass

        def abort(self):
            pass

        def close(self):
            pass

    module = SimpleNamespace(
        RawInputStream=Stream,
        RawOutputStream=Stream,
        CallbackStop=CallbackStop,
        CallbackAbort=CallbackAbort,
        WasapiSettings=WasapiSettings,
        query_devices=query_devices,
        query_hostapis=query_hostapis,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module, state


def test_duplicate_windows_names_resolve_to_wasapi_with_sample_rate_conversion(monkeypatch):
    sd, _ = portaudio(monkeypatch, windows_endpoints())
    index, settings = speech.select_device(sd, "Microphone (USB Audio)", "input")
    assert index == 4 and settings.auto_convert and not settings.exclusive
    index, settings = speech.select_device(sd, "speakers (realtek(r) audio)", "output")
    assert index == 5 and settings.auto_convert


@pytest.mark.parametrize(
    "selector,kind,expected",
    [
        ("MME:Microphone (USB Audio)", "input", 0),
        ("Windows DirectSound : Microphone (USB Audio)", "input", 2),
        ("wasapi:Speakers (Realtek(R) Audio)", "output", 5),
        ("WDM-KS:Speakers (Realtek(R) Audio)", "output", 7),
        ("Microphone (USB Audio), MME", "input", 0),
        ("USB", "input", 4),
        ("realtek mme", "output", 1),
    ],
)
def test_host_api_prefixes_and_sounddevice_queries_select_one_endpoint(
    monkeypatch, selector, kind, expected
):
    sd, _ = portaudio(monkeypatch, windows_endpoints())
    index, settings = speech.select_device(sd, selector, kind)
    assert index == expected
    assert (settings is not None) == (expected in {4, 5})


def test_name_falls_back_through_mme_directsound_then_wdm_ks(monkeypatch):
    rows = [
        ("Line In (Audio Interface)", 3, 2, 0),
        ("Line In (Audio Interface)", 1, 2, 0),
        ("Line In (Audio Interface)", 0, 2, 0),
    ]
    sd, _ = portaudio(monkeypatch, endpoints(*rows))
    assert speech.select_device(sd, "Line In (Audio Interface)", "input") == (2, None)
    sd, _ = portaudio(monkeypatch, endpoints(*rows[:2]))
    assert speech.select_device(sd, "Line In (Audio Interface)", "input") == (1, None)


def test_numeric_selection_applies_wasapi_conversion_only_to_wasapi(monkeypatch):
    sd, _ = portaudio(monkeypatch, windows_endpoints())
    assert speech.select_device(sd, 4, "input")[1].auto_convert
    assert speech.select_device(sd, 0, "input") == (0, None)
    for index in (1, 8, -1):
        with pytest.raises(speech.SpeechUnavailable, match="does not exist"):
            speech.select_device(sd, index, "input")


def test_missing_and_ambiguous_names_fail_with_a_clear_reason(monkeypatch):
    rows = [("Microphone (USB Audio)", 2, 1, 0), ("Microphone (USB Audio)", 2, 1, 0)]
    sd, _ = portaudio(monkeypatch, windows_endpoints())
    with pytest.raises(speech.SpeechUnavailable, match="No audio input device matches 'Headset'"):
        speech.select_device(sd, "Headset", "input")
    with pytest.raises(speech.SpeechUnavailable, match="No audio output device"):
        speech.select_device(sd, "Microphone (USB Audio)", "output")
    sd, _ = portaudio(monkeypatch, endpoints(*rows))
    with pytest.raises(speech.SpeechUnavailable, match=r"Windows WASAPI devices \[0, 1\]"):
        speech.select_device(sd, "Microphone (USB Audio)", "input")


def test_colons_in_device_names_are_names_unless_they_name_a_host_api(monkeypatch):
    rows = [("HDA Intel PCH: ALC892 Analog (hw:0,0)", 0, 2, 0)]
    sd, _ = portaudio(monkeypatch, endpoints(*rows), ("ALSA",))
    selector = "HDA Intel PCH: ALC892 Analog (hw:0,0)"
    assert speech.select_device(sd, selector, "input") == (0, None)
    assert speech.select_device(sd, "ALSA:" + selector, "input") == (0, None)


async def test_microphone_opens_resolved_wasapi_device_and_keeps_default_unchanged(
    monkeypatch, tmp_path
):
    sd, state = portaudio(monkeypatch, windows_endpoints())
    mic = speech.Microphone("Microphone (USB Audio)")
    await asyncio.to_thread(mic.start)
    kwargs = state.streams[-1].kwargs
    assert (kwargs["device"], kwargs["samplerate"], kwargs["channels"]) == (4, 16000, 1)
    assert kwargs["extra_settings"].auto_convert
    assert mic.stop(tmp_path / "wasapi.wav").sample_rate == 16000
    queries = state.queries
    default = speech.Microphone()
    await asyncio.to_thread(default.start)
    assert state.streams[-1].kwargs["device"] is None
    assert state.streams[-1].kwargs["extra_settings"] is None
    assert state.queries == queries
    default.cancel()


async def test_playback_opens_resolved_wasapi_device_and_keeps_default_unchanged(
    monkeypatch, tmp_path
):
    sd, state = portaudio(monkeypatch, windows_endpoints())
    audio = wav(tmp_path / "piper.wav", rate=22050)
    await speech.play_wav(audio, "Speakers (Realtek(R) Audio)")
    kwargs = state.streams[-1].kwargs
    assert (kwargs["device"], kwargs["samplerate"]) == (5, 22050)
    assert kwargs["extra_settings"].auto_convert
    queries = state.queries
    await speech.play_wav(audio)
    assert state.streams[-1].kwargs["device"] is None
    assert state.streams[-1].kwargs["extra_settings"] is None
    assert state.queries == queries


async def test_device_failures_keep_the_driver_reason_instead_of_blaming_permission(
    monkeypatch, tmp_path
):
    error = PortAudioError(
        "Error opening RawInputStream:\nInvalid sample rate\t[PaErrorCode -9997]"
    )
    portaudio(monkeypatch, windows_endpoints(), fail=error)
    with pytest.raises(speech.SpeechUnavailable) as captured:
        await asyncio.to_thread(speech.Microphone(0).start)
    message = str(captured.value)
    assert message == (
        "Microphone is unavailable: Error opening RawInputStream: Invalid sample rate "
        "[PaErrorCode -9997]."
    )
    assert "permission" not in message
    with pytest.raises(speech.SpeechUnavailable, match="Audio playback device is unavailable: Err"):
        await speech.play_wav(wav(tmp_path / "audio.wav"), 1)
    portaudio(monkeypatch, windows_endpoints())
    with pytest.raises(speech.SpeechUnavailable, match="No audio input device matches 'Headset'"):
        await asyncio.to_thread(speech.Microphone("Headset").start)


async def test_device_probe_names_each_host_api(monkeypatch):
    portaudio(monkeypatch, windows_endpoints())
    result = await speech.probe(argparse.Namespace(command="devices"))
    assert result["hostapis"] == list(WINDOWS_HOSTAPIS) and result["game_verified"] is False
    assert [(d["index"], d["hostapi_name"]) for d in result["devices"][::2]] == [
        (0, "MME"),
        (2, "Windows DirectSound"),
        (4, "Windows WASAPI"),
        (6, "Windows WDM-KS"),
    ]
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(
    "value,expected",
    [("", None), (" 3 ", 3), ("WASAPI:Microphone (USB Audio)", "WASAPI:Microphone (USB Audio)")],
)
def test_probe_device_arguments_accept_indexes_and_names(value, expected):
    assert speech.device_selector(value) == expected
