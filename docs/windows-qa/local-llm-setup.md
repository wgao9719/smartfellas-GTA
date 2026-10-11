# Local language model setup and validation

Completed on TOWER-PENGU, 2026-10-11 (America/New_York). Official portable llama.cpp Vulkan binaries and official Qwen2.5-7B-Instruct Q4_K_M are installed under `C:\Users\Brian\.lls-tools\llm`. Real model dialogue passed through the repository's normal HTTP service. No hosted inference, API key, admin operation, installer, PATH/registry/firewall change, repository code edit, commit, or push was used. **The llama server and all probe service processes are stopped.**

This validates real local inference through a simulated bridge client. GTA V, Steam and Rockstar were not launched; in-game rendering, FPS, simultaneous GPU contention, speech, native actions and before/after video recordings remain for the in-game QA worker.

## Repository and configuration

Read `docs/provider-controls.md`, `service/lls/settings.py`, `service/lls/gateway_config.py`, `service/lls/providers.py`, and `.env.example` in `C:\Users\Brian\Documents\Github\smartfellas-GTA-wt\integration`, branch `win/validation`. Also inspected the normal entrypoint, gateway precedence, HTTP client and runtime deadlines.

The shared worktree advanced from `83d096849b6c5c87a26449dc793b87726a4c37fe` to `8e5c980d3ff08eff49eadfc1e8cd12b41b7bdb2d` through concurrent workers' merges while setup ran. Both service probes ran after the latter merge (03:43:14 EDT). Their health source SHA-256 was `0a0f604106e22c318e49d437eecfbb4d61d582b4c74c9818c1574be065b6c527`. Both repository working trees were clean at the final status check. This task made no tracked changes and ran no full regression suite because it changed no repository code.

`service-env.ps1` sets only these process-local values:

| Variable | Value | Purpose |
| --- | --- | --- |
| `LLS_PROVIDER` | `local_openai` | Select real local provider |
| `LLS_CLOUD_OPT_IN` | `0` | Keep hosted consent off |
| `LLS_LOCAL_BASE_URL` | `http://127.0.0.1:8091/v1` | Loopback OpenAI-compatible endpoint |
| `LLM_DIALOGUE_MODEL` | `qwen2.5-7b-instruct` | Server alias |
| `LLM_ACTION_MODEL` | `qwen2.5-7b-instruct` | Same local model for action selection |
| `LLS_DATABASE` | `C:\Users\Brian\.lls-tools\llm\qa-runtime.sqlite3` | Separate QA state; avoids existing saved provider overrides |

No API key or bridge token is contained in the scripts or report. Existing `LLM_API_KEY` values are never sent to a local provider (`OpenAIProvider.headers()` returns an empty dictionary). The probe additionally removed inherited LLS/LLM environment values before applying its explicit local settings.

Saved database provider configuration and cloud consent override environment values. The supplied environment script selects a new, separate QA database instead of altering the normal service database. This QA database retains its own NPC memories across later QA runs. If its dashboard settings are changed later, save the local provider/endpoint/models there and leave cloud consent off; merely dot-sourcing the script cannot override saved dashboard configuration. The script does not copy existing profiles, memories or dashboard pairing state.

`LLS_OPENAI_BASE_URL` selects a hosted endpoint only with the hosted provider; it is not set here. `.env.example` is documentation and is not automatically loaded. Local endpoint validation requires literal loopback IPs, no credentials/query/fragment; model HTTP clients disable redirects and environment proxies.

Relevant unchanged defaults (these are Settings/GatewayConfig fields, **not supported environment variable names**):

| Control | Default / observed behavior |
| --- | --- |
| Foreground allowance | 100 model requests/day; configured USD limit $1, local attempts cost $0 |
| Background allowance | 0 requests/day, $0 |
| Request reservation | Each model HTTP attempt consumes a request; action selection/repair can consume additional requests |
| Dialogue/action output | 300 / 200 tokens; skill output 1,200 tokens |
| Gateway prompt reservation ceiling | 16,384 tokens, distinct from the server's actual 8,192 context |
| Model timeout | 30 s overall; HTTP connection timeout 5 s |
| Runtime deadlines | Dialogue 30 s, action stage 20 s |
| Context budget | 12,000 UTF-8 bytes; memory and knowledge 512 tokens each |
| Response bounds | 262,144 bytes, 1,200 dialogue characters |
| Concurrency/rate | 2 gateway requests maximum; 12 player turns/minute per session; server uses one slot |

Budgets can be configured through the supported dashboard; Settings.from_env does not expose timeout/context/GatewayConfig budget overrides. No invented timeout or budget environment variables were set. The 100-request default can be consumed in fewer than 100 conversations when action selection also runs. Larger authored contexts may need a server context adjustment; only the short contexts below were measured.

## Official downloads and integrity

GitHub API `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest` returned stable **v0.6.0**, published 2026-10-05, `prerelease=false`. That stable release contains `nightly-tag.txt` rather than platform archives; its official pointer resolves to **b11429**:

- Stable release: https://github.com/ggml-org/llama.cpp/releases/tag/v0.6.0
- Stable build pointer: https://github.com/ggml-org/llama.cpp/releases/download/v0.6.0/nightly-tag.txt
- Binary release: https://github.com/ggml-org/llama.cpp/releases/tag/b11429

The linked binary release is itself labelled `prerelease=true` by GitHub, but it is the build selected by the current stable release. The newer b11552 prerelease was not selected. All binary URLs were verified to belong to `ggml-org/llama.cpp`. `llama-server --version` reports `0.6.0-dev (build 11429, commit d81235049)`, built with Clang 20.1.8 for Windows x86_64.

Model repository: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF. Revision pinned to `bb5d59e06d9551d752d08b292a50eb208b07ab1f`. The official Q4_K_M model consists of two shards; llama.cpp loads both by opening shard 1. No merge/re-quantization was needed. Total GGUF size: 4,683,073,632 bytes (about 4.36 GiB). Model README and license are retained under `models`.

- **llama-b11429-bin-win-vulkan-x64.zip**
  - URL: https://github.com/ggml-org/llama.cpp/releases/download/b11429/llama-b11429-bin-win-vulkan-x64.zip
  - Size: 33,337,769 bytes
  - SHA-256: `1bfe78ad9168b79fa02bf67f6af9f5e17a966d824d77238517f7bef12ac73b36`
  - Verified against GitHub release asset digest.
- **qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf**
  - URL: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/bb5d59e06d9551d752d08b292a50eb208b07ab1f/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf
  - Size: 3,993,201,344 bytes
  - SHA-256: `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db`
  - Verified against official Hugging Face LFS SHA-256.
- **qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf**
  - URL: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/bb5d59e06d9551d752d08b292a50eb208b07ab1f/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf
  - Size: 689,872,288 bytes
  - SHA-256: `539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a`
  - Verified against official Hugging Face LFS SHA-256.

Downloads were anonymous HTTPS file transfers, not inference calls. Final downloads used the existing Git-bundled curl, with `?download=true` on Hugging Face URLs, followed by complete SHA-256 validation. Source API responses, zip and verified metadata remain under the tools directory. No new Python packages or runtimes were installed.

## Server and memory

Scripts:

- `C:\Users\Brian\.lls-tools\llm\start-llm.ps1`: starts a hidden, detached process, waits for `/health`, writes `llama-server.pid` plus executable/start-time identity in `server-state.json`, and logs under `logs`.
- `C:\Users\Brian\.lls-tools\llm\stop-llm.ps1`: checks PID, exact executable and start time before stopping only its managed process; retains `last-server-state.json` and removes active PID/state files.
- `C:\Users\Brian\.lls-tools\llm\service-env.ps1`: dot-source in the service's shell.

Actual llama command options (paths are supplied by the script):

```text
--model models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf
--alias qwen2.5-7b-instruct --host 127.0.0.1 --port 8091
--device Vulkan0 --gpu-layers all --ctx-size 8192 --parallel 1
--batch-size 512 --ubatch-size 128 --threads 8 --threads-batch 8
--threads-http 4 --flash-attn on --cache-ram 0 --fit off
--offline --no-webui --no-agent
```

`--list-devices` identified Vulkan0 as AMD Radeon RX 9070 XT (16,304 MiB); Vulkan1 is the integrated AMD graphics device. The explicit Vulkan0 selection uses the discrete GPU. `/props` verified one slot, context 8,192 and build b11429-d81235049. Windows TCP inspection verified the listener was only `127.0.0.1:8091`.

Windows GPU Process Memory counters measured **5,008,203,776 dedicated bytes (4.66 GiB)** for server PID 16200 across its device instances. The main device allocation was 5,008,084,992 bytes; the other instance used 118,784 bytes. This is a process memory counter, not an FPS or available-VRAM guarantee. GTA was absent during measurement. The 7B model was fast enough in this idle-GPU test that no 3B fallback was installed.

Startup reached health readiness in about 5.6 seconds including the launcher health poll. The log reports model loaded at approximately 4.15 seconds. Logs include harmless model metadata token-type and cache-idle-slots notices; no inference errors occurred. The server logs its expected no-API-key notice; it is loopback-only with its web UI and agent tools disabled.

## Measurements

Three short, distinct streamed OpenAI-compatible prompts, max 96 output tokens, cache_prompt=false, no GTA workload. First-token timing uses the first nonempty content event; throughput is llama.cpp's reported decode rate. Startup model loading is excluded. Run 1 is the first request after server load/warmup, not a cold OS boot.

| Run | Input tokens | Output tokens | First token ms | Complete ms | Decode tokens/s |
| --- | --- | --- | --- | --- | --- |
| 1 | 52 | 33 | 367.04 | 784.13 | 76.78 |
| 2 | 55 | 35 | 62.63 | 436.45 | 91.04 |
| 3 | 54 | 21 | 48.37 | 264.18 | 92.74 |

The service integration check uses the repository's unchanged `service.lls` normal entrypoint (`Settings.from_env -> create_app -> uvicorn.run`) through an external audit wrapper. `--demo` and the existing live-check fixture server hard-code the fake provider, so neither was used to establish real-model success. The external `probe-service.py` uses a separate SQLite database for each run and the actual localhost HTTP bridge: session creation, world snapshot, civilian actor snapshot, typed `player.utterance_text`, command polling, dialogue acknowledgements and session close. The simulated adapter advertised no action capabilities; this test verifies dialogue, not action execution.

| Service run | First dialogue chunk | Complete dialogue | Input/output tokens | Result |
| --- | --- | --- | --- | --- |
| Initial real turn | 414.62 ms | 773.49 ms | 329 / 39 | Passed |
| Rerun with HTTP dispatch audit | 162.82 ms | 382.92 ms | 329 / 25 | Passed |

The service emits sentence chunks, so first dialogue chunk is different from model first-token latency. The second service run reused a warm server and may benefit from prompt caching. Service trace reported generated dialogue in 766 ms and 375 ms respectively, and the durable ledger settled each real local request at $0.

Player: “Hey neighbor, what makes a quiet evening in Los Santos worth enjoying?”

Initial real NPC reply (Vespucci Neighbor):

> A quiet evening in Los Santos?  There's the sound of the sea, the smell of the ocean, and maybe a cold drink on the porch.  Not much, but it's home.

Audited rerun reply:

> Hello, it's simple really.  A good meal, maybe some music, and company that doesn't mind the quiet.

**Zero cloud model requests were observed for both service probes.** The strengthened second run records exactly one real `POST http://127.0.0.1:8091/v1/chat/completions` at httpx dispatch, with `authorization_header_present=false`; it matches one settled `local_openai` ledger request. Cloud consent was false, no inherited model credentials were passed to the service, and no non-loopback network events were attempted. The external instrumentation rejects non-loopback HTTP dispatches and Python socket/DNS events while delegating allowed sends to the real HTTP client. llama.cpp was launched with a local model path and `--offline`.

Audit scope is the tested Python service and its real HTTP dispatches, not a whole-machine packet capture. Windows asyncio IOCP ConnectEx can bypass Python's socket.connect audit; that was discovered in the first run and addressed by adding actual httpx.AsyncClient.send auditing for the second run. The unrelated loopback socket entries in the raw socket log are not presented as evidence of the model request.

## Start QA

In a dedicated PowerShell window:

```powershell
Set-Location 'C:\Users\Brian\Documents\Github\smartfellas-GTA-wt\integration'
& 'C:\Users\Brian\.lls-tools\llm\start-llm.ps1'
. 'C:\Users\Brian\.lls-tools\llm\service-env.ps1'
& 'C:\Users\Brian\.lls-tools\uv\uv.exe' run --frozen --offline python -m service.lls
```

The service normally listens on `127.0.0.1:8765`; the isolated probe used 18765. If 8765 already belongs to another QA worker's service, coordinate with that worker; do not terminate their process. The new QA database uses the normal local pairing/session flow. Preserve any separately configured local speech settings if speech QA is desired; this setup supplies the language model only.

After QA, press Ctrl+C to stop that foreground service, then run:

```powershell
& 'C:\Users\Brian\.lls-tools\llm\stop-llm.ps1'
```

Closing that dedicated shell discards its process-local environment. Nothing is written to user/machine environment settings.

To repeat only the measured probes while llama is running, from the integration directory:

```powershell
& '.\.venv\Scripts\python.exe' -B 'C:\Users\Brian\.lls-tools\llm\benchmark.py'
& '.\.venv\Scripts\python.exe' -B 'C:\Users\Brian\.lls-tools\llm\probe-service.py'
```

## Evidence and final state

Machine-readable evidence under `C:\Users\Brian\.lls-tools\llm`:

- `source-metadata.json`, `downloads/stable-release.json`, `downloads/binary-release.json`.
- `benchmark-results.json` (all benchmark prompts, replies, usage and server timings).
- `machine-validation.json` (GPU memory, actual server properties and TCP bindings).
- `probe-runs/20261011T074612-744045Z/result.json` (first end-to-end turn).
- `probe-runs/20261011T074719-356119Z/result.json`, `service-network.jsonl`, `service.log` (strengthened audit and actual dialogue).
- `logs/llama-20261011-034548-441.stderr.log` and matching stdout log.
- `shutdown-verification.json`, `last-server-state.json`.

PowerShell parsed all three requested scripts with zero errors. `start-llm.ps1` actually started PID 16200 and passed `/health`; `stop-llm.ps1` actually stopped it. Service probe PIDs 59084 and 62020 exited, and downloader processes also exited. At `2026-10-11T07:48:10.6407952Z`, there were no remaining task processes, no listeners on 8091/18765, no active PID file, and `LLS_PROVIDER`/`LLS_CLOUD_OPT_IN` were unset in the task's clean shell. Both repository status checks were clean.

No blocking setup problem remains. The remaining practical limits are GTA sharing the GPU, untested live speech/native action behavior, model dialogue quality over longer sessions, context growth and the default request allowance. Actual in-game QA and before/after recordings are not claimed by this report.
