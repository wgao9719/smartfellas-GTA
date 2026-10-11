# Shared locations for the Windows rig scripts. Every value can be overridden by an environment variable.
$script:RigRepo = if ($env:LLS_REPO) { $env:LLS_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
$script:RigTools = if ($env:LLS_TOOLS) { $env:LLS_TOOLS } else { Join-Path $env:USERPROFILE '.lls-tools' }
$script:RigGtaRoot = if ($env:LLS_GTA_ROOT) { $env:LLS_GTA_ROOT } else { 'C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V' }
$script:RigEvidence = if ($env:LLS_RIG_EVIDENCE) { $env:LLS_RIG_EVIDENCE } else { Join-Path $script:RigRepo 'artifacts\rig' }
$script:RigDownloads = Join-Path $script:RigTools 'rig\downloads'
$script:RigLlmHome = if ($env:LLS_LLM_HOME) { $env:LLS_LLM_HOME } else { Join-Path $script:RigTools 'llm' }
$script:RigProfile = if ($env:LLS_QA_PROFILE) { $env:LLS_QA_PROFILE } else { 'qa-rig' }
$script:RigSaveSlot = if ($env:LLS_QA_SAVE_SLOT) { $env:LLS_QA_SAVE_SLOT } else { 'qa-save-slot-1' }
$script:RigDotnet = if ($env:LLS_DOTNET) { $env:LLS_DOTNET } else { Join-Path $script:RigRepo '.dotnet\dotnet.exe' }
$script:RigUv = Join-Path $script:RigTools 'uv\uv.exe'
