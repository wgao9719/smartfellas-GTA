# Install Script Hook V + SHVDN + the Living Los Santos Debug adapter into GTA V Legacy for G0 QA.
# Records every file it adds so rig-uninstall.ps1 can restore the original folder exactly.
# Paths come from rig-paths.ps1 (override with LLS_GTA_ROOT, LLS_TOOLS, LLS_RIG_EVIDENCE, LLS_QA_PROFILE, ...).
param([string]$Repo = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rig.ps1')
if (-not $Repo) { $Repo = $script:RigRepo }
$root = $script:RigGtaRoot
$evidence = $script:RigEvidence
$dl = $script:RigDownloads
if (Get-Process GTA5, PlayGTAV -ErrorAction SilentlyContinue) { throw 'Close GTA V before installing.' }
New-Item -ItemType Directory -Force $evidence | Out-Null

# 1. Before snapshot (top level only; game archives are never touched).
if (-not (Test-Path "$evidence\game-root-before.csv")) { Get-RigGameRootSnapshot $root "$evidence\game-root-before.csv" | Out-Null }
$before = Import-Csv "$evidence\game-root-before.csv"
foreach ($n in 'dinput8.dll','ScriptHookV.dll','scripts','ScriptHookVDotNet.asi') {
    if ($before.Name -contains $n) { throw "$n already exists in the game root; refusing to overwrite." }
}

# 2. Extract into a staging folder, then copy only what is needed.
$stage = Join-Path $script:RigTools 'rig\stage'; Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
Expand-Archive "$dl\ScriptHookV_3889.0_1158.13.zip" "$stage\shv"
Expand-Archive "$dl\ScriptHookVDotNet-v3.7.0-nightly.193.zip" "$stage\shvdn"
$installed = New-Object System.Collections.Generic.List[string]
foreach ($f in 'dinput8.dll','ScriptHookV.dll','args.txt') {
    if ($before.Name -contains $f) { throw "$f already exists in the game root." }
    Copy-Item "$stage\shv\bin\$f" "$root\$f"; $installed.Add($f)
}
foreach ($f in Get-ChildItem "$stage\shvdn" -File | Where-Object { $_.Name -like 'ScriptHookVDotNet*' }) {
    Copy-Item $f.FullName "$root\$($f.Name)"; $installed.Add($f.Name)
}
New-Item -ItemType Directory "$root\scripts" | Out-Null; $installed.Add('scripts')

# 3. Build and deploy the Debug adapter from this repository.
$env:PATH = (Split-Path $script:RigUv) + ";$env:PATH"; $env:LLS_DOTNET = $script:RigDotnet
$env:RestoreSources = 'https://api.nuget.org/v3/index.json'; $env:DOTNET_NOLOGO = '1'
Push-Location $Repo
& $env:LLS_DOTNET build adapter/LivingNPC.GameAdapter/LivingNPC.GameAdapter.csproj --configuration Debug | Select-Object -Last 3
& uv run --frozen python tools/deploy_adapter.py --configuration Debug --scripts-dir "$root\scripts" | Tee-Object "$evidence\deploy-receipt.txt"
Pop-Location

# 4. INI for this rig. Profile/save labels are explicit, as SPECS §8.3 requires.
$ini = "$root\scripts\LivingNPC\LivingNPC.ini"
(Get-Content "$root\scripts\LivingNPC\LivingNPC.ini.example") `
    -replace '^ProfileId=$', "ProfileId=$($script:RigProfile)" `
    -replace '^SaveSlot=$', "SaveSlot=$($script:RigSaveSlot)" `
    -replace '^ScriptHookVBuild=.*$', 'ScriptHookVBuild=v3889.0' `
    -replace '^EnableUnverifiedActions=false$', 'EnableUnverifiedActions=true' | Set-Content -Encoding utf8 $ini

$installed | Set-Content -Encoding utf8 "$evidence\installed-files.txt"
Get-RigGameRootSnapshot $root "$evidence\game-root-modded.csv" | Out-Null
"Installed: " + ($installed -join ', ')
Get-ChildItem "$root\scripts" -Recurse | Select-Object FullName, Length | Format-Table -AutoSize | Out-String -Width 200
