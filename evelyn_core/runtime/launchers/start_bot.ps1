$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }
$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
$env:CONTROL_PAGE_PORT = if ($env:CONTROL_PAGE_PORT) { $env:CONTROL_PAGE_PORT } elseif ($env:CONTROL_PAGE_BOT_API_PORT) { $env:CONTROL_PAGE_BOT_API_PORT } else { '8798' }
Set-Location $projectRoot

function Test-EvelynTruthy {
    param([string]$Value)
    if (-not $Value) {
        return $false
    }
    return @('1', 'true', 'yes', 'on') -contains $Value.ToLower()
}

function Test-EvelynExplicitFalse {
    param([string]$Value)
    if (-not $Value) {
        return $false
    }
    return @('0', 'false', 'no', 'off') -contains $Value.ToLower()
}

function Should-SkipDependencyWait {
    return (
        (Test-EvelynTruthy $env:EVELYN_FAST_BOOT) -or
        (Test-EvelynTruthy $env:EVELYN_FAST_BOOT_SKIP_DEPENDENCY_WAIT) -or
        (Test-EvelynTruthy $env:LOCAL_ONLY) -or
        (Test-EvelynTruthy $env:LOCAL_ONLY_MODE) -or
        (Test-EvelynExplicitFalse $env:DISCORD_ENABLED)
    )
}

function Wait-Port {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Label
    )

    $timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 120 }
    $intervalSec = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC } else { 2 }
    $deadline = (Get-Date).AddSeconds($timeoutSec)

    Write-Host "[Evelyn] Waiting for $Label at ${HostName}:$Port"
    while ((Get-Date) -lt $deadline) {
        $client = $null
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $iar = $client.BeginConnect($HostName, $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(1000)) {
                $client.EndConnect($iar)
                $client.Close()
                Write-Host "[Evelyn] $Label is ready"
                return
            }
        } catch {
        } finally {
            if ($client) {
                $client.Close()
            }
        }
        Start-Sleep -Seconds $intervalSec
    }

    throw "$Label was not ready in time"
}

$env:OPUS_ERROR_TO_SILENCE = if ($env:OPUS_ERROR_TO_SILENCE) { $env:OPUS_ERROR_TO_SILENCE } else { 'false' }
$env:STT_USE_RAW_48K = if ($env:STT_USE_RAW_48K) { $env:STT_USE_RAW_48K } else { 'false' }

$mainPort = if ($env:MAIN_LLM_PORT) { [int]$env:MAIN_LLM_PORT } else { 9820 }
$routerPort = if ($env:ROUTER_LLM_PORT) { [int]$env:ROUTER_LLM_PORT } else { 9822 }
$subPort = if ($env:SUB_LLM_PORT) { [int]$env:SUB_LLM_PORT } else { 9821 }
$ttsPort = if ($env:TTS_PORT) { [int]$env:TTS_PORT } else { 8880 }

$skipDependencyWait = Should-SkipDependencyWait
if ($skipDependencyWait) {
    Write-Host '[Evelyn] Skipping model/TTS dependency waits for fast startup path.'
} else {
    Wait-Port -HostName '127.0.0.1' -Port $mainPort -Label 'Main-LLM'
    Wait-Port -HostName '127.0.0.1' -Port $routerPort -Label 'Router-LLM'
    Wait-Port -HostName '127.0.0.1' -Port $subPort -Label 'Sub-LLM'
    Wait-Port -HostName '127.0.0.1' -Port $ttsPort -Label 'OmniVoice-TTS'
}

if (-not $env:DISCORD_BOT_TOKEN) {
    if (-not $skipDependencyWait) {
        throw '[Evelyn] DISCORD_BOT_TOKEN is required when Discord mode is enabled.'
    }
    Write-Host '[Evelyn] DISCORD_BOT_TOKEN is not set. Running Bot API in fast/local mode.'
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    & $venvPython 'main.py'
} else {
    py -3 'main.py'
}
