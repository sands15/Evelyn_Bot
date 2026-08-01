$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$projectRoot = [string]$projectRoot
$coreRuntime = Join-Path $projectRoot 'evelyn_core\runtime'
$composeFile = Join-Path $projectRoot 'docker-compose.fast-control.yml'
$controlPagePublicPort = if ($env:CONTROL_PAGE_PUBLIC_PORT) { [int]$env:CONTROL_PAGE_PUBLIC_PORT } else { 8799 }
$botApiPort = if ($env:CONTROL_PAGE_BOT_API_PORT) { [int]$env:CONTROL_PAGE_BOT_API_PORT } else { 8798 }
$controlPageUrl = "http://127.0.0.1:$controlPagePublicPort/"
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
$logDir = Join-Path $projectRoot 'runtime_artifacts\logs\background_start'
$supervisorLog = Join-Path $logDir 'Host-Supervisor.log'
$supervisorStatus = Join-Path $projectRoot 'runtime_artifacts\host_supervisor\status.json'
$dockerImageBuilder = Join-Path $PSScriptRoot 'build_local_docker_images.ps1'
$minecraftOwnerClaim = Join-Path $projectRoot 'runtime_artifacts\minecraft_world_lease\owner_claim.json'
$ttsProfilesRoot = if ($env:EVELYN_OMNIVOICE_PROFILES_DIR) {
    [System.IO.Path]::GetFullPath([string]$env:EVELYN_OMNIVOICE_PROFILES_DIR)
} else {
    Join-Path $projectRoot 'omnivoice_profiles'
}

$env:CONTROL_PAGE_PUBLIC_PORT = [string]$controlPagePublicPort
$env:CONTROL_PAGE_BOT_API_PORT = [string]$botApiPort
$env:EVELYN_HOST_PROJECT_ROOT = $projectRoot
$env:EVELYN_OMNIVOICE_PROFILES_DIR = $ttsProfilesRoot
if ([string]::IsNullOrWhiteSpace($env:DISCORD_BOT_TOKEN)) {
    $env:DISCORD_BOT_TOKEN = 'local-only-disabled'
}

if (Test-Path $stopMarker) {
    Remove-Item -LiteralPath $stopMarker -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-PortConnect {
    param(
        [string]$HostName = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 1000
    )

    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $client.EndConnect($iar)
            return $true
        }
    } catch {
    } finally {
        if ($client) {
            $client.Close()
        }
    }
    return $false
}

function Wait-Port {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Label
    )

    $timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 180 }
    $intervalMs = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC * 1000 } else { 2000 }
    $deadline = (Get-Date).AddSeconds($timeoutSec)

    Write-Host "[Evelyn] Waiting for $Label at ${HostName}:$Port"
    while ((Get-Date) -lt $deadline) {
        if (Test-PortConnect -HostName $HostName -Port $Port -TimeoutMs 1000) {
            Write-Host "[Evelyn] $Label is ready"
            return
        }
        Start-Sleep -Milliseconds $intervalMs
    }

    throw "$Label was not ready in time"
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [string]$Label,
        [ValidateSet('ready', 'vision')]
        [string]$Contract = 'ready'
    )

    $timeoutSec = if ($env:START_MODEL_WAIT_TIMEOUT_SEC) {
        [int]$env:START_MODEL_WAIT_TIMEOUT_SEC
    } else {
        600
    }
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    Write-Host "[Evelyn] Waiting for $Label readiness at $Url"
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 5
            $ready = if ($Contract -eq 'vision') {
                [bool]$health.ok -and [bool]$health.models.smol.loaded
            } else {
                [bool]$health.ok -and [bool]$health.ready
            }
            if ($ready) {
                Write-Host "[Evelyn] $Label readiness contract passed"
                return
            }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    throw "$Label did not satisfy its HTTP readiness contract in time."
}

function New-EncodedCommand {
    param([string]$Script)
    $bytes = [System.Text.Encoding]::Unicode.GetBytes($Script)
    return [Convert]::ToBase64String($bytes)
}

function Start-PowerShellWindow {
    param(
        [string]$Title,
        [string]$Script,
        [bool]$Visible = $true
    )

    $bootstrap = @"
try { `$Host.UI.RawUI.WindowTitle = '$Title' } catch {}
$Script
"@
    $encoded = New-EncodedCommand -Script $bootstrap
    $windowStyle = if ($Visible) { 'Normal' } else { 'Hidden' }
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $projectRoot -WindowStyle $windowStyle -ArgumentList @(
        '-NoLogo',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-EncodedCommand', $encoded
    ) | Out-Null
}

function Test-HostPythonReady {
    param([string]$FilePath)

    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return $false
    }
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $coreRuntime
        & $FilePath -c "import aiohttp, numpy, sounddevice; from PIL import ImageGrab; import evelyn_core.local_io_bridge" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
}

function Resolve-HostPython {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($env:EVELYN_HOST_PYTHON)) {
        $candidates.Add([string]$env:EVELYN_HOST_PYTHON)
    }
    $candidates.Add((Join-Path $projectRoot '.venv-host\Scripts\python.exe'))
    $candidates.Add((Join-Path $projectRoot '.venv\Scripts\python.exe'))
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'))
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        $key = ([string]$candidate).ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        if (Test-HostPythonReady -FilePath $candidate) {
            return [string](Resolve-Path -LiteralPath $candidate)
        }
    }
    throw (
        "No verified Windows host runtime is available. Run: powershell -ExecutionPolicy Bypass " +
        "-File .\evelyn_core\runtime\launchers\bootstrap_host_runtime.ps1"
    )
}

function Wait-HostSupervisorReady {
    param(
        [double]$MinimumHeartbeat,
        [int]$TimeoutSec = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastHeartbeat = 0.0
    $consecutiveFreshHeartbeats = 0
    while ((Get-Date) -lt $deadline) {
        try {
            if (Test-Path -LiteralPath $supervisorStatus -PathType Leaf) {
                $status = Get-Content -Raw -LiteralPath $supervisorStatus | ConvertFrom-Json
                $heartbeat = [double]$status.heartbeatAt
                $ready = (
                    [string]$status.schema -eq 'host_supervisor.status.v1' -and
                    [string]$status.state -eq 'running' -and
                    [bool]$status.localBridge.running -and
                    $heartbeat -ge $MinimumHeartbeat
                )
                if ($ready -and $heartbeat -gt $lastHeartbeat) {
                    $lastHeartbeat = $heartbeat
                    $consecutiveFreshHeartbeats += 1
                    if ($consecutiveFreshHeartbeats -ge 2) {
                        return
                    }
                } elseif (-not $ready) {
                    $consecutiveFreshHeartbeats = 0
                }
            }
        } catch {
            $consecutiveFreshHeartbeats = 0
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Windows Host Supervisor or Local I/O Bridge did not produce two fresh healthy heartbeats."
}

function Assert-TtsProfileReady {
    $profileRoot = Join-Path $ttsProfilesRoot 'evelyn'
    $referenceAudio = Join-Path $profileRoot 'ref_audio.wav'
    $metadataPath = Join-Path $profileRoot 'meta.json'
    $remediation = (
        "Restore or provision ref_audio.wav and meta.json under '$profileRoot' " +
        "before starting Evelyn."
    )

    if (-not (Test-Path -LiteralPath $referenceAudio -PathType Leaf)) {
        throw "Evelyn TTS profile audio is missing. $remediation"
    }
    if ((Get-Item -LiteralPath $referenceAudio).Length -le 44) {
        throw "Evelyn TTS profile audio is empty or invalid. $remediation"
    }
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        throw "Evelyn TTS profile metadata is missing. $remediation"
    }
    try {
        $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
    } catch {
        throw "Evelyn TTS profile metadata is not valid JSON. $remediation"
    }
    if ([string]::IsNullOrWhiteSpace([string]$metadata.ref_text)) {
        throw "Evelyn TTS profile ref_text is missing. $remediation"
    }
}

function Invoke-DockerCommand {
    param(
        [string[]]$Arguments,
        [switch]$IgnoreFailure
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0 -and -not $IgnoreFailure) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

function Test-DockerContainerRunning {
    param([string]$ContainerName)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $state = & docker inspect --format '{{.State.Running}}' $ContainerName 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return (
        $exitCode -eq 0 -and
        ([string]$state).Trim().ToLowerInvariant() -eq 'true'
    )
}

function Stop-BotApiForImageRefresh {
    Write-Host '[Evelyn] Stopping the current Bot API cleanly before replacing its image.'
    Invoke-DockerCommand -Arguments @(
        'stop',
        '--timeout', '60',
        'evelyn-bot-api'
    ) -IgnoreFailure

    if (Test-DockerContainerRunning -ContainerName 'evelyn-bot-api') {
        throw 'Bot API is still running after docker stop. Refusing to replace a live owner.'
    }
    if (Test-Path -LiteralPath $minecraftOwnerClaim -PathType Leaf) {
        Write-Warning (
            'A stale Minecraft owner claim remains after Bot API stop. ' +
            'The claim JSON is diagnostic only; the replacement Bot API must acquire ' +
            'the process-lifetime OS lock before it can become owner.'
        )
    }
}

function Start-DockerCore {
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "Compose file not found: $composeFile"
    }

    Write-Host '[Evelyn] Starting Docker local core services.'
    $composeBaseArgs = @(
        '-f', $composeFile,
        '--profile', 'llm',
        '--profile', 'tts',
        '--profile', 'vision',
        '--profile', 'stt'
    )
    $coreServices = @(
        'bot_api',
        'control_page',
        'main_llm',
        'router_llm',
        'sub_llm',
        'tts',
        'stt',
        'vision'
    )

    $buildEnabled = $env:EVELYN_DOCKER_BUILD -and ([string]$env:EVELYN_DOCKER_BUILD).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    if ($buildEnabled) {
        Write-Host '[Evelyn] Rebuilding Docker app images because EVELYN_DOCKER_BUILD is enabled.'
        if (-not (Test-Path -LiteralPath $dockerImageBuilder -PathType Leaf)) {
            throw "Docker image builder not found: $dockerImageBuilder"
        }
        & $dockerImageBuilder -ProjectRoot $projectRoot -Services @(
            'bot_api',
            'control_page',
            'vision'
        )
        Stop-BotApiForImageRefresh
    } else {
        Write-Host '[Evelyn] Reusing existing Docker images. Set EVELYN_DOCKER_BUILD=true to rebuild app images.'
    }

    $keepDiscordBot = $env:EVELYN_LOCAL_KEEP_DISCORD_BOT -and ([string]$env:EVELYN_LOCAL_KEEP_DISCORD_BOT).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    if (-not $keepDiscordBot) {
        Invoke-DockerCommand -Arguments (@('compose') + $composeBaseArgs + @('--profile', 'discord', 'stop', 'discord_bot')) -IgnoreFailure
    }

    $composeArgs = $composeBaseArgs + @('up', '-d') + $coreServices
    Invoke-DockerCommand -Arguments (@('compose') + $composeArgs)
    Write-Host '[Evelyn] Minecraft services are deferred. Run start_voyager.bat when a Minecraft command is requested.'
}

function Test-HostSupervisorRunning {
    $escapedRoot = [Regex]::Escape($projectRoot)
    $matches = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'evelyn_core\.host_supervisor' -and
            $_.CommandLine -match $escapedRoot
        }
    return [bool]$matches
}

function Start-HostSupervisor {
    if (Test-HostSupervisorRunning) {
        Write-Host '[Evelyn] Windows Host Supervisor is already running.'
        Wait-HostSupervisorReady -MinimumHeartbeat ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 4)
        return
    }

    $hostPython = Resolve-HostPython
    $escapedHostPython = $hostPython.Replace("'", "''")
    $launchStartedAt = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
    $pythonCommand = @"
Set-Location '$projectRoot'
`$env:PYTHONPATH = '$coreRuntime'
`$env:EVELYN_PROJECT_ROOT = '$projectRoot'
`$env:EVELYN_CORE_ROOT = '$projectRoot\evelyn_core'
`$env:EVELYN_CORE_RUNTIME = '$coreRuntime'
`$env:LOCAL_BRIDGE_BOT_API_BASE = 'http://127.0.0.1:$botApiPort'
`$env:STT_SERVICE_URL = 'http://127.0.0.1:8892'
`$env:OMNIVOICE_SERVER_URL = 'http://127.0.0.1:8880'
`$env:VISION_SERVICE_URL = 'http://127.0.0.1:8891'
`$env:EVELYN_RUNTIME_ARTIFACTS_DIR = '$projectRoot\runtime_artifacts'
if (-not `$env:LOCAL_MIC_START_THRESHOLD) { `$env:LOCAL_MIC_START_THRESHOLD = '0.002' }
if (-not `$env:LOCAL_MIC_CONTINUE_THRESHOLD) { `$env:LOCAL_MIC_CONTINUE_THRESHOLD = '0.001' }
if (-not `$env:LOCAL_MIC_MIN_VOICED_MS) { `$env:LOCAL_MIC_MIN_VOICED_MS = '280' }
if (-not `$env:LOCAL_MIC_WAVEFORM_FILTER_ENABLED) { `$env:LOCAL_MIC_WAVEFORM_FILTER_ENABLED = 'true' }
if (-not `$env:LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC) { `$env:LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC = '0.7' }
if (-not `$env:LOCAL_BRIDGE_STATUS_INTERVAL_SEC) { `$env:LOCAL_BRIDGE_STATUS_INTERVAL_SEC = '0.25' }
if (-not `$env:LOCAL_BRIDGE_TTS_WARMUP_ENABLED) { `$env:LOCAL_BRIDGE_TTS_WARMUP_ENABLED = 'true' }
if (-not `$env:LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC) { `$env:LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = '0.5' }
& '$escapedHostPython' -m evelyn_core.host_supervisor --project-root '$projectRoot' --artifacts-root '$projectRoot\runtime_artifacts' *>> '$supervisorLog'
"@

    $visible = if ($env:EVELYN_HOST_SUPERVISOR_VISIBLE) {
        ([string]$env:EVELYN_HOST_SUPERVISOR_VISIBLE).ToLowerInvariant() -notin @('0', 'false', 'no', 'off')
    } else {
        $false
    }
    Start-PowerShellWindow -Title 'Evelyn Host Supervisor' -Script $pythonCommand -Visible:$visible
    Wait-HostSupervisorReady -MinimumHeartbeat $launchStartedAt
}

function Open-ControlPage {
    if ($env:CONTROL_PAGE_AUTO_OPEN -and ([string]$env:CONTROL_PAGE_AUTO_OPEN).ToLowerInvariant() -in @('0', 'false', 'no', 'off')) {
        return
    }

    $chromeCandidates = @(@(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path $_) })

    if ($chromeCandidates) {
        Start-Process -FilePath $chromeCandidates[0] -ArgumentList @('--new-tab', $controlPageUrl) | Out-Null
        return
    }

    Start-Process $controlPageUrl | Out-Null
}

Assert-TtsProfileReady
Start-DockerCore

Wait-Port -HostName '127.0.0.1' -Port 9820 -Label 'Main-LLM'
Wait-Port -HostName '127.0.0.1' -Port 9822 -Label 'Router-LLM'
Wait-Port -HostName '127.0.0.1' -Port 9821 -Label 'Sub-LLM'
Wait-Port -HostName '127.0.0.1' -Port 8880 -Label 'OmniVoice-TTS'
Wait-Port -HostName '127.0.0.1' -Port 8892 -Label 'STT'
Wait-Port -HostName '127.0.0.1' -Port 8891 -Label 'Vision'
Wait-HttpReady -Url 'http://127.0.0.1:8880/health' -Label 'OmniVoice-TTS'
Wait-HttpReady -Url 'http://127.0.0.1:8892/health' -Label 'STT'
Wait-HttpReady -Url 'http://127.0.0.1:8891/health' -Label 'Vision' -Contract 'vision'
Wait-Port -HostName '127.0.0.1' -Port $botApiPort -Label 'Docker Bot API'
Wait-Port -HostName '127.0.0.1' -Port $controlPagePublicPort -Label 'Docker Control Page'

Start-HostSupervisor

Write-Host "[Evelyn] Docker local core is ready. Control page: $controlPageUrl"
Write-Host "[Evelyn] Windows Host Supervisor log: $supervisorLog"
Open-ControlPage
