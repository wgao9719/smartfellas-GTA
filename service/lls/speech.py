"""Optional local speech probes. Importing this module never opens an audio device.

No model weights or third-party executables are downloaded or bundled. Callers
must select trusted local programs and licensed model assets explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import operator
import os
import re
import sys
import tempfile
import threading
import time
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

MAX_AUDIO_SECONDS = 30
MAX_PROCESS_OUTPUT = 2 * 1024 * 1024
# Speech programs inherit no secrets, proxies, or service settings. Windows keeps what CPython,
# .NET, PowerShell, COM/SAPI registrations (REG_EXPAND_SZ paths), and native tools resolve.
WINDOWS_ENVIRONMENT = frozenset(
    "SYSTEMROOT WINDIR SYSTEMDRIVE TEMP TMP PATH PATHEXT COMSPEC USERPROFILE LOCALAPPDATA APPDATA "
    "PROGRAMDATA ALLUSERSPROFILE PROGRAMFILES PROGRAMFILES(X86) PROGRAMW6432 COMMONPROGRAMFILES "
    "COMMONPROGRAMFILES(X86) COMMONPROGRAMW6432 NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE".split()
)
POSIX_ENVIRONMENT = frozenset({"PATH", "HOME", "TMPDIR", "LANG"})
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "PROXY")
# PortAudio lists each Windows endpoint once per host API. A name resolves within one of them.
WINDOWS_HOST_APIS = ("Windows WASAPI", "MME", "Windows DirectSound", "Windows WDM-KS")
ANNOTATION = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")


class SpeechUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None
    provider: str
    elapsed_ms: int


def inspect_wav(path: str | Path) -> AudioInfo:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise SpeechUnavailable("Audio file is missing or exceeds 16 MiB.")
    try:
        with wave.open(str(path), "rb") as source:
            rate, channels, frames = (
                source.getframerate(),
                source.getnchannels(),
                source.getnframes(),
            )
            if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                raise SpeechUnavailable("Use uncompressed 16-bit PCM WAV audio.")
            if rate not in {16000, 22050, 24000, 44100, 48000} or channels not in {1, 2}:
                raise SpeechUnavailable("Unsupported audio format.")
            if not 0 < frames <= rate * MAX_AUDIO_SECONDS:
                raise SpeechUnavailable("Audio must contain between 0 and 30 seconds.")
            if len(source.readframes(frames)) != frames * channels * 2:
                raise SpeechUnavailable("Audio file is truncated.")
    except (wave.Error, EOFError) as exc:
        raise SpeechUnavailable("Invalid WAV audio.") from exc
    return AudioInfo(rate, channels, frames, frames / rate)


def audio_level(path: str | Path) -> tuple[float, float]:
    """RMS and peak dBFS of 16-bit PCM. Digital silence is -inf. No audio is kept or logged."""
    info = inspect_wav(path)
    with wave.open(str(path), "rb") as source:
        samples = array("h", source.readframes(info.frames))
    if sys.byteorder == "big":
        samples.byteswap()

    def dbfs(value):
        return 20 * math.log10(value / 32768) if value else -math.inf

    rms = math.sqrt(sum(map(operator.mul, samples, samples)) / len(samples))
    return dbfs(rms), dbfs(max(map(abs, samples)))


def is_speech(text: str) -> bool:
    """Whisper marks silence and noise as [BLANK_AUDIO], (silence), [MUSIC], and similar."""
    return any(character.isalnum() for character in ANNOTATION.sub("", text))


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SpeechUnavailable(f"{label} is missing.")
    return path


def child_environment(extra=None) -> dict[str, str]:
    """Operating-system variables only, plus explicit ``extra`` values for this child."""
    windows = os.name == "nt"
    allowed = WINDOWS_ENVIRONMENT if windows else POSIX_ENVIRONMENT
    inherited = {
        name: value
        for name, value in os.environ.items()
        if ((name.upper() if windows else name) in allowed or not windows and name[:3] == "LC_")
        and not any(marker in name.upper() for marker in SECRET_MARKERS)
    }
    return {**inherited, **(extra or {})}


async def run_program(argv, *, stdin: bytes = b"", timeout: float = 60, env=None) -> bytes:
    """Run an owned process tree without a shell, inherited secrets, or pipe cleanup waits."""
    from .speech_process import ProcessFailure, run_owned

    environment = child_environment(env)
    try:
        return await run_owned(
            argv, stdin=stdin, timeout=timeout, env=environment, max_output=MAX_PROCESS_OUTPUT
        )
    except ProcessFailure as exc:
        raise SpeechUnavailable(str(exc)) from exc


class WhisperCpp:
    """whisper-cli full JSON adapter. Unknown confidence stays unknown."""

    def __init__(self, executable: str | Path, model: str | Path):
        self.executable = require_file(executable, "whisper.cpp executable")
        self.model = require_file(model, "Whisper model")

    async def transcribe(self, audio: str | Path) -> Transcript:
        info = inspect_wav(audio)
        if info.sample_rate != 16000 or info.channels != 1:
            raise SpeechUnavailable("Whisper input must be 16 kHz mono PCM WAV.")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="lls-stt-") as temporary:
            output = Path(temporary) / "transcript"
            await run_program(
                [
                    self.executable,
                    "-m",
                    self.model,
                    "-f",
                    Path(audio).resolve(),
                    "-l",
                    "en",
                    "-ojf",
                    "-of",
                    output,
                    "-np",
                ]
            )
            path = output.with_suffix(".json")
            if not path.is_file() or path.stat().st_size > MAX_PROCESS_OUTPUT:
                raise SpeechUnavailable("Whisper did not produce bounded JSON output.")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Annotation-only segments are not player words. Nothing left means silence.
                segments = [s for s in data["transcription"] if is_speech(s["text"])]
                text = " ".join(segment["text"].strip() for segment in segments).strip()
                if len(text) > 4000:
                    raise ValueError("oversized transcript")
                # Special tokens such as [_BEG_] and [_TT_50] carry no recognized words.
                # Missing probabilities stay unknown instead of becoming confidence.
                probabilities = [
                    float(token["p"])
                    for segment in segments
                    for token in segment.get("tokens", [])
                    if "p" in token and not token.get("text", "").startswith("[_")
                ]
                if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
                    raise ValueError("invalid token probability")
                confidence = sum(probabilities) / len(probabilities) if probabilities else None
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise SpeechUnavailable("Whisper returned an invalid transcript.") from exc
        return Transcript(
            text, confidence, "whisper.cpp", round((time.monotonic() - started) * 1000)
        )


class Piper:
    def __init__(self, executable: str | Path, model: str | Path):
        self.executable = require_file(executable, "Piper executable")
        self.model = require_file(model, "Piper voice model")
        require_file(str(self.model) + ".json", "Piper voice configuration")

    async def synthesize(self, text: str, destination: str | Path) -> AudioInfo:
        if not text.strip() or len(text) > 1200:
            raise SpeechUnavailable("Speech text must contain 1 to 1200 characters.")
        target = Path(destination).resolve()
        if target.exists():
            raise SpeechUnavailable("Audio destination already exists.")
        with tempfile.TemporaryDirectory(prefix="lls-tts-") as temporary:
            output = Path(temporary) / "speech.wav"
            await run_program(
                [self.executable, "--model", self.model, "--output_file", output],
                stdin=(text.strip() + "\n").encode("utf-8"),
            )
            info = inspect_wav(output)
            # Exclusive creation prevents overwriting recordings or user files.
            with target.open("xb") as stream:
                stream.write(output.read_bytes())
        return info


def windows_powershell() -> str:
    """Windows PowerShell from the real system directory, never from PATH or the working one."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(260)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise SpeechUnavailable("The Windows system directory is unavailable.")
    return str(PureWindowsPath(buffer.value, "WindowsPowerShell", "v1.0", "powershell.exe"))


class WindowsSapi:
    """Optional installed Windows voice fallback, with no cloud dependency."""

    async def synthesize(self, text: str, destination: str | Path) -> AudioInfo:
        if os.name != "nt":
            raise SpeechUnavailable("Windows SAPI requires Windows.")
        if not text.strip() or len(text) > 1200:
            raise SpeechUnavailable("Speech text must contain 1 to 1200 characters.")
        target = Path(destination).resolve()
        if target.exists():
            raise SpeechUnavailable("Audio destination already exists.")
        with tempfile.TemporaryDirectory(prefix="lls-sapi-") as temporary:
            output = Path(temporary) / "speech.wav"
            environment = {"LLS_SAPI_TEXT": text, "LLS_SAPI_WAV": str(output)}
            # Text and path stay data. A pinned format passes inspect_wav for every voice.
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList "
                "22050,([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen),"
                "([System.Speech.AudioFormat.AudioChannel]::Mono); "
                "$speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "try {$speaker.SetOutputToWaveFile($env:LLS_SAPI_WAV,$format); "
                "$speaker.Speak($env:LLS_SAPI_TEXT)} finally {$speaker.Dispose()}"
            )
            await run_program(
                [windows_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                env=environment,
            )
            info = inspect_wav(output)
            with target.open("xb") as stream:
                stream.write(output.read_bytes())
        return info


def device_selector(value: str):
    """Parse a configured device: empty selects the default, and digits select an index."""
    value = value.strip()
    return int(value) if value.isdecimal() else value or None


def select_device(sd, selector, kind: str):
    """Resolve a configured device to a PortAudio index and stream settings without opening it.

    ``None`` keeps PortAudio's default device. A name resolves within one host API, in
    ``WINDOWS_HOST_APIS`` order and then PortAudio order. Exact names precede ordered
    fragments, as in sounddevice. ``"<host API>:<name>"`` restricts the search to that host
    API. WASAPI shared mode converts 16 kHz capture and 22.05 kHz playback.
    """
    if selector is None:
        return None, None
    hostapis = [api["name"] for api in sd.query_hostapis()]
    devices = list(sd.query_devices())
    if type(selector) is int:
        if not 0 <= selector < len(devices) or devices[selector][f"max_{kind}_channels"] < 1:
            raise SpeechUnavailable(f"Audio {kind} device {selector} does not exist.")
        index = selector
    else:
        index = _match_device(selector, kind, hostapis, devices)
    if hostapis[devices[index]["hostapi"]] == "Windows WASAPI":
        return index, sd.WasapiSettings(auto_convert=True)
    return index, None


def _match_device(selector, kind, hostapis, devices):
    aliases = {}
    for api, label in enumerate(hostapis):
        aliases.setdefault(label.lower(), api)
        aliases.setdefault(label.lower().removeprefix("windows "), api)
    prefix, colon, rest = selector.partition(":")
    scope = aliases.get(prefix.strip().lower()) if colon and rest.strip() else None
    query = (selector if scope is None else rest).strip().lower()
    rank = {label: position for position, label in enumerate(WINDOWS_HOST_APIS)}
    order = sorted(range(len(hostapis)), key=lambda api: (rank.get(hostapis[api], len(rank)), api))
    for exact in (True, False):
        for api in order if scope is None else [scope]:
            found = [
                device["index"]
                for device in devices
                if device["hostapi"] == api
                and device[f"max_{kind}_channels"] > 0
                and _device_matches(device["name"], hostapis[api], query, exact)
            ]
            if len(found) == 1:
                return found[0]
            if found:
                raise SpeechUnavailable(
                    f"Audio {kind} device {selector!r} matches {hostapis[api]} devices {found}. "
                    "Select one by index."
                )
    raise SpeechUnavailable(f"No audio {kind} device matches {selector!r}.")


def _device_matches(name, hostapi, query, exact):
    full = f"{name}, {hostapi}".lower()
    if exact:
        return query in {name.lower(), full}
    position = 0
    for fragment in query.split():
        position = full.find(fragment, position)
        if position < 0:
            return False
        position += len(fragment)
    return True


def device_failure(label: str, error: Exception) -> str:
    """The driver's reason on one bounded line. Denied permission is one cause among several."""
    detail = "".join(c for c in " ".join(str(error).split()) if c.isprintable())[:200]
    reason = "permission was denied" if isinstance(error, PermissionError) else "is unavailable"
    return f"{label} {reason}: {detail or type(error).__name__}."


class Microphone:
    """One capture, confirmed by valid input frames, with bounded in-memory PCM."""

    def __init__(self, device=None, *, first_frame_timeout=1.0):
        if (
            type(first_frame_timeout) not in {int, float}
            or not math.isfinite(first_frame_timeout)
            or not 0 < first_frame_timeout <= 5
        ):
            raise ValueError("Microphone first-frame timeout must be between 0 and 5 seconds.")
        self.device = device
        self.first_frame_timeout = first_frame_timeout
        self.stream = None
        self.frames = bytearray()
        self.lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._generation = 0
        self._startup_ready = threading.Event()
        self._cancel_requested = threading.Event()
        self._cleanup_failed = False
        self.error = None
        self.accepting = False

    @property
    def capture_error(self):
        with self.lock:
            return self.error

    def start(self, *, cancel_event=None):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise SpeechUnavailable(
                "Install the speech dependency group to use audio devices."
            ) from exc
        with self._lifecycle_lock:
            with self.lock:
                if self._cleanup_failed:
                    raise SpeechUnavailable(
                        "Microphone cleanup failed. Recreate the audio provider."
                    )
                if self.stream is not None:
                    raise SpeechUnavailable("Microphone capture is already active.")
                self._generation += 1
                generation = self._generation
                self.frames = bytearray()
                self.error = None
                self.accepting = True
                ready = self._startup_ready = threading.Event()
                canceled = self._cancel_requested = threading.Event()

            def is_canceled():
                return canceled.is_set() or (cancel_event is not None and cancel_event.is_set())

            def capture(data, count, timing, status):
                with self.lock:
                    if generation != self._generation or not self.accepting or is_canceled():
                        raise sd.CallbackAbort
                    error = "Microphone capture lost audio frames." if status else None
                    try:
                        pcm = memoryview(data).cast("B")
                        if type(count) is not int or count <= 0 or len(pcm) != count * 2:
                            error = error or "Microphone supplied invalid PCM frames."
                    except (TypeError, ValueError):
                        error = error or "Microphone supplied invalid PCM frames."
                    if error:
                        self.error = error
                        self.accepting = False
                        ready.set()
                        raise sd.CallbackAbort
                    remaining = MAX_AUDIO_SECONDS * 16000 * 2 - len(self.frames)
                    self.frames.extend(pcm[:remaining])
                    # Digital silence is valid audio. No amplitude check establishes
                    # microphone permission, physical audibility, or speech content.
                    ready.set()
                    if remaining <= len(pcm):
                        raise sd.CallbackStop

            try:
                if is_canceled():
                    raise SpeechUnavailable("Microphone capture was canceled.")
                device, settings = select_device(sd, self.device, "input")
                stream = sd.RawInputStream(
                    samplerate=16000,
                    channels=1,
                    dtype="int16",
                    device=device,
                    callback=capture,
                    extra_settings=settings,
                )
                with self.lock:
                    self.stream = stream
                if is_canceled():
                    raise SpeechUnavailable("Microphone capture was canceled.")
                stream.start()
                deadline = time.monotonic() + self.first_frame_timeout
                while True:
                    with self.lock:
                        if is_canceled():
                            raise SpeechUnavailable("Microphone capture was canceled.")
                        if self.error:
                            raise SpeechUnavailable(self.error)
                        if self.frames:
                            return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SpeechUnavailable("Microphone did not supply input frames in time.")
                    # Poll an external cancellation Event without invoking driver methods
                    # concurrently with start(). Native calls still require joined cleanup.
                    ready.wait(min(remaining, 0.02))
            except Exception as exc:
                try:
                    self._close(abort=True)
                finally:
                    with self.lock:
                        self.frames.clear()
                if isinstance(exc, SpeechUnavailable):
                    raise
                raise SpeechUnavailable(device_failure("Microphone", exc)) from exc

    def _close(self, *, abort):
        with self.lock:
            stream, self.stream = self.stream, None
            self.accepting = False
            self._startup_ready.set()
        if stream is None:
            if self._cleanup_failed:
                raise SpeechUnavailable("Microphone device could not shut down cleanly.")
            return
        error = None
        try:
            stream.abort() if abort else stream.stop()
        except Exception as exc:
            error = exc
        finally:
            try:
                stream.close()
            except Exception as exc:
                error = error or exc
        if error:
            self._cleanup_failed = True
            raise SpeechUnavailable("Microphone device could not shut down cleanly.") from error

    def stop(self, destination: str | Path) -> AudioInfo:
        with self._lifecycle_lock:
            if self.stream is None:
                raise SpeechUnavailable("Microphone capture is not active.")
            try:
                self._close(abort=False)
                with self.lock:
                    if self._cancel_requested.is_set():
                        raise SpeechUnavailable("Microphone capture was canceled.")
                    captured = bytes(self.frames)
            finally:
                with self.lock:
                    self.frames.clear()
            if self.error:
                raise SpeechUnavailable(self.error)
            if not captured:
                raise SpeechUnavailable("Microphone capture was empty.")
            with Path(destination).open("xb") as output:
                with wave.open(output, "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(16000)
                    stream.writeframes(captured)
            return inspect_wav(destination)

    def cancel(self):
        # Wake a first-frame waiter before joining the lifecycle lock. A canceled
        # old start or callback must never close or append to a replacement capture.
        with self.lock:
            generation = self._generation
            self._cancel_requested.set()
            self.accepting = False
            self._startup_ready.set()
        with self._lifecycle_lock:
            if generation != self._generation:
                return
            try:
                self._close(abort=True)
            finally:
                with self.lock:
                    self.frames.clear()


async def _device_call(function, *, on_cancel=None):
    """Keep driver calls off the loop, then join before touching the device again."""
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass
        if not task.cancelled():
            task.exception()
        raise


async def play_wav(path: str | Path, device=None, *, on_started=None):
    """Callback playback has no orphaned blocking-write worker on cancellation."""
    info = inspect_wav(path)
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SpeechUnavailable(
            "Install the speech dependency group to use audio devices."
        ) from exc
    with wave.open(str(path), "rb") as audio:
        pcm = audio.readframes(info.frames)
    finished, canceled, submitted = threading.Event(), threading.Event(), threading.Event()
    position = 0
    playback_error = None

    def output(data, frames, timing, status):
        nonlocal position, playback_error
        if canceled.is_set():
            data[:] = bytes(len(data))
            raise sd.CallbackAbort
        if status:
            playback_error = "Audio playback lost frames."
            data[:] = bytes(len(data))
            raise sd.CallbackAbort
        count = min(len(data), len(pcm) - position)
        data[:count] = pcm[position : position + count]
        data[count:] = bytes(len(data) - count)
        position += count
        if count:
            submitted.set()
        if position >= len(pcm):
            raise sd.CallbackStop

    stream = None
    failed = False

    def open_output():
        nonlocal stream
        index, settings = select_device(sd, device, "output")
        stream = sd.RawOutputStream(
            samplerate=info.sample_rate,
            channels=info.channels,
            dtype="int16",
            device=index,
            callback=output,
            finished_callback=finished.set,
            extra_settings=settings,
        )
        stream.start()

    def close_output():
        cleanup_error = None
        try:
            stream.abort()
        except Exception as exc:
            cleanup_error = exc
        finally:
            try:
                stream.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None and not failed:
            raise SpeechUnavailable("Audio device could not shut down cleanly.") from cleanup_error

    try:
        await _device_call(open_output, on_cancel=canceled.set)
        deadline = time.monotonic() + info.duration_seconds + 5
        notified = False
        while True:
            # Run notifications on the event loop, after the device callback has
            # accepted PCM frames. This is not proof of physical audibility.
            if submitted.is_set() and not notified:
                notified = True
                if on_started is not None:
                    on_started()
            if finished.is_set():
                break
            if time.monotonic() >= deadline:
                raise SpeechUnavailable("Audio playback timed out.")
            await asyncio.sleep(0.01)
        if playback_error:
            raise SpeechUnavailable(playback_error)
        if not notified or position < len(pcm):
            raise SpeechUnavailable("Audio playback ended before all frames were submitted.")
    except asyncio.CancelledError:
        failed = True
        raise
    except Exception as exc:
        failed = True
        if isinstance(exc, SpeechUnavailable):
            raise
        raise SpeechUnavailable(device_failure("Audio playback device", exc)) from exc
    finally:
        canceled.set()
        if stream is not None:
            await _device_call(close_output)


async def probe(args):
    started = time.monotonic()
    if args.command == "devices":
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise SpeechUnavailable("Install the speech dependency group first.") from exc
        hostapis = [api["name"] for api in sd.query_hostapis()]
        return {
            "hostapis": hostapis,
            "devices": [{**d, "hostapi_name": hostapis[d["hostapi"]]} for d in sd.query_devices()],
            "game_verified": False,
        }
    if args.command == "record":
        mic = Microphone(args.device)
        try:
            mic.start()
            await asyncio.sleep(args.seconds)
            result = asdict(mic.stop(args.output))
        finally:
            mic.cancel()
    elif args.command == "transcribe":
        result = asdict(await WhisperCpp(args.executable, args.model).transcribe(args.audio))
    elif args.command == "synthesize":
        provider = WindowsSapi() if args.provider == "sapi" else Piper(args.executable, args.model)
        result = asdict(await provider.synthesize(args.text, args.output))
    else:
        await play_wav(args.audio, args.device)
        result = {"playback_completed": True}
    return {
        **result,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "game_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Explicit local speech probes without GTA")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices")
    record = commands.add_parser("record")
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--seconds", type=int, choices=range(1, 31), default=5)
    record.add_argument("--device", type=device_selector)
    stt = commands.add_parser("transcribe")
    stt.add_argument("--executable", type=Path, required=True)
    stt.add_argument("--model", type=Path, required=True)
    stt.add_argument("--audio", type=Path, required=True)
    tts = commands.add_parser("synthesize")
    tts.add_argument("--provider", choices=["piper", "sapi"], default="piper")
    tts.add_argument("--executable", type=Path)
    tts.add_argument("--model", type=Path)
    tts.add_argument("--text", required=True)
    tts.add_argument("--output", type=Path, required=True)
    play = commands.add_parser("play")
    play.add_argument("--audio", type=Path, required=True)
    play.add_argument("--device", type=device_selector)
    args = parser.parse_args()
    if args.command == "synthesize" and args.provider == "piper":
        if not args.executable or not args.model:
            parser.error("Piper requires --executable and --model.")
    try:
        print(json.dumps(asyncio.run(probe(args)), indent=2))
    except (SpeechUnavailable, OSError) as exc:
        parser.exit(1, f"Speech probe failed: {exc}\n")


if __name__ == "__main__":
    main()
