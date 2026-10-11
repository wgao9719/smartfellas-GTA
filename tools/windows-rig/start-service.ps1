# Start the Living Los Santos service for in-game QA, detached, logging to artifacts/rig.
# -Provider local_openai expects the local llama.cpp server from start-llm.ps1 (no cloud, no keys).
param([ValidateSet('fake','local_openai','disabled')][string]$Provider = 'fake',
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
if ($Provider -eq 'local_openai') {
    $env:LLS_CLOUD_OPT_IN = '0'
    $env:LLS_LOCAL_BASE_URL = 'http://127.0.0.1:8091/v1'
    $env:LLM_DIALOGUE_MODEL = 'qwen2.5-7b-instruct'
    $env:LLM_ACTION_MODEL = 'qwen2.5-7b-instruct'
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$p = Start-Process -FilePath $script:RigUv -ArgumentList @('run','--frozen','python','-m','service.lls') `
     -WorkingDirectory $Repo -PassThru -WindowStyle Hidden `
     -RedirectStandardOutput "$evidence\service-$stamp.out.log" -RedirectStandardError "$evidence\service-$stamp.err.log"
$p.Id | Set-Content "$evidence\service.pid"
for ($i = 0; $i -lt 40; $i++) {
    try { $h = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2; "service up (pid $($p.Id)): " + ($h | ConvertTo-Json -Compress); return } catch { Start-Sleep -Milliseconds 500 }
}
"service did not answer /health; see $evidence\service-$stamp.err.log"
