import asyncio
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import wave

import pytest

from service.lls import speech


def wav(path, *, rate=16000, seconds=0.1):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def test_audio_level_reports_rms_and_peak_dbfs(tmp_path):
    def level(name, samples):
        with wave.open(str(tmp_path / name), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return speech.audio_level(tmp_path / name)

    assert level("silence.wav", [0] * 1600) == (float("-inf"), float("-inf"))
    assert level("full.wav", [32767, -32768] * 800) == pytest.approx((0, 0), abs=0.001)
    rms, peak = level("tone.wav", [round(1000 * math.sin(n / 3)) for n in range(1600)])
    assert rms == pytest.approx(-33.3, abs=0.1) and peak == pytest.approx(-30.3, abs=0.1)
    assert level("click.wav", [0] * 1599 + [-3277]) == pytest.approx((-52.04, -20.0), abs=0.01)


def test_reject_malformed_and_oversized_audio(tmp_path):
    good = wav(tmp_path / "valid.wav")
    assert speech.inspect_wav(good).duration_seconds == 0.1
    long = wav(tmp_path / "long.wav", seconds=31)
    with pytest.raises(speech.SpeechUnavailable, match="30 seconds"):
        speech.inspect_wav(long)
    good.write_bytes(good.read_bytes()[:-4])
    with pytest.raises(speech.SpeechUnavailable, match="truncated"):
        speech.inspect_wav(good)


@pytest.mark.asyncio
async def test_process_limits_timeout_and_private_errors():
    assert (await speech.run_program([sys.executable, "-c", "print('ok')"])).splitlines() == [b"ok"]
    with pytest.raises(speech.SpeechUnavailable, match="timed out"):
        await speech.run_program(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05
        )
    with pytest.raises(speech.SpeechUnavailable, match="output limit"):
        await speech.run_program([sys.executable, "-c", "print('x' * 3000000)"])
    with pytest.raises(speech.SpeechUnavailable) as error:
        await speech.run_program(
            [
                sys.executable,
                "-c",
                "import sys; print('private text', file=sys.stderr); sys.exit(2)",
            ]
        )
    assert "private text" not in str(error.value)


@pytest.mark.asyncio
async def test_cancel_kills_child(tmp_path):
    marker = tmp_path / "should-not-exist"
    script = "import time,pathlib,sys; time.sleep(1); pathlib.Path(sys.argv[1]).touch()"
    task = asyncio.create_task(speech.run_program([sys.executable, "-c", script, str(marker)]))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_whisper_missing_confidence_and_temp_cleanup(tmp_path, monkeypatch):
    model = tmp_path / "model.bin"
    model.touch()
    audio = wav(tmp_path / "mic.wav")
    output_paths = []

    async def whisper(argv, **kwargs):
        output = argv[argv.index("-of") + 1].with_suffix(".json")
        output_paths.append(output)
        output.write_text(json.dumps({"transcription": [{"text": "Hello neighbor."}]}))

    monkeypatch.setattr(speech, "run_program", whisper)
    result = await speech.WhisperCpp(sys.executable, model).transcribe(audio)
    assert result.text == "Hello neighbor."
    assert result.confidence is None
    assert not output_paths[0].parent.exists()


FAKE_WHISPER_CLI = """
import shutil
import sys

arguments = sys.argv[1:]
# Like whisper-cli, write token data only for full JSON output.
if "-ojf" not in arguments or "-np" not in arguments:
    sys.exit(2)
model, output = (arguments[arguments.index(flag) + 1] for flag in ("-m", "-of"))
shutil.copyfile(model, output + ".json")
print("[00:00:00.000 --> 00:00:01.000]   fixture")
"""


def fake_whisper_cli(tmp_path):
    """A real executable for WhisperCpp and run_owned. Its model file holds -ojf JSON."""
    script = tmp_path / "fake_whisper_cli.py"
    script.write_text(FAKE_WHISPER_CLI)
    executable = tmp_path / "whisper-cli"
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
    executable.chmod(0o755)
    return executable


def timed(start, end):
    def stamp(milliseconds):
        return f"00:00:{milliseconds // 1000:02},{milliseconds % 1000:03}"

    return {
        "timestamps": {"from": stamp(start), "to": stamp(end)},
        "offsets": {"from": start, "to": end},
    }


def segment(text, tokens, end=1000):
    return {
        **timed(0, end),
        "text": text,
        "tokens": [
            {"text": piece, **timed(0, end), "id": identifier, "p": p, "t_dtw": -1}
            for piece, identifier, p in tokens
        ],
    }


def full_json(*segments):
    """The whisper-cli -ojf shape observed from whisper.cpp v1.9.5 with ggml-base.en."""
    layers = {"state": 512, "head": 8, "layer": 6}
    return {
        "systeminfo": "WHISPER : COREML = 0 | OPENVINO = 0 | CPU : NEON = 1 | ",
        "model": {
            "type": "base", "multilingual": False, "vocab": 51864,
            "audio": {"ctx": 1500, **layers}, "text": {"ctx": 448, **layers},
            "mels": 80, "ftype": 1,
        },
        "params": {"model": "ggml-base.en.bin", "language": "en", "translate": False},
        "result": {"language": "en"},
        "transcription": list(segments),
    }


# Token text, id, and p values copied from real whisper-cli output.
SPEECH = segment(" Please look at me.", [
    ("[_BEG_]", 50363, 0.994657), (" Please", 4222, 0.549483), (" look", 804, 0.986557),
    (" at", 379, 0.99858), (" me", 502, 0.999464), (".", 13, 0.887515),
    ("[_TT_50]", 50413, 0.0355188),
])
BLANK = segment(" [BLANK_AUDIO]", [
    ("[_BEG_]", 50363, 0.796836), (" [", 685, 0.226629), ("BL", 9148, 0.112603),
    ("ANK", 15154, 0.992328), ("_", 62, 0.989101), ("AUD", 48877, 0.998976),
    ("IO", 9399, 0.998612), ("]", 60, 0.998224), ("[_TT_500]", 50863, 0.0348323),
], end=10000)
NOISE = segment(" (blender whirring)", [
    ("[_BEG_]", 50363, 0.834179), (" (", 357, 0.513252), ("bl", 2436, 0.0273796),
    ("ender", 2194, 0.484162), (" wh", 348, 0.733939), ("ir", 343, 0.909456),
    ("ring", 1806, 0.99161), (")", 8, 0.981791), ("[_TT_150]", 50513, 0.410055),
], end=3000)
SPOKEN_CONFIDENCE = (0.549483 + 0.986557 + 0.99858 + 0.999464 + 0.887515) / 5
UNSCORED = {key: value for key, value in SPEECH.items() if key != "tokens"}


@pytest.mark.skipif(os.name == "nt", reason="the fake whisper-cli is a POSIX shell script")
@pytest.mark.parametrize(
    ("segments", "text", "confidence"),
    [
        ([SPEECH], "Please look at me.", SPOKEN_CONFIDENCE),
        ([BLANK, SPEECH, NOISE], "Please look at me.", SPOKEN_CONFIDENCE),
        ([BLANK], "", None),
        ([NOISE, BLANK], "", None),
        ([], "", None),
        ([UNSCORED], "Please look at me.", None),
    ],
)
async def test_full_json_confidence_and_annotations_through_the_owned_process(
    tmp_path, segments, text, confidence
):
    model = tmp_path / "model.bin"
    model.write_text(json.dumps(full_json(*segments), indent="\t"))
    whisper = speech.WhisperCpp(fake_whisper_cli(tmp_path), model)
    result = await whisper.transcribe(wav(tmp_path / "mic.wav"))
    assert (result.text, result.provider) == (text, "whisper.cpp")
    if confidence is None:
        assert result.confidence is None
    else:
        assert result.confidence == pytest.approx(confidence)


@pytest.mark.skipif(os.name == "nt", reason="the fake whisper-cli is a POSIX shell script")
@pytest.mark.parametrize("p", [1.5, -0.1, "NaN"])
async def test_invalid_token_probability_fails_through_the_owned_process(tmp_path, p):
    model = tmp_path / "model.bin"
    document = full_json(SPEECH)
    document["transcription"][0]["tokens"][1]["p"] = p
    model.write_text(json.dumps(document))
    whisper = speech.WhisperCpp(fake_whisper_cli(tmp_path), model)
    with pytest.raises(speech.SpeechUnavailable, match="invalid transcript"):
        await whisper.transcribe(wav(tmp_path / "mic.wav"))


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        (" [BLANK_AUDIO]", False), (" [ Silence ]", False), (" (silence)", False),
        (" [MUSIC]", False), (" [NO SPEECH]", False), (" (blender whirring) [Music]", False),
        (" ...", False), (" Please look at me.", True), (" [MUSIC] Hello.", True), (" 42", True),
    ],
)
def test_annotation_only_segments_are_not_speech(text, spoken):
    assert speech.is_speech(text) is spoken


@pytest.mark.skipif(
    not (os.environ.get("LLS_WHISPER_EXECUTABLE") and os.environ.get("LLS_WHISPER_MODEL")),
    reason="set LLS_WHISPER_EXECUTABLE and LLS_WHISPER_MODEL to check an installed whisper.cpp",
)
async def test_installed_whisper_cli_full_json(tmp_path):
    whisper = speech.WhisperCpp(
        os.environ["LLS_WHISPER_EXECUTABLE"], os.environ["LLS_WHISPER_MODEL"]
    )
    generator = random.Random(3)
    with wave.open(str(tmp_path / "hiss.wav"), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(
            b"".join(struct.pack("<h", int(generator.gauss(0, 30))) for _ in range(64000))
        )
    quiet = await whisper.transcribe(tmp_path / "hiss.wav")
    assert not set(quiet.text) & set("[]()")
    wav(tmp_path / "silence.wav", seconds=2)
    # Whisper hears "You" in digital silence. The coordinator gate never runs it.
    assert await gated_capture(whisper, tmp_path / "silence.wav") == ("failed", "", 0)
    assert await gated_capture(whisper, tmp_path / "hiss.wav") == ("failed", "", 0)
    if shutil.which("say") and shutil.which("afconvert"):
        voice = str(tmp_path / "voice.aiff")
        subprocess.run(["say", "-o", voice, "Please look at me."], check=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", voice, voice[:-4] + "wav"],
            check=True,
        )
        spoken = await whisper.transcribe(tmp_path / "voice.wav")
        assert "look at me" in spoken.text.lower()
        assert spoken.confidence is not None and 0 < spoken.confidence <= 1
        state, text, calls = await gated_capture(whisper, tmp_path / "voice.wav")
        assert (state, calls) == ("completed", 1) and "look at me" in text.lower()


async def gated_capture(transcriber, recording):
    """Run a recorded file through the real coordinator gates and the given recognizer."""
    from service.lls.speech_coordinator import SpeechConfig, SpeechCoordinator, SpeechIdentity

    identity = SpeechIdentity("session", 1, "scene", "actor", 0, "instance")
    calls = []

    class Recorded:
        def start(self):
            pass

        def stop(self, destination):
            shutil.copyfile(recording, destination)
            return speech.inspect_wav(destination)

        def cancel(self):
            pass

    class Counted:
        async def transcribe(self, audio):
            calls.append(audio)
            return await transcriber.transcribe(audio)

    coordinator = SpeechCoordinator(
        SpeechConfig(enabled=True),
        is_current=lambda current: current == identity,
        microphone=Recorded(),
        transcriber=Counted(),
    )
    await coordinator.start_capture(identity)
    result = await coordinator.finish_capture(identity)
    await coordinator.close()
    return result.state, result.text, len(calls)


@pytest.mark.asyncio
async def test_piper_validates_audio_and_never_overwrites(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.touch()
    (tmp_path / "voice.onnx.json").write_text("{}")
    target = tmp_path / "reply.wav"

    async def piper(argv, **kwargs):
        assert kwargs["stdin"] == b"Hello.\n"
        wav(argv[argv.index("--output_file") + 1], rate=22050)

    monkeypatch.setattr(speech, "run_program", piper)
    provider = speech.Piper(sys.executable, model)
    assert (await provider.synthesize("Hello.", target)).sample_rate == 22050
    original = target.read_bytes()
    with pytest.raises(speech.SpeechUnavailable, match="already exists"):
        await provider.synthesize("Hello.", target)
    assert target.read_bytes() == original


def test_microphone_explicit_capture_limit_and_cancel(tmp_path, monkeypatch):
    class Stop(Exception):
        pass

    class Device:
        callback = None
        closed = False

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            self.callback(bytes(320), 160, None, False)

        def stop(self):
            pass

        def abort(self):
            pass

        def close(self):
            self.closed = True

    class SoundDevice:
        RawInputStream = Device
        CallbackStop = Stop
        CallbackAbort = Stop

    monkeypatch.setitem(sys.modules, "sounddevice", SoundDevice)
    mic = speech.Microphone()
    assert mic.stream is None
    mic.start()
    stream = mic.stream
    with pytest.raises(Stop):
        stream.callback(b"\x00" * (31 * 32000), 31 * 16000, None, False)
    assert len(mic.frames) == 30 * 32000
    assert mic.stop(tmp_path / "limited.wav").duration_seconds == 30
    assert stream.closed and not mic.frames
    mic.start()
    mic.stream.callback(b"\x00" * 3200, 1600, None, False)
    mic.cancel()
    assert mic.stream is None and not mic.frames


def test_microphone_permission_failure_cleans_up(monkeypatch):
    class Denied:
        def __init__(self, **kwargs):
            raise PermissionError("denied")

    class SoundDevice:
        RawInputStream = Denied

    monkeypatch.setitem(sys.modules, "sounddevice", SoundDevice)
    mic = speech.Microphone()
    with pytest.raises(speech.SpeechUnavailable, match="permission was denied"):
        mic.start()
    assert mic.stream is None and not mic.frames


@pytest.mark.asyncio
async def test_exited_parent_descendant_is_owned_and_cannot_hold_cleanup(tmp_path):
    import time

    marker = tmp_path / "descendant-must-not-write"
    child = "import pathlib,sys,time; time.sleep(0.8); pathlib.Path(sys.argv[1]).touch()"
    parent = (
        "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])"
    )
    started = time.monotonic()
    await speech.run_program([sys.executable, "-c", parent, child, str(marker)], timeout=0.5)
    assert time.monotonic() - started < 0.7
    await asyncio.sleep(0.9)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_timeout_kills_descendant_even_when_parent_is_alive(tmp_path):
    marker = tmp_path / "tree-must-not-write"
    child = "import pathlib,sys,time; time.sleep(0.8); pathlib.Path(sys.argv[1]).touch()"
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(30)"
    )
    with pytest.raises(speech.SpeechUnavailable, match="timed out"):
        await speech.run_program([sys.executable, "-c", parent, child, str(marker)], timeout=0.2)
    await asyncio.sleep(0.9)
    assert not marker.exists()


def fake_windows_job(pid, *, assigned=True):
    """A WindowsJob over a recording kernel32 fake, so access rights are checked on any OS."""
    import ctypes
    import types

    from service.lls.speech_process import WindowsJob

    class Ctypes:
        # Pass structures through so the fake can fill THREADENTRY32 fields.
        byref = staticmethod(lambda value: value)

        def __getattr__(self, name):
            return getattr(ctypes, name)

    threads = iter([(5, pid + 1), (7, pid)])

    def next_thread(snapshot, entry):
        entry.thread_id, entry.owner_pid = next(threads, (0, 0))
        return entry.thread_id != 0

    calls, api = [], types.SimpleNamespace()
    results = {
        "OpenProcess": lambda *args: 101,
        "AssignProcessToJobObject": lambda *args: assigned,
        "CreateToolhelp32Snapshot": lambda *args: 102,
        "Thread32First": next_thread,
        "Thread32Next": next_thread,
        "OpenThread": lambda *args: 103,
        "ResumeThread": lambda *args: 1,
        "TerminateJobObject": lambda *args: True,
        "CloseHandle": lambda *args: True,
    }
    for name, result in results.items():
        # Plain functions accept the argtypes/restype assignments made for real ctypes calls.
        def function(*args, name=name, result=result):
            calls.append((name, *args))
            return result(*args)

        setattr(api, name, function)
    job = WindowsJob.__new__(WindowsJob)
    job.ctypes, job.api, job.handle = Ctypes(), api, 100
    return job, calls


def test_windows_job_requests_assignment_and_resume_rights_and_closes_handles():
    job, calls = fake_windows_job(42)
    job.attach_and_resume(42)
    # AssignProcessToJobObject needs PROCESS_SET_QUOTA | PROCESS_TERMINATE (0x0100 | 0x0001),
    # and ResumeThread needs THREAD_SUSPEND_RESUME (0x0002).
    assert ("OpenProcess", 0x0100 | 0x0001, False, 42) in calls
    assert ("AssignProcessToJobObject", 100, 101) in calls
    assert ("OpenThread", 0x0002, False, 7) in calls and ("ResumeThread", 103) in calls
    assert sorted(args[0] for name, *args in calls if name == "CloseHandle") == [101, 102, 103]


def test_windows_job_assignment_failure_closes_handle_and_kills_suspended_child():
    from service.lls.speech_process import ProcessFailure, terminate_tree

    class Suspended:
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = 1

    job, calls = fake_windows_job(42, assigned=False)
    with pytest.raises(ProcessFailure, match="Job Object"):
        job.attach_and_resume(42)
    assert [call[0] for call in calls] == [
        "OpenProcess", "AssignProcessToJobObject", "CloseHandle",
    ]
    assert calls[-1] == ("CloseHandle", 101)
    child = Suspended()
    terminate_tree(child, job)
    assert child.returncode == 1 and job.handle is None
    assert calls[-2:] == [("TerminateJobObject", 100, 1), ("CloseHandle", 100)]


@pytest.mark.parametrize("operation", ["stop", "cancel"])
def test_microphone_device_failure_still_closes_and_clears(operation, tmp_path):
    class FailedDevice:
        closed = False

        def stop(self):
            raise OSError("private driver detail")

        def abort(self):
            raise OSError("private driver detail")

        def close(self):
            self.closed = True

    mic = speech.Microphone()
    device = FailedDevice()
    mic.stream = device
    mic.accepting = True
    mic.frames.extend(b"private audio")
    with pytest.raises(speech.SpeechUnavailable, match="shut down"):
        if operation == "stop":
            mic.stop(tmp_path / "must-not-exist.wav")
        else:
            mic.cancel()
    assert device.closed
    assert mic.stream is None and not mic.frames and not mic.accepting
    assert not (tmp_path / "must-not-exist.wav").exists()


@pytest.mark.asyncio
async def test_callback_playback_cancel_closes_without_blocking_write_worker(tmp_path, monkeypatch):
    import threading
    import types

    entered = threading.Event()
    devices = []

    class Device:
        def __init__(self, **kwargs):
            assert "callback" in kwargs and "finished_callback" in kwargs
            self.closed = False
            self.aborted = False
            devices.append(self)

        def start(self):
            entered.set()

        def write(self, data):
            pytest.fail("Playback must not create a blocking write worker.")

        def abort(self):
            self.aborted = True

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Device))
    task = asyncio.create_task(speech.play_wav(wav(tmp_path / "play.wav")))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert devices[0].aborted and devices[0].closed


@pytest.mark.asyncio
async def test_callback_pcm_padding_and_device_cleanup_error(tmp_path, monkeypatch):
    import types

    class Stop(Exception):
        pass

    devices = []

    class Device:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.finished = kwargs["finished_callback"]
            self.closed = False
            devices.append(self)

        def start(self):
            buffer = bytearray(b"x" * 4000)
            with pytest.raises(Stop):
                self.callback(buffer, 2000, None, False)
            assert buffer == bytes(4000)
            self.finished()

        def abort(self):
            raise OSError("device detached")

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        types.SimpleNamespace(RawOutputStream=Device, CallbackStop=Stop, CallbackAbort=Stop),
    )
    with pytest.raises(speech.SpeechUnavailable, match="shut down"):
        await speech.play_wav(wav(tmp_path / "play.wav"))
    assert devices[0].closed


@pytest.mark.parametrize("stalled_stage", ["construct", "start", "abort", "close"])
@pytest.mark.asyncio
async def test_driver_lifecycle_never_blocks_loop_and_cancel_joins_worker(
    stalled_stage, tmp_path, monkeypatch
):
    import threading
    import types

    entered, release = threading.Event(), threading.Event()
    stages = []
    notifications = []
    event_loop_thread = threading.get_ident()

    class Stop(Exception):
        pass

    class Abort(Exception):
        pass

    def stall(stage):
        assert threading.get_ident() != event_loop_thread
        if stage == stalled_stage:
            entered.set()
            assert release.wait(2), "The event loop could not release the stalled driver."
        stages.append(stage)

    class Device:
        def __init__(self, **kwargs):
            stall("construct")
            self.callback = kwargs["callback"]
            self.finished = kwargs["finished_callback"]

        def start(self):
            stall("start")
            try:
                self.callback(bytearray(4000), 2000, None, False)
            except (Stop, Abort):
                self.finished()

        def abort(self):
            stall("abort")

        def close(self):
            stall("close")

    def started():
        assert threading.get_ident() == event_loop_thread
        notifications.append("started")

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        types.SimpleNamespace(RawOutputStream=Device, CallbackStop=Stop, CallbackAbort=Abort),
    )
    task = asyncio.create_task(speech.play_wav(wav(tmp_path / "audio.wav"), on_started=started))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    # These loop ticks happen while the driver is stalled in a joined worker.
    for _ in range(5):
        await asyncio.sleep(0.002)
    assert stalled_stage not in stages
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stages == ["construct", "start", "abort", "close"]
    if stalled_stage in {"construct", "start"}:
        assert notifications == []
    else:
        assert notifications == ["started"]


@pytest.mark.asyncio
async def test_device_finished_without_pcm_is_not_completed_playback(tmp_path, monkeypatch):
    import types

    class Device:
        def __init__(self, **kwargs):
            self.finished = kwargs["finished_callback"]

        def start(self):
            self.finished()

        def abort(self):
            pass

        def close(self):
            pass

    notifications = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Device))
    with pytest.raises(speech.SpeechUnavailable):
        await speech.play_wav(
            wav(tmp_path / "audio.wav"), on_started=lambda: notifications.append(1)
        )
    assert not notifications
