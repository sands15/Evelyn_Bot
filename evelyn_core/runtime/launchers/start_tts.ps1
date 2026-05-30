$ErrorActionPreference = 'Stop'
$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }
$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
Set-Location $projectRoot

$profileDir = if ($env:OMNIVOICE_PROFILE_DIR) { $env:OMNIVOICE_PROFILE_DIR } else { Join-Path $projectRoot 'omnivoice_profiles' }
$venvDir = if ($env:OMNIVOICE_VENV) { $env:OMNIVOICE_VENV } else { 'C:\Users\Admin\omnivoice-server\.venv' }
$ttsPort = if ($env:TTS_PORT) { $env:TTS_PORT } else { '8880' }
$ttsGpu = if ($env:TTS_GPU) { $env:TTS_GPU } else { '1' }
$ttsDevice = if ($env:TTS_DEVICE) { $env:TTS_DEVICE } else { 'cuda' }

$env:CUDA_VISIBLE_DEVICES = $ttsGpu
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

$pythonExe = Join-Path $venvDir 'Scripts\python.exe'
& $pythonExe -m omnivoice_server.cli --host 127.0.0.1 --port $ttsPort --device $ttsDevice --profile-dir $profileDir
