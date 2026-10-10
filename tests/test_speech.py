import asyncio
import json
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
