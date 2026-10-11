[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rig-paths.ps1')
$root = $script:RigLlmHome
$stateFile = Join-Path $root 'server-state.json'
$pidFile = Join-Path $root 'llama-server.pid'
if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Output 'No managed llama server is recorded.'
    return
}
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if ($process) {
    $expected = Join-Path $root 'llama-b11429-vulkan\llama-server.exe'
    if ($state.executable -ne $expected -or $process.Path -ne $expected -or $process.StartTime.ToUniversalTime().Ticks.ToString() -ne $state.startTicks) {
        throw 'PID identity does not match this installation. No process was stopped.'
    }
    Stop-Process -InputObject $process
    if (-not $process.WaitForExit(15000)) { throw 'Managed llama server did not stop.' }
    Write-Output "Stopped managed llama server PID $($state.pid)."
} else {
    Write-Output 'The recorded llama server has already exited.'
}
Copy-Item -LiteralPath $stateFile -Destination (Join-Path $root 'last-server-state.json') -Force
Remove-Item -LiteralPath $stateFile -Force
if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
