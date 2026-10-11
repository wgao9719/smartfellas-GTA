**Windows real local speech verification — 2026-10-11**

Real local speech-to-text and file-only Windows SAPI synthesis passed on TOWER-PENGU. No product defect was found in the exercised paths. No microphone was opened and no audio was played. No tracked file was modified.

Repository: `C:\Users\Brian\Documents\Github\smartfellas-GTA`, branch `living-los-santos-foundation`, commit `7aeabaa719205d8a9eb8d4b8520c9c3159f39430`. Initial and final Git status were clean; tracked worktree and index diff checks both returned 0. Artifacts are under the ignored `artifacts\speech-real` directory. Downloaded tools and the model are exclusively under `C:\Users\Brian\.lls-tools\whisper`.

Environment: Windows 11 Pro 10.0.22631; AMD Ryzen 9 9950X, 16 cores / 32 logical processors; Windows PowerShell 5.1.22621.6133; uv 0.12.17; existing `.venv`, Python 3.12.14. Evidence: [environment.json](environment.json), [git-verification.json](git-verification.json). Reviewed `docs/speech-probes.md`, runtime configuration in `docs/speech-coordinator.md`, and `tests/test_speech.py`, including the opt-in test at line 237.

**Official downloads and provenance**

GitHub's official `/repos/ggml-org/whisper.cpp/releases/latest` endpoint identified **v1.9.5**, published 2026-10-06, with `draft=false`, `prerelease=false`. That stable entry has no attached binaries. Its release notes explicitly link **b5454** as its binary build. Although GitHub marks the b5454 entry as a prerelease/nightly, the v1.9.5 annotated tag and b5454 tag resolve to the **same commit**, `d1be6fde11ac6e0407606b4e42fe72d34add8037`. The downloaded executable reports `whisper.cpp version: 1.9.5`. Thus this is the official binary build linked by the latest stable release, not a third-party build or an unrelated nightly.

- Stable release: https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.5
- Binary release: https://github.com/ggml-org/whisper.cpp/releases/tag/b5454
- Windows x64 CPU archive: https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip
- Official model repository: https://huggingface.co/ggerganov/whisper.cpp
- Pinned English base model: https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.en.bin

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `whisper-bin-x64-b5454.zip` | 8,928,640 | `6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb` |
| `b5454\Release\whisper-cli.exe` | 479,744 | `331dba46d6427105d2b802cdbc7eae916ea1c5abf9b0d3b5cfe460d8db8e4366` |
| `ggml-base.en.bin` | 147,964,211 | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` |

Archive size and SHA-256 matched the official GitHub asset metadata. Model size and SHA-256 matched the official Hugging Face LFS metadata. Model revision: `5359861c739e955e79d9a303bcbc70fb988958b1`. All extracted files, including DLL dependencies, have sizes and SHA-256 hashes in [extracted-manifest.json](extracted-manifest.json). See also [download-manifest.json](download-manifest.json), [upstream-release.json](upstream-release.json), [upstream-tag-objects.json](upstream-tag-objects.json), [stable-tag-resolved.json](stable-tag-resolved.json), and [upstream-model-tree.json](upstream-model-tree.json).

The selected package is the CPU archive, not a CUDA/BLAS build. Real full JSON output reported CPU features including AVX2, AVX512, AVX512_VNNI and OPENMP. No installer, persistent PATH change, registry change, system configuration change, or cloud provider configuration was used.

**Executed checks and results**

| Check | Observed result |
| --- | --- |
| Requested `tests/test_speech.py`, `-W error`, real binary enabled | **32 passed, 9 skipped in 5.57 s**, exit 0. The installed-whisper test ran. All skips were the 6 + 3 POSIX fake-shell-executable cases. |
| SAPI known sentence to file | Microsoft David Desktop, en-US; 16,000 Hz, mono, 16-bit PCM; 3.925 s; RMS −22.6845 dBFS. SAPI `Speak` took 88 ms, excluding setup. |
| Repository `transcribe` CLI | Exact transcript: **“Please look at me. The weather is nice today.”** Confidence **0.92583**, elapsed **828 ms**. |
| Real provider through `SpeechCoordinator` with a file-backed capture fixture | `completed`, same exact transcript and confidence; one recognizer call; adapter **844 ms**, coordinator receipt **859 ms**. |
| Two-second digital-silence WAV through coordinator | RMS `-inf`, `failed / empty_transcript`, empty text, confidence `null`, **zero recognizer calls**, 16 ms receipt. |
| Four-second deterministic quiet-hiss WAV through coordinator | RMS **−60.84715 dBFS**, `failed / empty_transcript`, empty text, confidence `null`, **zero recognizer calls**, 0 ms rounded receipt. |
| Same hiss sent directly to the real adapter, bypassing the coordinator gate deliberately | Native model JSON contained **` [BLANK_AUDIO]`**. Adapter returned **empty text**, confidence **`null`**, 765 ms. |
| Additional synthetic annotation JSON through actual adapter parser and coordinator | `[BLANK_AUDIO]`, `[ Silence ]`, `(silence)`, `[MUSIC]`, `(blender whirring)` all discarded; `failed / empty_transcript`, empty text, confidence `null`. This case is synthetic, separate from the real hiss evidence. |
| Runtime empty/silent/uncertain transcript rejection tests | **3 passed in 0.82 s**, exit 0; no player turn, provider call, or stored player utterance. These tests use fake providers/devices. |
| Repository `synthesize --provider sapi` | Valid 22,050 Hz mono 16-bit PCM file; 89,284 frames, 4.049161 s; RMS −21.1983 dBFS; **1,359 ms** probe elapsed. No playback. |

The confidence is the mean of 11 ordinary token probabilities, including punctuation. Their values in order were `0.801416, 0.988821, 0.999510, 0.996251, 0.799963, 0.697922, 0.999895, 0.999314, 0.998047, 0.998243, 0.904748`; sum `10.18413`, mean `0.92583`. Special beginning/timestamp tokens were excluded. Full token text and probabilities are retained in [phrase-native.json](phrase-native.json). The coordinator marked the confidence gate `action_eligible=true` at its default 0.85 threshold; no game action was requested or executed.

The file-backed capture fixture copies a WAV into the coordinator's temporary capture path. It does not instantiate `Microphone`, `sounddevice`, or playback. It uses the real `WhisperCpp.transcribe` and owned Windows child-process path. An observation wrapper copies native JSON before normal temporary-output cleanup; the 844 ms adapter measurement includes that small wrapper overhead. These are single-run observations, not a latency benchmark.

Evidence: [pytest-speech.log](pytest-speech.log), [transcribe-cli.json](transcribe-cli.json), [file-pipeline-results.json](file-pipeline-results.json), [hiss-direct-native.json](hiss-direct-native.json), [pytest-runtime-rejection.log](pytest-runtime-rejection.log), [sapi-generation.json](sapi-generation.json), [synthesize-cli.json](synthesize-cli.json). Generated WAV files remain in this directory; their formats, sizes and hashes are in `file-pipeline-results.json`.

**Exact execution commands**

All commands ran from the main clone in Windows PowerShell. PATH and `LLS_WHISPER_*` assignments were confined to the invoked shell process and its children. `LLS_PROVIDER` and cloud settings were not set.

Official metadata was fetched using `Invoke-RestMethod` from these URLs and retained as the corresponding `upstream-*.json` files:

```text
https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest
https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=10
https://api.github.com/repos/ggml-org/whisper.cpp/git/ref/tags/v1.9.5
https://api.github.com/repos/ggml-org/whisper.cpp/git/ref/tags/b5454
https://api.github.com/repos/ggml-org/whisper.cpp/git/tags/d2a764c648779feb36d44363f212a29b00aad1af
https://huggingface.co/api/models/ggerganov/whisper.cpp
https://huggingface.co/api/models/ggerganov/whisper.cpp/tree/main?recursive=false&expand=false
```

Download, hash validation, extraction, and the full requested test invocation were executed by the retained scripts [download-assets.ps1](download-assets.ps1) and [run-tests.ps1](run-tests.ps1):

```powershell
& .\artifacts\speech-real\download-assets.ps1
& .\artifacts\speech-real\run-tests.ps1
```

The test script's exact environment assignments and pytest command are:

```powershell
$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
$env:LLS_WHISPER_EXECUTABLE = 'C:\Users\Brian\.lls-tools\whisper\b5454\Release\whisper-cli.exe'
$env:LLS_WHISPER_MODEL = 'C:\Users\Brian\.lls-tools\whisper\ggml-base.en.bin'
uv run --frozen pytest -W error tests/test_speech.py -q -rs *> 'artifacts\speech-real\pytest-speech.log'
```

File generation and probes:

```powershell
& C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -File .\artifacts\speech-real\synthesize-16k.ps1
& 'C:\Users\Brian\.lls-tools\whisper\b5454\Release\whisper-cli.exe' --version *> 'artifacts/speech-real/whisper-version.log'

$env:PATH = 'C:\Users\Brian\.lls-tools\uv;' + $env:PATH
uv run --frozen python -m service.lls.speech transcribe --executable 'C:\Users\Brian\.lls-tools\whisper\b5454\Release\whisper-cli.exe' --model 'C:\Users\Brian\.lls-tools\whisper\ggml-base.en.bin' --audio artifacts/speech-real/sapi-known-16k.wav > artifacts/speech-real/transcribe-cli.json
uv run --frozen python -m service.lls.speech synthesize --provider sapi --text 'Hello. This is the Windows fallback voice.' --output artifacts/speech-real/repository-sapi-22050.wav > artifacts/speech-real/synthesize-cli.json
uv run --frozen python -W error artifacts/speech-real/verify-file-pipeline.py > artifacts/speech-real/file-pipeline.log
uv run --frozen pytest -W error tests/test_runtime_speech.py::test_empty_transcript_reports_it_and_starts_no_turn tests/test_runtime_speech.py::test_silence_and_uncertain_words_report_without_a_turn -q -rs > artifacts/speech-real/pytest-runtime-rejection.log
```

[synthesize-16k.ps1](synthesize-16k.ps1) loads `System.Speech`, explicitly creates a 16,000 Hz / Sixteen / Mono `SpeechAudioFormatInfo`, and calls `$speaker.SetOutputToWaveFile($taskAudio, $format)` **before** `$speaker.Speak($taskSentence)`. It always disposes the synthesizer. The full file-backed verification harness is [verify-file-pipeline.py](verify-file-pipeline.py). These one-time scripts deliberately refuse to overwrite the existing audio/download destinations.

The native command built by the repository is `whisper-cli.exe -m <model> -f <capture.wav> -l en -ojf -of <temporary-output-prefix> -np`; the exact argv, including temporary paths for the observed calls, is retained in `file-pipeline-results.json`.

**Code paths, defects, and limits**

No verified product defect was found and no code was fixed. Relevant file:line references at the tested commit:

- `service/lls/speech.py:138`: real Whisper adapter; full-JSON parsing and annotation-only filtering at `:173`; confidence computation at `:178`.
- `service/lls/speech_coordinator.py:48`: default −60 dBFS threshold; gate at `:389`–`:400`; empty/low-confidence refusal at `:413`–`:429`.
- `service/lls/runtime_speech.py:226`: failed receipts return before `player.utterance_text` creation at `:240`. The three runtime tests corroborated the no-turn behavior.
- `service/lls/speech.py:234`: Windows SAPI provider; `SetOutputToWaveFile` before `Speak` at `:255`–`:256`.
- `service/lls/speech.py:669`: standalone transcription calls the adapter directly. It does not apply the coordinator silence gate; silence rejection was therefore verified through the coordinator, not inferred from CLI output.

The opt-in test's spoken-sentence branch at `tests/test_speech.py:257` is macOS-specific; the separate Windows SAPI/Whisper probes here supply real spoken-audio coverage on Windows. Nine POSIX fixture cases remain skipped as designed.

Hardware capture, audible playback quality, Piper, spatial audio, lip sync, GTA integration and broader recognition accuracy were not tested. GTA, Steam and Rockstar were not launched; their installation directories were untouched. Every launched command completed, no background service was started, and the final process check found zero remaining executables under the downloaded Whisper directory ([native-processes-after.json](native-processes-after.json)).
