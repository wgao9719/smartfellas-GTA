"""Input-callback confirmation using real Microphone lifecycle code and fake devices."""

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from service.lls.speech import Microphone, SpeechUnavailable
from service.lls.speech_coordinator import SpeechConfig, SpeechCoordinator, SpeechIdentity


class CallbackAbort(Exception):
    pass


class CallbackStop(Exception):
    pass


@pytest.fixture
def audio_device(monkeypatch):
    state = SimpleNamespace(
        instances=[],
        started=threading.Event(),
        release_start=threading.Event(),
        first_data=None,
        close_error=False,
    )
    state.release_start.set()

    class Device:
        def __init__(self, **kwargs):
            assert kwargs["samplerate"] == 16000
            assert kwargs["channels"] == 1 and kwargs["dtype"] == "int16"
            self.callback = kwargs["callback"]
            self.in_start = False
            self.closed = self.aborted = False
            state.instances.append(self)

        def start(self):
            self.in_start = True
            state.started.set()
            try:
                assert state.release_start.wait(3), "test did not release device start"
                if state.first_data is not None:
                    self.callback(state.first_data, len(state.first_data) // 2, None, False)
            finally:
                self.in_start = False

        def abort(self):
            assert not self.in_start, "cleanup raced the native device-start call"
            self.aborted = True

        def stop(self):
            assert not self.in_start

        def close(self):
            assert not self.in_start
            self.closed = True
            if state.close_error:
                raise OSError("synthetic device close failure")

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            RawInputStream=Device,
            CallbackAbort=CallbackAbort,
            CallbackStop=CallbackStop,
        ),
    )
    return state


async def started(audio_device):
    assert await asyncio.to_thread(audio_device.started.wait, 2)
    return audio_device.instances[-1]


async def test_start_waits_for_first_valid_callback_and_accepts_digital_silence(
    audio_device, tmp_path
):
    mic = Microphone(first_frame_timeout=0.5)
    task = asyncio.create_task(asyncio.to_thread(mic.start))
    device = await started(audio_device)
    assert not task.done() and not mic.frames
    device.callback(bytes(320), 160, None, False)
    await asyncio.wait_for(task, 1)
    assert mic.frames == bytes(320) and mic.accepting
    assert mic.stop(tmp_path / "silence.wav").frames == 160
    assert device.closed and not mic.frames


async def test_no_callback_times_out_and_joins_cleanup(audio_device):
    mic = Microphone(first_frame_timeout=0.03)
    began = time.monotonic()
    with pytest.raises(SpeechUnavailable, match="did not supply input frames"):
        await asyncio.to_thread(mic.start)
    assert time.monotonic() - began < 1
    assert audio_device.instances[0].aborted and audio_device.instances[0].closed
    assert mic.stream is None and not mic.frames and not mic.accepting


@pytest.mark.parametrize(
    "data,count,status",
    [
        (b"", 0, False),
        (b"\x00", 1, False),
        (bytes(4), 1, False),
        (bytes(2), True, False),
        (None, 1, False),
        (bytes(2), 1, True),
    ],
)
async def test_invalid_first_callback_fails_without_recording(audio_device, data, count, status):
    mic = Microphone(first_frame_timeout=0.5)
    task = asyncio.create_task(asyncio.to_thread(mic.start))
    device = await started(audio_device)
    with pytest.raises(CallbackAbort):
        device.callback(data, count, None, status)
    with pytest.raises(SpeechUnavailable, match="frames"):
        await asyncio.wait_for(task, 1)
    assert mic.stream is None and not mic.frames and not mic.accepting
    assert device.aborted and device.closed


async def test_later_callback_error_discards_previously_valid_audio(audio_device, tmp_path):
    audio_device.first_data = bytes(320)
    mic = Microphone()
    await asyncio.to_thread(mic.start)
    with pytest.raises(CallbackAbort):
        audio_device.instances[0].callback(bytes(320), 160, None, True)
    with pytest.raises(SpeechUnavailable, match="lost audio frames"):
        mic.stop(tmp_path / "must-not-exist.wav")
    assert not (tmp_path / "must-not-exist.wav").exists()
    assert not mic.frames and audio_device.instances[0].closed


async def test_direct_cancel_wakes_start_and_stale_callback_cannot_modify_replacement(audio_device):
    mic = Microphone(first_frame_timeout=1)
    task = asyncio.create_task(asyncio.to_thread(mic.start))
    old = await started(audio_device)
    await asyncio.wait_for(asyncio.to_thread(mic.cancel), 0.5)
    with pytest.raises(SpeechUnavailable, match="canceled"):
        await task
    assert old.closed and mic.stream is None and not mic.frames
    audio_device.first_data = b"\x01\x00" * 160
    await asyncio.to_thread(mic.start)
    current = mic.stream
    before = bytes(mic.frames)
    with pytest.raises(CallbackAbort):
        old.callback(bytes(320), 160, None, False)
    assert mic.stream is current and bytes(mic.frames) == before
    mic.cancel()
    assert current.closed


async def test_external_cancellation_before_worker_start_never_opens_device(audio_device):
    canceled = threading.Event()
    canceled.set()
    mic = Microphone()
    with pytest.raises(SpeechUnavailable, match="canceled"):
        await asyncio.to_thread(mic.start, cancel_event=canceled)
    assert audio_device.instances == [] and mic.stream is None and not mic.frames


@pytest.mark.parametrize("identity_changes", [False, True])
async def test_recording_receipt_requires_valid_frames_for_current_identity(
    audio_device,
    identity_changes,
):
    mic = Microphone(first_frame_timeout=1)
    identity = SpeechIdentity("session", 1, "scene", "actor", 0, "instance")
    current = True
    coordinator = SpeechCoordinator(
        SpeechConfig(enabled=True),
        is_current=lambda _: current,
        microphone=mic,
        transcriber=object(),
    )
    task = asyncio.create_task(coordinator.start_capture(identity))
    device = await started(audio_device)
    assert not task.done() and not coordinator.receipts
    current = not identity_changes
    device.callback(bytes(320), 160, None, False)
    receipt = await asyncio.wait_for(task, 0.5)
    assert receipt.state == ("canceled" if identity_changes else "recording")
    if identity_changes:
        assert device.closed and not mic.frames
        assert not any(item.state == "recording" for item in coordinator.receipts)
    else:
        assert not device.closed and mic.frames == bytes(320)
    await coordinator.close()


@pytest.mark.parametrize("blocked_native_start", [False, True])
async def test_coordinator_cancellation_joins_real_microphone_start(
    audio_device, blocked_native_start
):
    if blocked_native_start:
        audio_device.release_start.clear()
    mic = Microphone(first_frame_timeout=1)
    identity = SpeechIdentity("session", 1, "scene", "actor", 0, "instance")
    coordinator = SpeechCoordinator(
        SpeechConfig(enabled=True),
        is_current=lambda _: True,
        microphone=mic,
        transcriber=object(),
    )
    task = asyncio.create_task(coordinator.start_capture(identity))
    device = await started(audio_device)
    assert not task.done()
    assert all(receipt.state != "recording" for receipt in coordinator.receipts)
    cancellation = asyncio.create_task(coordinator.cancel(identity))
    await asyncio.sleep(0)
    if blocked_native_start:
        assert not cancellation.done() and not device.closed
        audio_device.release_start.set()
    await asyncio.wait_for(cancellation, 0.5)
    receipt = await task
    assert receipt.state == "canceled"
    assert not any(receipt.state == "recording" for receipt in coordinator.receipts)
    assert device.aborted and device.closed and not mic.frames and mic.stream is None
    await coordinator.close()


async def test_canceled_caller_signals_frame_wait_before_joining(audio_device):
    mic = Microphone(first_frame_timeout=1)
    identity = SpeechIdentity("session", 1, "scene", "actor", 0, "instance")
    coordinator = SpeechCoordinator(
        SpeechConfig(enabled=True),
        is_current=lambda _: True,
        microphone=mic,
        transcriber=object(),
    )
    task = asyncio.create_task(coordinator.start_capture(identity))
    device = await started(audio_device)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)
    assert device.aborted and device.closed and mic.stream is None and not mic.frames
    await coordinator.close()


async def test_start_cleanup_failure_prevents_device_reuse(audio_device):
    audio_device.close_error = True
    mic = Microphone(first_frame_timeout=0.01)
    with pytest.raises(SpeechUnavailable, match="shut down"):
        await asyncio.to_thread(mic.start)
    with pytest.raises(SpeechUnavailable, match="cleanup failed"):
        await asyncio.to_thread(mic.start)
    assert len(audio_device.instances) == 1 and not mic.frames and not mic.accepting


async def test_callback_error_after_recording_emits_failure_and_closes_capture(audio_device):
    audio_device.first_data = bytes(320)
    mic = Microphone()
    identity = SpeechIdentity("session", 1, "scene", "actor", 0, "instance")
    coordinator = SpeechCoordinator(
        SpeechConfig(enabled=True),
        is_current=lambda _: True,
        microphone=mic,
        transcriber=object(),
    )
    assert (await coordinator.start_capture(identity)).state == "recording"
    watchdog = coordinator._active.timer
    device = audio_device.instances[0]
    with pytest.raises(CallbackAbort):
        device.callback(bytes(320), 160, None, True)
    await asyncio.wait_for(watchdog, 0.5)
    assert coordinator.receipts[-1].state == "failed"
    assert coordinator.receipts[-1].reason == "microphone_capture_error"
    assert device.aborted and device.closed and mic.stream is None and not mic.frames
    await coordinator.close()
