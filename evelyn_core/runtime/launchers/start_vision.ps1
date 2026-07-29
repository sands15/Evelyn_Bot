$ErrorActionPreference = 'Stop'
$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }
$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
Set-Location $projectRoot

$visionBackend = if ($env:VISION_BACKEND) { $env:VISION_BACKEND } else { 'wsl' }
$visionGpu = if ($env:VISION_GPU) { $env:VISION_GPU } else { 'GPU-96c554e6-feef-2980-6722-efcb0af098f9' }
$visionPort = if ($env:VISION_PORT) { $env:VISION_PORT } else { '8891' }
$visionDevice = if ($env:VISION_DEVICE) { $env:VISION_DEVICE } else { 'cuda:0' }
$visionDtype = if ($env:VISION_DTYPE) { $env:VISION_DTYPE } else { 'float16' }
$visionOcrDtype = if ($env:VISION_OCR_DTYPE) { $env:VISION_OCR_DTYPE } else { 'auto' }
$visionOcrLazyLoad = if ($env:VISION_OCR_LAZY_LOAD) { $env:VISION_OCR_LAZY_LOAD } else { 'false' }
$visionOcrIdleUnloadSec = if ($env:VISION_OCR_IDLE_UNLOAD_SEC) { $env:VISION_OCR_IDLE_UNLOAD_SEC } else { '600' }
$visionOcrUnloadAfterRequest = if ($env:VISION_OCR_UNLOAD_AFTER_REQUEST) { $env:VISION_OCR_UNLOAD_AFTER_REQUEST } else { 'false' }

if ($visionBackend -ieq 'wsl') {
    $wslCudaVisibleDevices = if ($env:VISION_WSL_CUDA_VISIBLE_DEVICES) { $env:VISION_WSL_CUDA_VISIBLE_DEVICES } else { '1' }
    $wslVenvAct = if ($env:VISION_WSL_VENV_ACT) { $env:VISION_WSL_VENV_ACT } else { 'source ~/venvs/vllm-env/bin/activate' }
    $smolModel = if ($env:VISION_SMOL_MODEL) { $env:VISION_SMOL_MODEL } else { 'HuggingFaceTB/SmolVLM2-500M-Video-Instruct' }
    $ocrModel = if ($env:VISION_OCR_MODEL) { $env:VISION_OCR_MODEL } else { 'tiiuae/Falcon-OCR' }
    $loadSmol = if ($env:VISION_LOAD_SMOL) { $env:VISION_LOAD_SMOL } else { 'true' }
    $loadOcr = if ($env:VISION_LOAD_OCR) { $env:VISION_LOAD_OCR } else { 'true' }
    Write-Host "[Evelyn Vision] backend=wsl port=$visionPort cuda_visible_devices=$wslCudaVisibleDevices device=$visionDevice dtype=$visionDtype"
    Write-Host "[Evelyn Vision] Physical WSL CUDA device 1 is expected to be RTX 3090 on this host."
    $bashCommand = "cd /mnt/c/Evelyn && $wslVenvAct && export PYTHONPATH=/mnt/c/Evelyn/evelyn_core/runtime && export CUDA_VISIBLE_DEVICES='$wslCudaVisibleDevices' && export VISION_PORT='$visionPort' && export VISION_DEVICE='$visionDevice' && export VISION_DTYPE='$visionDtype' && export VISION_OCR_DTYPE='$visionOcrDtype' && export VISION_OCR_LAZY_LOAD='$visionOcrLazyLoad' && export VISION_OCR_IDLE_UNLOAD_SEC='$visionOcrIdleUnloadSec' && export VISION_OCR_UNLOAD_AFTER_REQUEST='$visionOcrUnloadAfterRequest' && export VISION_SMOL_MODEL='$smolModel' && export VISION_OCR_MODEL='$ocrModel' && export VISION_LOAD_SMOL='$loadSmol' && export VISION_LOAD_OCR='$loadOcr' && exec python -m evelyn_core.vision_service"
    wsl.exe bash -lc $bashCommand
    exit $LASTEXITCODE
}

$env:CUDA_VISIBLE_DEVICES = $visionGpu
$env:VISION_PORT = $visionPort
$env:VISION_DEVICE = $visionDevice
$env:VISION_DTYPE = $visionDtype
$env:VISION_OCR_DTYPE = $visionOcrDtype
$env:VISION_OCR_LAZY_LOAD = $visionOcrLazyLoad
$env:VISION_OCR_IDLE_UNLOAD_SEC = $visionOcrIdleUnloadSec
$env:VISION_OCR_UNLOAD_AFTER_REQUEST = $visionOcrUnloadAfterRequest
$venvPython = Join-Path $projectRoot '.venv-vision\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    py -3 -m venv --system-site-packages (Join-Path $projectRoot '.venv-vision')
}

Write-Host "[Evelyn Vision] port=$visionPort gpu=$visionGpu device=$visionDevice dtype=$visionDtype"
& $venvPython -m evelyn_core.vision_service
