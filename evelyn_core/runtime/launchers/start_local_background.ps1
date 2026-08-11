$ErrorActionPreference = 'Stop'
$startupStage = 'source_revision'
$startupExitCode = 0
$projectRoot = ''

$startupFailures = @{
    docker_cli = @('EVL-START-1001', 'Docker CLI was not found.', 'Install or repair Docker Desktop, then run start.bat again.')
    docker_engine = @('EVL-START-1002', 'Docker Engine is not available.', 'Start Docker Desktop, wait for Engine running, then retry.')
    docker_compose = @('EVL-START-1003', 'Docker Compose is not available.', 'Repair or update Docker Desktop and verify: docker compose version')
    source_revision = @('EVL-START-2001', 'The Evelyn source revision could not be verified.', 'Run: git status --short. Review and commit or stash changes; repair Git if unavailable.')
    tts_profile = @('EVL-START-2002', 'The OmniVoice profile is missing or invalid.', 'Check ref_audio.wav, meta.json, and meta.json ref_text in the README.')
    docker_start = @('EVL-START-3001', 'A Docker image build or Compose start failed.', 'Retry once. If it repeats, report this code and the failure time.')
    service_readiness = @('EVL-START-4001', 'A required Docker service did not become ready in time.', 'Run tools\check_docker_runtime.ps1, then report this code and time.')
    host_supervisor = @('EVL-START-4002', 'The Windows Host Supervisor or Local I/O Bridge failed.', 'Check Host-Supervisor.log. Run bootstrap_host_runtime.ps1 only if the host runtime is missing.')
    control_page_open = @('EVL-START-4003', 'Evelyn started, but the Control Page could not be opened.', 'Open the Control Page URL printed above in a browser.')
    unknown = @('EVL-START-9000', 'An unclassified startup failure occurred.', 'Retry once. If it repeats, report this code and the failure time.')
}

function Get-EvelynStartupFailure {
    param([string]$Stage)

    $definition = if ($startupFailures.ContainsKey($Stage)) {
        $startupFailures[$Stage]
    } else {
        $startupFailures.unknown
    }
    return [pscustomobject]@{
        Code = $definition[0]
        Message = $definition[1]
        Action = $definition[2]
    }
}

function Write-EvelynStartupFailure {
    param(
        [string]$Stage,
        [System.Management.Automation.ErrorRecord]$ErrorRecord,
        [string]$ProjectRoot
    )

    $failure = Get-EvelynStartupFailure -Stage $Stage
    $errorType = if ($ErrorRecord -and $ErrorRecord.Exception) {
        $ErrorRecord.Exception.GetType().Name
    } else {
        'Error'
    }
    $relativeLog = 'runtime_artifacts\logs\background_start\startup-error.log'

    Write-Host ''
    Write-Host "[Evelyn] Startup failed. errorCode=$($failure.Code)"
    Write-Host "[Evelyn] $($failure.Message)"
    Write-Host "[Evelyn] Action: $($failure.Action)"
    Write-Host '[Evelyn] Help: README.md#startup-error-codes'

    try {
        $root = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
            [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
        } else {
            $ProjectRoot
        }
        $logPath = Join-Path $root $relativeLog
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) |
            Out-Null
        @(
            'schema=evelyn.startup_error.v1'
            'timestamp=' + [DateTimeOffset]::Now.ToString('o')
            'errorCode=' + $failure.Code
            'errorType=' + $errorType
            'stage=' + $Stage
        ) | Set-Content -LiteralPath $logPath -Encoding UTF8
        Write-Host "[Evelyn] Log: $relativeLog"
    } catch {
        Write-Host '[Evelyn] Log: unavailable'
    }
}

$previousLocalBridgeStatusAuthToken = [Environment]::GetEnvironmentVariable(
    'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',
    [EnvironmentVariableTarget]::Process
)
$previousInternalControlToken = [Environment]::GetEnvironmentVariable(
    'EVELYN_INTERNAL_CONTROL_TOKEN',
    [EnvironmentVariableTarget]::Process
)
$previousVoiceCaptureHostAuthToken = [Environment]::GetEnvironmentVariable(
    'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',
    [EnvironmentVariableTarget]::Process
)
try {
# Strip inherited channel credentials before even the source-revision Git
# probes can create a child. Narrow helpers below reintroduce only the exact
# credential required by an authorized child at process-creation time.
[Environment]::SetEnvironmentVariable(
    'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',
    $null,
    [EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    'EVELYN_INTERNAL_CONTROL_TOKEN',
    $null,
    [EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',
    $null,
    [EnvironmentVariableTarget]::Process
)

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$projectRoot = [string]$projectRoot
$sourceRevisionHelper = Join-Path $PSScriptRoot 'source_revision.ps1'
if (-not (Test-Path -LiteralPath $sourceRevisionHelper -PathType Leaf)) {
    throw "Source revision helper not found: $sourceRevisionHelper"
}
. $sourceRevisionHelper
$sourceRevision = Initialize-EvelynSourceRevision -ProjectRoot $projectRoot
$startupStage = 'unknown'
Write-Host "[Evelyn] Runtime source revision: $sourceRevision"
$coreRuntime = Join-Path $projectRoot 'evelyn_core\runtime'
$composeFile = Join-Path $projectRoot 'docker-compose.fast-control.yml'
$controlPagePublicPort = if ($env:CONTROL_PAGE_PUBLIC_PORT) { [int]$env:CONTROL_PAGE_PUBLIC_PORT } else { 8799 }
$botApiPort = if ($env:CONTROL_PAGE_BOT_API_PORT) { [int]$env:CONTROL_PAGE_BOT_API_PORT } else { 8798 }
$controlPageUrl = "http://127.0.0.1:$controlPagePublicPort/"
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
$logDir = Join-Path $projectRoot 'runtime_artifacts\logs\background_start'
$supervisorLog = Join-Path $logDir 'Host-Supervisor.log'
$supervisorStatus = Join-Path $projectRoot 'runtime_artifacts\host_supervisor\status.json'
$supervisorStopRequest = Join-Path $projectRoot 'runtime_artifacts\host_supervisor\stop.request'
$localBridgeStatus = Join-Path $projectRoot 'runtime_artifacts\local_bridge\status.json'
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

function New-SecureRuntimeToken {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$localBridgeStatusAuthToken = if (
    -not [string]::IsNullOrWhiteSpace($previousLocalBridgeStatusAuthToken) -and
    $previousLocalBridgeStatusAuthToken.Trim().Length -ge 32
) {
    $previousLocalBridgeStatusAuthToken.Trim()
} else {
    New-SecureRuntimeToken
}
$internalControlToken = if (
    -not [string]::IsNullOrWhiteSpace($previousInternalControlToken) -and
    $previousInternalControlToken.Trim().Length -ge 32
) {
    $previousInternalControlToken.Trim()
} else {
    New-SecureRuntimeToken
}
$voiceCaptureHostAuthToken = New-SecureRuntimeToken

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
        [string]$Label,
        [switch]$ModelStartup
    )

    $timeoutSec = if ($ModelStartup) {
        if ($env:START_MODEL_WAIT_TIMEOUT_SEC) { [int]$env:START_MODEL_WAIT_TIMEOUT_SEC } else { 600 }
    } elseif ($env:START_WAIT_TIMEOUT_SEC) {
        [int]$env:START_WAIT_TIMEOUT_SEC
    } else {
        180
    }
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
        [ValidateSet('ready', 'vision', 'omnivoice', 'bot_api', 'control_page')]
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
            $ready = if ($Contract -eq 'omnivoice') {
                $health.ready -eq $true -and
                    $health.model_loaded -eq $true -and
                    [string]$health.status -eq 'healthy' -and
                    [string]$health.model_id -eq 'k2-fsa/OmniVoice' -and
                    [string]$health.model_revision -eq 'c5fdb5ccb189668d56333f77ba2629f4cd7535f4'
            } elseif ($Contract -eq 'vision') {
                [bool]$health.ok -and [bool]$health.models.smol.loaded
            } elseif ($Contract -eq 'bot_api') {
                $health.ok -eq $true -and
                    [string]$health.role -ceq 'fast-control-bot-api' -and
                    $health.sourceIdentity.ready -eq $true -and
                    [string]$health.sourceIdentity.imageSourceRevision -ceq $sourceRevision -and
                    [string]$health.sourceIdentity.expectedSourceRevision -ceq $sourceRevision
            } elseif ($Contract -eq 'control_page') {
                $health.ok -eq $true -and
                    [string]$health.role -ceq 'control-page' -and
                    $health.botProxyReady -eq $true -and
                    $health.sourceIdentity.ready -eq $true -and
                    [string]$health.sourceIdentity.imageSourceRevision -ceq $sourceRevision -and
                    [string]$health.sourceIdentity.expectedSourceRevision -ceq $sourceRevision -and
                    $health.botSourceIdentity.ready -eq $true -and
                    [string]$health.botSourceIdentity.imageSourceRevision -ceq $sourceRevision -and
                    [string]$health.botSourceIdentity.expectedSourceRevision -ceq $sourceRevision
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
    $lastSupervisorHeartbeat = 0.0
    $lastBridgeHeartbeat = 0.0
    $consecutiveFreshHeartbeats = 0
    while ((Get-Date) -lt $deadline) {
        try {
            if (
                (Test-Path -LiteralPath $supervisorStatus -PathType Leaf) -and
                (Test-Path -LiteralPath $localBridgeStatus -PathType Leaf)
            ) {
                $supervisor = Get-Content -Raw -LiteralPath $supervisorStatus |
                    ConvertFrom-Json
                $bridge = Get-Content -Raw -LiteralPath $localBridgeStatus |
                    ConvertFrom-Json
                $supervisorHeartbeat = [double]$supervisor.heartbeatAt
                $bridgeHeartbeat = [double]$bridge.heartbeatAt
                $supervisorReady = (
                    [string]$supervisor.schema -eq 'host_supervisor.status.v1' -and
                    [string]$supervisor.state -eq 'running' -and
                    ($supervisor.localBridge.running -is [bool]) -and
                    $supervisor.localBridge.running -eq $true -and
                    ($supervisor.localBridge.ownershipReady -is [bool]) -and
                    $supervisor.localBridge.ownershipReady -eq $true -and
                    ($supervisor.localBridge.birthIdentityRecorded -is [bool]) -and
                    $supervisor.localBridge.birthIdentityRecorded -eq $true -and
                    $supervisorHeartbeat -ge $MinimumHeartbeat
                )
                $bridgePidMatches = (
                    $null -ne $bridge.pid -and
                    $null -ne $supervisor.localBridge.pid -and
                    [int]$bridge.pid -eq [int]$supervisor.localBridge.pid
                )
                $micStateMatches = (
                    ($bridge.micEnabled -is [bool]) -and
                    ($bridge.mic.enabled -is [bool]) -and
                    $bridge.micEnabled -eq $bridge.mic.enabled
                )
                $captureReady = (
                    $micStateMatches -and
                    (
                        $bridge.micEnabled -eq $false -or
                        (
                            ($bridge.mic.captureReady -is [bool]) -and
                            $bridge.mic.captureReady -eq $true
                        )
                    )
                )
                $bridgeReady = (
                    [string]$bridge.schema -eq 'local_io_bridge.status.v1' -and
                    ($bridge.ready -is [bool]) -and
                    $bridge.ready -eq $true -and
                    $bridgePidMatches -and
                    $captureReady -and
                    $bridgeHeartbeat -ge $MinimumHeartbeat
                )
                if (
                    $supervisorReady -and
                    $bridgeReady -and
                    $supervisorHeartbeat -gt $lastSupervisorHeartbeat -and
                    $bridgeHeartbeat -gt $lastBridgeHeartbeat
                ) {
                    $lastSupervisorHeartbeat = $supervisorHeartbeat
                    $lastBridgeHeartbeat = $bridgeHeartbeat
                    $consecutiveFreshHeartbeats += 1
                    if ($consecutiveFreshHeartbeats -ge 2) {
                        return
                    }
                } elseif (-not $supervisorReady -or -not $bridgeReady) {
                    $consecutiveFreshHeartbeats = 0
                }
            } else {
                $consecutiveFreshHeartbeats = 0
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

function Assert-DockerReady {
    $script:startupStage = 'docker_cli'
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $dockerCommand) {
        $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    }
    if (-not $dockerCommand) {
        throw 'Docker CLI is unavailable.'
    }

    $script:startupStage = 'docker_engine'
    & $dockerCommand.Source info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Engine is unavailable.'
    }

    $script:startupStage = 'docker_compose'
    & $dockerCommand.Source compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose is unavailable.'
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

function Set-ProcessEnvironmentVariable {
    param(
        [string]$Name,
        [AllowNull()][string]$Value
    )
    [Environment]::SetEnvironmentVariable(
        $Name,
        $Value,
        [EnvironmentVariableTarget]::Process
    )
}

function Invoke-DockerCommandWithRuntimeChannelTokens {
    param([string[]]$Arguments)

    $previousReporter = [Environment]::GetEnvironmentVariable(
        'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    $previousInternal = [Environment]::GetEnvironmentVariable(
        'EVELYN_INTERNAL_CONTROL_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    $previousVoiceCapture = [Environment]::GetEnvironmentVariable(
        'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    try {
        Set-ProcessEnvironmentVariable `
            -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
            -Value $localBridgeStatusAuthToken
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
            -Value $internalControlToken
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
            -Value $voiceCaptureHostAuthToken
        Invoke-DockerCommand -Arguments $Arguments
    } finally {
        Set-ProcessEnvironmentVariable `
            -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
            -Value $previousReporter
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
            -Value $previousInternal
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
            -Value $previousVoiceCapture
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

function Test-DockerImageExists {
    param([string]$Image)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $imageId = & docker image inspect --format '{{.Id}}' $Image 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return $exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$imageId)
}

function Test-DockerImageSourceRevision {
    param(
        [string]$Image,
        [string]$ExpectedRevision
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $imageEnvironment = @(
            & docker image inspect `
                --format '{{range .Config.Env}}{{println .}}{{end}}' `
                $Image 2>$null
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        return $false
    }

    $prefix = 'EVELYN_IMAGE_SOURCE_REVISION='
    $revisions = @(
        $imageEnvironment |
            ForEach-Object { [string]$_ } |
            Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) } |
            ForEach-Object { $_.Substring($prefix.Length) }
    )
    return $revisions.Count -eq 1 -and $revisions[0] -ceq $ExpectedRevision
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

    $keepDiscordBot = $env:EVELYN_LOCAL_KEEP_DISCORD_BOT -and ([string]$env:EVELYN_LOCAL_KEEP_DISCORD_BOT).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    $buildEnabled = $env:EVELYN_DOCKER_BUILD -and ([string]$env:EVELYN_DOCKER_BUILD).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    $botApiImage = 'evelyn-fast-control-bot_api:latest'
    $controlPageImage = 'evelyn-fast-control-control_page:latest'
    $discordBotImage = 'evelyn-fast-control-discord_bot:latest'
    $ttsImage = 'evelyn-omnivoice-tts:recipe-7cfc51e96088'
    $ttsImageMissing = -not (Test-DockerImageExists -Image $ttsImage)
    $coreAppImagesNeedBuild = $buildEnabled -or
        -not (Test-DockerImageSourceRevision -Image $botApiImage -ExpectedRevision $sourceRevision) -or
        -not (Test-DockerImageSourceRevision -Image $controlPageImage -ExpectedRevision $sourceRevision)
    $discordImageNeedsBuild = $keepDiscordBot -and (
        $buildEnabled -or
        -not (Test-DockerImageSourceRevision -Image $discordBotImage -ExpectedRevision $sourceRevision)
    )
    $dockerBuildServices = @()
    if ($coreAppImagesNeedBuild) {
        if ($buildEnabled) {
            Write-Host '[Evelyn] Rebuilding Docker app images because EVELYN_DOCKER_BUILD is enabled.'
        } else {
            Write-Host '[Evelyn] Rebuilding missing or stale source-gated Docker app images.'
        }
        $dockerBuildServices += @(
            'bot_api',
            'control_page',
            'vision'
        )
    }
    if ($discordImageNeedsBuild) {
        $dockerBuildServices += 'discord_bot'
    }
    if ($buildEnabled -or $ttsImageMissing) {
        $dockerBuildServices += 'tts'
    }
    if ($dockerBuildServices.Count -gt 0) {
        if (-not (Test-Path -LiteralPath $dockerImageBuilder -PathType Leaf)) {
            throw "Docker image builder not found: $dockerImageBuilder"
        }
        if ($ttsImageMissing -and -not $buildEnabled) {
            Write-Host '[Evelyn] Building the missing source-gated OmniVoice TTS image.'
        }
        & $dockerImageBuilder -ProjectRoot $projectRoot -Services $dockerBuildServices
        if ($dockerBuildServices -contains 'bot_api') {
            Stop-BotApiForImageRefresh
        }
    } else {
        Write-Host '[Evelyn] Reusing existing Docker images. Set EVELYN_DOCKER_BUILD=true to rebuild app images.'
    }

    if (-not $keepDiscordBot) {
        Invoke-DockerCommand -Arguments (@('compose') + $composeBaseArgs + @('--profile', 'discord', 'stop', 'discord_bot')) -IgnoreFailure
    }

    $composeArgs = $composeBaseArgs + @('up', '-d', '--no-build') + $coreServices
    Invoke-DockerCommandWithRuntimeChannelTokens -Arguments (
        @('compose') + $composeArgs
    )
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

function Stop-PreviousHostSupervisorGeneration {
    if (Test-HostSupervisorRunning) {
        # Every launcher invocation creates (or accepts) one coherent set of
        # process-scoped channel credentials. Retire an older supervisor tree
        # before accepting a bridge from the new generation.
        Write-Host '[Evelyn] Rotating the existing Host Supervisor generation.'
        New-Item -ItemType Directory -Force -Path (
            Split-Path -Parent $supervisorStopRequest
        ) | Out-Null
        Set-Content -LiteralPath $supervisorStopRequest -Value (
            'credential generation rotation at ' + (Get-Date).ToString('s')
        ) -Encoding UTF8 -Force
        $retireDeadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $retireDeadline) {
            if (-not (Test-HostSupervisorRunning)) {
                break
            }
            Start-Sleep -Milliseconds 250
        }
        if (Test-HostSupervisorRunning) {
            throw 'Existing Host Supervisor did not stop for credential rotation.'
        }
    }
}

function Start-HostSupervisor {
    if (Test-HostSupervisorRunning) {
        throw 'A Host Supervisor started concurrently; refusing a duplicate owner.'
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
# The Windows bridge is a status reporter, not a control-plane caller. Keep
# the independent Bot API control credential out of the supervisor tree.
Remove-Item Env:EVELYN_INTERNAL_CONTROL_TOKEN -ErrorAction SilentlyContinue
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
    $previousReporter = [Environment]::GetEnvironmentVariable(
        'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    $previousInternal = [Environment]::GetEnvironmentVariable(
        'EVELYN_INTERNAL_CONTROL_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    $previousVoiceCapture = [Environment]::GetEnvironmentVariable(
        'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',
        [EnvironmentVariableTarget]::Process
    )
    try {
        Set-ProcessEnvironmentVariable `
            -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
            -Value $localBridgeStatusAuthToken
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
            -Value $null
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
            -Value $voiceCaptureHostAuthToken
        Start-PowerShellWindow `
            -Title 'Evelyn Host Supervisor' `
            -Script $pythonCommand `
            -Visible:$visible
    } finally {
        Set-ProcessEnvironmentVariable `
            -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
            -Value $previousReporter
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
            -Value $previousInternal
        Set-ProcessEnvironmentVariable `
            -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
            -Value $previousVoiceCapture
    }
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

try {
    # Keep channel credentials out of every child by default. The two narrow
    # launch helpers above add only the credential each authorized child needs
    # for the instant its process environment is captured.
    Set-ProcessEnvironmentVariable `
        -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
        -Value $null
    Set-ProcessEnvironmentVariable `
        -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
        -Value $null
    Set-ProcessEnvironmentVariable `
        -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
        -Value $null

    Assert-DockerReady
    $startupStage = 'tts_profile'
    Assert-TtsProfileReady
    $startupStage = 'host_supervisor'
    Stop-PreviousHostSupervisorGeneration
    $startupStage = 'docker_start'
    Start-DockerCore

    $startupStage = 'service_readiness'
    Wait-Port -HostName '127.0.0.1' -Port 9820 -Label 'Main-LLM'
    Wait-Port -HostName '127.0.0.1' -Port 9822 -Label 'Router-LLM'
    Wait-Port -HostName '127.0.0.1' -Port 9821 -Label 'Sub-LLM'
    Wait-Port -HostName '127.0.0.1' -Port 8880 -Label 'OmniVoice-TTS' -ModelStartup
    Wait-Port -HostName '127.0.0.1' -Port 8892 -Label 'STT'
    Wait-Port -HostName '127.0.0.1' -Port 8891 -Label 'Vision'
    Wait-HttpReady -Url 'http://127.0.0.1:8880/health' -Label 'OmniVoice-TTS' -Contract 'omnivoice'
    Wait-HttpReady -Url 'http://127.0.0.1:8892/health' -Label 'STT'
    Wait-HttpReady -Url 'http://127.0.0.1:8891/health' -Label 'Vision' -Contract 'vision'
    Wait-HttpReady -Url "http://127.0.0.1:$botApiPort/health" -Label 'Docker Bot API' -Contract 'bot_api'
    Wait-HttpReady -Url "http://127.0.0.1:$controlPagePublicPort/health" -Label 'Docker Control Page' -Contract 'control_page'

    $startupStage = 'host_supervisor'
    Start-HostSupervisor

    Write-Host "[Evelyn] Docker local core is ready. Control page: $controlPageUrl"
    Write-Host "[Evelyn] Windows Host Supervisor log: $supervisorLog"
    $startupStage = 'control_page_open'
    Open-ControlPage
    $startupStage = 'complete'
    Remove-Item -LiteralPath (
        Join-Path $projectRoot 'runtime_artifacts\logs\background_start\startup-error.log'
    ) -Force -ErrorAction SilentlyContinue
} finally {
    Set-ProcessEnvironmentVariable `
        -Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN' `
        -Value $previousLocalBridgeStatusAuthToken
    Set-ProcessEnvironmentVariable `
        -Name 'EVELYN_INTERNAL_CONTROL_TOKEN' `
        -Value $previousInternalControlToken
    Set-ProcessEnvironmentVariable `
        -Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN' `
        -Value $previousVoiceCaptureHostAuthToken
}
} catch {
    $startupExitCode = 1
    Write-EvelynStartupFailure `
        -Stage $startupStage `
        -ErrorRecord $_ `
        -ProjectRoot $projectRoot
} finally {
    # This outer boundary also covers failures before helper functions or the
    # main launch block have been defined (for example a dirty-source error).
    [Environment]::SetEnvironmentVariable(
        'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',
        $previousLocalBridgeStatusAuthToken,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        'EVELYN_INTERNAL_CONTROL_TOKEN',
        $previousInternalControlToken,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',
        $previousVoiceCaptureHostAuthToken,
        [EnvironmentVariableTarget]::Process
    )
}

if ($startupExitCode -ne 0) {
    exit $startupExitCode
}
