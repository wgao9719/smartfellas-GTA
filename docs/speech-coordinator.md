# Speech coordinator and runtime

`service/lls/speech_coordinator.py` contains optional local speech orchestration. `service/lls/runtime_speech.py` connects it to push-to-talk, dialogue, cancellation, and delivery persistence. The real C# HTTP bridge passes with synthetic audio providers. Hardware audio and native GTA acceptance remain pending.

`SpeechConfig` defaults to disabled. `SpeechCoordinator.local()` accepts explicit local executable and model paths. Construction does not open an audio device. The coordinator downloads no models or programs.

The microphone and speaker belong to the service computer. A service running on a Mac uses that Mac's audio devices, including during Windows development through Tailscale. The game bridge does not transport audio.

## Interface

```python
identity = SpeechIdentity(
    session_id=session.id,
    runtime_generation=session.request.runtime_generation,
    scene_id=scene_id,
    actor_id=actor_id,
    epoch=session.epoch,
    runtime_instance_id=session.actors[actor_id]["runtime_instance_id"],
)

speech = SpeechCoordinator.local(
    SpeechConfig(),  # Disabled until the application supplies explicit configuration.
    is_current=validate_identity,
    on_started=playback_started,
)

recording = await speech.start_capture(identity)
transcript = await speech.finish_capture(identity)
playback = await speech.speak(identity, "Hello.")
await speech.cancel(identity)
await speech.close()
```

The coordinator permits one audio operation. It rejects overlapping operations instead of queuing audio. One capture remains active until stop, cancellation, identity invalidation, or the capture deadline. The maximum capture duration is 30 seconds.

`is_current(identity)` must check session state, runtime generation, scene, actor binding, protagonist context, and epoch. The runtime must call `cancel` on invalidation. The coordinator also polls identity during recording and asynchronous provider work.

The coordinator waits for device worker and provider cleanup before releasing the audio operation. Local subprocess providers own their process trees. Device drivers can exceed configured operation deadlines during shutdown. The coordinator joins those calls instead of accessing a device concurrently.

Output-device construction, start, abort, and close run in joined worker threads. A stalled driver does not block the service event loop. Cancellation waits for the driver to return before it completes cleanup.

## Receipts

`SpeechReceipt` includes the identity, operation kind, state, and elapsed milliseconds. The coordinator retains a bounded receipt history. Callers receive terminal receipts from each operation.

| Operation | Receipt | Meaning |
| --- | --- | --- |
| Capture | `recording` | The microphone supplied valid input frames through its device callback. |
| Transcript | `completed` | Transcription finished and temporary capture audio was removed before return. |
| Playback | `started` | The output callback accepted PCM frames. |
| Playback | `completed` | The output callback accepted all frames and the device reported completion. |
| Any | `canceled` | Cancellation or identity invalidation stopped the operation. |
| Any | `failed` | The operation failed or exceeded a provider deadline. |

`on_started(receipt)` runs synchronously on the event loop after the playback callback accepts PCM frames. The runtime uses this event to schedule subtitles. `audio_duration_ms` comes from the validated synthesized WAV. The callback must not block or return an awaitable.

`on_transcribing(receipt)` runs after the microphone stops and the identity passes validation. Its `started` transcript receipt contains no copied text. The runtime uses this callback to show transcription status without implying that capture stopped early.

Only completed playback supplies `delivered_text`. Canceled or failed playback supplies no full-text delivery evidence, even when playback started. Software playback completion does not prove that a person heard the audio.

Transcript `action_eligible` indicates only that finite confidence meets the configured threshold. Unknown confidence returns false. Invalid confidence fails transcription. Action policy, consent, actor identity, and world-state checks remain mandatory. The runtime must treat a false confidence gate as dialogue-only input.

## Data retention

Receipt history holds bounded transcript and playback text in memory. Ordinary cancellation preserves legitimate receipts. `close()` cancels active work and clears all retained receipts before returning.

`await speech.forget(session_id=session_id, actor_id=actor_id)` cancels matching work and removes its retained receipts. Omitting `actor_id` removes all receipts for that session. The runtime must invalidate the matching identities before calling `forget`, so new work cannot race erasure. Callers must also remove any receipt copies that they retain elsewhere.

## Runtime configuration

| Environment variable | Purpose |
| --- | --- |
| `LLS_SPEECH_ENABLED=1` | Enables explicit push-to-talk capture and configured speech output. |
| `LLS_WHISPER_EXECUTABLE` | Selects the local `whisper-cli` executable. |
| `LLS_WHISPER_MODEL` | Selects the local Whisper model file. |
| `LLS_TTS_PROVIDER` | Selects `disabled`, `piper`, or Windows `sapi`. |
| `LLS_PIPER_EXECUTABLE` | Selects the local Piper executable. |
| `LLS_PIPER_MODEL` | Selects the voice model with its adjacent `.json` configuration. |
| `LLS_INPUT_DEVICE` | Selects an input device name or numeric index. Empty selects the default. |
| `LLS_OUTPUT_DEVICE` | Selects an output device name or numeric index. Empty selects the default. |
| `LLS_STT_ACTION_CONFIDENCE` | Sets the finite-confidence gate. The default is `0.85`. |

Service startup never opens the microphone. In the game, holding the configured push-to-talk key requests capture. Releasing it stops capture and starts transcription. A disabled or unavailable provider leaves typed input available.

The adapter locks the scene, actor, runtime instance, and generation at key-down. It waits for the target snapshot's HTTP receipt before sending the capture request. Releasing the key before that receipt prevents a late microphone opening.

The adapter displays `Recording` only after a bound service status confirms capture. Target changes, cancellation, unsafe world state, and reconnect reset local input. The service cancels its matching speech operation and joins cleanup.

Audio subtitles arrive when software playback starts. A later audio chunk can replace an earlier audio subtitle. The adapter acknowledges only text that it rendered. The backend owns playback completion receipts and prevents duplicate memory records across audio and subtitle delivery.

Speech synthesis receives the same display-rule text as the audio subtitle. Markup never reaches the voice, the subtitle, or a delivery receipt. The [display rule](g0-toolchain.md#implemented-boundaries) describes the normalization.

## Simulated bridge acceptance

1. Run the typed-input bridge check.

   ```sh
   uv run python tests/live_bridge_check.py
   ```

2. Run the speech bridge check with synthetic audio devices.

   ```sh
   uv run python tests/live_bridge_check.py --speech
   ```

The speech check verifies bound capture, confirmed recording, transcription, two audio chunks, rendered subtitles, and the final marker through real HTTP. Its native action uses a simulated executor. No microphone, speaker, or model file is opened by the fixture.

Hardware audio, Windows process ownership, native GTA interaction, and Tailscale operation remain unverified. Focused tests use mocked devices and providers.
