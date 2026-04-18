$ErrorActionPreference = 'Stop'
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $projectRoot

$profileDir = if ($env:OMNIVOICE_PROFILE_DIR) { $env:OMNIVOICE_PROFILE_DIR } else { Join-Path $projectRoot 'omnivoice_profiles' }
$venvDir = if ($env:OMNIVOICE_VENV) { $env:OMNIVOICE_VENV } else { 'C:\Users\Admin\omnivoice-server\.venv' }
$ttsPort = if ($env:TTS_PORT) { $env:TTS_PORT } else { '8880' }

$env:CUDA_VISIBLE_DEVICES = '1'
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

$pythonExe = Join-Path $venvDir 'Scripts\python.exe'
& $pythonExe -m omnivoice_server.cli --host 127.0.0.1 --port $ttsPort --device cuda --profile-dir $profileDir
