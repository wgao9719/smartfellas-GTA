# Dependency and asset record

This is a development inventory, not release license approval.
No GTA game files or speech model weights are bundled.

| Component | Role | License or release requirement |
| --- | --- | --- |
| Original Living Los Santos code and character content | Service and adapter | The project owner must select a distribution license before release. |
| GTA V Legacy | User-supplied Windows Story Mode game | Proprietary. Never package the game or its assets. |
| Script Hook V | User-installed native loader | Check the pinned distribution's current terms before release. |
| ScriptHookVDotNet | Managed GTA API and runtime | Upstream uses zlib licensing. Keep its notices with any permitted redistributed component. Pin and verify the runtime separately. |
| .NET reference assemblies | Cross-platform compilation of net48 | NuGet dependency. Audit the resolved package notices before distribution. |
| Python, FastAPI, Pydantic, HTTPX, Uvicorn, PyYAML | Local backend dependencies | Audit the lockfile and resolved package licenses for each release. |
| sounddevice, CFFI, pycparser | Optional microphone/playback stack | Audit these Python packages and native audio libraries in each platform wheel. The optional wheels and their PortAudio contents have not been inspected locally. |
| SQLite | Local durable storage | Public domain upstream. Verify FTS5 availability on Windows and macOS. |
| SkyrimNet reference gitlink | Read-only design reference | Not a release dependency. Do not copy dialogue, assets, or code without applicable permission. |
| whisper.cpp | Implemented adapter for a user-supplied executable and model | MIT upstream. Model files have separate provenance and checksums. No executable or model currently bundled. |
| Kokoro | Planned local speech synthesis candidate | Verify the exact model, voice, runtime, and notices before bundling. Model quality and Windows performance are unverified. |
| Piper | Implemented adapter for a user-supplied executable and voice | Current OHF runtime is GPL-3.0. Each voice has separate terms. No bundling decision has been made. |

Kokoro runtime and pronunciation dependencies remain unselected. Audit the exact
dependency graph before choosing or bundling it. A model's license does not cover
its runtime or pronunciation libraries. No current project dependency includes
Kokoro or espeak-ng.

Record each approved voice's model version, voice ID, source, checksum, license,
and permitted use before distributing it. Do not clone GTA performers or other
real people without the necessary rights.

Release packaging must exclude `references/`, local databases, credentials,
microphone recordings, build caches, and unapproved model files.
