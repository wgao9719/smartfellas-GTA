[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rig-paths.ps1')
$root = $script:RigLlmHome
$exe = Join-Path $root 'llama-b11429-vulkan\llama-server.exe'
$model = Join-Path $root 'models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf'
$stateFile = Join-Path $root 'server-state.json'
$pidFile = Join-Path $root 'llama-server.pid'
if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Path -eq $state.executable -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.startTicks) {
            Write-Output "The managed llama server is already running (PID $($existing.Id))."
            return
        }
        throw 'Saved PID belongs to a different process. Refusing to start or stop it.'
    }
}
foreach ($required in @($exe, $model, (Join-Path $root 'models\qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing file: $required" }
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse('127.0.0.1'), 8091)
try { $listener.Start() } finally { $listener.Stop() }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $root "logs\llama-$stamp.stdout.log"
$stderr = Join-Path $root "logs\llama-$stamp.stderr.log"
# Explicit local files and offline mode; one slot limits VRAM while GTA shares the GPU.
$arguments = @('--model', ('"' + $model + '"'), '--alias', 'qwen2.5-7b-instruct',
    '--host', '127.0.0.1', '--port', '8091', '--device', 'Vulkan0', '--gpu-layers', 'all',
    '--ctx-size', '8192', '--parallel', '1', '--batch-size', '512', '--ubatch-size', '128',
    '--threads', '8', '--threads-batch', '8', '--threads-http', '4', '--flash-attn', 'on',
    '--cache-ram', '0', '--fit', 'off', '--offline', '--no-webui', '--no-agent')
$process = $null
try {
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
    @{ pid = $process.Id; executable = $exe; startTicks = $process.StartTime.ToUniversalTime().Ticks.ToString(); stdout = $stdout; stderr = $stderr; arguments = $arguments } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateFile -Encoding utf8
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) { throw "llama-server exited; inspect $stderr" }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8091/health' -TimeoutSec 2
            if ($health.status -eq 'ok') {
                Write-Output "Local llama server ready at http://127.0.0.1:8091/v1 (PID $($process.Id))."
                Write-Output "Log: $stderr"
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "llama-server did not become ready within 120 seconds; inspect $stderr"
} catch {
    if ($process) {
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-Process -InputObject $process
            $null = $process.WaitForExit(15000)
        }
        if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile -Force }
        if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
    }
    throw
}
