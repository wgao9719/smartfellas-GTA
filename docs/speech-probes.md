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

The probe prints the recognized text. Standard whisper.cpp JSON can omit confidence. The probe reports `null` in that case. Unknown confidence must not authorize game actions.

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

Automated tests use generated silent WAV fixtures and controlled fake devices. They check process cancellation, output bounds, malformed audio, missing confidence, microphone cleanup, and overwrite protection. They do not verify a real microphone, real voice model, Windows SAPI, or GTA.
