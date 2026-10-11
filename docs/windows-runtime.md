# Windows runtime setup and G0 checks

G0 is not done. No game, loader, or SHVDN combination is currently certified by this repository. `compatibility.yaml` records unknown runtime fields as `UNVERIFIED`.

1. Install and launch a legally owned GTA V Legacy copy in Story Mode.
2. Capture five minutes of unmodded frame times.
3. Record the game build, storefront, hardware, and driver in `compatibility.yaml`.
4. Install a compatible Script Hook V and ASI loader from their official sources.
5. Install one complete compatible SHVDN release.
6. Install the .NET Framework 4.8 and required Visual C++ runtimes.
7. Confirm Story Mode starts without adapter errors.
8. Copy the staged adapter bundle into the game's existing `scripts` directory.
9. Copy `scripts/LivingNPC/LivingNPC.ini.example` to `scripts/LivingNPC/LivingNPC.ini`. Preserve an existing INI.
10. Set `ScriptHookVBuild` to the installed build in the INI.
11. Install Python 3.11 or 3.12 for the local service.
12. Install the project dependencies with `uv sync --group dev`.
13. Start the local service.

    ```powershell
    uv run python -m service.lls
    ```

14. Check `http://127.0.0.1:8765/health`.
15. Start Story Mode and inspect the latest `scripts/LivingNPC/LivingNPC-*.log` for `adapter_loaded`.
16. Press F6 near an eligible resident.
17. Press F7 to enter a message.
18. Hold N to request microphone capture.
19. Release N to stop capture and start transcription.
20. Hold B to request a whispered capture. Release B to stop it.
21. Start a typed message with `/w ` to whisper it. Start it with `//` to send a literal leading slash.
22. Press Backspace to cancel the current interaction.

The service defaults to AI disabled. The fake provider is for simulation tests. A hosted provider requires explicit configuration and cloud consent. PTT may report that speech is unavailable until microphone and transcription support is configured. The adapter shows `Recording` only after a service status confirms it, and `Recording (whispering)` for a whisper capture.

A whisper reaches only a target within the whisper hearing radius, which defaults to 3 meters. A more distant target shows `Too far to whisper.` and the service opens no microphone and runs no turn. `WhisperKey=B` is a provisional default. Verify on the rig that B does not conflict with a game binding before you rely on it.

The [speech runtime configuration](speech-coordinator.md#runtime-configuration) selects local speech programs, models, and audio devices. Startup does not open the microphone. Audio runs on the service computer, including a Mac during Tailscale development.

Path settings such as `LLS_DATABASE`, `LLS_WHISPER_MODEL`, and `LLS_PIPER_EXECUTABLE` expand `~` and `%VAR%` variables. The expanded path must be absolute, including its drive. The service refuses to start with a relative path instead of resolving it against the working directory.

Windows lists each audio endpoint once per host API under the same name. A configured device name selects one of them as described in [device selection](speech-probes.md#device-selection).

The adapter refuses actions in Online, missions, cutscenes, random events, loading screens, and unknown protagonist states. It limits targeting to the pilot civilian model allowlist. It never adopts a mission ped. These checks still require rig tests.

## Provisional native action test

CAUTION: Save the game before a development action test. Unverified game task behavior can interfere with ambient gameplay.

1. Deploy a Debug build.
2. Set `EnableUnverifiedActions=true` in the INI's `[Development]` section.
3. Reload the adapter through the SHVDN console.
4. Test `face_player` using its capability sheet.
5. Test `wait_here` using its capability sheet.
6. Record task traces, outcomes, and any engine takeover.
7. Restore `EnableUnverifiedActions=false` after the test.

Release builds expose no native actions until verified capability mappings exist. The current two executors accept at most five seconds. Other actions fail closed.

The adapter reports `task_dispatched=true` in a started acknowledgment. This confirms dispatch, not completion. Completed `face_player` acknowledgments require an observed active task and a heading error of at most 20 degrees. Completed `wait_here` acknowledgments require an observed active task, the full requested duration, and displacement of at most 1.5 meters.

Both completed acknowledgments include `distance_meters`, the cumulative distance between successive position samples. Tracking starts immediately before dispatch and continues through completion. The adapter uses a monotonic clock and rejects any sample interval above 250 milliseconds. It rejects unavailable positions, nonfinite coordinates, and backward timestamps. A failed measurement supplies no completion facts. A stationary sequence of valid samples can report zero distance.

The distance describes the sampled path. It cannot measure movement between samples. `wait_here` also reports `elapsed_ms` and `displacement_meters`. `face_player` also reports `heading_error_degrees`. These receipts remain unverified until the Windows rig confirms native behavior.

## Recovery and rollback

1. Stop the service during a pending interaction.
2. Verify regular GTA play continues.
3. Restart the service.
4. Verify the adapter opens a fresh session after authentication invalidates the old session.
5. Confirm old replies cannot act on the new runtime generation.
6. Reload the adapter after changing the DLL.
7. Compare the logged informational version with `scripts/adapter-build.json`.

If the loader locks a DLL, close GTA before deployment. The deployment tool stores replaced files under `LivingNPC-backup/<timestamp>/`. Restore one complete bundle from that directory when a build fails. Do not mix SHVDN binaries across releases.

SHVDN loads every DLL below `scripts`. Local deployment therefore stages files in a temporary `lls-stage-*` directory beside `scripts`, on the same volume. An interrupted deployment can leave that directory behind, but never a second adapter copy inside `scripts`. Delete a leftover `lls-stage-*` directory after the interrupted deployment ends.

The default profile is `default`. Use a distinct `ProfileId` for each save lineage when automatic timeline association is ambiguous. G0 must test full in-game dates after restart and rewind. Same-date divergent saves remain an explicit selection case.

## Evidence required before release

Record the 20-interaction trial ledger, 20-minute soak, FPS comparison, script tick percentiles, memory growth, microphone timings, and restart recovery. Include a Mac-built version and second-Mac reproduction. Keep native action results separate from simulated transport results.


## Installed paths and diagnostics

The DLLs remain directly under `scripts/`. The adapter reads only `scripts/LivingNPC/LivingNPC.ini`. Logs use the same `LivingNPC` directory. Deployment installs an INI template and preserves existing settings and logs. The adapter does not create a persistent local state database.

If an earlier setup used `scripts/LivingNPC.ini`, copy its settings into the new INI before reloading. The adapter does not read the old path.

Each adapter load creates a UTC timestamped log. `adapter_loaded` records the loaded assembly version and module ID. `installed_dll` records the file hash at startup. The installed file hash alone cannot prove that a later reload loaded that file. Bridge transitions and operational error codes follow. Logs exclude tokens, endpoints, player text, and model replies.

The log worker runs outside the script thread. Each log stops accepting bytes at 2 MiB. The adapter preserves earlier logs. A failed log write does not stop GTA. Missing logs therefore mean missing evidence, not a successful startup.

## After the Tailscale connection

This procedure is prepared for a connected rig. It has not run on Windows. Tailscale connectivity alone does not establish SSH access, loader compatibility, or GTA acceptance.

WARNING: Keep the bridge token private. Share it only with the authorized service and game processes.

1. Record both Tailscale IPv4 addresses and the rig's exact GTA `scripts` path.
2. Confirm the rig's existing OpenSSH account accepts the Mac's authorized SSH key.
3. Close GTA before replacing adapter files.
4. Build the Debug adapter on the Mac.

   ```sh
   dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj -c Debug
   ```

5. Deploy the bundle using the recorded rig values.

   ```sh
   python3 tools/deploy_adapter.py --configuration Debug \
     --ssh-target "$LLS_WINDOWS_USER@$LLS_WINDOWS_TAILSCALE_IP" \
     --remote-scripts "$LLS_WINDOWS_SCRIPTS"
   ```

   SFTP uploads the bundle, a deployment script, and its JSON parameters to `lls-upload-<timestamp>` in the SSH user's home. SSH then runs `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File lls-upload-<timestamp>/deploy.ps1`. This short command needs no quoting under the default `cmd.exe` shell or a PowerShell `DefaultShell`. The script reads the scripts path from the JSON data and removes its upload directory after success or failure. The tool also removes the upload through SFTP after a failed transfer or launch. A Group Policy execution policy overrides `Bypass` and stops the script.

6. Preserve the deployment receipt and staged `adapter-build.json` in the G0 evidence directory.
7. Create the installed INI from its template if no INI exists.

   ```powershell
   $LivingNpcScripts = 'C:\Games\Grand Theft Auto V\scripts'
   $LivingNpcData = Join-Path $LivingNpcScripts 'LivingNPC'
   $LivingNpcIni = Join-Path $LivingNpcData 'LivingNPC.ini'
   if (-not (Test-Path -LiteralPath $LivingNpcIni)) {
     Copy-Item -LiteralPath (Join-Path $LivingNpcData 'LivingNPC.ini.example') -Destination $LivingNpcIni
   }
   ```

8. Set these INI values using the actual Mac address and installed Script Hook V build.

   ```ini
   [Service]
   URL=http://100.100.1.10:8765
   DevLAN=true
   [Compatibility]
   ScriptHookVBuild=ACTUAL_INSTALLED_BUILD
   ServiceOS=macos
   ```

9. Supply the same private `LLS_BRIDGE_TOKEN` to the Mac service and GTA process environments.
10. Start the Mac service on its exact Tailscale address with only the rig's source address allowed.

    ```sh
    LLS_RELEASE=0 LLS_DEV_LAN=1 \
    LLS_HOST="$LLS_MAC_TAILSCALE_IP" LLS_ALLOW_SOURCES="$LLS_WINDOWS_TAILSCALE_IP" \
    uv run python -m service.lls
    ```

11. Retrieve `/health` from the rig using the configured Mac address.

    ```powershell
    Invoke-RestMethod 'http://100.100.1.10:8765/health' | ConvertTo-Json -Depth 6
    ```

12. Preserve `build.source_sha256`, `build.registry_sha256`, and `release_mode` from that response.
13. Start Story Mode with the recorded compatible loader and SHVDN release.
14. Inspect the latest adapter log for `adapter_loaded`, `installed_dll`, and `bridge_ready`.
15. Compare its version and installed DLL hash with the deployment manifest.
16. Run the target, text, PTT, whisper, and cancel checks from the setup procedure.
17. Run the provisional action capability sheet with the explicit Debug opt-in.
18. Record redeployment, `Reload()`, DLL-lock behavior, rollback, and service-restart recovery.
19. Repeat latency and performance trials with the service running locally on Windows.
20. Repeat build, deployment, and reload on a second Mac.

The commands assume SSH access and network policies already permit the connection. A failed connection requires diagnosis. These commands do not change firewall rules or Tailscale policy.

`build.source_sha256` identifies the service source checkpoint captured at startup. `build.kind=source_tree` and `build.packaged_build=false` do not establish a packaged release. `release_mode` describes configuration only. A Mac service uses Mac audio devices. The Tailscale run cannot establish Windows microphone behavior or local-release latency.

## G0 evidence index

Run `python3 tools/validate_gate_evidence.py /path/to/evidence/index.json` after collecting the actual artifacts. This tool checks an evidence index and file integrity. It never sets `g0_passed=true`. A reviewer must inspect native behavior, visual results, measurements, and all seven G0 procedures.

Keep unit tests, mock HTTP results, and simulated-game receipts outside the GTA evidence index. Their success remains development evidence. The validator rejects simulated origins and gameplay evidence that declares unverified game behavior.

Service health and build metadata identify the running software. They do not prove GTA behavior. The validator permits their truthful `game_verified=false` values and validates their structure and digests separately.

Use this exact top-level index shape:

```json
{
  "schema_version": 1,
  "gate": "G0",
  "environment": {},
  "artifacts": {},
  "checks": {}
}
```

The empty objects above are a format sketch. The validator rejects them. Populate `environment` with these fields:

- `os`, `hardware`, `driver`, and `storefront`.
- `gta_build`, `scripthookv_build`, and `shvdn_release`.
- `game_edition`, `mode`, `compat_layer`, and `service_host_mode`.
- `adapter_sha256` and `service_source_sha256`.

Final reference evidence requires `windows`, `legacy`, `story_mode`, `none`, and `local` for the corresponding environment fields. Keep development-network traces as G0-4 artifacts within that reference-rig evidence set.

Give every artifact a stable ID. Each artifact requires `path`, `sha256`, `kind`, and `origin`. Paths must resolve inside the evidence directory. Allowed origins are `windows_rig`, `mac_build`, and `second_mac`. Origin labels are declarations that the reviewer must verify.

| Check | Required artifact kinds |
|---|---|
| G0-1 | `baseline_frame_times` |
| G0-2 | `loaded_modules`, `gameplay_recording` |
| G0-3 | `mac_build`, `deployment_receipt`, `adapter_log`, `gameplay_recording` |
| G0-4 | `service_health`, `request_trace`, `network_configuration` |
| G0-5 | `audio_control_trace`, `gameplay_recording`, `database_check` |
| G0-6 | `trial_ledger`, `performance_capture`, `restart_trace` |
| G0-7 | `independent_reproduction`, `deployment_receipt`, `adapter_log` |

Each check requires `scenario`, `expected_result`, `source_paths`, `inputs`, `procedure`, `result`, `artifacts`, and `review`. Use lists for source paths, procedure steps, and artifact IDs.

`result` requires numeric `measurements`, a positive `sample_count`, and a `failures` list. Narrative pass booleans are invalid. G0-1 requires `duration_seconds >= 300`. G0-6 requires `duration_seconds >= 1200`, `interaction_count >= 20`, `bridge_crashes = 0`, and `bridge_deadlocks = 0`. The reviewer must verify those numbers against the referenced captures.

`review` requires `reviewer`, a timezone-qualified `recorded_at`, `manual_observations`, and `limitations`. Use lists for observations and limitations. Include recording timestamps for visual and native observations. A valid index remains pending the manual gate decision.
