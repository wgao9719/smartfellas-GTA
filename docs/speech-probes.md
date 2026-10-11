# Local speech probes

These probes run without GTA. They do not establish G0 acceptance. Runtime speech integration has passed synthetic bridge checks. Speech remains disabled until configured. Windows devices and actual model execution remain unverified.

The service provides a local whisper.cpp adapter, a local Piper adapter, and a Windows SAPI fallback. The project does not bundle executables or model weights. Each voice model needs its own license review. CPU execution is supported by the provider interfaces. CUDA is not required.

## Preparation

1. Install the optional audio dependencies.

   ```sh
   uv sync --group dev --group speech
   ```

2. List the available audio devices. Each entry includes its numeric index and host API name.

   ```sh
   uv run --group speech python -m service.lls.speech devices
   ```

3. Install a trusted whisper.cpp build from [upstream](https://github.com/ggml-org/whisper.cpp).
4. Select a local English Whisper model.
5. Select a licensed Piper voice with its matching `.onnx.json` configuration.

No command below contacts a hosted AI provider. Device permission, model compatibility, latency, and audible output require checks on the Windows rig.

Speech programs receive only operating-system variables, such as `PATH`, the temporary directory, and locale settings on macOS, or the system, profile, and program directories on Windows. They never receive `LLS_` or `LLM_` settings, tokens, keys, passwords, or proxy settings. GPU selection variables are also withheld.

## Device selection

The `--device` option and the `LLS_INPUT_DEVICE` and `LLS_OUTPUT_DEVICE` settings accept these values:

- An empty value selects the system default device without enumerating devices.
- A number selects that device index from the device list.
- A name selects a device by name, such as `Microphone (USB Audio)`.
- `<host API>:<name>` restricts a name to one host API, such as `WASAPI:Microphone (USB Audio)` or `MME:Microphone (USB Audio)`. The `Windows ` prefix of a host API name is optional.

Windows lists each endpoint once per host API under the same name. A name therefore resolves within one host API. The search order is Windows WASAPI, MME, Windows DirectSound, Windows WDM-KS, and then any other host API in PortAudio order. Matching ignores case. An exact name match takes precedence over a partial match. A partial match uses sounddevice rules: space-separated fragments must appear in order in `<name>, <host API>`. Two matches in the first matching host API fail with their indexes. Select one by index in that case.

A WASAPI device opens in shared mode with automatic sample-rate and channel conversion. That conversion allows 16 kHz capture and 22.05 kHz Piper playback when the Windows mixer uses another format. Other host APIs open without extra settings. Device errors report the driver's reason instead of assuming denied permission.

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

The full JSON shape was observed on macOS with whisper.cpp v1.9.5 and the `ggml-base.en` model. Each token has `text`, `timestamps`, `offsets`, `id`, `p`, and `t_dtw`. Low hiss produced `[BLANK_AUDIO]`, Gaussian noise produced `(blender whirring)`, and pure tones produced no segments. Digital silence produced the word `You` with a token probability near 0.11. The service therefore measures each capture first. A capture whose RMS is below `LLS_STT_SILENCE_DBFS` (default -60 dBFS) never reaches whisper.cpp. A transcript whose finite confidence is below `LLS_STT_DIALOGUE_CONFIDENCE` (default 0.3) is refused. Both report `Didn't catch that. Try again.` and start no turn. Unknown confidence still reaches dialogue but never actions.

On this Mac, `say` speech measured -21.3 dBFS RMS and transcribed with confidence 0.88. A whispered `say` voice measured -29.9 dBFS with confidence 0.81. The same speech followed by 8 s of silence measured -31.3 dBFS. Low hiss measured -60.8 dBFS and was gated. Verify the JSON shape, the silence behavior, and real microphone levels with the Windows build on the rig.

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

Windows also supports a separate installed-voice probe. It runs Windows PowerShell from the system directory, never from `PATH` or the working directory. SAPI writes 22.05 kHz, 16-bit mono PCM for every installed voice. The text reaches PowerShell as an environment value, never as script source.

```powershell
uv run python -m service.lls.speech synthesize --provider sapi --text 'Hello. This is the Windows fallback voice.' --output sapi-test.wav
```

The output is system audio. It does not prove spatial audio or lip sync in GTA. The probes refuse to replace existing files.

## Evidence to collect on the rig

Record the executable versions, model checksums, voice licenses, audio devices, host APIs, and selected compute backend. Verify 16 kHz capture and 22.05 kHz playback through a WASAPI device selected by name and by index. Run the probes while GTA is active. Record recognition accuracy, elapsed times, audible output, and cancellation behavior. Repeat microphone denial and device removal checks.

Automated tests use generated silent WAV fixtures and controlled fake devices. They check process cancellation, output bounds, malformed audio, missing confidence, microphone cleanup, and overwrite protection. A fake `whisper-cli` executable replays the observed `-ojf` shape through the owned-process path to check confidence, annotation removal, and empty transcripts. They do not verify a real microphone, real voice model, Windows SAPI, or GTA.

An opt-in test runs an installed whisper.cpp when you set both `LLS_WHISPER_EXECUTABLE` and `LLS_WHISPER_MODEL`. Otherwise it is skipped.

```sh
LLS_WHISPER_EXECUTABLE=/path/to/whisper-cli LLS_WHISPER_MODEL=/path/to/ggml-base.en.bin uv run pytest tests/test_speech.py -k installed
```
