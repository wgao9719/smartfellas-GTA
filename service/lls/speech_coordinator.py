"""Optional, single-lane speech orchestration, independent of the HTTP runtime.

No device opens at construction. The runtime must supply an identity validator
and cancel this coordinator when a scene, session, actor, or generation changes.
The polling validator is a second guard, not a replacement for explicit cancel.
Audio belongs to the service host. No audio crosses the game bridge.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Literal

from .speech import (
    Microphone,
    Piper,
    SpeechUnavailable,
    WhisperCpp,
    WindowsSapi,
    audio_level,
    inspect_wav,
    play_wav,
)


@dataclass(frozen=True)
class SpeechConfig:
    enabled: bool = False
    whisper_executable: str = ""
    whisper_model: str = ""
    tts_provider: Literal["disabled", "piper", "sapi"] = "disabled"
    piper_executable: str = ""
    piper_model: str = ""
    input_device: int | str | None = None
    output_device: int | str | None = None
    minimum_action_confidence: float = 0.85
    # Finite confidence below this is too uncertain to answer. Unknown confidence is allowed.
    minimum_dialogue_confidence: float = 0.3
    # Captures with RMS below this never reach the recognizer, which can invent words.
    silence_dbfs: float = -60
    capture_seconds: float = 30
    transcription_seconds: float = 60
    synthesis_seconds: float = 60
    playback_seconds: float = 35
    max_text_chars: int = 1200
    receipt_limit: int = 128

    def __post_init__(self):
        if type(self.enabled) is not bool or self.tts_provider not in {"disabled", "piper", "sapi"}:
            raise ValueError("invalid speech configuration")
        for name, minimum, maximum in (
            ("minimum_action_confidence", 0, 1),
            ("minimum_dialogue_confidence", 0, 1),
            ("silence_dbfs", -96, -20),
            ("capture_seconds", 0.01, 30),
            ("transcription_seconds", 0.01, 120),
            ("synthesis_seconds", 0.01, 120),
            ("playback_seconds", 0.01, 35),
        ):
            value = getattr(self, name)
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"invalid {name}")
        if self.minimum_dialogue_confidence > self.minimum_action_confidence:
            raise ValueError("dialogue confidence cannot exceed action confidence")
        for name, minimum, maximum in (("max_text_chars", 1, 1200), ("receipt_limit", 1, 256)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"invalid {name}")
        for name in ("whisper_executable", "whisper_model", "piper_executable", "piper_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > 4096 or "\0" in value:
                raise ValueError(f"invalid {name}")
        for name in ("input_device", "output_device"):
            value = getattr(self, name)
            if value is not None and not (
                (type(value) is int and value >= 0)
                or (isinstance(value, str) and 1 <= len(value) <= 256 and "\0" not in value)
            ):
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class SpeechIdentity:
    session_id: str
    runtime_generation: int
    scene_id: str
    actor_id: str
    epoch: int
    runtime_instance_id: str

    def __post_init__(self):
        for value in (self.session_id, self.scene_id, self.actor_id, self.runtime_instance_id):
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError("invalid speech identity")
        for value in (self.runtime_generation, self.epoch):
            if type(value) is not int or value < 0:
                raise ValueError("invalid speech generation or epoch")


@dataclass(frozen=True)
class SpeechReceipt:
    identity: SpeechIdentity
    kind: Literal["capture", "transcript", "playback"]
    state: Literal["recording", "started", "completed", "canceled", "failed"]
    elapsed_ms: int
    text: str = ""
    delivered_text: str = ""
    confidence: float | None = None
    action_eligible: bool = False
    playback_started: bool = False
    audio_duration_ms: int | None = None
    reason: str = ""


@dataclass
class _Operation:
    identity: SpeechIdentity
    kind: str
    began: float
    task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None
    timer: asyncio.Task | None = None
    recording: bool = False
    canceled: bool = False
    reason: str = "canceled"
    playback_started: bool = False
    audio_duration_ms: int | None = None


class _IdentityChanged(Exception):
    pass


async def _join(task):
    """Join even if the caller receives another cancellation during cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, Exception):
            pass
    if not task.cancelled():
        task.exception()


async def _thread(function, *args, on_cancel=None):
    # A canceled asyncio waiter does not stop a device call. Join before another
    # operation can touch that device. Driver shutdown can exceed our deadlines.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        await _join(task)
        raise


class SpeechCoordinator:
    """Reject overlapping work instead of accumulating audio or subprocesses.

    Injected providers must finish teardown when their awaitable is canceled.
    Playback must call ``on_started`` after accepting PCM and return only after
    draining its output. Receipts establish software playback, not audibility.
    ``action_eligible`` is only a confidence gate, never action authorization.
    """

    def __init__(
        self,
        config: SpeechConfig,
        *,
        is_current: Callable[[SpeechIdentity], bool],
        microphone=None,
        transcriber=None,
        synthesizer=None,
        playback=None,
        on_started: Callable[[SpeechReceipt], None] | None = None,
        on_transcribing: Callable[[SpeechReceipt], None] | None = None,
    ):
        self.config = config
        self.is_current = is_current
        self.microphone = microphone
        self.transcriber = transcriber
        self.synthesizer = synthesizer
        self.playback = playback
        self.on_started = on_started
        self.on_transcribing = on_transcribing
        self._watchdogs: set[asyncio.Task] = set()
        self._receipts = deque(maxlen=config.receipt_limit)
        self._active: _Operation | None = None
        self._closed = False
        self._microphone_failed = False

    @classmethod
    def local(cls, config: SpeechConfig, *, is_current, on_started=None, on_transcribing=None):
        """Build explicit local providers without downloading or opening devices."""
        microphone = transcriber = synthesizer = playback = None
        if config.enabled:
            if not config.whisper_executable or not config.whisper_model:
                raise SpeechUnavailable("Explicit Whisper executable and model paths are required.")
            microphone = Microphone(config.input_device)
            transcriber = WhisperCpp(config.whisper_executable, config.whisper_model)
            if config.tts_provider == "piper":
                if not config.piper_executable or not config.piper_model:
                    raise SpeechUnavailable(
                        "Explicit Piper executable and model paths are required."
                    )
                synthesizer = Piper(config.piper_executable, config.piper_model)
            elif config.tts_provider == "sapi":
                synthesizer = WindowsSapi()
            playback = partial(play_wav, device=config.output_device)
        return cls(
            config,
            is_current=is_current,
            microphone=microphone,
            transcriber=transcriber,
            synthesizer=synthesizer,
            playback=playback,
            on_started=on_started,
            on_transcribing=on_transcribing,
        )

    @property
    def receipts(self):
        return tuple(self._receipts)

    def _check(self, operation):
        if self._closed or operation.canceled or not self._identity_current(operation.identity):
            raise _IdentityChanged

    def _identity_current(self, identity):
        try:
            return self.is_current(identity) is True
        except Exception:
            return False

    def _begin(self, identity, kind):
        if not self.config.enabled or self._closed:
            raise SpeechUnavailable("Speech is disabled or closed.")
        if self._active is not None:
            raise SpeechUnavailable("The speech lane is busy.")
        operation = _Operation(identity, kind, time.monotonic())
        try:
            self._check(operation)
        except _IdentityChanged as exc:
            raise SpeechUnavailable("Speech identity is no longer current.") from exc
        self._active = operation
        return operation

    def _receipt(self, operation, state, **values):
        receipt = SpeechReceipt(
            operation.identity,
            operation.kind,
            state,
            round((time.monotonic() - operation.began) * 1000),
            playback_started=operation.playback_started,
            audio_duration_ms=operation.audio_duration_ms,
            **values,
        )
        self._receipts.append(receipt)
        return receipt

    def _release(self, operation):
        if operation.timer is not None:
            operation.timer.cancel()
            operation.timer = None
        if self._active is operation:
            self._active = None

    async def _wait(self, operation, awaitable, seconds):
        task = asyncio.create_task(awaitable)
        deadline = time.monotonic() + seconds
        try:
            while not task.done():
                self._check(operation)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait({task}, timeout=min(0.05, remaining))
            self._check(operation)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await _join(task)

    async def _run(self, operation, coroutine):
        operation.task = asyncio.create_task(coroutine)
        try:
            return await asyncio.shield(operation.task)
        except asyncio.CancelledError:
            await self._cancel_operation(operation)
            raise
        finally:
            if operation.task is not None and operation.task.done():
                operation.task = None

    async def start_capture(self, identity: SpeechIdentity):
        if self.microphone is None or self.transcriber is None:
            raise SpeechUnavailable("Microphone and transcription providers are unavailable.")
        if self._microphone_failed:
            raise SpeechUnavailable("Microphone cleanup failed. Recreate the audio provider.")
        operation = self._begin(identity, "capture")
        return await self._run(operation, self._start_capture(operation))

    async def _start_capture(self, operation):
        try:
            self._check(operation)
            if isinstance(self.microphone, Microphone):
                canceled = threading.Event()
                await _thread(
                    partial(self.microphone.start, cancel_event=canceled),
                    on_cancel=canceled.set,
                )
            else:
                await _thread(self.microphone.start)
            self._check(operation)
            operation.recording = True
            operation.timer = asyncio.create_task(self._expire_capture(operation))
            self._watchdogs.add(operation.timer)
            operation.timer.add_done_callback(self._watchdogs.discard)
            return self._receipt(operation, "recording")
        except (asyncio.CancelledError, _IdentityChanged) as exc:
            state, reason = "canceled", self._cancel_reason(operation, exc)
        except Exception:
            state, reason = "failed", "microphone_unavailable"
        finally:
            if not operation.recording:
                clean = await self._clean_microphone()
                self._release(operation)
        if not clean:
            state, reason = "failed", "microphone_cleanup_failed"
        return self._receipt(operation, state, reason=reason)

    async def _expire_capture(self, operation):
        try:
            deadline = time.monotonic() + self.config.capture_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
                if not self._identity_current(operation.identity):
                    operation.reason = "identity_changed"
                    break
                if isinstance(self.microphone, Microphone) and self.microphone.capture_error:
                    operation.reason = "microphone_capture_error"
                    break
            else:
                operation.reason = "capture_timeout"
            operation.timer = None
            await self._cancel_operation(operation)
        except asyncio.CancelledError:
            pass

    async def finish_capture(self, identity: SpeechIdentity):
        operation = self._active
        if (
            operation is None
            or operation.identity != identity
            or not operation.recording
            or operation.canceled
            or operation.cancel_task is not None
            or operation.task is not None
        ):
            raise SpeechUnavailable("No matching microphone capture is ready to stop.")
        operation.recording = False
        operation.kind = "transcript"
        if operation.timer is not None:
            operation.timer.cancel()
            operation.timer = None
        return await self._run(operation, self._finish_capture(operation))

    async def _finish_capture(self, operation):
        try:
            self._check(operation)
            with tempfile.TemporaryDirectory(prefix="lls-capture-") as temporary:
                audio = Path(temporary) / "capture.wav"
                await _thread(self.microphone.stop, audio)
                self._check(operation)
                rms_dbfs, _ = await _thread(audio_level, audio)
                self._check(operation)
                transcript = None
                if rms_dbfs >= self.config.silence_dbfs:
                    receipt = self._receipt(operation, "started")
                    if self.on_transcribing is not None:
                        self.on_transcribing(receipt)
                    transcript = await self._wait(
                        operation,
                        self.transcriber.transcribe(audio),
                        self.config.transcription_seconds,
                    )
                state, values = self._transcript_result(transcript)
        except (asyncio.CancelledError, _IdentityChanged) as exc:
            state, values = "canceled", {"reason": self._cancel_reason(operation, exc)}
        except Exception:
            state, values = "failed", {"reason": "transcription_unavailable"}
        finally:
            clean = await self._clean_microphone()
            self._release(operation)
        if not clean:
            state, values = "failed", {"reason": "microphone_cleanup_failed"}
        return self._receipt(operation, state, **values)

    def _transcript_result(self, transcript):
        """Silence, no words, and too-uncertain words share the runtime's no-turn path."""
        if transcript is None:
            return "failed", {"reason": "empty_transcript"}
        confidence = transcript.confidence
        if not isinstance(transcript.text, str) or len(transcript.text.strip()) > 4000:
            raise ValueError("invalid transcript")
        if confidence is not None and (
            type(confidence) not in {int, float}
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("invalid transcript confidence")
        if not transcript.text.strip() or (
            confidence is not None and confidence < self.config.minimum_dialogue_confidence
        ):
            return "failed", {"reason": "empty_transcript"}
        return "completed", {
            "text": transcript.text.strip(),
            "confidence": confidence,
            "action_eligible": (
                confidence is not None and confidence >= self.config.minimum_action_confidence
            ),
        }

    async def speak(self, identity: SpeechIdentity, text: str):
        if self.synthesizer is None or self.playback is None:
            raise SpeechUnavailable("Synthesis and playback providers are unavailable.")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= self.config.max_text_chars:
            raise SpeechUnavailable("Speech text exceeds the configured bounds.")
        operation = self._begin(identity, "playback")
        return await self._run(operation, self._speak(operation, text))

    async def _speak(self, operation, text):
        def started():
            self._check(operation)
            if not operation.playback_started:
                operation.playback_started = True
                receipt = self._receipt(operation, "started", text=text)
                if self.on_started is not None:
                    self.on_started(receipt)

        try:
            with tempfile.TemporaryDirectory(prefix="lls-playback-") as temporary:
                audio = Path(temporary) / "reply.wav"
                await self._wait(
                    operation,
                    self.synthesizer.synthesize(text, audio),
                    self.config.synthesis_seconds,
                )
                operation.audio_duration_ms = max(
                    1, math.ceil(inspect_wav(audio).duration_seconds * 1000)
                )
                await self._wait(
                    operation,
                    self.playback(audio, on_started=started),
                    self.config.playback_seconds,
                )
                if not operation.playback_started:
                    raise ValueError("playback did not acknowledge submitted frames")
                return self._receipt(operation, "completed", text=text, delivered_text=text)
        except (asyncio.CancelledError, _IdentityChanged) as exc:
            return self._receipt(
                operation, "canceled", text=text, reason=self._cancel_reason(operation, exc)
            )
        except Exception:
            return self._receipt(operation, "failed", text=text, reason="playback_unavailable")
        finally:
            self._release(operation)

    @staticmethod
    def _cancel_reason(operation, error):
        return "identity_changed" if isinstance(error, _IdentityChanged) else operation.reason

    async def _clean_microphone(self):
        try:
            await _thread(self.microphone.cancel)
            return True
        except Exception:
            # Microphone.cancel clears captured PCM even when device close fails.
            self._microphone_failed = True
            return False

    async def _cancel_recording(self, operation):
        try:
            if not await self._clean_microphone():
                return self._receipt(operation, "failed", reason="microphone_cleanup_failed")
            if operation.reason == "microphone_capture_error":
                return self._receipt(operation, "failed", reason=operation.reason)
            return self._receipt(operation, "canceled", reason=operation.reason)
        finally:
            self._release(operation)

    async def _cancel_operation(self, operation):
        if self._active is not operation:
            return None
        if operation.cancel_task is None:
            operation.cancel_task = asyncio.create_task(self._do_cancel(operation))
        await _join(operation.cancel_task)
        return operation.cancel_task.result()

    async def _do_cancel(self, operation):
        if operation.timer is not None:
            operation.timer.cancel()
            operation.timer = None
        operation.canceled = True
        task = operation.task
        if task is not None:
            if not task.done():
                task.cancel()
            await _join(task)
        # A just-created task can be canceled before its finally block starts.
        # A completed capture-start task leaves the microphone recording.
        if self._active is operation:
            if operation.kind in {"capture", "transcript"}:
                return await self._cancel_recording(operation)
            self._release(operation)
            return self._receipt(operation, "canceled", reason=operation.reason)
        if task is not None:
            return None if task.cancelled() else task.result()
        return None

    async def cancel(self, identity: SpeechIdentity | None = None):
        operation = self._active
        if operation is None or (identity is not None and operation.identity != identity):
            return None
        return await self._cancel_operation(operation)

    async def close(self):
        self._closed = True
        try:
            return await self.cancel()
        finally:
            for task in tuple(self._watchdogs):
                task.cancel()
                await _join(task)
                self._watchdogs.discard(task)
            self._receipts.clear()

    async def forget(self, *, session_id: str, actor_id: str | None = None):
        """Cancel matching work, then erase its retained text before returning.

        The runtime must invalidate matching identities before calling this API,
        so new work cannot race an administrative reset or session erasure.
        """

        def matches(identity):
            return identity.session_id == session_id and (
                actor_id is None or identity.actor_id == actor_id
            )

        operation = self._active
        if operation is not None and matches(operation.identity):
            await self._cancel_operation(operation)
        retained = [receipt for receipt in self._receipts if not matches(receipt.identity)]
        self._receipts.clear()
        self._receipts.extend(retained)
