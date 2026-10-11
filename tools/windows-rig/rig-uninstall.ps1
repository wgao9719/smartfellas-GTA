# Remove everything rig-install.ps1 added, keep a zip of logs/configuration, and prove the game root matches the before snapshot.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rig.ps1')
$root = $script:RigGtaRoot
$evidence = $script:RigEvidence
if (Get-Process GTA5, PlayGTAV -ErrorAction SilentlyContinue) { throw 'Close GTA V before uninstalling.' }
$installed = Get-Content "$evidence\installed-files.txt"
# Keep adapter logs, SHV/SHVDN logs and the INI as evidence before removal.
$keep = "$evidence\rig-backup"; New-Item -ItemType Directory -Force $keep | Out-Null
foreach ($log in 'ScriptHookV.log','ScriptHookVDotNet.log','asiloader.log') { if (Test-Path "$root\$log") { Copy-Item "$root\$log" $keep } }
if (Test-Path "$root\scripts") { Compress-Archive -Force "$root\scripts\*" "$keep\scripts.zip" }
foreach ($name in $installed) { Remove-Item -Recurse -Force -LiteralPath "$root\$name" -ErrorAction SilentlyContinue }
# Logs and backups created by the loaders / deploy tool at the root are also mod artifacts.
foreach ($extra in 'ScriptHookV.log','ScriptHookVDotNet.log','asiloader.log','LivingNPC-backup') { Remove-Item -Recurse -Force -LiteralPath "$root\$extra" -ErrorAction SilentlyContinue }
Get-ChildItem -LiteralPath $root -Directory -Filter 'lls-stage-*' | Remove-Item -Recurse -Force
$after = Get-RigGameRootSnapshot $root "$evidence\game-root-after.csv"
$before = Import-Csv "$evidence\game-root-before.csv"
$diff = Compare-Object ($before | ForEach-Object { "$($_.Name)|$($_.SHA256)" }) ($after | ForEach-Object { "$($_.Name)|$($_.SHA256)" })
if ($diff) { "DIFFERENCES vs before snapshot:"; $diff | Format-Table | Out-String } else { "Game root matches the before snapshot (names + hashes of small files)." }
