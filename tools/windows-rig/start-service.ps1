# Start the Living Los Santos service for in-game QA, detached, logging to artifacts/rig.
# -Provider local_openai expects the local llama.cpp server from start-llm.ps1 (no cloud, no keys).
# -Provider claude sends NPC context to Anthropic's API. Choosing it is the explicit cloud opt-in.
#   Paste your Anthropic key into secrets\LLM_API_KEY.txt (git-ignored); see secrets\README.md.
param([ValidateSet('fake','local_openai','claude','disabled')][string]$Provider = 'fake',
      [string]$Model = 'claude-opus-5-5',
      [string]$Repo = '')
. (Join-Path $PSScriptRoot 'rig-paths.ps1')
if (-not $Repo) { $Repo = $script:RigRepo }
$evidence = $script:RigEvidence
New-Item -ItemType Directory -Force $evidence | Out-Null
$env:PATH = (Split-Path $script:RigUv) + ";$env:PATH"
$env:PYTHONIOENCODING = 'utf-8'
$env:LLS_DATABASE = "$evidence\qa.sqlite3"
$env:LLS_RELEASE = '0'            # Debug adapter exposes the unverified face/wait primitives.
$env:LLS_PROVIDER = $Provider
# Clear earlier cloud choices from this window so another provider never inherits them.
$env:LLS_CLOUD_OPT_IN = '0'
Remove-Item Env:LLS_AI_GATEWAY_FILE -ErrorAction SilentlyContinue
if ($Provider -eq 'local_openai') {
    $env:LLS_LOCAL_BASE_URL = 'http://127.0.0.1:8091/v1'
    $env:LLM_DIALOGUE_MODEL = 'qwen2.5-7b-instruct'
    $env:LLM_ACTION_MODEL = 'qwen2.5-7b-instruct'
}
if ($Provider -eq 'claude') {
    # The service reads the key from this git-ignored file; see secrets\README.md.
    $keyFile = Join-Path $Repo 'secrets\LLM_API_KEY.txt'
    $hasKey = $env:LLM_API_KEY -or ((Test-Path $keyFile) -and
        (Get-Content $keyFile | Where-Object { $_.Trim() -and -not $_.TrimStart().StartsWith('#') }))
    if (-not $hasKey) {
        if (-not (Test-Path $keyFile)) {
            Set-Content -Path $keyFile -Encoding utf8 -Value @(
                '# Paste your Anthropic API key on the line below, then save this file.',
                '# This file is git-ignored: it stays on this computer and is never committed.', '')
        }
        Start-Process notepad.exe $keyFile
        throw "Paste your Anthropic API key into $keyFile (now open in Notepad), save it, then run this again."
    }
    $gateway = Join-Path $PSScriptRoot 'claude-gateway.json'
    if (-not (Get-Content $gateway -Raw | ConvertFrom-Json).prices.$Model) {
        throw "Add input and output prices for $Model to $gateway before using it."
    }
    $env:LLS_PROVIDER = 'anthropic'
    $env:LLS_CLOUD_OPT_IN = '1'
    $env:LLM_DIALOGUE_MODEL = $Model
    $env:LLM_ACTION_MODEL = $Model
    $env:LLS_ANTHROPIC_EFFORT = 'medium'
    $env:LLS_AI_GATEWAY_FILE = $gateway   # Prices, daily limits and output budgets.
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$p = Start-Process -FilePath $script:RigUv -ArgumentList @('run','--frozen','python','-m','service.lls') `
     -WorkingDirectory $Repo -PassThru -WindowStyle Hidden `
     -RedirectStandardOutput "$evidence\service-$stamp.out.log" -RedirectStandardError "$evidence\service-$stamp.err.log"
$p.Id | Set-Content "$evidence\service.pid"
for ($i = 0; $i -lt 40; $i++) {
    try { $h = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500; continue }
    "service up (pid $($p.Id)): " + ($h | ConvertTo-Json -Compress)
    if ($env:LLS_PROVIDER -ne $h.provider) {
        Write-Warning ("Saved dashboard settings in $($env:LLS_DATABASE) override the environment: the service uses " +
            "'$($h.provider)'. Select the provider at http://127.0.0.1:8765/dashboard, or start with a new LLS_DATABASE.")
    }
    return
}
"service did not answer /health; see $evidence\service-$stamp.err.log"
