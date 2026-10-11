# QA evidence helpers for the running Living Los Santos service (local admin API).
# Dot-source after rig.ps1:  . .\tools\windows-rig\rig-qa.ps1
. (Join-Path $PSScriptRoot 'rig-paths.ps1')
$script:LlsBase = 'http://127.0.0.1:8765'
$script:LlsEvidence = $script:RigEvidence
$script:LlsProfile = $script:RigProfile

function Get-LlsAdmin {
    # The dashboard page embeds a per-process admin token and CSRF token for local requests.
    $page = Invoke-WebRequest "$script:LlsBase/dashboard" -UseBasicParsing -TimeoutSec 5
    $m = [regex]::Match($page.Content, '\{"token":\s*"([^"]+)",\s*"csrf":\s*"([^"]+)"\}')
    if (-not $m.Success) { throw 'admin bootstrap not found in dashboard' }
    @{ Authorization = 'Bearer ' + $m.Groups[1].Value; 'X-CSRF-Token' = $m.Groups[2].Value }
}
function Invoke-LlsAdmin([string]$Path, [string]$Method = 'GET', $Body = $null) {
    $h = Get-LlsAdmin
    # Mutations must look same-origin, like the dashboard page itself.
    if ($Method -ne 'GET') { $h['Origin'] = $script:LlsBase; $h['Sec-Fetch-Site'] = 'same-origin' }
    $args = @{ Uri = "$script:LlsBase/v1/admin$Path"; Method = $Method; Headers = $h; TimeoutSec = 10 }
    if ($Body -ne $null) { $args.Body = ($Body | ConvertTo-Json -Depth 8); $args.ContentType = 'application/json' }
    Invoke-RestMethod @args
}
function Save-LlsEvidence([string]$Name, $Object) {
    $p = Join-Path $script:LlsEvidence "evidence\$Name.json"
    New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null
    $Object | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 $p
    $p
}
function Get-LlsOverview { Invoke-LlsAdmin '/overview' }
function Get-LlsActors { Invoke-LlsAdmin "/profiles/$script:LlsProfile/actors" }
function Get-LlsMemories { Invoke-LlsAdmin "/profiles/$script:LlsProfile/memories" }
function Get-LlsSessionDiagnostics([string]$SessionId) { Invoke-LlsAdmin "/sessions/$SessionId/diagnostics" }
function Get-AdapterLogTail([int]$Lines = 20) {
    $dir = Join-Path $script:RigGtaRoot 'scripts\LivingNPC'
    Get-ChildItem $dir -Filter 'LivingNPC-*.log' | Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { Get-Content $_.FullName -Tail $Lines }
}
