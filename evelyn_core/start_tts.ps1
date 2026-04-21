$ErrorActionPreference = 'Stop'
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $projectRoot

$profileDir = if ($env:OMNIVOICE_PROFILE_DIR) { $env:OMNIVOICE_PROFILE_DIR } else { Join-Path $projectRoot 'omnivoice_profiles' }
$venvDir = if ($env:OMNIVOICE_VENV) { $env:OMNIVOICE_VENV } else { 'C:\Users\Admin\omnivoice-server\.venv' }
$ttsPort = if ($env:TTS_PORT) { $env:TTS_PORT } else { '8880' }
$ttsGpu = if ($env:TTS_GPU) { $env:TTS_GPU } else { '0' }
$ttsDevice = if ($env:TTS_DEVICE) { $env:TTS_DEVICE } else { 'cuda' }
$ttsNumStep = if ($env:OMNIVOICE_NUM_STEP) { $env:OMNIVOICE_NUM_STEP } else { '16' }
$ttsMaxConcurrent = if ($env:OMNIVOICE_MAX_CONCURRENT) { $env:OMNIVOICE_MAX_CONCURRENT } else { '1' }
$ttsTimeout = if ($env:OMNIVOICE_REQUEST_TIMEOUT_S) { $env:OMNIVOICE_REQUEST_TIMEOUT_S } else { '180' }

$env:CUDA_VISIBLE_DEVICES = $ttsGpu
$env:OMNIVOICE_NUM_STEP = $ttsNumStep
$env:OMNIVOICE_MAX_CONCURRENT = $ttsMaxConcurrent
$env:OMNIVOICE_REQUEST_TIMEOUT_S = $ttsTimeout
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

$pythonExe = Join-Path $venvDir 'Scripts\python.exe'
& $pythonExe -m omnivoice_server.cli --host 127.0.0.1 --port $ttsPort --device $ttsDevice --profile-dir $profileDir --num-step $ttsNumStep --max-concurrent $ttsMaxConcurrent --timeout $ttsTimeout
