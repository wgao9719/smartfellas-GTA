# API key

Each person uses their own Anthropic API key. Git ignores everything in this folder except this file, so a key saved here stays on your computer and is never committed or pushed.

## Set it up

1. Get a key at https://platform.claude.com/settings/keys.
2. Paste it into `secrets/LLM_API_KEY.txt`, on the first line that doesn't start with `#`, then save.
   - On the Windows rig, `tools\windows-rig\start-service.ps1 -Provider claude` creates the file and opens it in Notepad if your key is missing.
   - Otherwise, the service creates the file the first time it starts with a hosted provider, or you can create it yourself.
3. Start the service. It reads the key at startup.

A key already set in the `LLM_API_KEY` environment variable takes precedence over the file. To keep the file somewhere else, set `LLS_API_KEY_FILE` to its absolute path.

Never paste the key into any other file in this repository. A key pushed to GitHub is found by automated scanners within minutes. If that happens, delete the key at platform.claude.com and create a new one.
