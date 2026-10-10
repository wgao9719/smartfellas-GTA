import asyncio
import threading
import wave
from dataclasses import replace

import pytest

from service.lls.speech import SpeechUnavailable, Transcript, inspect_wav
from service.lls.speech_coordinator import SpeechConfig, SpeechCoordinator, SpeechIdentity

IDENTITY = SpeechIdentity("session_1", 1, "scene_1", "actor_1", 0, "instance_1")


def wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(3200))
    return inspect_wav(path)


class Microphone:
    active = False
    canceled = False
    paths = None

    def start(self):
        self.active = True

    def stop(self, destination):
        self.active = False
        self.paths = [destination]
        return wav(destination)

    def cancel(self):
        self.active = False
        self.canceled = True


class Transcriber:
    confidence = None

    async def transcribe(self, audio):
        assert audio.is_file()
        return Transcript("Hello there.", self.confidence, "mock", 1)


class Synthesizer:
    paths = None

    async def synthesize(self, text, destination):
        self.paths = [destination]
        return wav(destination)


async def playback(audio, *, on_started):
    assert audio.is_file()
    on_started()


def coordinator(**kwargs):
    options = {
        "config": SpeechConfig(enabled=True),
        "is_current": lambda identity: identity == IDENTITY,
        "microphone": Microphone(),
        "transcriber": Transcriber(),
        "synthesizer": Synthesizer(),
        "playback": playback,
    }
    options.update(kwargs)
    return SpeechCoordinator(**options)


@pytest.mark.asyncio
async def test_disabled_default_does_not_construct_providers_or_open_audio():
    speech = SpeechCoordinator.local(SpeechConfig(), is_current=lambda identity: True)
    assert speech.microphone is speech.transcriber is speech.synthesizer is None
    with pytest.raises(SpeechUnavailable):
        await speech.start_capture(IDENTITY)
    enabled = coordinator(config=SpeechConfig())
    with pytest.raises(SpeechUnavailable, match="disabled"):
        await enabled.start_capture(IDENTITY)
    assert not enabled.microphone.active
    with pytest.raises(SpeechUnavailable, match="Explicit Whisper"):
        SpeechCoordinator.local(SpeechConfig(enabled=True), is_current=lambda identity: True)


@pytest.mark.parametrize("confidence", [None, 0.1, 0.849, 0.85, 1.0])
@pytest.mark.asyncio
async def test_transcript_confidence_gate_and_raw_audio_cleanup(confidence):
    transcriber = Transcriber()
    transcriber.confidence = confidence
    speech = coordinator(transcriber=transcriber)
    started = await speech.start_capture(IDENTITY)
    assert started.state == "recording" and speech.microphone.active
    result = await speech.finish_capture(IDENTITY)
    assert result.state == "completed" and result.text == "Hello there."
    assert result.confidence == confidence
    assert result.action_eligible is (confidence is not None and confidence >= 0.85)
    assert result.delivered_text == ""
    assert not speech.microphone.active
    assert not speech.microphone.paths[0].parent.exists()
    await speech.close()


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.01, 1.01, True, "0.99"])
@pytest.mark.asyncio
async def test_invalid_confidence_never_authorizes_actions(confidence):
    transcriber = Transcriber()
    transcriber.confidence = confidence
    speech = coordinator(transcriber=transcriber)
    await speech.start_capture(IDENTITY)
    result = await speech.finish_capture(IDENTITY)
    assert result.state == "failed" and not result.action_eligible
    assert not result.text and not speech.microphone.paths[0].exists()


@pytest.mark.asyncio
async def test_receipts_follow_actual_start_and_playback_completion():
    accepted = asyncio.Event()
    drain = asyncio.Event()
    notifications = []

    async def output(audio, *, on_started):
        accepted.set()
        await asyncio.sleep(0)
        on_started()
        await drain.wait()

    speech = coordinator(playback=output, on_started=notifications.append)
    task = asyncio.create_task(speech.speak(IDENTITY, "Hello."))
    await accepted.wait()
    await asyncio.sleep(0)
    assert len(notifications) == 1 and notifications[0].state == "started"
    assert notifications[0].audio_duration_ms == 100
    assert notifications[0].delivered_text == ""
    assert not task.done()
    drain.set()
    result = await task
    assert result.state == "completed" and result.delivered_text == "Hello."
    assert result.playback_started and not result.action_eligible
    assert not speech.synthesizer.paths[0].parent.exists()


@pytest.mark.asyncio
async def test_synthesis_success_without_playback_start_is_not_delivery():
    async def broken_output(audio, *, on_started):
        return None

    speech = coordinator(playback=broken_output)
    result = await speech.speak(IDENTITY, "Hello.")
    assert result.state == "failed" and not result.playback_started
    assert result.delivered_text == ""


@pytest.mark.asyncio
async def test_playback_cancel_joins_teardown_and_preserves_no_full_text_delivery():
    started, cleanup = asyncio.Event(), asyncio.Event()

    async def output(audio, *, on_started):
        on_started()
        started.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.01)
            cleanup.set()

    speech = coordinator(playback=output)
    task = asyncio.create_task(speech.speak(IDENTITY, "Only partly played."))
    await started.wait()
    result = await speech.cancel(IDENTITY)
    assert cleanup.is_set()
    assert result.state == "canceled" and result.playback_started
    assert result.delivered_text == ""
    assert await task == result
    assert not speech.synthesizer.paths[0].parent.exists()


@pytest.mark.asyncio
async def test_identity_change_during_synthesis_prevents_playback():
    entered, cleaned = asyncio.Event(), asyncio.Event()
    current = True

    class BlockingSynthesizer:
        async def synthesize(self, text, destination):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

    async def unexpected_output(audio, *, on_started):
        pytest.fail("A stale identity reached playback.")

    speech = coordinator(
        synthesizer=BlockingSynthesizer(),
        playback=unexpected_output,
        is_current=lambda identity: current,
    )
    task = asyncio.create_task(speech.speak(IDENTITY, "Hello."))
    await entered.wait()
    current = False
    result = await asyncio.wait_for(task, 1)
    assert result.state == "canceled" and result.reason == "identity_changed"
    assert cleaned.is_set() and result.delivered_text == ""


@pytest.mark.asyncio
async def test_same_actor_new_runtime_instance_invalidates_capture():
    current_instance = "instance_1"
    speech = coordinator(
        is_current=lambda identity: identity.runtime_instance_id == current_instance
    )
    await speech.start_capture(IDENTITY)
    current_instance = "instance_2"
    result = await speech.finish_capture(IDENTITY)
    assert result.state == "canceled" and result.reason == "identity_changed"
    assert not result.action_eligible and not speech.microphone.active
    assert speech.microphone.paths is None


@pytest.mark.asyncio
async def test_capture_timeout_releases_the_device_and_lane():
    speech = coordinator(config=SpeechConfig(enabled=True, capture_seconds=0.03))
    try:
        await speech.start_capture(IDENTITY)
        # A worker can set microphone.canceled before its event-loop receipt.
        # Wait for the coordinator's terminal boundary, not an internal device flag.
        async with asyncio.timeout(2):
            while speech.receipts[-1].state == "recording":
                await asyncio.sleep(0.001)
        assert not speech.microphone.active
        assert speech.receipts[-1].state == "canceled"
        assert speech.receipts[-1].reason == "capture_timeout"
        assert (await speech.speak(IDENTITY, "Hello.")).state == "completed"
    finally:
        await speech.close()
    assert not speech._watchdogs


@pytest.mark.asyncio
async def test_transcribing_callback_waits_for_device_stop_and_contains_no_text():
    entered, release = threading.Event(), threading.Event()
    notifications = []

    class SlowStop(Microphone):
        def stop(self, destination):
            entered.set()
            assert release.wait(2)
            return super().stop(destination)

    speech = coordinator(microphone=SlowStop(), on_transcribing=notifications.append)
    task = None
    try:
        await speech.start_capture(IDENTITY)
        task = asyncio.create_task(speech.finish_capture(IDENTITY))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert speech.microphone.active and not notifications
        release.set()
        result = await task
        assert result.state == "completed"
        assert len(notifications) == 1
        assert notifications[0].kind == "transcript" and notifications[0].state == "started"
        assert notifications[0].text == notifications[0].delivered_text == ""
        assert not notifications[0].action_eligible
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await speech.close()


@pytest.mark.asyncio
async def test_overlap_and_wrong_capture_identity_are_rejected():
    speech = coordinator()
    await speech.start_capture(IDENTITY)
    with pytest.raises(SpeechUnavailable, match="busy"):
        await speech.speak(IDENTITY, "Hello.")
    wrong = replace(IDENTITY, scene_id="scene_2")
    assert await speech.cancel(wrong) is None
    with pytest.raises(SpeechUnavailable, match="matching"):
        await speech.finish_capture(wrong)
    assert speech.microphone.active
    await speech.close()
    assert not speech.microphone.active
    with pytest.raises(SpeechUnavailable, match="closed"):
        await speech.start_capture(IDENTITY)


@pytest.mark.asyncio
async def test_failed_device_cleanup_returns_failure_and_disables_capture():
    class FailedMicrophone(Microphone):
        def cancel(self):
            super().cancel()
            raise OSError("private driver details")

    speech = coordinator(microphone=FailedMicrophone())
    await speech.start_capture(IDENTITY)
    result = await speech.cancel(IDENTITY)
    assert result.state == "failed" and result.reason == "microphone_cleanup_failed"
    assert not speech.microphone.active
    with pytest.raises(SpeechUnavailable, match="cleanup failed"):
        await speech.start_capture(IDENTITY)


@pytest.mark.asyncio
async def test_finish_cannot_race_cancellation_device_cleanup():
    entered, release = threading.Event(), threading.Event()

    class SlowCleanup(Microphone):
        def cancel(self):
            entered.set()
            assert release.wait(2)
            super().cancel()

    speech = coordinator(microphone=SlowCleanup())
    await speech.start_capture(IDENTITY)
    task = asyncio.create_task(speech.cancel(IDENTITY))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    try:
        with pytest.raises(SpeechUnavailable, match="matching"):
            await speech.finish_capture(IDENTITY)
    finally:
        release.set()
    assert (await task).state == "canceled"


@pytest.mark.asyncio
async def test_cancel_during_device_start_joins_worker_before_device_cleanup():
    entered, release = threading.Event(), threading.Event()

    class SlowMicrophone(Microphone):
        def start(self):
            entered.set()
            assert release.wait(2)
            super().start()

        def cancel(self):
            assert release.is_set(), "Device cleanup raced the device-start worker."
            super().cancel()

    speech = coordinator(microphone=SlowMicrophone())
    task = asyncio.create_task(speech.start_capture(IDENTITY))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert speech.microphone.canceled and not speech.microphone.active
    assert (await speech.speak(IDENTITY, "Hello.")).state == "completed"


@pytest.mark.asyncio
async def test_transcription_timeout_joins_provider_and_deletes_audio():
    paths = []
    cleaned = asyncio.Event()

    class SlowTranscriber:
        async def transcribe(self, audio):
            paths.append(audio)
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.01)
                assert audio.exists()
                cleaned.set()

    speech = coordinator(
        transcriber=SlowTranscriber(),
        config=SpeechConfig(enabled=True, transcription_seconds=0.03),
    )
    await speech.start_capture(IDENTITY)
    result = await speech.finish_capture(IDENTITY)
    assert result.state == "failed" and not result.action_eligible
    assert cleaned.is_set() and not paths[0].parent.exists()


@pytest.mark.asyncio
async def test_receipt_history_is_bounded_and_stale_identity_fails_before_work():
    speech = coordinator(config=SpeechConfig(enabled=True, receipt_limit=3))
    for _ in range(5):
        await speech.speak(IDENTITY, "Hello.")
    assert len(speech.receipts) == 3
    with pytest.raises(SpeechUnavailable, match="identity"):
        await speech.speak(replace(IDENTITY, epoch=1), "Hello.")


@pytest.mark.asyncio
async def test_forget_cancels_matching_work_and_erases_only_matching_receipts():
    speech = coordinator(is_current=lambda identity: True)
    other_actor = replace(IDENTITY, actor_id="actor_2", runtime_instance_id="instance_2")
    other_session = replace(IDENTITY, session_id="session_2")
    await speech.speak(other_actor, "Keep actor two.")
    await speech.speak(other_session, "Keep session two.")
    await speech.speak(IDENTITY, "Forget this text.")
    await speech.start_capture(IDENTITY)
    await speech.forget(session_id=IDENTITY.session_id, actor_id=IDENTITY.actor_id)
    assert not speech.microphone.active
    assert all(receipt.identity != IDENTITY for receipt in speech.receipts)
    assert {receipt.identity for receipt in speech.receipts} == {other_actor, other_session}
    await speech.forget(session_id=IDENTITY.session_id)
    assert {receipt.identity for receipt in speech.receipts} == {other_session}
    await speech.close()
    assert speech.receipts == ()


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": "true"},
        {"minimum_action_confidence": float("nan")},
        {"capture_seconds": 31},
        {"max_text_chars": 1201},
        {"receipt_limit": 0},
        {"transcription_seconds": True},
        {"tts_provider": "remote"},
    ],
)
def test_configuration_rejects_invalid_bounds(values):
    with pytest.raises(ValueError):
        SpeechConfig(**values)
