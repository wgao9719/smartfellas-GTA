# Local speech probes

These probes run without GTA. They do not establish G0 acceptance. Runtime speech integration has passed synthetic bridge checks. Speech remains disabled until configured. Windows devices and actual model execution remain unverified.

The service provides a local whisper.cpp adapter, a local Piper adapter, and a Windows SAPI fallback. The project does not bundle executables or model weights. Each voice model needs its own license review. CPU execution is supported by the provider interfaces. CUDA is not required.

## Preparation

1. Install the optional audio dependencies.

   ```sh
   uv sync --group dev --group speech
   ```

2. List the available audio devices.

   ```sh
   uv run --group speech python -m service.lls.speech devices
   ```

3. Install a trusted whisper.cpp build from [upstream](https://github.com/ggml-org/whisper.cpp).
4. Select a local English Whisper model.
5. Select a licensed Piper voice with its matching `.onnx.json` configuration.

No command below contacts a hosted AI provider. Device permission, model compatibility, latency, and audible output require checks on the Windows rig.

## Microphone and recognition

The `record` command opens the microphone immediately. It records for the specified duration and writes an explicit local file. Normal service startup never opens the microphone.

1. Record a short test phrase.

   ```sh
   uv run --group speech python -m service.lls.speech record --seconds 5 --output mic-test.wav
   ```

2. Transcribe the recording with your local paths.

   ```sh
   uv run python -m service.lls.speech transcribe --executable /path/to/whisper-cli --model /path/to/ggml-base.en.bin --audio mic-test.wav
   ```

The probe prints the recognized text. It runs `whisper-cli` with full JSON output (`-ojf`) and reports the mean token probability as confidence. Special tokens such as `[_BEG_]` and timestamp tokens such as `[_TT_50]` do not count. JSON without token probabilities reports `null`. Unknown confidence must not authorize game actions.

Whisper marks silence and noise with annotations such as `[BLANK_AUDIO]`, `[ Silence ]`, `(silence)`, `[MUSIC]`, and `(blender whirring)`. The service drops every segment that has no letters or digits outside brackets and parentheses. When nothing remains, the transcript is empty. The runtime then shows `Didn't catch that. Try again.` and starts no turn.

The full JSON shape was observed on macOS with whisper.cpp v1.9.5 and the `ggml-base.en` model. Each token has `text`, `timestamps`, `offsets`, `id`, `p`, and `t_dtw`. Low hiss produced `[BLANK_AUDIO]`, Gaussian noise produced `(blender whirring)`, and pure tones produced no segments. Digital silence produced the word `You` with a token probability near 0.11, so it remains dialogue-only input. Verify the JSON shape and the silence behavior with the Windows build on the rig.

Capture uses 16 kHz mono PCM audio. It stops after 30 seconds. Cancellation clears the in-memory recording. The standalone command keeps the file you explicitly requested.

The real microphone reports readiness only after its callback supplies valid input frames.
The default first-frame wait is one second after device startup returns.
Silent frames count as valid audio. Silence does not prove that the operating system denied microphone permission.
Malformed frames, callback errors, and a missing first frame prevent a recording receipt.
Cancellation joins device cleanup before another capture can start. A stalled native driver call can still delay that cleanup.

## Synthesis and playback

1. Generate a short Piper sample with your local paths.

   ```sh
   uv run python -m service.lls.speech synthesize --provider piper --executable /path/to/piper --model /path/to/voice.onnx --text 'Hello. This is a local voice test.' --output voice-test.wav
   ```

2. Play the sample through the system audio device.

   ```sh
   uv run --group speech python -m service.lls.speech play --audio voice-test.wav
   ```

Windows also supports a separate installed-voice probe.

```powershell
uv run python -m service.lls.speech synthesize --provider sapi --text 'Hello. This is the Windows fallback voice.' --output sapi-test.wav
```

The output is system audio. It does not prove spatial audio or lip sync in GTA. The probes refuse to replace existing files.

## Evidence to collect on the rig

Record the executable versions, model checksums, voice licenses, audio devices, and selected compute backend. Run the probes while GTA is active. Record recognition accuracy, elapsed times, audible output, and cancellation behavior. Repeat microphone denial and device removal checks.

Automated tests use generated silent WAV fixtures and controlled fake devices. They check process cancellation, output bounds, malformed audio, missing confidence, microphone cleanup, and overwrite protection. A fake `whisper-cli` executable replays the observed `-ojf` shape through the owned-process path to check confidence, annotation removal, and empty transcripts. They do not verify a real microphone, real voice model, Windows SAPI, or GTA.

An opt-in test runs an installed whisper.cpp when you set both `LLS_WHISPER_EXECUTABLE` and `LLS_WHISPER_MODEL`. Otherwise it is skipped.

```sh
LLS_WHISPER_EXECUTABLE=/path/to/whisper-cli LLS_WHISPER_MODEL=/path/to/ggml-base.en.bin uv run pytest tests/test_speech.py -k installed
```
