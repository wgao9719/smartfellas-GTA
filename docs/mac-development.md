# Mac development

The Mac builds the adapter and runs service tests. GTA runs on the Windows rig. G0 remains unverified until the rig and second-Mac checks pass.

1. Install Python 3.11 or 3.12 and the .NET 10 SDK from their official distributions.
2. Install the Python dependencies with `uv sync --group dev`.
3. Fetch the public compile reference.

   ```sh
   python3 tools/fetch_shvdn_reference.py
   ```

4. Build the adapter.

   ```sh
   dotnet build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj -c Debug
   ```

5. Run the C# policy, lifecycle, and HTTP-lane tests.

   ```sh
   dotnet test adapter/LivingNPC.Core.Tests/LivingNPC.Core.Tests.csproj
   ```

6. Run the Python tests.

   ```sh
   uv run pytest
   ```

7. Start an isolated fake-provider service for the cross-language probe.

   ```sh
   LLS_PROVIDER=fake LLS_DATABASE=/tmp/lls-bridge-probe.sqlite3 LLS_PORT=18765 uv run python -m service.lls
   ```

8. Run the C# HTTP probe in another terminal.

   ```sh
   dotnet run --project adapter/LivingNPC.BridgeProbe/LivingNPC.BridgeProbe.csproj -- http://127.0.0.1:18765
   ```

9. Stage a checksum-labeled deployment bundle.

   ```sh
   python3 tools/deploy_adapter.py --configuration Debug
   ```

The build output is `adapter/LivingNPC.GameAdapter/bin/Debug/net48/`. The staged bundle is `artifacts/adapter/`. The build writes `adapter-build-identity.json` beside the DLL. Staging verifies its DLL hash and configuration. The deployment manifest copies the build identity and records payload hashes. The DLL informational version contains the build source commit. Staging never substitutes the current checkout for an older build.

The reference DLL and generated binaries are ignored. Never commit Script Hook V, the ASI loader, or proprietary game assets. `fetch_shvdn_reference.py` verifies the official archive and DLL hashes before installation.

This workspace also supports an isolated SDK in `.dotnet/`. Replace `dotnet` with `.dotnet/dotnet` when using that installation. A normal system SDK requires no task-specific path.

## Tailscale development

The Windows rig will connect later. The following procedure is prepared, but has not run against a rig.

WARNING: Keep the service token private. A person with the token and an allowed network source can open a bridge session.

1. Connect the Mac and Windows rig to the same authorized Tailscale network.
2. Record each machine's Tailscale IPv4 address.
3. Set a random token of at least 24 characters in `LLS_BRIDGE_TOKEN` on both processes.
4. Bind the service to the Mac's exact Tailscale address.

   ```sh
   LLS_RELEASE=0 LLS_DEV_LAN=1 LLS_HOST=100.100.1.10 LLS_ALLOW_SOURCES=100.100.1.20 uv run python -m service.lls
   ```

5. Set `URL=http://100.100.1.10:8765` and `DevLAN=true` in the rig's `scripts/LivingNPC/LivingNPC.ini`.
6. Permit the rig's exact source address through the Mac firewall and Tailscale access policy.
7. Deploy through the rig's configured Windows OpenSSH server.

   ```sh
   python3 tools/deploy_adapter.py --configuration Debug --ssh-target player@100.100.1.20 --remote-scripts 'C:\Games\Grand Theft Auto V\scripts'
   ```

8. Verify the deployment receipt before running `Reload()` in the SHVDN console.

Replace the example addresses and game path with the rig's actual values. The adapter accepts explicit RFC1918 addresses and Tailscale's `100.64.0.0/10` range only in a Debug build with development settings. Release refuses remote hosts.

The deployment tool uses SFTP, verifies remote hashes, makes a backup, and rolls back copied files after a replacement failure. It uploads its PowerShell script with the bundle and runs that file by path, so the SSH command stays far below the 8191-character `cmd.exe` limit of Windows OpenSSH. It removes the upload directory after success or failure. It never changes installed loaders or the user's INI. It installs the configuration template at `scripts/LivingNPC/LivingNPC.ini.example`. The tool needs an existing scripts directory and configured SSH access. A locked DLL causes deployment failure. Close GTA if the pinned runtime does not release its files during reload.

Tailscale latency cannot establish release performance. In development mode, microphone capture and speech output occur on the Mac that runs the service.

Use the [Windows post-Tailscale procedure](windows-runtime.md#after-the-tailscale-connection) for configuration, loaded-build checks, and G0 evidence collection.
